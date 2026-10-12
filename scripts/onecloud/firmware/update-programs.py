#!/usr/bin/env python3
"""Bring /opt/dashplay inside an image's root filesystem up to a bundle, without a box, root or a mount.

    python3 update-programs.py ROOTFS.ext4 BUNDLE_DIR [--apply]

ROOTFS.ext4 is the root partition taken out of a firmware image (OneCloud: `burnimg.py extract` + `unsparse`;
N1: `dd` of partition 2), BUNDLE_DIR an unpacked build-bundle.sh tar. Without --apply it prints what would
change. With it, debugfs makes the tree match the bundle: removed files go, changed files keep the image's
mode, new ones take the bundle's, everything is owned by root; state/ and identity/ stay as they are except
the AppCDS cache, which belongs to the old jars (the box rebuilds it on its first start). Only program files
change, so this fits when install.sh and the system services are the same as when the image was made.

Needs e2fsprogs 1.47 or newer on PATH (the N1 image uses orphan_file, which 1.46 refuses). Afterwards:
e2fsck -fy -E discard, then pack as in README.md (OneCloud) or dd the partition back (N1).
"""
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = "/opt/dashplay"
KEEP = ("state/", "identity/")   # the box's own data, not program files
STALE = ("state/receiver/app-cds.jsa", "state/receiver/app-cds.classpath")


def debugfs(image, *args, write=False):
    return subprocess.run(["debugfs", *(["-w"] if write else []), *args, image],
                          capture_output=True, text=True, check=True).stdout


def tree(root):
    files = {str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file()}
    dirs = {str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_dir()}
    return files, dirs


def plan(image, old, new):
    before, old_dirs = tree(old)
    after, new_dirs = tree(new)
    lines, report = [], []
    for path in sorted(before - after):
        if path.startswith(KEEP) and path not in STALE:
            continue
        lines.append(f'rm "{BASE}/{path}"')
        report.append(f"remove {path}")
    for directory in sorted(old_dirs - new_dirs, key=lambda d: -d.count("/")):
        if not directory.startswith(KEEP):
            lines.append(f'rmdir "{BASE}/{directory}"')
    for directory in sorted(new_dirs - old_dirs, key=lambda d: d.count("/")):
        lines.append(f'mkdir "{BASE}/{directory}"')
    for path in sorted(after):
        if path.startswith(KEEP):
            continue
        source, target = Path(new) / path, f"{BASE}/{path}"
        if path in before:
            if (Path(old) / path).read_bytes() == source.read_bytes():
                continue
            mode = int(re.search(r"Mode:\s+(\d+)", debugfs(image, "-R", f'stat "{target}"')).group(1), 8)
            lines.append(f'rm "{target}"')
            report.append(f"replace {path}")
        else:
            mode = stat.S_IMODE(os.stat(source).st_mode)
            report.append(f"add {path}")
        lines += [f'cd "{os.path.dirname(target)}"', f'write "{source}" "{os.path.basename(target)}"',
                  f'sif "{target}" mode 0{stat.S_IFREG | mode:o}']
    # Root owns the tree (bundles made on a Mac carry its user, 501; older images had 1000 here and there).
    for path in [""] + sorted(after | new_dirs):
        target = f"{BASE}/{path}".rstrip("/")
        lines += [f'sif "{target}" uid 0', f'sif "{target}" gid 0']
    return lines, report


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    image, bundle, apply = argv[0], argv[1], "--apply" in argv
    with tempfile.TemporaryDirectory() as dump:
        # Without root, rdump warns that it cannot chown each file; the contents are still written.
        subprocess.run(["debugfs", "-R", f"rdump {BASE} {dump}", image], capture_output=True)
        lines, report = plan(image, Path(dump) / "dashplay", bundle)
        print("\n".join(report) or "nothing to change")
        if apply:
            with tempfile.NamedTemporaryFile("w", suffix=".debugfs") as commands:
                commands.write("\n".join(lines) + "\n")
                commands.flush()
                subprocess.run(["debugfs", "-w", "-f", commands.name, image], capture_output=True, check=True)
            print(f"applied {len(report)} changes to {image}")


if __name__ == "__main__":
    main(sys.argv[1:])
