#!/usr/bin/env python3
"""Capture a OneCloud boot over a USB-TTL adapter and time the boot stages.

Standard library only (termios), so it runs on the Mac without pyserial:
    python3 serial-boot-log.py /dev/cu.usbserial-XXXX --seconds 120 --out boot.log
Start it, then power the box on (or reboot it). Times are host-monotonic seconds
from the first byte received, which for a cold start is within milliseconds of
power-on (the Amlogic boot ROM prints immediately).
"""
import argparse
import os
import re
import select
import sys
import termios
import time
import tty

# (label, pattern) — the first line matching each pattern is a milestone.
MILESTONES = [
    ("u-boot banner", re.compile(r"U-Boot \d")),
    ("boot script", re.compile(r"Try to boot from")),
    ("kernel loaded", re.compile(r"Starting kernel")),
    ("kernel first line", re.compile(r"Booting Linux on")),
    ("initramfs", re.compile(r"Run /init|Loading, please wait")),
    ("root mounted", re.compile(r"EXT4-fs \(mmcblk\w+\): mounted")),
    ("systemd", re.compile(r"systemd\[1\]: systemd \d|Welcome to")),
    ("login prompt", re.compile(r"login:")),
]


def open_port(path, baud):
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    tty.setraw(fd)
    attrs = termios.tcgetattr(fd)
    speed = getattr(termios, "B%d" % baud)
    attrs[4] = attrs[5] = speed
    attrs[2] |= termios.CLOCAL | termios.CREAD
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIFLUSH)
    return fd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--out", required=True)
    ap.add_argument("--echo", action="store_true", help="also print every line")
    args = ap.parse_args()

    fd = open_port(args.port, args.baud)
    print("listening on %s; power the box on now" % args.port, flush=True)
    t0 = None
    deadline = time.monotonic() + args.seconds
    buf = b""
    hits = {}
    with open(args.out, "w", encoding="utf-8") as out:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if not ready:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                continue
            now = time.monotonic()
            if t0 is None:
                t0 = now
                # After the first byte, keep capturing for the full window.
                deadline = now + args.seconds
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").rstrip("\r")
                rel = now - t0
                out.write("%8.3f %s\n" % (rel, line))
                if args.echo:
                    print("%8.3f %s" % (rel, line), flush=True)
                for label, pat in MILESTONES:
                    if label not in hits and pat.search(line):
                        hits[label] = rel
                        print("%8.3f  %s" % (rel, label), flush=True)
        if buf:
            out.write("%8.3f %s\n" % (time.monotonic() - t0, buf.decode("utf-8", "replace")))
    os.close(fd)
    if t0 is None:
        print("no data received: check TX/RX crossing, GND and the port name", file=sys.stderr)
        return 1
    print("log: %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
