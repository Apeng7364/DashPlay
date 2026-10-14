"""Offline safeguards: GPT corruption, full backup planning and staged raw readback gates."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).resolve().parents[2] / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


backup = load('f50-backup')
root_plan = load('f50-root-plan')


def synthetic_gpt():
    names = ['boot_a','boot_b','trustos_a','trustos_b','sml_a','sml_b','misc','userdata']
    entries = bytearray(128 * len(names))
    for i,name in enumerate(names):
        start = i*128
        entries[start:start+16] = b'1' * 16
        struct.pack_into('<QQ',entries,start+32,2048+i*8,2055+i*8)
        entries[start+56:start+56+len(name)*2] = name.encode('utf-16-le')
    header = bytearray(512);header[:8] = b'EFI PART'
    struct.pack_into('<III',header,8,0x10000,92,0)
    struct.pack_into('<4Q',header,24,1,4095,2048,4090)
    struct.pack_into('<QIII',header,72,2,len(names),128,zlib.crc32(entries))
    struct.pack_into('<I',header,16,zlib.crc32(header[:92]))
    return b'\0'*512+header+entries


class BackupTests(unittest.TestCase):
    def test_all_partitions_including_userdata_use_raw_offsets(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'gpt').write_bytes(synthetic_gpt());(p/'tool').write_text('offline fixture')
            backup.plan(p/'gpt',p/'out',p/'tool')
            text=(p/'out/backup.sh').read_text()
            self.assertIn('userdata.bin',text)
            self.assertIn('read_part user_partition 1048576 4096 boot_a.bin',text)
            self.assertIn('boot_a.verify.bin',text)
            self.assertNotIn(' w ',text)
            self.assertNotIn(' r all',text)
    def test_rejects_corrupt_header_and_partition_table(self):
        for position in (512+40,1024+56):
            raw=bytearray(synthetic_gpt());raw[position] ^= 1
            with self.assertRaises(ValueError):backup.layout(raw)


class RootReadbackTests(unittest.TestCase):
    def test_checks_raw_both_slots_and_protected_gpt_before_continuing(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);sha=lambda x:hashlib.sha256(x).hexdigest()
            parts={n:dict(size=4,sha256=sha(b'orig')) for n in (*root_plan.PARTS,'primary_gpt','secondary_gpt')}
            (out/'expected.json').write_text(json.dumps(dict(parts=parts,boot_sha256=sha(b'boot'),trustos_sha256=sha(b'toss'))))
            for stage in ('before','boot','trustos'):
                for name in parts:
                    data=b'orig'
                    if stage != 'before' and name.startswith('boot_'):data=b'boot'
                    if stage == 'trustos' and name.startswith('trustos_'):data=b'toss'
                    (out/f'{stage}-{name}.bin').write_bytes(data)
                root_plan.check(out,stage)
            (out/'trustos-secondary_gpt.bin').write_bytes(b'bad!')
            with self.assertRaises(ValueError):root_plan.check(out,'trustos')
            (out/'boot-boot_a.bin').write_bytes(b'bad!')
            with self.assertRaises(ValueError):root_plan.check(out,'boot')


if __name__ == '__main__':unittest.main()
