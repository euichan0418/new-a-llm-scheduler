# SPDX-License-Identifier: GPL-2.0
"""C/E 공통 읽기 전용 커널 감시 프로세스의 수명만 관리한다."""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
from records import save, require

class KernelGuard:
    def __init__(self, path, binary):
        self.path, self.binary = Path(path), binary
        self.process = None
        self.pidfd = None

    def start(self):
        argv = ['sudo', '-n', 'taskset', '-c', '15', self.binary]
        save(self.path / 'kernel-watch-command.json', dict(argv=argv, bpf_loaded=False))
        self.err = (self.path / 'kernel-watch.stderr.log').open('x')
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.err, text=True, bufsize=1)
        self.pidfd = os.pidfd_open(self.process.pid)
        ready, _, _ = select.select([self.process.stdout], [], [], 8)
        require(ready and self.process.stdout.readline() == 'READY\n' and self.process.poll() is None, '읽기 전용 커널 감시가 시작되지 않았습니다')

    def check(self):
        require(self.process is not None and self.process.poll() is None, '커널 감시 오류 또는 예기치 않은 종료가 발생했습니다')

    def owns(self, pid):
        if self.process is None or self.process.poll() is not None:
            return False
        for _ in range(8):
            if pid == self.process.pid:
                return True
            try:
                raw = Path(f'/proc/{pid}/stat').read_text()
                pid = int(raw[raw.rfind(')')+2:].split()[1])
            except (OSError, ValueError, IndexError):
                return False
            if pid <= 1:
                break
        return False

    def close(self):
        errors, record = [], None
        try:
            if self.process and self.process.poll() is None:
                self.process.stdin.write('STOP\n')
                self.process.stdin.flush()
            if self.process:
                try:
                    self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    errors.append('커널 감시 종료 제한 시간을 초과했습니다')
                    try:
                        signal.pidfd_send_signal(self.pidfd, signal.SIGTERM)
                        self.process.wait(timeout=3)
                    except (PermissionError, ProcessLookupError, subprocess.TimeoutExpired):
                        pass
                if self.process.poll() is not None:
                    self.process.stdin.close()
                    text = self.process.stdout.read().strip()
                    record = json.loads(text) if text else None
                    require(self.process.returncode == 0 and isinstance(record, dict) and record.get('status') == 'PASS' and record.get('kernel_opened') is True and record.get('fd_closed') is True and record.get('bpf_loaded') is False, '커널 감시 완료 증거가 불완전합니다')
        except BaseException as error:
            errors.append(str(error))
        finally:
            if self.process:
                if not self.process.stdin.closed:
                    self.process.stdin.close()
                self.process.stdout.close()
            if self.pidfd is not None:
                os.close(self.pidfd)
            if hasattr(self, 'err'):
                self.err.close()
            save(self.path / 'kernel-watch-result.json', dict(status='FAIL' if errors else 'PASS', errors=errors, record=record))
        require(not errors and record is not None, '; '.join(errors) or '커널 감시 결과가 없습니다')
