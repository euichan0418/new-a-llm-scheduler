# SPDX-License-Identifier: GPL-2.0
"""기존 연구 도구에서 필요한 지원 함수만 보존한 공개 사본이다."""
import hashlib
from pathlib import Path
import struct


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def elf_sections(path):
    data=Path(path).read_bytes()
    if data[:6]!=b'\x7fELF\x02\x01':raise ValueError('Expected ELF64 little-endian')
    header=struct.unpack_from('<16sHHIQQQIHHHHHH',data)
    offset,size,count,string_index=header[6],header[11],header[12],header[13]
    sections=[struct.unpack_from('<IIQQQQIIQQ',data,offset+i*size) for i in range(count)]
    strings=sections[string_index];names=data[strings[4]:strings[4]+strings[5]]
    result={}
    for s in sections:
        name=names[s[0]:].split(b'\0',1)[0].decode()
        raw=data[s[4]:s[4]+s[5]]
        result[name]=dict(flags=s[2],size=s[5],raw=raw)
    return result
