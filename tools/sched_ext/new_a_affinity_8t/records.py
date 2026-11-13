# SPDX-License-Identifier: GPL-2.0
"""새 준비물의 JSON과 봉인 입력만 다룬다."""
import hashlib
import json
import os
from pathlib import Path

def require(ok, message):
    if not ok:
        raise ValueError(message)

def read(path):
    return json.loads(Path(path).read_text())

def save(path, value):
    with Path(path).open('x') as output:
        json.dump(value, output, indent=2, sort_keys=True, allow_nan=False)
        output.write('\n')

def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()

def verify(manifest):
    for name, digest in manifest['files'].items():
        require(Path(name).is_file() and sha(name) == digest, '봉인 입력이 변경됐습니다: ' + name)
    for name, target in manifest['symlinks'].items():
        require(Path(name).is_symlink() and os.readlink(name) == target, '봉인 링크가 변경됐습니다: ' + name)

def seal_check(root):
    root = Path(root)
    seal = read(root / 'seal.json')
    require(seal['status'] == 'READY_FOR_AFFINITY_FACTOR_ONLY', 'Affinity 요인 비교용 봉인이 아닙니다')
    require(sha(root / 'prepared.json') == seal['prepared_sha256'] and sha(root / 'host-profile.exploratory.json') == seal['profile_sha256'], '봉인 manifest 또는 Host Profile이 변경됐습니다')
    verify(read(root / 'prepared.json'))

def ttwu_enabled(text):
    words = text.split()
    require('TTWU_QUEUE' in words and 'NO_TTWU_QUEUE' not in words, 'TTWU_QUEUE 활성 상태를 확인할 수 없습니다')
    return True
