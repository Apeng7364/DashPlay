#!/usr/bin/env python3
"""Phicomm N1: its BCM43455 Wi-Fi on the newer Cypress firmware the image already carries (2026-10-17).

The image loads brcm/brcmfmac43455-sdio.phicomm,n1.bin, firmware 7.45.154 (2018-02). With the box on a car hotspot and
its own hotspot for the iPhone (direct_phone, design 5.2a), that firmware went silent as a client after 1-1.5 minutes:
still associated, no carrier loss, no log line, nothing through; 2.4 and 5 GHz alike. brcmfmac's roamoff=1 kept the
client up but dropped every hotspot client for 20-30 s about every 90 s, even with the hotspot alone, so it stays at
its default. The same image has cypress/cyfmac43455-sdio.bin 7.45.234 (2021-04) with its clm_blob, meant for other
boards; on it the N1 kept both links for ten minutes with no loss.

So the N1's board-specific names point at the Cypress files: brcmfmac tries "<chip>.<board>.<ext>" (the device
tree's "phicomm,n1") before the shared "<chip>.<ext>", for the firmware and the clm_blob alike, and no other board
reads these names. The nvram (.txt) stays. The supervisor calls apply() on every start, before the Wi-Fi comes up,
so it reaches boxes by a program upgrade and comes back if a system update rewrites the files; when it changed a
link, brcmfmac is reloaded once. The originals are kept in state/n1-firmware-originals/; "board_phicomm_n1.py
revert" puts them back (the next boot loads them).
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

BOARD = b"phicomm,n1"
FIRMWARE = Path("/lib/firmware")
ORIGINALS = Path(__file__).resolve().parent / "state" / "n1-firmware-originals"
VERSION = b"7.45.234"   # the firmware measured on an N1; a different Cypress build is not taken unseen
# board-specific name in brcm/ -> the Cypress file it points at
LINKS = {
    "brcmfmac43455-sdio.phicomm,n1.bin": "../cypress/cyfmac43455-sdio.bin",
    "brcmfmac43455-sdio.phicomm,n1.clm_blob": "../cypress/cyfmac43455-sdio.clm_blob",
}
MODULES = ("brcmfmac_wcc", "brcmfmac_cyw", "brcmfmac_bca", "brcmfmac")   # vendor parts first: they hold brcmfmac


def is_n1(compatible=Path("/proc/device-tree/compatible")):
    try:
        return BOARD in compatible.read_bytes().split(b"\0")
    except OSError:
        return False


def apply(firmware=FIRMWARE, originals=ORIGINALS, say=print):
    """Point the N1's names at the Cypress files. True when a link changed (the driver must load them again)."""
    brcm = firmware / "brcm"
    targets = {name: (brcm / target).resolve() for name, target in LINKS.items()}
    missing = [str(path) for path in targets.values() if not path.is_file()]
    if missing:
        say(f"N1 Wi-Fi firmware: {', '.join(missing)} missing, the image's own firmware stays")
        return False
    if VERSION not in targets["brcmfmac43455-sdio.phicomm,n1.bin"].read_bytes():
        say(f"N1 Wi-Fi firmware: the Cypress firmware is not {VERSION.decode()}, the image's own firmware stays")
        return False
    changed = False
    for name, target in LINKS.items():
        link = brcm / name
        if link.is_symlink() and os.readlink(link) == target:
            continue
        originals.mkdir(mode=0o700, parents=True, exist_ok=True)
        kept, there = originals / name, link.exists() or link.is_symlink()
        first = not (kept.exists() or kept.is_symlink() or (originals / (name + ".absent")).exists())
        if there and first:   # the image's own file or link, kept once
            if link.is_symlink():
                os.symlink(os.readlink(link), kept)
                link.unlink()
            else:
                shutil.move(str(link), str(kept))
        elif there:
            link.unlink()   # a system update wrote it again: the first original is the one kept
        elif first:
            (originals / (name + ".absent")).touch()   # nothing was there: revert removes the link
        os.symlink(target, link)
        changed = True
    if changed:
        say(f"N1 Wi-Fi firmware: brcm/ now points at the Cypress {VERSION.decode()} firmware and its clm_blob")
    return changed


def revert(firmware=FIRMWARE, originals=ORIGINALS, say=print):
    brcm = firmware / "brcm"
    for name in LINKS:
        link, kept = brcm / name, originals / name
        if kept.exists() or kept.is_symlink():
            if link.exists() or link.is_symlink():
                link.unlink()
            shutil.move(str(kept), str(link))
            say(f"{link}: original back")
        elif (originals / (name + ".absent")).exists():
            if link.is_symlink():
                link.unlink()
            (originals / (name + ".absent")).unlink()
            say(f"{link}: removed (it was not there before)")
        elif link.is_symlink() and os.readlink(link) == LINKS[name]:
            # linked by hand before this ran (the N1 of 2026-10-17): nothing kept, so without the link brcmfmac
            # reads the shared name, the image's own file
            link.unlink()
            say(f"{link}: removed, no original kept; the shared {name.replace('.phicomm,n1', '')} loads")


def reload_driver(run=subprocess.run, loaded=lambda: Path("/proc/modules").read_text()):
    """Unload brcmfmac (its vendor parts first) and load it again, so it reads the firmware now linked."""
    present = {line.split()[0] for line in loaded().splitlines() if line.strip()}
    for module in MODULES:
        if module in present:
            run(["modprobe", "-r", module], capture_output=True, timeout=30)
    run(["modprobe", "brcmfmac"], capture_output=True, timeout=30, check=True)


def direct_ready(firmware=FIRMWARE, compatible=Path("/proc/device-tree/compatible")):
    """For the direct iPhone hotspot: not an N1, or an N1 whose names point at the Cypress files (on the old
    firmware a station beside the hotspot went silent after a minute)."""
    if not is_n1(compatible):
        return True
    brcm = firmware / "brcm"
    return all((brcm / name).is_symlink() and os.readlink(brcm / name) == target for name, target in LINKS.items())


def prepare(say=print):
    """For the supervisor, before the Wi-Fi comes up: the N1 on the Cypress firmware. True when the driver was reloaded."""
    if not is_n1():
        return False
    try:
        if not apply(say=say):
            return False
        reload_driver()
        return True
    except (OSError, subprocess.SubprocessError) as error:
        say(f"N1 Wi-Fi firmware unchanged: {type(error).__name__}: {error}")
        return False


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=("apply", "revert"))
    args = p.parse_args()
    if not is_n1():
        print("not a Phicomm N1, nothing to do")
        return 0
    if args.action == "apply":
        if apply():
            reload_driver()
    else:
        revert()
        print("the image's own firmware loads at the next boot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
