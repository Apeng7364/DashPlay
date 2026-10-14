#!/usr/bin/env python3
"""Upgrade DashPlay in place on a running OneCloud / Phicomm N1 box, keeping everything the owner set up.

    python3 upgrade.py BUNDLE.tar [--dry-run]     # as root, on the box
    python3 upgrade.py --rollback                 # put the program from before the last upgrade back

BUNDLE.tar is dashplay-bundle-<version>.tar from a DashPlay release. Only program files change: state/
(first-run setup, hotspot name and password, paired iPhones, certificates, logs), identity/ (the CarPlay
identity) and service.json (this box's interface and channel) stay as they are. Files the new program no
longer has are removed; each file is replaced whole (a new file renamed over the old one). The program from
before is kept in /opt/dashplay-previous.tar for --rollback. Then the service restarts; when its check fails or
it does not come up, the previous program goes back by itself.

Restarting the service restarts the box's Wi-Fi, so an SSH session over that Wi-Fi drops: run it apart from
the session, and read the outcome afterwards:
    systemd-run --unit=dashplay-upgrade --collect python3 /tmp/upgrade.py /tmp/dashplay-bundle.tar
    cat /opt/dashplay/state/upgrade.log
For the same system packages and services as when the box was installed (firmware/install.sh); a release
that changes those says so and needs the firmware image instead.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path("/opt/dashplay")
PREVIOUS = Path("/opt/dashplay-previous.tar")
UNIT = Path("/etc/systemd/system/dashplay.service")
KEEP = ("state/", "identity/")          # the owner's data
KEEP_FILES = ("service.json",)          # this box's own settings, written at installation
STALE = ("state/receiver/app-cds.jsa", "state/receiver/app-cds.classpath")   # belongs to the old jars
NEEDED = ("supervisor.py", "run-receiver.py", "dashplay.service", "playport/server/build/install/server/lib/server-0.1.0.jar")


def program_files(root):
    """Relative paths of the program's files under root: not the owner's data."""
    root = Path(root)
    return {str(p.relative_to(root)) for p in root.rglob("*")
            if (p.is_file() or p.is_symlink()) and not str(p.relative_to(root)).startswith(KEEP)
            and str(p.relative_to(root)) not in KEEP_FILES and "__pycache__" not in p.parts}


def plan(old, new):
    """(paths to remove, paths to write) to make the program under old match new."""
    before, after = program_files(old), program_files(new)
    remove = sorted(before - after)
    write = sorted(p for p in after if p not in before or not same(Path(old) / p, Path(new) / p))
    return remove, write


def same(a, b):
    if a.is_symlink() or b.is_symlink():
        return a.is_symlink() and b.is_symlink() and os.readlink(a) == os.readlink(b)
    return a.stat().st_size == b.stat().st_size and a.read_bytes() == b.read_bytes()


def apply(old, new, remove, write):
    old, new = Path(old), Path(new)
    for path in write:
        source, target = new / path, old / path
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.upgrade")
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        if source.is_symlink():
            os.symlink(os.readlink(source), temporary)
        else:
            shutil.copy2(source, temporary)
            if os.geteuid() == 0:
                os.chown(temporary, 0, 0)   # bundles made on a Mac carry its user
        os.replace(temporary, target)   # a running program keeps the old file until it restarts
    for path in remove:
        (old / path).unlink(missing_ok=True)
    for directory in sorted({p for p in old.rglob("*") if p.is_dir()}, key=lambda d: -len(d.parts)):
        relative = str(directory.relative_to(old)) + "/"
        if not relative.startswith(KEEP) and not any(directory.iterdir()):
            directory.rmdir()
    for path in STALE:
        (old / path).unlink(missing_ok=True)


def say(message, log):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    with open(log, "a") as out:
        out.write(line + "\n")


def run(*args):
    return subprocess.run(list(args), capture_output=True, text=True)


def service_up(seconds=90):
    """The service runs and its supervisor got past its start (the settings page answers on port 80)."""
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if run("systemctl", "is-active", "dashplay").stdout.strip() == "active" and \
                run("python3", "-c", "import socket; socket.create_connection(('127.0.0.1', 80), 2)").returncode == 0:
            return True
        time.sleep(2)
    return False


def backup(root):
    with tarfile.open(PREVIOUS.with_suffix(".tar.tmp"), "w") as out:
        for path in sorted(program_files(root)):
            out.add(root / path, arcname=path, recursive=False)
    os.replace(PREVIOUS.with_suffix(".tar.tmp"), PREVIOUS)


def extract_bundle(bundle, staging):
    # Older Python versions without the security backport must not extract as root unchecked.
    if not hasattr(tarfile, "data_filter"):
        raise ValueError("Python lacks safe tar extraction; update Python before upgrading")
    with tarfile.open(bundle) as archive:
        archive.extractall(staging, filter="data")


def install_unit(root, log):
    unit = root / "dashplay.service"
    if not UNIT.exists() or UNIT.read_bytes() != unit.read_bytes():
        shutil.copy2(unit, UNIT)
        run("systemctl", "daemon-reload")
        say("service unit updated", log)


def restore(root, log):
    """Put the program from PREVIOUS back (its files over the current ones, files it lacked removed)."""
    with tempfile.TemporaryDirectory(prefix="dashplay-previous.") as staging:
        extract_bundle(PREVIOUS, staging)
        remove, write = plan(root, staging)
        apply(root, staging, remove, write)
    install_unit(root, log)
    say(f"previous program restored ({len(write)} files back, {len(remove)} removed)", log)


def main(argv):
    if os.geteuid() != 0:
        raise SystemExit("run as root")
    if not (ROOT / "supervisor.py").exists():
        raise SystemExit(f"no DashPlay in {ROOT}: install it with the firmware image instead")
    log = ROOT / "state" / "upgrade.log"
    if argv[:1] == ["--rollback"]:
        if not PREVIOUS.exists():
            raise SystemExit(f"{PREVIOUS} missing: nothing to roll back to")
        restore(ROOT, log)
        run("systemctl", "restart", "dashplay")
        up = service_up()
        say("rolled back; service " + ("up" if up else "NOT up: send state/upgrade.log and receiver.log"), log)
        return 0 if up else 1
    if not argv or not Path(argv[0]).is_file():
        raise SystemExit(__doc__)
    bundle, dry = Path(argv[0]), "--dry-run" in argv
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="dashplay-upgrade.") as staging:
        extract_bundle(bundle, staging)
        missing = [p for p in NEEDED if not (Path(staging) / p).exists()]
        if missing:
            raise SystemExit(f"{bundle} is not a DashPlay program bundle (missing {', '.join(missing)})")
        remove, write = plan(ROOT, staging)
        print("\n".join([f"replace/add {p}" for p in write] + [f"remove {p}" for p in remove]) or "nothing to change")
        if dry:
            return 0
        if not write and not remove:
            say(f"already at bundle {digest[:12]}", log)
            return 0
        say(f"upgrade to bundle sha256 {digest}: {len(write)} files new or changed, {len(remove)} removed", log)
        backup(ROOT)
        say(f"previous program saved in {PREVIOUS}", log)
        apply(ROOT, staging, remove, write)
    install_unit(ROOT, log)
    check = run("python3", str(ROOT / "supervisor.py"), "--root", str(ROOT), "--check")
    if check.returncode != 0:
        say("new program failed its check: " + (check.stderr or check.stdout).strip()[-400:], log)
        restore(ROOT, log)
        run("systemctl", "restart", "dashplay")
        return 1
    say("check passed; restarting the service (the box's Wi-Fi restarts too)", log)
    run("systemctl", "restart", "dashplay")
    if service_up():
        (ROOT / "state" / "upgrade.json").write_text(json.dumps({"bundle_sha256": digest, "at": int(time.time())}) + "\n")
        say("upgrade done; service up", log)
        return 0
    say("service did not come up with the new program: rolling back", log)
    restore(ROOT, log)
    run("systemctl", "restart", "dashplay")
    say("rolled back; service " + ("up" if service_up() else "NOT up"), log)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
