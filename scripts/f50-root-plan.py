#!/usr/bin/env python3
"""Print staged FDL commands and verify their raw readbacks. Never contacts a device.

prepare BACKUP BOOT TRUSTOS OUTPUT: requires f50-backup.py verified full backup.
check OUTPUT before|boot|trustos: stop on any mismatch. In one interactive FDL
session, run stage-*.txt one line at a time, then its host-side check, then next stage.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

PARTS = ('boot_a', 'boot_b', 'trustos_a', 'trustos_b', 'misc')


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def prepare(backup, boot, tos, out):
    manifest = json.loads((backup/'verified.json').read_text())
    verified = {p['file']:p for p in manifest}
    plan = json.loads((backup/'plan.json').read_text())
    parts = {p['name']:p for p in plan['items']}
    expected = {}
    for name in (*PARTS, 'primary_gpt', 'secondary_gpt'):
        f = backup/(name+'.bin')
        if f.stat().st_size != parts[name]['size'] or digest(f) != verified[f.name]['sha256']:
            raise ValueError('backup changed: '+name)
        expected[name] = dict(parts[name], sha256=verified[f.name]['sha256'])
    # This boot builder uses the audited B16 kernel. A different stock kernel stops here.
    with (backup/'boot_b.bin').open('rb') as f:
        header = f.read(4096)
        size = int.from_bytes(header[8:12], 'little')
        kernel_hash = hashlib.sha256(f.read(size)).hexdigest()
    if header[:8] != b'ANDROID!' or kernel_hash != '2fa5831843df70e11da3b5d1f0f5489767d9e8bfc1e25005d2051aed483823dc':
        raise ValueError('unreviewed stock B16 kernel; stop')
    if boot.stat().st_size != parts['boot_b']['size'] or tos.stat().st_size != parts['trustos_b']['size']:
        raise ValueError('candidate partition size mismatch')
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    shutil.copyfile(boot,out/'root-boot.img');shutil.copyfile(tos,out/'root-trustos.bin')
    result = dict(parts=expected, boot_sha256=digest(boot), trustos_sha256=digest(tos))
    (out/'expected.json').write_text(json.dumps(result,indent=2)+'\n')
    for stage in ('before','boot','trustos'):
        lines = [] if stage == 'before' else [('w boot_b root-boot.img' if stage == 'boot' else 'w trustos_b root-trustos.bin')]
        for name,p in expected.items():
            lines.append(f"read_part user_partition {p['offset']} {p['size']} {stage}-{name}.bin")
        (out/f'stage-{stage}.txt').write_text('\n'.join(lines)+'\n')
    print('ROOT_PLAN_READY; execute before/check, boot/check, trustos/check in one FDL session; reset only after all pass')


def check(out, stage):
    data = json.loads((out/'expected.json').read_text())
    for name,p in data['parts'].items():
        file = out/f'{stage}-{name}.bin'
        if file.stat().st_size != p['size']:
            raise ValueError('readback size mismatch: '+name)
        wanted = {p['sha256']}
        if stage in ('boot','trustos') and name.startswith('boot_'):
            wanted = {data['boot_sha256']} if name == 'boot_b' else {p['sha256'],data['boot_sha256']}
        if stage == 'trustos' and name.startswith('trustos_'):
            wanted = {data['trustos_sha256']} if name == 'trustos_b' else {p['sha256'],data['trustos_sha256']}
        if digest(file) not in wanted:
            raise ValueError('readback mismatch; NO next write/reset: '+name)
        if stage == 'trustos' and name.startswith('boot_') and digest(file) != digest(out/f'boot-{name}.bin'):
            raise ValueError('boot changed during TrustOS write: '+name)
    print('READBACK_OK '+stage)


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('prepare')
    for n in ('backup','boot','tos','out'):q.add_argument(n,type=Path)
    q=sub.add_parser('check');q.add_argument('out',type=Path);q.add_argument('stage',choices=('before','boot','trustos'))
    a=p.parse_args()
    prepare(a.backup,a.boot,a.tos,a.out) if a.command=='prepare' else check(a.out,a.stage)
