#!/usr/bin/env python3
"""Offline adaptation of pinned upstream Magisk installer for its panel rootfs.

Does not install anything. Root/slot/storage guards and write routines stay intact.
"""
import argparse
import hashlib
import zipfile
from pathlib import Path

ZIP_SHA = '5b033b8bb9034ff53445189ea6f9fa7d5494697a91073ec6a249b5b1f2e8327c'
PANEL_SHA = '8ff2008f00b891c021b139e5f2cd225f54718957176a827c677c055560229eff'


def adapt(files, panel):
    def replace(name, old, new):
        assert files[name].count(old.encode()) == 1, (name, old)
        files[name] = files[name].replace(old.encode(), new.encode())

    replace('mu300/install.sh',
            'openwrt:openwrt::mu300-openwrt-rootfs.tar.gz) ;;',
            'openwrt:openwrt::mu300-openwrt-rootfs.tar.gz|openwrt-luci:openwrt-luci::mu300-openwrt-luci-rootfs.tar.gz) ;;')
    replace('mu300/install.sh', 'case $1 in openwrt) echo $NEED_OPENWRT',
            'case $1 in openwrt|openwrt-luci) echo $NEED_OPENWRT')
    replace('mu300/android-boot-image.sh', ';; openwrt) _fw=lib/firmware',
            ';; openwrt|openwrt-luci) _fw=lib/firmware')
    for old, new in [('SYSTEM=openwrt\n', 'SYSTEM=openwrt-luci\n'),
                     ('OS=openwrt\n', 'OS=openwrt-luci\n'),
                     ('ROOTFS_ASSET=mu300-openwrt-rootfs.tar.gz',
                      'ROOTFS_ASSET=mu300-openwrt-luci-rootfs.tar.gz'),
                     ('SHA256_ROOTFS=f6e4459c1edfcbb8d941017b33e673354c9dd73aa8e91a6195a5f9ca9073b364',
                      'SHA256_ROOTFS=' + PANEL_SHA)]:
        replace('mu300/manifest', old, new)
    del files['payload/mu300-openwrt-rootfs.tar.gz']
    files['payload/mu300-openwrt-luci-rootfs.tar.gz'] = panel
    return files


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('upstream_zip', type=Path)
    p.add_argument('panel_rootfs', type=Path)
    p.add_argument('output', type=Path)
    a = p.parse_args()
    assert not a.output.exists(), 'refusing overwrite'
    assert hashlib.sha256(a.upstream_zip.read_bytes()).hexdigest() == ZIP_SHA
    panel = a.panel_rootfs.read_bytes()
    assert hashlib.sha256(panel).hexdigest() == PANEL_SHA
    with zipfile.ZipFile(a.upstream_zip) as z:
        assert z.testzip() is None
        files = {n: z.read(n) for n in z.namelist()}
    adapt(files, panel)
    with zipfile.ZipFile(a.output, 'x', zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    print(hashlib.sha256(a.output.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
