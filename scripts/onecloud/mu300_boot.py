#!/usr/bin/env python3
"""ZTE F50 (MU300 OpenWrt): three changes to the system's own boot scripts, measured on an F50 (2026-10-16).
The OpenWrt package runs `mu300_boot.py apply` when it is installed or upgraded on an MU300 system; it changes a
file only where the text is exactly the system's original, keeps that original in /opt/dashplay/state/mu300-originals/
(kept across upgrades and removal) and leaves a file it does not recognise alone. `mu300_boot.py revert` puts the originals back.

1. preinit waits 4 s for a key press to enter failsafe mode, on a device nobody types into: 1 s.
2. /etc/init.d/mu300-hw loaded the thermal, LED, GPU and sound modules and zram before the Wi-Fi; the Wi-Fi's own
   modules need only what the initramfs already loaded, so it now comes first (the hotspot about 2.5 s earlier).
3. mu300-hw then ran "wifi down; wifi up" on every boot, to restart a hostapd that went AP-DISABLED under the world
   regulatory rules. It power-cycles the Wi-Fi/Bluetooth chip, and with the Wi-Fi first it fell right after
   CarPlay came up: every boot dropped the iPhone and showed the picture a second time at 47-53 s. Now it runs
   only when hostapd is not ENABLED (mu300-post still checks the access point at 45 s).
Boot to CarPlay on the F50: 36.4 s -> about 31 s (kernel start; plus about 6 s of bootloader), and no drop.
"""
import argparse
from pathlib import Path
import sys

PREINIT = Path("/lib/preinit/00_preinit.conf")
HW = Path("/etc/init.d/mu300-hw")
MARK = "DashPlay (2026-10-16)"
ORIGINALS = Path("/opt/dashplay/state/mu300-originals")

MODULES = """\t/opt/mu300/bin/extra-modules >/tmp/mu300-extra-modules.log 2>&1
\t/opt/mu300/bin/zram-start >/dev/null 2>&1
"""
WIFI_FIRST = f"""\t# {MARK}: Wi-Fi first, the thermal/LED/GPU modules and zram after it (mu300_boot.py)
"""
DOWN_UP = """\t# was still active; one down/up right after the country is live does
\twifi down >>/tmp/mu300-wifi-start.log 2>&1
\twifi up >>/tmp/mu300-wifi-start.log 2>&1
"""
DOWN_UP_IF_NEEDED = f"""\t# was still active; one down/up right after the country is live does.
\t# {MARK}: only then - it power-cycles the Wi-Fi/Bluetooth chip and dropped CarPlay right after it came up
\t# (mu300_boot.py). Up to 10 s for hostapd to settle; anything but ENABLED gets the down/up.
\tap=""
\tfor n in $(seq 1 10); do
\t\tap=$(ubus call hostapd.wlan0 get_status 2>/dev/null | jsonfilter -e '@.status')
\t\t[ "$ap" = ENABLED ] || [ "$ap" = DISABLED ] && break
\t\tsleep 1
\tdone
\tif [ "$ap" = ENABLED ]; then
\t\techo "access point enabled after the country: no wifi down/up" >>/tmp/mu300-wifi-start.log
\telse
\t\techo "access point '$ap' after the country: wifi down/up" >>/tmp/mu300-wifi-start.log
\t\twifi down >>/tmp/mu300-wifi-start.log 2>&1
\t\twifi up >>/tmp/mu300-wifi-start.log 2>&1
\tfi
""" + MODULES


def preinit(text):
    """The failsafe wait, 4 s -> 1 s; None when the line is not the original one."""
    old = "\nfs_failsafe_wait_timeout=4\n"
    return text.replace(old, "\nfs_failsafe_wait_timeout=1\n") if text.count(old) == 1 else None


def hw(text):
    """mu300-hw: the Wi-Fi before the other modules, the down/up only when needed; None when not the original."""
    if text.count(MODULES) != 1 or text.count(DOWN_UP) != 1 or text.index(MODULES) > text.index(DOWN_UP):
        return None
    return text.replace(MODULES, WIFI_FIRST).replace(DOWN_UP, DOWN_UP_IF_NEEDED)


def apply(path, change, say=print, originals=ORIGINALS):
    if not path.exists():
        return say(f"{path}: not there, nothing to do")
    text = path.read_text()
    if MARK in text or "\nfs_failsafe_wait_timeout=1\n" in text:
        return say(f"{path}: already changed")
    new = change(text)
    if new is None:
        return say(f"{path}: not the text this was made for, left alone")
    originals.mkdir(mode=0o700, parents=True, exist_ok=True)
    keep = originals / path.name
    if not keep.exists():
        keep.write_text(text)
    tmp = path.with_name(path.name + ".dashplay-new")
    tmp.write_text(new)
    tmp.chmod(path.stat().st_mode & 0o7777)
    tmp.replace(path)
    say(f"{path}: changed (original in {keep})")


def revert(path, say=print, originals=ORIGINALS):
    keep = originals / path.name
    if not keep.exists():
        return say(f"{path}: no original kept, nothing to do")
    mode = path.stat().st_mode & 0o7777 if path.exists() else 0o644
    path.write_text(keep.read_text())
    path.chmod(mode)
    keep.unlink()
    say(f"{path}: original back")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=("apply", "revert"))
    args = p.parse_args()
    if not Path("/opt/mu300").is_dir():
        print("not an MU300 system, nothing to do")
        return 0
    for path, change in ((PREINIT, preinit), (HW, hw)):
        apply(path, change) if args.action == "apply" else revert(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
