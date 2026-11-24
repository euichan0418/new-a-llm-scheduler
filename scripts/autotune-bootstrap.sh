#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
set -euo pipefail
umask 077
[[ "$EUID" != 0 ]] || { echo 'BLOCKED: 일반 사용자로 실행하십시오.' >&2; exit 2; }
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON_BIN=''
for candidate in python3 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -I -B -c 'import sys;sys.exit(sys.version_info < (3,11))'; then
        PYTHON_BIN="$candidate"; break
    fi
done
if [[ -z "$PYTHON_BIN" ]]; then
    AUTO=0
    for arg in "$@"; do [[ "$arg" != '--auto' ]] || AUTO=1; done
    if [[ "$AUTO" != 1 ]] || [[ ! -t 0 ]] || ! grep -Eq '^ID="?ubuntu"?$' /etc/os-release || [[ ! -x /usr/bin/sudo ]]; then
        echo 'BLOCKED: Python 3.11 이상이 필요합니다. --auto 일반 Ubuntu 터미널에서만 승인 후 설치를 시도합니다.' >&2; exit 2
    fi
    distro_python="$(LC_ALL=C /usr/bin/apt-cache policy python3 | awk '/Candidate:/ {print $2; exit}')"
    distro_python="${distro_python#*:}"
    if [[ ! "$distro_python" =~ ^([0-9]+)\.([0-9]+) ]] || (( BASH_REMATCH[1] < 3 || (BASH_REMATCH[1] == 3 && BASH_REMATCH[2] < 11) )); then
        echo 'BLOCKED: 현재 Ubuntu 저장소 기본 Python이3.11 미만이거나 확인되지 않습니다. OS/PPA를 자동 변경하지 않습니다.' >&2; exit 2
    fi
    available="$(df -Pk "$SCRIPT_DIR" | awk 'NR==2 {print $4}')"
    [[ "$available" -ge 524288 ]] || { echo 'BLOCKED: 디스크 여유512MiB 필요' >&2; exit 2; }
    plan="$(/usr/bin/apt-get --simulate --no-remove --no-upgrade --no-install-recommends install python3)"
    if grep -Eq '^Remv |^Inst [^ ]+ \[' <<<"$plan"; then
        echo 'BLOCKED: Python 설치가 기존 패키지 변경을 요구합니다. OS/PPA/커널 자동 변경은 하지 않습니다.' >&2; exit 2
    fi
    printf '%s\n' "$plan" '위 Ubuntu Python3 설치를 승인하면 YES를 입력하십시오. 설치 패키지는 자동 제거하지 않습니다.'
    read -r reply
    [[ "$reply" == 'YES' ]] || { echo 'BLOCKED: 설치 승인 없음' >&2; exit 2; }
    LOG_DIR="$SCRIPT_DIR/../.autotune-work"
    mkdir -p -- "$LOG_DIR"
    LOG_FILE="$LOG_DIR/bootstrap-python-$(date -u +%Y%m%dT%H%M%SZ)-$$.private.log"
    printf '%s\n' "$plan" > "$LOG_FILE"
    /usr/bin/sudo -v
    /usr/bin/sudo -N -n /usr/bin/apt-get -y --no-remove --no-upgrade --no-install-recommends install python3 2>&1 | tee -a "$LOG_FILE"
    python3 -I -B -c 'import sys;sys.exit(sys.version_info < (3,11))' || { echo 'BLOCKED: Ubuntu 저장소의 Python이3.11 미만입니다. OS/PPA를 자동 변경하지 않습니다.' >&2; exit 2; }
    PYTHON_BIN=python3
fi
export PYTHONDONTWRITEBYTECODE=1
exec "$PYTHON_BIN" -I -B "$SCRIPT_DIR/../tools/sched_ext/new_a_bootstrap/package_driver.py" "$@"
