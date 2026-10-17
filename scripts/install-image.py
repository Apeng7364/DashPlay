#!/usr/bin/env python3
"""The long steps of flashing an N1 (and a OneCloud's download), with progress (2026-10-17).

People waited 7-10 minutes on a silent "dd" or download, took it for a failure and pulled the stick or the power. Each
command prints a line every 15 s and keeps WORK/progress/<step>.json and .log (install_progress.py), which an AI
assistant reads while the command runs in the background, or in the person's own terminal (sudo).

  download URL DIR                  the file and its URL.sha256, resumed after a break, checked: SHA256_OK <file>
  unpack FILE.img.xz                FILE.img beside it: UNPACK_OK <file> <bytes>
  write-usb IMAGE DEVICE            onto a whole USB disk (macOS /dev/diskN, Linux /dev/sdX), then read back:
                                    USB_WRITE_OK, USB_READBACK_OK. It refuses a disk inside the computer, the system disk,
                                    a partition and a disk smaller than the image. Needs sudo; Windows: balenaEtcher.
  wait HOST PORT                    until the box answers there, e.g. after its first boot: BOX_UP or BOX_TIMEOUT
Every command takes --work WORK (default: the current folder), where progress/ goes.
"""
import argparse
import hashlib
import json
import lzma
import os
from pathlib import Path
import plistlib
import re
import socket
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from install_progress import Progress  # noqa: E402

CHUNK = 4 << 20
SECTOR = 512


def stop(text):
    raise SystemExit(f'STOP: {text}')


def download(args):
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    name = args.url.rsplit('/', 1)[-1]
    target, part = out / name, out / (name + '.part')
    with urllib.request.urlopen(args.url + '.sha256', timeout=60) as r:
        wanted = r.read().decode().split()[0].lower()
    (out / (name + '.sha256')).write_text(f'{wanted}  {name}\n')
    if not (target.exists() and sha256(target) == wanted):
        have = part.stat().st_size if part.exists() else 0
        request = urllib.request.Request(args.url, headers={'Range': f'bytes={have}-'} if have else {})
        with urllib.request.urlopen(request, timeout=60) as r:
            if have and r.status != 206:
                have = 0   # the server does not resume: from the start
            total = have + int(r.headers.get('Content-Length') or 0)
            with Progress(args.work, f'download-{name}', f'下载 {name}', total=total or None, expected=600,
                          unplug=None) as p, part.open('ab' if have else 'wb') as f:
                p.set(done=have, note=f'resuming at {have}' if have else 'from the start')
                while data := r.read(1 << 20):
                    f.write(data)
                    p.advance(len(data))
        part.replace(target)
    if sha256(target) != wanted:
        target.unlink()
        stop(f'{name} does not match its .sha256 (deleted; download it again)')
    print('SHA256_OK', name, flush=True)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        while data := f.read(CHUNK):
            digest.update(data)
    return digest.hexdigest()


def unpack(args):
    source = Path(args.file)
    if source.suffix != '.xz':
        stop(f'{source} is not an .xz file')
    target, part = source.with_suffix(''), source.with_suffix('.part')
    with Progress(args.work, f'unpack-{target.name}', f'解压 {source.name}', total=source.stat().st_size, expected=180) as p, \
            source.open('rb') as raw, lzma.open(raw) as xz, part.open('wb') as f:
        while data := xz.read(CHUNK):
            f.write(data)
            p.set(done=raw.tell())
    part.replace(target)
    print('UNPACK_OK', target, target.stat().st_size, flush=True)


def usb_disk(device, size):
    """The raw device to write and a description, after the checks; stops on anything but a whole external disk."""
    if sys.platform == 'darwin':
        info = plistlib.loads(subprocess.run(['diskutil', 'info', '-plist', device], capture_output=True, check=True).stdout)
        system = plistlib.loads(subprocess.run(['diskutil', 'info', '-plist', '/'], capture_output=True, check=True).stdout)
        ident = info.get('DeviceIdentifier', '')
        if not info.get('WholeDisk'):
            stop(f'{device} is a partition: give the whole disk (/dev/diskN)')
        if ident in (system.get('ParentWholeDisk'), system.get('DeviceIdentifier')):
            stop(f'{device} holds this Mac\'s system')
        if info.get('Internal') and info.get('VirtualOrPhysical') != 'Virtual':
            stop(f'{device} is inside this Mac, not a USB stick')
        disk_size = int(info.get('TotalSize') or info.get('Size') or 0)
        about = f'{info.get("MediaName", "?")} {disk_size / 10**9:.1f} GB ({info.get("BusProtocol", "?")})'
        raw = '/dev/r' + ident
        unmount = ['diskutil', 'unmountDisk', device]
    elif sys.platform.startswith('linux'):
        out = subprocess.run(['lsblk', '-J', '-b', '-o', 'NAME,SIZE,TYPE,TRAN,RM,MOUNTPOINT,MODEL', device],
                             capture_output=True, check=True, text=True).stdout
        disk = json.loads(out)['blockdevices'][0]
        if disk['type'] != 'disk':
            stop(f'{device} is a {disk["type"]}: give the whole disk (/dev/sdX)')
        if disk.get('tran') != 'usb' and str(disk.get('rm')) not in ('1', 'True', 'true'):
            stop(f'{device} is not a USB or removable disk')
        mounts = [c.get('mountpoint') for c in [disk] + disk.get('children', []) if c.get('mountpoint')]
        if any(m in ('/', '/boot', '/home') for m in mounts):
            stop(f'{device} holds this computer\'s system')
        disk_size = int(disk['size'])
        about = f'{disk.get("model") or "?"} {disk_size / 10**9:.1f} GB (usb)'
        raw = device
        unmount = ['sh', '-c', f'for p in $(lsblk -nro PATH {device}); do umount "$p" 2>/dev/null; done; true']
    else:
        stop('on Windows write the stick with balenaEtcher (or Rufus)')
    if disk_size < size:
        stop(f'{device} is {disk_size / 10**9:.1f} GB, the image {size / 10**9:.1f} GB')
    subprocess.run(unmount, capture_output=True, check=True)
    return raw, about


def write_all(disk, data):
    view = memoryview(data)
    while view:
        view = view[os.write(disk, view):]


def read_exactly(disk, n):
    data = b''
    while len(data) < n:
        chunk = os.read(disk, n - len(data))
        if not chunk:
            break
        data += chunk
    return data


def write_usb(args):
    image = Path(args.image)
    size = image.stat().st_size
    raw, about = usb_disk(args.device, size)
    print(f'writing {image.name} ({size / 10**9:.1f} GB) to {args.device}: {about}', flush=True)
    try:
        disk = os.open(raw, os.O_RDWR)
    except PermissionError:
        stop(f'no permission to write {raw}: run this command with sudo (in the Terminal app, it asks for the password)')
    try:
        with Progress(args.work, 'usb-write', '写 U 盘', total=size, unit='B',
                      unplug='不要拔 U 盘：中途拔掉要重写（N1 不受影响）') as p, image.open('rb') as src:
            while data := src.read(CHUNK):
                if len(data) % SECTOR:
                    data += bytes(SECTOR - len(data) % SECTOR)   # a raw disk takes whole sectors
                write_all(disk, data)
                p.advance(min(len(data), size - p.done))
            p.set(note='flushing to the stick')
            os.fsync(disk)
        print('USB_WRITE_OK', flush=True)
        if hasattr(os, 'posix_fadvise'):
            os.posix_fadvise(disk, 0, 0, os.POSIX_FADV_DONTNEED)   # Linux: read the stick, not the page cache
        os.lseek(disk, 0, os.SEEK_SET)
        with Progress(args.work, 'usb-readback', '读回核对 U 盘', total=size, unit='B',
                      unplug='不要拔 U 盘：正在核对刚写进去的内容') as p, image.open('rb') as src:
            while data := src.read(CHUNK):
                want = len(data) + (-len(data) % SECTOR)
                got = read_exactly(disk, want)
                if got[:len(data)] != data:
                    stop(f'USB readback differs at byte {p.done}: write the stick again or use another stick')
                p.advance(len(data))
    finally:
        os.close(disk)
    if sys.platform == 'darwin' and not args.no_eject:
        subprocess.run(['diskutil', 'eject', args.device], capture_output=True)
    print('USB_READBACK_OK', flush=True)


def wait(args):
    timeout = args.timeout or args.expect * 3
    with Progress(args.work, args.step, args.title, expected=args.expect, unplug=args.unplug or None) as p:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((args.host, args.port), timeout=3):
                    print('BOX_UP', args.host, args.port, flush=True)
                    return
            except OSError as error:
                p.set(note=f'{args.host}:{args.port} not yet ({type(error).__name__})')
            time.sleep(5)
        p.finish('failed', f'{args.host}:{args.port} did not answer within {timeout} s')
    print('BOX_TIMEOUT', args.host, args.port, flush=True)
    sys.exit(1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--work', default='.', help='where progress/ goes (default: the current folder)')
    sub = p.add_subparsers(dest='command', required=True)
    q = sub.add_parser('download'); q.add_argument('url'); q.add_argument('dir')
    q = sub.add_parser('unpack'); q.add_argument('file')
    q = sub.add_parser('write-usb'); q.add_argument('image'); q.add_argument('device')
    q.add_argument('--no-eject', action='store_true', help=argparse.SUPPRESS)
    q = sub.add_parser('wait'); q.add_argument('host'); q.add_argument('port', type=int)
    q.add_argument('--expect', type=int, default=120, help='seconds it usually takes')
    q.add_argument('--timeout', type=int, help='give up after (default: three times --expect)')
    q.add_argument('--title', default='等盒子开机'); q.add_argument('--step', default='wait-box')
    q.add_argument('--unplug', default='', help='what unplugging now does (empty: harmless)')
    args = p.parse_args()
    if not re.fullmatch(r'[\w.-]+', getattr(args, 'step', 'x')):
        stop('--step: letters, digits, . - _ only')
    {'download': download, 'unpack': unpack, 'write-usb': write_usb, 'wait': wait}[args.command](args)


if __name__ == '__main__':
    main()
