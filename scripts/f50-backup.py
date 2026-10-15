#!/usr/bin/env python3
"""Plan/verify a raw F50 user-area backup. No device writes or subprocess execution.

plan PRIMARY_GPT OUTPUT_DIR SPD_DUMP creates backup.sh (run after authorization).
verify OUTPUT_DIR checks every planned partition and both GPTs. Reads raw offsets, never r all/all_lite:
named NV reads may alias/strip their header.
Skips SKIPPED (about 27 GiB of the 31 GB user area, ~40 minutes of reading): the installation never writes
them, and the 2026-10-14 restoration to stock B16 rebuilt them with the stock factory reset.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import struct
import zlib

# Android user data and logs, rebuilt by a factory reset; everything else, NV/modem and both GPTs included, is read.
SKIPPED = ('userdata', 'cache', 'blackbox', 'fulldumpdb', 'sysdumpdb', 'uboot_log')

def require(ok, why):
    if not ok:
        raise ValueError(why)


def header(block):
    require(block[:8] == b"EFI PART", "missing GPT signature")
    size, crc = struct.unpack_from("<II", block, 12)
    require(92 <= size <= 512, "invalid GPT header size")
    raw = bytearray(block[:size]); raw[16:20] = b"\0" * 4
    require(zlib.crc32(raw) == crc, "GPT header CRC mismatch")
    current, other, first, last = struct.unpack_from("<4Q", block, 24)
    table, count, entry_size, entries_crc = struct.unpack_from("<QIII", block, 72)
    require(count <= 4096 and entry_size >= 128 and entry_size % 8 == 0, "invalid GPT table dimensions")
    return dict(current=current, other=other, first=first, last=last, table=table,
                count=count, entry_size=entry_size, entries_crc=entries_crc)


def layout(primary):
    h = header(primary[512:1024])
    require(h['current'] == 1 and h['first'] <= h['last'] < h['other'], "invalid primary GPT LBAs")
    entries = primary[h['table'] * 512:h['table'] * 512 + h['count'] * h['entry_size']]
    require(len(entries) == h['count'] * h['entry_size'], "truncated GPT table")
    require(zlib.crc32(entries) == h['entries_crc'], "GPT entries CRC mismatch")
    parts = []
    for i in range(h['count']):
        e = entries[i*h['entry_size']:(i+1)*h['entry_size']]
        if e[:16] == b"\0" * 16:
            continue
        start, end = struct.unpack_from("<QQ", e, 32)
        name = e[56:128].decode('utf-16-le').rstrip('\0')
        require(re.fullmatch(r'[A-Za-z0-9_]+', name), "unsafe partition name")
        require(h['first'] <= start <= end <= h['last'], "partition outside usable LBAs")
        parts.append(dict(name=name, offset=start*512, size=(end-start+1)*512))
    parts.sort(key=lambda p: p['offset'])
    require(len({p['name'] for p in parts}) == len(parts), "duplicate partition name")
    require(all(a['offset']+a['size'] <= b['offset'] for a,b in zip(parts,parts[1:])), "overlapping partitions")
    require({'boot_a','boot_b','trustos_a','trustos_b','sml_a','sml_b','misc','userdata'} <= {p['name'] for p in parts}, "not expected F50 A/B layout")
    return h, entries, parts


def plan(primary, out, tool):
    raw = primary.read_bytes()
    h, entries, parts = layout(raw)
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    (out/'discovery-gpt.bin').write_bytes(raw)
    end = (h['other']+1)*512
    tail_size = ((len(entries)+511)//512 + 1)*512
    kept = [p for p in parts if p['name'] not in SKIPPED]
    items = [dict(name='primary_gpt',offset=0,size=len(raw)), *kept,
             dict(name='secondary_gpt',offset=end-tail_size,size=tail_size)]
    commands = [str(tool.resolve()), '--wait','600','--kickto','2','exec','path','.']
    for p in items:
        commands += ['read_part','user_partition',str(p['offset']),str(p['size']),p['name']+'.bin']
        if p['name'] in ('boot_a','boot_b','trustos_a','trustos_b','misc','primary_gpt','secondary_gpt'):
            commands += ['read_part','user_partition',str(p['offset']),str(p['size']),p['name']+'.verify.bin']
    commands += ['reset']
    (out/'plan.json').write_text(json.dumps(dict(disk_bytes=end, items=items,
        tool_sha256=hashlib.sha256(tool.read_bytes()).hexdigest(), skipped=[p['name'] for p in parts if p not in kept],
        uncovered=['eMMC boot0/boot1/RPMB (not user area)', 'SD card']),indent=2)+'\n')
    script = '#!/bin/sh\nset -eu\ncd -- '+shlex.quote(str(out.resolve()))+'\nexec '+shlex.join(commands)+'\n'
    (out/'backup.sh').write_text(script)
    (out/'backup.sh').chmod(0o700)
    print(f"PLAN_READY {len(kept)} of {len(parts)} partitions, skipped {' '.join(p['name'] for p in parts if p not in kept)}; "
          f"need {sum(p['size'] for p in items)} bytes plus repeated reads")


def verify(out):
    plan = json.loads((out/'plan.json').read_text())
    result = []
    for p in plan['items']:
        file = out/(p['name']+'.bin')
        require(file.stat().st_size == p['size'], f"wrong size: {p['name']}")
        with file.open('rb') as f:
            sha = hashlib.file_digest(f,'sha256').hexdigest()
        repeat = out/(p['name']+'.verify.bin')
        if p['name'] in ('boot_a','boot_b','trustos_a','trustos_b','misc','primary_gpt','secondary_gpt'):
            require(repeat.stat().st_size == p['size'], "repeat size mismatch")
            with repeat.open('rb') as f:
                require(hashlib.file_digest(f,'sha256').hexdigest() == sha, "repeat read mismatch")
        result.append(dict(file=file.name,bytes=p['size'],sha256=sha))
    primary = (out/'primary_gpt.bin').read_bytes()
    h, entries, parts = layout(primary)
    require(primary == (out/'discovery-gpt.bin').read_bytes(), "GPT changed since discovery")
    kept = [p for p in parts if p['name'] not in SKIPPED]
    require({p['name'] for p in kept} <= {p['name'] for p in plan['items']}, "plan misses a partition")
    tail = (out/'secondary_gpt.bin').read_bytes()
    b = header(tail[-512:])
    require(b['current'] == h['other'] and b['other'] == 1, "GPT headers not reciprocal")
    require(b['first'] == h['first'] and b['last'] == h['last'], "usable ranges differ")
    require(tail[:len(entries)] == entries and b['entries_crc'] == h['entries_crc'], "GPT tables differ")
    require(b['table']*512 == plan['disk_bytes']-len(tail), "backup table offset differs")
    (out/'verified.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f"BACKUP_VERIFIED {len(kept)} of {len(parts)} partitions; {' '.join(plan['skipped'])}, boot0/boot1/RPMB and SD not included")


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('plan');q.add_argument('primary',type=Path);q.add_argument('out',type=Path);q.add_argument('tool',type=Path)
    q=sub.add_parser('verify');q.add_argument('out',type=Path)
    a=p.parse_args()
    plan(a.primary,a.out,a.tool) if a.command=='plan' else verify(a.out)
