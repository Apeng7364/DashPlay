#!/usr/bin/env python3
"""ZTE F50 from stock B16 to OpenWrt with DashPlay, one command per stage (Apple Silicon Mac).

    python3 f50-install.py check    WORK            tools, pinned downloads, free space
    python3 f50-install.py flash    WORK            full backup and Root in ONE download-mode session
    python3 f50-install.py openwrt  WORK            OpenWrt on the SD card, Android B kept
    python3 f50-install.py dashplay WORK            DashPlay packages and Bluetooth
    python3 f50-install.py identity WORK [DIR]      CarPlay identity (DIR, or from DiPlay's release) onto the box
    python3 f50-install.py tesla    WORK --sim yes|no [--cn-149]

WORK is the install directory of the skill (skills/dashplay-install/SKILL.md); this stage's files go to WORK/f50.
Run it with WORK/venv/bin/python (the pinned requirements-f50.txt). Every stage prints progress, "ACTION: ..."
for what the owner has to do, and ends with "F50_OK <stage>" (exit 0) or "F50_STOP <stage>: <reason>" (exit 1).
A stop changes nothing more and retries nothing: the files stay in WORK/f50 for whoever looks next.
The checks are those of the skill's F0-F3 (it was the manual procedure, 2026-10-14): the device is only written
after a verified backup, every write is read back from both slots, and nothing but boot_b, trustos_b (Root) and
the SD card, boot_a and 32 bytes of misc (the installer of OpenWrt) is ever written.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import pty
import re
import select
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile

DP = Path(__file__).resolve().parents[1]
SPD = DP / 'tools/mac-arm64/spd_dump'
SPD_VERSION = 'branch:pinned-raw-read, sha1:d24c21a0c93a545c0130668caa1826d59ceebe48'
PROMPT = b'FDL2 >'    # spd_dump's interactive prompt, with the space
ANDROID = '192.168.0.1:5555'
OPENWRT = '192.168.77.1'
TESLA_LAN = '100.96.0.1'
B16 = 'F50_FLYMODEM_ZYV1.0.0B16'
FEED = 'https://api.github.com/repos/yizhixiaoheigou/openwrt-dashplay/releases/latest'
DIPLAY = 'https://api.github.com/repos/shihabal3amri/DiPlay/releases'
CHECK_IDENTITY = '''set -eu
umask 077
cd /opt/dashplay/identity/offline-mfi
chmod 600 identity.pk8 certificate.p7b
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
openssl pkey -inform DER -in identity.pk8 -pubout -outform DER -out "$tmp/key.pub"
openssl pkcs7 -inform DER -in certificate.p7b -print_certs -out "$tmp/certs.pem"
openssl x509 -in "$tmp/certs.pem" -pubkey -noout > "$tmp/cert.pub.pem"
openssl pkey -pubin -in "$tmp/cert.pub.pem" -outform DER -out "$tmp/cert.pub"
test -s "$tmp/key.pub"
test -s "$tmp/cert.pub"
cmp -s "$tmp/key.pub" "$tmp/cert.pub"
echo IDENTITY_MATCH
'''


class Stop(Exception):
    pass


def require(ok, why):
    if not ok:
        raise Stop(why)


def say(text):
    print(f'[{time.strftime("%H:%M:%S")}] {text}', flush=True)


def action(text):
    print(f'ACTION: {text}', flush=True)


def digest(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


class Work:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.f50 = self.root / 'f50'
        self.private = self.f50 / 'private'
        self.logs = self.f50 / 'logs'
        for d in (self.f50, self.private, self.logs):
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state_file = self.f50 / 'install-state.json'
        self.key = self.private / 'ssh-key'
        self.known_hosts = self.private / 'known_hosts'
        # pinned tools: the skill's bootstrap Python first, then this repository's arm64 builds
        tools = [str(DP / 'tools/mac-arm64'), str(Path(sys.executable).parent), str(self.root / 'adb/platform-tools')]
        os.environ['PATH'] = os.pathsep.join(tools + [os.environ.get('PATH', '')])

    def state(self):
        return json.loads(self.state_file.read_text()) if self.state_file.exists() else {}

    def done(self, stage, **extra):
        s = self.state()
        s[stage] = dict(extra, at=time.strftime('%Y-%m-%d %H:%M:%S'))
        self.state_file.write_text(json.dumps(s, indent=2) + '\n')

    def needs(self, *stages):
        s = self.state()
        for stage in stages:
            require(stage in s, f'run "{stage}" first')

    def run(self, argv, log=None, check=True, **kw):
        """A host command; its output goes to logs/LOG (or is returned) and a failure stops the stage."""
        result = subprocess.run([str(a) for a in argv], capture_output=True, text=True, **kw)
        if log:
            with (self.logs / log).open('a') as f:
                f.write(f'$ {" ".join(map(str, argv))}\n{result.stdout}{result.stderr}\n')
        if check and result.returncode != 0:
            tail = (result.stdout + result.stderr).strip().splitlines()[-5:]
            raise Stop(f'{Path(str(argv[0])).name} failed: {" | ".join(tail)}')
        return result

    # Android over TCP ADB (the Root image opens it; it does not authenticate the client, so only on the USB link)
    def adb(self, *args, check=True, binary=False, timeout=120):
        argv = ['adb', '-s', ANDROID, *args]
        if binary:
            result = subprocess.run(argv, capture_output=True, timeout=timeout)
            require(result.returncode == 0 or not check, f'adb {" ".join(args[:2])} failed')
            return result.stdout
        return self.run(argv, log='adb.log', check=check, timeout=timeout).stdout

    # OpenWrt over SSH, with this install's own key (the first login sets it with the installer's password)
    def ssh_options(self):
        return ['-o', f'UserKnownHostsFile={self.known_hosts}', '-o', 'StrictHostKeyChecking=accept-new',
                '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'BatchMode=yes',
                '-o', 'IdentitiesOnly=yes', '-i', str(self.key)]

    def ssh(self, host, command, check=True, stdin=None, timeout=300):
        argv = ['ssh', *self.ssh_options(), f'root@{host}', command]
        result = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout)
        out = result.stdout.decode(errors='replace')
        with (self.logs / 'ssh.log').open('a') as f:
            f.write(f'$ ssh {host} {command[:200]}\n{out}{result.stderr.decode(errors="replace")}\n')
        if check and result.returncode != 0:
            tail = (out + result.stderr.decode(errors='replace')).strip().splitlines()[-5:]
            raise Stop(f'on the box: {command[:80]} failed: {" | ".join(tail)}')
        return out

    def push(self, host, local, remote):
        self.ssh(host, f'cat > {remote}', stdin=Path(local).read_bytes())
        remote_sum = self.ssh(host, f'sha256sum {remote}').split()[0]
        require(remote_sum == digest(local), f'{Path(local).name} arrived damaged')


def wait_port(host, port, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=3):
                return True
        except OSError:
            time.sleep(3)
    return False


class Fdl:
    """One interactive spd_dump session: each command is sent once and its prompt awaited."""

    def __init__(self, cwd, log):
        self.log = open(log, 'ab')
        self.buf = b''
        self.pos = 0
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.chdir(cwd)
                os.execv(str(SPD), [str(SPD), '--wait', '600', '--kickto', '2', 'exec'])
            finally:
                os._exit(127)
        self.pid, self.fd = pid, fd

    def wait(self, seconds, what):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.fd], [], [], 0.5)
            if ready:
                try:
                    chunk = os.read(self.fd, 65536)
                except OSError:
                    chunk = b''
                if not chunk:
                    raise Stop(f'spd_dump ended while waiting for {what}: see logs/fdl.log')
                self.buf += chunk
                self.log.write(chunk)
                self.log.flush()
            if self.buf.find(PROMPT, self.pos) != -1:
                self.pos = len(self.buf)
                return
        raise Stop(f'no "FDL2 >" after {what} within {seconds} s: see logs/fdl.log')

    def send(self, line, seconds=900):
        self.pos = len(self.buf)
        os.write(self.fd, (line + '\n').encode())
        self.wait(seconds, line)

    def close(self):
        """Closing the terminal ends spd_dump (after a reset it has ended already); the F50 stays where it is."""
        try:
            os.close(self.fd)
        except OSError:
            pass
        for _ in range(50):
            if os.waitpid(self.pid, os.WNOHANG)[0]:
                break
            time.sleep(0.1)
        else:
            os.kill(self.pid, 15)
            os.waitpid(self.pid, 0)
        self.log.close()


def check(w, args):
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64',
            'the F50 route runs on an Apple Silicon Mac (pinned arm64 tools)')
    free = shutil.disk_usage(w.root).free
    require(free >= 15_000_000_000, f'only {free // 10**9} GB free where WORK is; the backup needs 15 GB')
    out = w.run([sys.executable, DP / 'scripts/fetch-install-assets.py', 'f50', w.f50], log='check.log').stdout
    require(out.count('SHA256_OK') == 4, 'pinned downloads incomplete')
    shown = w.run([SPD, '--help'], check=False)
    require(SPD_VERSION in shown.stdout + shown.stderr, 'spd_dump is not the pinned build of this repository')
    for tool in ('lz4', 'ssh', 'ssh-keygen'):
        require(shutil.which(tool), f'{tool} missing (run mac-bootstrap.sh and its export first)')
    require(Path(shutil.which('lz4')).parent == DP / 'tools/mac-arm64', 'lz4 is not the pinned build')
    w.run([sys.executable, '-c', 'import cryptography'], log='check.log')
    if not shutil.which('adb'):
        out = w.run([sys.executable, DP / 'scripts/fetch-install-assets.py', 'adb-mac', w.root / 'adb'], log='check.log').stdout
        require('SHA256_OK' in out, 'ADB download not verified')
        w.run(['unzip', '-oq', w.root / 'adb/platform-tools-darwin.zip', '-d', w.root / 'adb'])
    w.run(['adb', 'version'], log='check.log')
    android = Path.home() / '.android'
    if not (android / 'adbkey.pub').exists():
        android.mkdir(mode=0o700, exist_ok=True)
        w.run(['adb', 'keygen', android / 'adbkey'], log='check.log')
    require((android / 'adbkey.pub').exists(), "no ADB key of this Mac")
    if not w.key.exists():
        w.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'dashplay-install', '-f', w.key])
    w.done('check')


def flash(w, args):
    w.needs('check')
    backup, session = w.f50 / 'backup', w.f50 / 'root-session'
    require(not backup.exists() and not session.exists(),
            'a backup or Root session is already in WORK/f50: a second run needs a person to look first')
    say('building the Root boot image from the pinned Magisk (offline)')
    boot = w.f50 / 'boot-root.img'
    if not boot.exists():
        out = w.run([sys.executable, DP / 'scripts/prepare-f50-root-candidate.py', w.f50 / 'magisk.img',
                     w.f50 / 'Magisk_28103.apk', Path.home() / '.android/adbkey.pub', boot], log='flash.log').stdout
        require('"ramdisk_roundtrip": true' in out, 'Root image did not round-trip')
    action('断开 F50 的全部电源（拔掉 USB 线），再用能打开原厂后台的那个 USB 口接回电脑。最多等 10 分钟。')
    for old in w.f50.glob('pgpt.bin'):
        old.unlink()
    fdl = Fdl(w.f50, w.logs / 'fdl.log')
    try:
        fdl.wait(620, 'the F50 in download mode')
        say('F50 in download mode (FDL2)')
        if not (w.f50 / 'pgpt.bin').exists():
            fdl.send('partition_list partition_list.xml', 120)
        require((w.f50 / 'pgpt.bin').exists(), 'no partition table read (pgpt.bin)')

        # F1: everything but Android's user data and logs, at raw GPT offsets; boot/trustos/misc/GPT twice
        out = w.run([sys.executable, DP / 'scripts/f50-backup.py', 'plan', w.f50 / 'pgpt.bin', backup, SPD],
                    log='flash.log').stdout
        require(out.startswith('PLAN_READY 69 of 75'), f'unexpected partition layout: {out.strip()}')
        plan = json.loads((backup / 'plan.json').read_text())
        say(f'backing up {sum(p["size"] for p in plan["items"]) // 2**20} MiB (a few minutes)')
        fdl.send('path backup', 30)
        for p in plan['items']:
            names = [p['name'] + '.bin']
            if p['name'] in ('boot_a', 'boot_b', 'trustos_a', 'trustos_b', 'misc', 'primary_gpt', 'secondary_gpt'):
                names.append(p['name'] + '.verify.bin')
            for name in names:
                fdl.send(f'read_part user_partition {p["offset"]} {p["size"]} {name}')
        out = w.run([sys.executable, DP / 'scripts/f50-backup.py', 'verify', backup], log='flash.log').stdout
        require(out.startswith('BACKUP_VERIFIED 69 of 75'), f'backup not verified: {out.strip()}')
        say('backup verified')

        # F2: TrustOS from this device's own trustos/sml, then staged writes, each read back from both slots
        tos = w.f50 / 'trustos-root.bin'
        w.run([sys.executable, DP / 'scripts/prepare-f50-trustos.py', backup / 'trustos_b.bin', backup / 'sml_b.bin', tos],
              log='flash.log')
        out = w.run([sys.executable, DP / 'scripts/f50-root-plan.py', 'prepare', backup, boot, tos, session],
                    log='flash.log').stdout
        require(out.startswith('ROOT_PLAN_READY'), f'Root plan refused: {out.strip()}')
        fdl.send('path root-session', 30)
        for stage in ('before', 'boot', 'trustos'):
            for line in (session / f'stage-{stage}.txt').read_text().splitlines():
                if line.startswith('w '):   # written from the session directory, read back into it (path)
                    part, image = line.split()[1:]
                    say(f'writing {part}')
                    line = f'w {part} root-session/{image}'
                fdl.send(line)
            out = w.run([sys.executable, DP / 'scripts/f50-root-plan.py', 'check', session, stage], log='flash.log',
                        check=False)
            if out.returncode != 0 or f'READBACK_OK {stage}' not in out.stdout:
                raise Stop(f'readback after "{stage}" does not match: no further write, no reset. Keep the F50 '
                           f'powered and connected, keep WORK/f50, and ask for help ({(out.stdout + out.stderr).strip()[-200:]})')
            say(f'readback {stage} ok')
        fdl.pos = len(fdl.buf)
        os.write(fdl.fd, b'reset\n')
        time.sleep(3)
    finally:
        fdl.close()
    say('rebooting into rooted Android (about 1 minute)')
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        connected = subprocess.run(['adb', 'connect', ANDROID], capture_output=True, text=True, timeout=30).stdout
        if 'connected' in connected and 'failed' not in connected and 'cannot' not in connected:
            props = w.adb('shell', 'id; getprop sys.boot_completed; getprop ro.boot.slot_suffix; getprop ro.build.display.id',
                          check=False)
            lines = [line.strip() for line in props.strip().splitlines()]
            if len(lines) >= 4 and 'uid=0' in lines[0] and lines[1] == '1':
                require(lines[2] == '_b' and B16 in lines[3], f'not Android B16 on slot b: {lines[2:4]}')
                w.done('flash')
                return
        time.sleep(5)
    raise Stop(f'no root ADB at {ANDROID} after 5 minutes: check the USB link, do not flash again')


def parse_password(text):
    found = re.search(r'^user: root\npassword: (.+)$', text, re.M)
    require(found, 'no root password in the installer output file')
    return found.group(1)


def check_dry_run(text):
    require('Dry run: nothing was written.' in text, 'installer dry run did not finish')
    require(re.search(r'storage:\s+SD card /dev/block/mmcblk1\b', text), 'installer would not use the SD card')
    require(re.search(r'writes:\s+/dev/block/mmcblk1\S*, boot_a, 32 bytes of misc \(boot_b', text),
            'installer would write something other than the SD card, boot_a and misc')
    require(not re.search(r'^! ', text, re.M), 'installer reported an error')


def ssh_first_login(w, host, password):
    """The only password login: puts this install's public key on the box (dropbear)."""
    key = (w.key.with_suffix('.pub')).read_text().strip()
    require(re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+ dashplay-install', key), 'unexpected SSH public key')
    command = f"umask 077; mkdir -p /etc/dropbear; grep -qxF '{key}' /etc/dropbear/authorized_keys 2>/dev/null " \
              f"|| echo '{key}' >> /etc/dropbear/authorized_keys; echo KEY_SET"
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp('ssh', ['ssh', '-o', f'UserKnownHostsFile={w.known_hosts}', '-o', 'StrictHostKeyChecking=accept-new',
                          '-o', 'PreferredAuthentications=password,keyboard-interactive', '-o', 'PubkeyAuthentication=no',
                          '-o', 'ConnectTimeout=10', f'root@{host}', command])
    buf, sent, deadline = b'', False, time.monotonic() + 60
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.5)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
        if not sent and b'assword' in buf:
            os.write(fd, password.encode() + b'\n')
            sent = True
    os.close(fd)
    os.waitpid(pid, 0)
    require(b'KEY_SET' in buf, 'first SSH login with the installer password failed')


# Magisk keeps a module installed from a running Android in modules_update until the next boot (modules/ holds only
# module.prop and "update" until then), so right after the install the switch is in modules_update.
SWITCH_PATHS = ('/data/adb/modules_update/mu300_linux_switch/switch.sh', '/data/adb/modules/mu300_linux_switch/switch.sh')


def arm_linux(w):
    """The installer's switch writes the one-shot Linux boot into misc and reboots. Its dry run checks the same things
    first; anything it refuses (its messages start with "mu300-linux:") stops here, with Android still the default."""
    found = w.adb('shell', ' || '.join(f'ls -d {p} 2>/dev/null' for p in SWITCH_PATHS), check=False).strip()
    require(found in SWITCH_PATHS, 'the installer\'s switch script is in neither modules_update nor modules; do not reboot')
    dry = w.adb('shell', f'sh {found} --dry-run 2>&1', check=False)
    require('dry run: nothing was written' in dry and 'mu300-linux:' not in dry,
            f'switch dry run refused: {dry.strip().splitlines()[-1:] or "no output"}; Android is unchanged, do not reboot')
    # the device reboots at the end, so adb may report the lost connection: the text decides
    out = w.adb('shell', f'sh {found} 2>&1', check=False)
    require('mu300-linux:' not in out, f'switch refused: {out.strip().splitlines()[-1]}; Android is unchanged')


def openwrt(w, args):
    w.needs('flash')
    subprocess.run(['adb', 'connect', ANDROID], capture_output=True, timeout=30)
    require('uid=0' in w.adb('shell', 'id'), 'no root ADB: is the F50 on rooted Android (after "flash")?')
    panel = w.f50 / 'mu300-panel.zip'
    if not panel.exists():
        w.run([sys.executable, DP / 'scripts/prepare-mu300-panel-installer.py', w.f50 / 'openwrt-k5.4.zip',
               w.f50 / 'openwrt-luci-rootfs.tar.gz', panel], log='openwrt.log')
    # the SD card: present, a real card (not eMMC), and big enough; A and B boot are different devices
    parts = w.adb('shell', 'cat /proc/partitions')
    found = re.search(r'^\s*\d+\s+\d+\s+(\d+)\s+mmcblk1$', parts, re.M)
    require(found, 'no SD card (mmcblk1): insert the card the owner agreed to erase')
    sd_gb = int(found.group(1)) * 1024 / 10**9
    require(sd_gb >= 7.5, f'SD card is {sd_gb:.1f} GB; 8 GB or more needed')
    a, b = (w.adb('shell', f'readlink -f /dev/block/by-name/boot_{s}').strip() for s in 'ab')
    require(a and b and a != b, 'boot_a and boot_b are not separate devices')
    say(f'SD card {sd_gb:.1f} GB will be erased')
    w.adb('push', panel, '/data/local/tmp/mu300-panel.zip', timeout=300)
    conf = w.f50 / 'mu300-install.conf'

    def install(dry):
        conf.write_text('MU300_STORAGE=sd\nMU300_SD_ERASE=yes\nMU300_BOOT_OS=openwrt-luci\nMU300_BOOT=android\n'
                        'MU300_BOOT_ATTEMPTS=5\nMU300_HOTSPOT=yes\nMU300_LANG=en\n' + ('MU300_DRY_RUN=1\n' if dry else ''))
        w.adb('push', conf, '/data/adb/mu300-install.conf')
        w.adb('shell', 'chown 0:0 /data/adb/mu300-install.conf; chmod 600 /data/adb/mu300-install.conf')
        # Magisk's outer status can be non-zero (the dry run exits 3): the text decides
        out = w.adb('shell', '/data/adb/magisk/magisk --install-module /data/local/tmp/mu300-panel.zip',
                    check=False, timeout=1800)
        (w.logs / f'mu300-{"dry-run" if dry else "install"}.log').write_text(out)
        return out

    check_dry_run(install(True))
    say('installer dry run ok: only the SD card, boot_a and misc')
    sizes = dict(boot_a=64 << 20, boot_b=64 << 20, misc=1 << 20)
    for name, size in sizes.items():
        data = w.adb('exec-out', f'cat /dev/block/by-name/{name}', binary=True)
        require(len(data) == size, f'{name} read back {len(data)} bytes')
        (w.f50 / f'preinstall-{name}.bin').write_bytes(data)
    say('installing OpenWrt on the SD card (several minutes)')
    out = install(False)
    require(not re.search(r'^! ', out, re.M), 'installer stopped with an error: see logs/mu300-install.log; do not reboot')
    after = w.adb('exec-out', 'cat /dev/block/by-name/boot_b', binary=True)
    require(after == (w.f50 / 'preinstall-boot_b.bin').read_bytes(), 'Android boot_b changed; do not reboot')
    password_file = w.private / 'openwrt-root-password.txt'
    password_file.write_bytes(w.adb('exec-out', 'cat /data/adb/mu300-linux-password.txt', binary=True))
    password_file.chmod(0o600)
    password = parse_password(password_file.read_text())
    say('switching to OpenWrt (about 1 minute)')
    arm_linux(w)
    if not wait_port(OPENWRT, 22, 300):
        action('拔下 F50 的 USB 线再插回电脑（让电脑重新取地址），然后等待。')
        require(wait_port(OPENWRT, 22, 300), f'OpenWrt not reachable at {OPENWRT}:22')
    ssh_first_login(w, OPENWRT, password)
    info = w.ssh(OPENWRT, '. /etc/openwrt_release; echo "$DISTRIB_RELEASE"; uname -r; cat /run/mu300/linux-slot; df -h / | tail -1')
    release, kernel, slot = info.splitlines()[:3]
    require(release.startswith('25.12') and kernel.startswith('5.4') and slot.strip() == 'a',
            f'unexpected system: {release} {kernel} slot {slot}')
    fw = subprocess.run(['ssh', *w.ssh_options(), f'root@{OPENWRT}', 'fw4 check'], capture_output=True, text=True)
    if fw.returncode != 0:
        require('flowtable' in fw.stdout + fw.stderr, f'firewall check failed: {(fw.stdout + fw.stderr).strip()[-200:]}')
        w.ssh(OPENWRT, 'uci set firewall.@defaults[0].flow_offloading=0; uci commit firewall; /etc/init.d/firewall restart')
        w.ssh(OPENWRT, 'fw4 check')
    require('default boot: linux' in w.ssh(OPENWRT, 'mu300-next-boot linux'), 'could not make OpenWrt the default boot')
    w.done('openwrt', release=release, kernel=kernel)
    say(f'OpenWrt {release}; admin page http://{OPENWRT}/ (root, password in WORK/f50/private)')


def dashplay(w, args):
    w.needs('openwrt')
    host = w.state().get('tesla', {}).get('host', OPENWRT)
    env = w.ssh(host, '. /etc/openwrt_release; echo "$DISTRIB_RELEASE"; cat /etc/apk/arch; '
                      "df -k / | awk 'NR==2{print $4}'; awk '/MemTotal/{print $2}' /proc/meminfo")
    release, arch, free_kb, mem_kb = env.split()[:4]
    require(release.startswith('25.12'), f'OpenWrt {release}, 25.12 needed')
    require(int(free_kb) >= 230 * 1024, f'only {int(free_kb) // 1024} MB free on the box')
    # the packages' dependencies (Python and friends) come from OpenWrt's feed: the box needs the internet now
    require('FEED_OK' in w.ssh(host, 'apk update >/dev/null 2>&1 && echo FEED_OK', check=False, timeout=180),
            'the box has no internet (insert the SIM card) to install the packages\' dependencies')
    with urllib.request.urlopen(FEED, timeout=60) as r:
        rel = json.load(r)
    tag = rel['tag_name']
    wanted = [re.compile(p) for p in (r'dashplay-\d[\d.]*-r\d+\.apk', rf'dashplay-jre-.*_{re.escape(arch)}\.apk',
                                      r'luci-app-dashplay-.*\.apk', r'luci-i18n-dashplay-zh-cn-.*\.apk', r'SHA256SUMS')]
    files = {}
    target = w.f50 / f'openwrt-dashplay-{tag}'
    target.mkdir(exist_ok=True)
    for asset in rel['assets']:
        if any(p.fullmatch(asset['name']) for p in wanted):
            path = target / asset['name']
            if not path.exists():
                with urllib.request.urlopen(asset['browser_download_url'], timeout=300) as r:
                    path.write_bytes(r.read())
            files[asset['name']] = path
    require(len(files) == 5, f'{tag}: expected 4 packages for {arch} and SHA256SUMS, found {sorted(files)}')
    sums = dict(reversed(line.split()) for line in files.pop('SHA256SUMS').read_text().splitlines() if line.strip())
    for name, path in files.items():
        require(sums.get(name) == digest(path), f'{name} does not match SHA256SUMS')
    say(f'openwrt-dashplay {tag} downloaded and verified; installing (Python, Java: about 225 MB)')
    w.ssh(host, 'rm -rf /tmp/dashplay-install && mkdir -m 700 /tmp/dashplay-install')
    for name, path in files.items():
        w.push(host, path, f'/tmp/dashplay-install/{name}')
    w.ssh(host, 'cd /tmp/dashplay-install && apk add --allow-untrusted dashplay-jre-*.apk dashplay-2*.apk luci-*.apk', timeout=1800)
    # F50 Bluetooth: a private BlueZ beside the 5.4 kernel; the system bluez-daemon would bring 6.12 modules
    w.ssh(host, 'python3 /opt/dashplay/board_zte_f50.py --setup', timeout=900)
    w.ssh(host, "uci set dashplay.main.bluetooth_service='/usr/bin/python3 /opt/dashplay/board_zte_f50.py' && "
                "uci set dashplay.main.dbus_address='unix:path=/var/run/dashplay/bluetooth/system_bus_socket' && "
                'uci commit dashplay && : > /var/run/dashplay/supervisor.log 2>/dev/null; /etc/init.d/dashplay restart')
    deadline = time.monotonic() + 150
    log = ''
    while time.monotonic() < deadline:
        log = w.ssh(host, 'cat /var/run/dashplay/supervisor.log 2>/dev/null', check=False)
        if 'bluetooth powered' in log or 'did not power on' in log:
            break
        time.sleep(5)
    require('bluetooth powered' in log, 'Bluetooth did not power on (wireless CarPlay needs it): see /var/run/dashplay/supervisor.log')
    version = w.ssh(host, 'apk info -v dashplay 2>/dev/null | head -1').strip()
    w.done('dashplay', version=version)
    say(f'{version} running, Bluetooth on; settings page http://{host}:18080/')


def fetch_identity(out):
    """The two files from the newest DiPlay APK that has them (pre-releases included), checked by the API digest."""
    def get(url):
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'DashPlay-install'}), timeout=120) as r:
            return r.read()
    releases = json.loads(get(DIPLAY))
    for release in sorted(releases, key=lambda x: x.get('published_at') or '', reverse=True):
        if release.get('draft'):
            continue
        for asset in release['assets']:
            if not (asset['name'].startswith('DiPlay-') and asset['name'].endswith('.apk')):
                continue
            want = asset.get('digest') or ''
            require(want.startswith('sha256:'), 'DiPlay asset without SHA-256: review its release')
            data = get(asset['browser_download_url'])
            require('sha256:' + hashlib.sha256(data).hexdigest() == want, 'DiPlay APK SHA-256 mismatch')
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = ['identity.pk8', 'certificate.p7b']
                if not all('assets/offline-mfi/' + n in z.namelist() for n in names):
                    continue
                out.mkdir(mode=0o700)
                for n in names:
                    with (out / n).open('xb') as f:
                        os.fchmod(f.fileno(), 0o600)
                        f.write(z.read('assets/offline-mfi/' + n))
            (out / 'source.json').write_text(json.dumps(
                {'tag': release['tag_name'], 'url': asset['browser_download_url'], 'sha256': want[7:]}) + '\n')
            return release['tag_name']
    raise Stop('no DiPlay release carries the identity')


def identity(w, args):
    w.needs('dashplay')
    host = w.state().get('tesla', {}).get('host', OPENWRT)
    source = Path(args.dir) if args.dir else w.root / 'identity-private'
    if not (source / 'identity.pk8').exists():
        require(not args.dir, f'no identity.pk8 in {source}')
        say(f'identity from DiPlay {fetch_identity(source)} (a shared experimental identity; keep it private)')
    remote = '/opt/dashplay/identity/offline-mfi'
    present = w.ssh(host, f'cd {remote} 2>/dev/null && sha256sum identity.pk8 certificate.p7b', check=False)
    mine = {n: digest(source / n) for n in ('identity.pk8', 'certificate.p7b')}
    if present.strip():
        require(all(mine[n] in present for n in mine), 'the box already has a different identity: back it up and ask first')
    else:
        w.ssh(host, f'umask 077; mkdir -p {remote}; chmod 700 {remote}')
        for n in mine:
            w.push(host, source / n, f'{remote}/{n}')
    require('IDENTITY_MATCH' in w.ssh(host, 'sh -s', stdin=CHECK_IDENTITY.encode()), 'identity files are not one pair')
    w.done('identity')


TESLA_SCRIPT = '''#!/bin/sh
# Runs on the box, apart from the SSH session: the LAN moves to 100.96.0.1 and the session drops.
{sim}uci set dashplay.main.tesla_mode=1
uci commit dashplay
/etc/init.d/dashplay restart
'''


def tesla(w, args):
    w.needs('dashplay')
    sim = args.sim == 'yes'
    host = w.state().get('tesla', {}).get('host', OPENWRT)
    if host != TESLA_LAN:
        script = TESLA_SCRIPT.format(sim='uci set dashplay.main.answer_tesla_checks=0\n' if sim else '')
        w.ssh(host, 'cat > /tmp/dashplay-tesla.sh', stdin=script.encode())
        w.ssh(host, '(sh /tmp/dashplay-tesla.sh > /tmp/dashplay-tesla.log 2>&1 </dev/null &) ; echo started')
        say(f'Tesla mode on; the box moves to {TESLA_LAN} (about 30 s)')
        if not wait_port(TESLA_LAN, 22, 120):
            action('拔下 F50 的 USB 线再插回电脑（让电脑重新取地址），然后等待。')
            require(wait_port(TESLA_LAN, 22, 300), f'box not reachable at {TESLA_LAN}: reconnect the computer to it')
        w.done('tesla', host=TESLA_LAN)
    host = TESLA_LAN
    require(f'inet {TESLA_LAN}/24' in w.ssh(host, 'ip -4 addr show br-lan'), f'br-lan is not {TESLA_LAN}/24')
    if sim:
        require(re.search(r'Address:\s*\d', w.ssh(host, 'nslookup duckdns.org 127.0.0.1', check=False).split('Name:', 1)[-1]),
                'no upstream DNS through the box (check answer_tesla_checks and the SIM card)')
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        count = w.ssh(host, "for p in $(pgrep -f run-receiver.py); do tr '\\0' ' ' < /proc/$p/cmdline; echo; done | grep -c -- '--tesla'",
                      check=False).strip()
        if count.isdigit() and int(count) >= 1:
            break
        time.sleep(5)
    else:
        raise Stop('the receiver did not restart in Tesla mode: see /var/run/dashplay/supervisor.log')
    radio = w.ssh(host, "uci show wireless | grep -E 'country|channel'")
    if args.cn_149:
        name = re.search(r'wireless\.(\w+)\.channel=', radio)
        require(name, 'no radio found')
        w.ssh(host, f'uci set wireless.{name.group(1)}.country=CN; uci set wireless.{name.group(1)}.channel=149; '
                    'uci commit wireless; (wifi reload >/dev/null 2>&1 &)')
        time.sleep(15)
        radio = w.ssh(host, "uci show wireless | grep -E 'country|channel'")
    say('Wi-Fi: ' + ' '.join(radio.split()))
    say(f'in the car: https://{TESLA_LAN}:18443/ (or the own name once set up)')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='stage', required=True)
    for name in ('check', 'flash', 'openwrt', 'dashplay', 'identity', 'tesla'):
        q = sub.add_parser(name)
        q.add_argument('work')
        if name == 'identity':
            q.add_argument('dir', nargs='?')
        if name == 'tesla':
            q.add_argument('--sim', choices=('yes', 'no'), required=True)
            q.add_argument('--cn-149', action='store_true', help='CN, channel 149 (only where that is allowed)')
    args = p.parse_args()
    try:
        w = Work(args.work)
        globals()[args.stage](w, args)
    except Stop as stop:
        print(f'F50_STOP {args.stage}: {stop}', flush=True)
        return 1
    except (subprocess.TimeoutExpired, OSError, ValueError, KeyError) as error:
        print(f'F50_STOP {args.stage}: {type(error).__name__}: {error}', flush=True)
        return 1
    print(f'F50_OK {args.stage}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
