# SPDX-License-Identifier: GPL-2.0
"""Foreground user reauthentication at verified resource-free boundaries only."""
import os
from pathlib import Path
import subprocess
import time
import uuid
from runtime_core import require,save
import evidence

# Local planning limits, not a claim about the administrator's sudo timeout.
AUTH_HORIZON_S=600
NEXT_SESSION_RESERVE_S=360


def terminal_context():
    require(os.getuid()!=0 and os.isatty(0),'AUTH_TERMINAL_REQUIRED: normal user terminal; no session started')
    try:
        value=dict(sid=os.getsid(0),tty=os.fstat(0).st_rdev,pgrp=os.getpgrp(),foreground=os.tcgetpgrp(0))
    except OSError as error:raise ValueError('AUTH_TERMINAL_UNAVAILABLE: '+str(error))from error
    require(value['tty']!=0 and value['pgrp']==value['foreground'],'AUTH_FOREGROUND_TERMINAL_REQUIRED')
    return value


class BoundaryAuth:
    def __init__(self,root,quiet,clock=None):
        self.folder=Path(root)/'auth-boundaries';self.folder.mkdir(exist_ok=True)
        self.quiet=quiet;self.clock=clock or time.monotonic;self.context=None;self.issued=None;self.previous=None

    def event(self,kind,**data):
        save(self.folder/(uuid.uuid4().hex+'.json'),dict(kind=kind,monotonic=self.clock(),background_keepalive=False,password_recorded=False,**data))

    def safe(self):
        self.quiet()
        if self.previous is not None:
            path,campaign,slot=self.previous
            evidence.verify_session(path,campaign,slot['stage'],slot)
        self.quiet()
        context=terminal_context()
        require(self.context is None or self.context==context,'AUTH_TERMINAL_CONTEXT_CHANGED; no session started')
        return context

    def cached(self):
        command=['/usr/bin/sudo','-N','-n','-v']
        self.event('CACHE_PROBE_STARTED',argv=command,updates_timestamp=False)
        try:result=subprocess.run(command,capture_output=True,text=True,timeout=20,env=dict(os.environ,LC_ALL='C',LANG='C'))
        except BaseException as error:
            self.event('CACHE_PROBE_STOPPED',error=type(error).__name__+': '+str(error),new_session_started=False)
            raise
        self.event('CACHE_PROBE',argv=command,returncode=result.returncode,stdout=result.stdout,stderr=result.stderr,updates_timestamp=False)
        if result.returncode==0:return True
        require(result.returncode==1 and 'sudo:'in result.stderr and 'a password is required'in result.stderr,
                'SUDO_CACHE_CHECK_FAILED: '+result.stderr+'; no session started')
        return False

    def authenticate(self,reason,*,initial=False,cached_valid=False):
        context=self.safe()
        self.event('USER_AUTH_REQUESTED',reason=reason,previous_raw=str(self.previous[0])if self.previous else None,context=context)
        print('안전한 세션 경계: 서버/로더/관측기 없음, sched_ext disabled'+', 직전 raw/Cleanup 재검증 완료'if self.previous else '안전한 시작 경계: 실행 자원 없음, sched_ext disabled',flush=True)
        print('sudo 사용자 인증 요청: '+reason+'. 일반 터미널에서 직접 비밀번호를 입력하십시오. 실패/취소 시 새 세션을 시작하지 않습니다.',flush=True)
        try:
            # -k -v together would not populate a fresh timestamp. Keep them separate.
            if not initial and cached_valid:
                command=['/usr/bin/sudo','-k']
                result=subprocess.run(command,check=False)
                self.event('CURRENT_CACHE_INVALIDATED',argv=command,returncode=result.returncode)
                require(result.returncode==0,'SUDO_CACHE_INVALIDATION_FAILED; no session started')
            command=['/usr/bin/sudo','-v']
            result=subprocess.run(command,check=False)
            validated_at=self.clock()
            self.event('USER_AUTH_RESULT',argv=command,returncode=result.returncode,reason=reason)
            require(result.returncode==0,'USER_AUTH_FAILED_OR_CANCELLED; no session started')
            require(self.cached(),'POST_AUTH_CACHE_UNAVAILABLE; no session started')
            require(self.safe()==context,'AUTH_CONTEXT_CHANGED_DURING_PROMPT')
            self.context=context;self.issued=validated_at
        except BaseException as error:
            self.issued=None
            self.event('USER_AUTH_STOPPED',reason=reason,error=type(error).__name__+': '+str(error),new_session_started=False)
            raise

    def initial(self):
        require(self.issued is None,'Initial authentication already recorded')
        self.authenticate('실험 시작 인증',initial=True)

    def before_session(self,label,ready=None,after_auth=None):
        self.safe();require(self.issued is not None,'INITIAL_USER_AUTH_REQUIRED; no session started')
        age=self.clock()-self.issued;require(age>=0,'AUTH_CLOCK_MOVED_BACKWARDS')
        valid=self.cached()
        if not valid or age+NEXT_SESSION_RESERVE_S>=AUTH_HORIZON_S:
            self.authenticate('인증 만료'if not valid else'다음 세션의 인증 시간 여유 부족',cached_valid=valid)
            if after_auth is not None:after_auth()
        self.safe()
        # Waiting for a password must not reset or bypass campaign limits/readiness.
        if ready is not None:ready()
        age=self.clock()-self.issued
        require(0<=age and age+NEXT_SESSION_RESERVE_S<AUTH_HORIZON_S,'AUTH_PLANNING_WINDOW_EXCEEDED; no session started')
        require(self.cached(),'AUTH_EXPIRED_BEFORE_SESSION; no session started')
        self.event('SESSION_BOUNDARY_READY',label=label,credential_age_s=age,planning_horizon_s=AUTH_HORIZON_S,next_session_reserve_s=NEXT_SESSION_RESERVE_S,sudo_remaining_time_known=False)

    def completed(self,path,campaign,slot):
        evidence.verify_session(path,campaign,slot['stage'],slot)
        self.previous=(Path(path),campaign,dict(slot))
