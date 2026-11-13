# SPDX-License-Identifier: GPL-2.0
"""최종 선택은 기존 E와 같으며 정책 파일 변경을 허용하지 않는다."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def verify():
    manifest = json.loads((HERE / 'policy-manifest.json').read_text())
    if manifest['selected_policy'] != 'NEW_A_E_FULL_UNCHANGED' or manifest['policy_change'] != 'NONE':
        raise ValueError('최종 정책 선택이 변경됐습니다')
    if manifest['strict_acceptance'] != 'FAILED' or manifest['safety_verdict'] != 'FAIL':
        raise ValueError('기존 안전성 판정이 변경됐습니다')
    for relative, digest in manifest['files'].items():
        path = (REPO / relative).resolve()
        if not path.is_relative_to(REPO) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('최종 E 원본이 변경됐습니다: ' + relative)
    return manifest
