"""f50-install.py offline: the spd_dump dialogue, the installer's dry-run gate and its password file."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).resolve().parents[2] / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


install = load('f50-install')

# Stands in for spd_dump's interactive mode: the prompt has no newline; read_part writes under "path".
FAKE_SPD = '''#!{python}
import os, sys
prompt = {prompt!r}
save = ''
sys.stdout.write('FDL2 ALREADY EXECUTED, SKIP\\n' + prompt); sys.stdout.flush()
for line in sys.stdin:
    cmd = line.split()
    if cmd[:1] == ['path']: save = cmd[1]
    if cmd[:1] == ['read_part']:
        with open(os.path.join(save, os.path.basename(cmd[4])), 'wb') as f: f.write(b'x' * int(cmd[3]))
    if cmd[:1] == ['quit']: sys.exit(0)
    sys.stdout.write('done\\n' + prompt); sys.stdout.flush()
'''

DRY_RUN = '''- Plan
  system:         openwrt-luci (zip 25.12.5, kernel 5.4.210)
  storage:        SD card /dev/block/mmcblk1, 29.7 GiB (/data/adb/mu300-install.conf)
  filesystem:     CREATE new ext4 (erases what is there) (default)
  boots:          openwrt-luci (/data/adb/mu300-install.conf)
  password:       generated, written to /data/adb/mu300-linux-password.txt
  writes:         /dev/block/mmcblk1, boot_a, 32 bytes of misc (boot_b, the partition table and userdata are not touched)
- Dry run: nothing was written.
'''


class FdlTests(unittest.TestCase):
    def session(self, prompt):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        root = Path(d.name)
        fake = root / 'spd_dump'
        fake.write_text(FAKE_SPD.format(python=sys.executable, prompt=prompt)); fake.chmod(0o755)
        old = install.SPD; install.SPD = fake; self.addCleanup(setattr, install, 'SPD', old)
        (root / 'backup').mkdir()
        fdl = install.Fdl(root, root / 'fdl.log'); self.addCleanup(fdl.close)
        return root, fdl

    def test_waits_for_each_prompt_and_reads_into_the_save_path(self):
        root, fdl = self.session('FDL2 >')
        fdl.wait(10, 'download mode')
        fdl.send('path backup', 10)
        for size in (4096, 1 << 20):
            fdl.send(f'read_part user_partition 0 {size} part{size}.bin', 10)
            # each send waits for its own prompt, so the file is complete when it returns
            self.assertEqual((root / 'backup' / f'part{size}.bin').stat().st_size, size)

    def test_a_prompt_without_the_space_is_not_taken_for_download_mode(self):
        # 2026-10-15: an agent's own driver waited for "FDL2>" while spd_dump printed "FDL2 >"
        root, fdl = self.session('FDL2>')
        with self.assertRaises(install.Stop):
            fdl.wait(2, 'download mode')

    def test_spd_dump_ending_stops_the_stage(self):
        root, fdl = self.session('FDL2 >')
        fdl.wait(10, 'download mode')
        with self.assertRaisesRegex(install.Stop, 'ended'):
            fdl.send('quit', 10)


class FakeAndroid:
    """Work.adb stand-in: each "adb shell" command gets the reply of the first key it ends with or starts with."""
    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def adb(self, *args, check=True, binary=False, timeout=120):
        command = args[-1]
        self.calls.append(command)
        for key, reply in self.replies.items():
            if command.endswith(key) or command.startswith(key):
                return reply
        return ''


class InstallerTests(unittest.TestCase):
    def test_dry_run_only_for_the_sd_card_boot_a_and_misc(self):
        install.check_dry_run(DRY_RUN)
        for broken in (DRY_RUN.replace('- Dry run: nothing was written.\n', ''),
                       DRY_RUN.replace('SD card /dev/block/mmcblk1', 'internal, offset 123'),
                       DRY_RUN.replace('/dev/block/mmcblk1, boot_a', 'the Linux region, boot_a'),
                       DRY_RUN + '! could not read the SD card\n'):
            with self.assertRaises(install.Stop):
                install.check_dry_run(broken)

    def test_root_password_from_the_installer_file(self):
        text = ('MU300 Linux 25.12.5, 2026-10-15 21:25\nuser: root\npassword: example-pass word\n'
                'Delete this file after the first login.\n\nMU300 Linux noble, 2026-10-01 10:00\nuser: ubuntu\n'
                'password: other\n')
        self.assertEqual(install.parse_password(text), 'example-pass word')
        with self.assertRaises(install.Stop):
            install.parse_password('MU300 Linux noble\nuser: ubuntu\npassword: other\n')

    def test_switch_runs_from_modules_update_right_after_the_install(self):
        # 2026-10-15: Magisk puts a freshly installed module in modules_update until the next boot; modules/ held only
        # module.prop and "update", so "sh /data/adb/modules/mu300_linux_switch/switch.sh" found no file
        fresh = '/data/adb/modules_update/mu300_linux_switch/switch.sh'
        w = FakeAndroid({'ls': fresh + '\n', '--dry-run 2>&1': 'misc  new: x\ndry run: nothing was written\n',
                         'switch.sh 2>&1': 'slot a armed for one boot. Rebooting into Linux.\n'})
        install.arm_linux(w)
        self.assertEqual([c for c in w.calls if not c.startswith('ls')],
                         [f'sh {fresh} --dry-run 2>&1', f'sh {fresh} 2>&1'])

    def test_switch_missing_or_refusing_stops_before_a_reboot(self):
        with self.assertRaisesRegex(install.Stop, 'switch script'):
            install.arm_linux(FakeAndroid({'ls': ''}))
        path = '/data/adb/modules/mu300_linux_switch/switch.sh\n'
        refusing = FakeAndroid({'ls': path, '--dry-run 2>&1': 'mu300-linux: boot_a is not a boot image - install Linux first\n'})
        with self.assertRaisesRegex(install.Stop, 'not a boot image'):
            install.arm_linux(refusing)
        self.assertFalse(any(c.endswith('switch.sh 2>&1') for c in refusing.calls))
        failing = FakeAndroid({'ls': path, '--dry-run 2>&1': 'dry run: nothing was written\n',
                               'switch.sh 2>&1': 'mu300-linux: misc verify failed\n'})
        with self.assertRaisesRegex(install.Stop, 'misc verify failed'):
            install.arm_linux(failing)

    def test_stages_need_the_one_before(self):
        # Work puts the pinned tools first on PATH, for the stage's own commands only
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ):
            w = install.Work(d)
            with self.assertRaisesRegex(install.Stop, 'run "check" first'):
                install.flash(w, None)
            w.done('check')
            w.needs('check')
            self.assertEqual(os.stat(w.private).st_mode & 0o777, 0o700)


if __name__ == '__main__':
    unittest.main()
