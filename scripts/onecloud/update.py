#!/usr/bin/env python3
"""Online update of DashPlay from its signed release manifest (docs/在线升级方案-20261019.md).

    update.py check   [--root /opt/dashplay]   # a newer release? -> state/update-check.json
    update.py launch  [--root /opt/dashplay]   # start `install` apart from the service, which it restarts
    update.py install [--root /opt/dashplay]   # download, verify, back up, install, check; go back on failure

Every release carries dashplay-update.json and its Ed25519 signature. Only a manifest signed by the key in
update-key.pem (beside this file) counts, and every downloaded file must match it, so a mirror needs no trust.
OpenWrt: the dashplay, luci-app-dashplay and zh-cn packages (the JRE only when its version changed) with apk,
refusing a plan with any kernel, kmod or BlueZ package (the ZTE F50 runs a vendor 5.4 kernel); the packages of
the installed version are fetched first to go back to. OneCloud / N1: firmware/upgrade.py with the release's
program bundle (it keeps state/ and identity/ and restores the previous program by itself).
Never while CarPlay is in use, and never by itself: the boxes only check (once a day) and say so.
Progress goes to state/update-status.json, which the settings page and LuCI show.
"""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

OWNER = "yizhixiaoheigou"
# Where the latest manifest and the installed version's OpenWrt packages (to go back to) come from. The environment
# may point both at a test server (scripts/openwrt/lab-test.sh); the signature check is the same.
LATEST = os.environ.get("DASHPLAY_UPDATE_LATEST", f"https://github.com/{OWNER}/DashPlay/releases/latest/download/")
PREVIOUS_FEED = os.environ.get("DASHPLAY_UPDATE_FEED",
                               f"https://github.com/{OWNER}/openwrt-dashplay/releases/download/v{{version}}/")
VERSION_FILE = "version.txt"
MANIFEST = "dashplay-update.json"
SIGNATURE = MANIFEST + ".sig"
KEY = Path(__file__).resolve().with_name("update-key.pem")
OPENSSL = os.environ.get("DASHPLAY_OPENSSL", "openssl")   # OpenSSL 3 on the boxes (a Mac's own is LibreSSL)
# Tried after GitHub itself, as a URL prefix. The signature makes any mirror safe; uci dashplay.main.update_mirror
# (OpenWrt) or state/options.json "update_mirror" (OneCloud / N1) replaces this list, "none" for GitHub only.
MIRRORS = ("https://ghfast.top/",)
OPENWRT_PACKAGES = ("dashplay", "luci-app-dashplay", "luci-i18n-dashplay-zh-cn")
FORBIDDEN = re.compile(r"^(kernel|kmod-|bluez)")   # never through an online update (ZTE F50: 5.4 kernel)
CHECK_EVERY = 24 * 3600
# What install.sh set up on a OneCloud / N1 comes with the firmware image, and an update replaces programs only. A
# programs-only release keeps these as in the images it links (prepare-release.sh checks), so a box whose own copies
# differ was flashed with older images: it needs the new images, not this update (a OneCloud from 2026-10-12).
SYSTEM_FILES = ("firmware/install.sh", "firmware/prepare-image.sh", "firmware/dashplay-firstboot.service",
                "dashplay.service")
BACKUP = "dashplay-update-backup.tar.gz"   # beside the program directory: the owner's data before the last update


class UpdateError(Exception):
    """Why an update stops, in words for the owner (Chinese, shown on the page)."""


class NotFound(UpdateError):
    """Every source answered 404: the file is not published, not a network problem."""


def is_openwrt():
    return Path("/etc/openwrt_release").exists()


def run(*args, timeout=300):
    return subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)


def version_key(version):
    """'2026.10.19.1' -> (2026, 10, 19, 1); None for anything else (unknown versions are never newer)."""
    if not version or not re.fullmatch(r"\d+(\.\d+)*", version):
        return None
    return tuple(int(part) for part in version.split("."))


def newer(latest, current):
    latest, current = version_key(latest), version_key(current)
    return latest is not None and (current is None or latest > current)


def mirrors(root, openwrt):
    """GitHub first, then the configured mirrors (prefixes)."""
    if openwrt:
        try:
            value = run("uci", "-q", "get", "dashplay.main.update_mirror", timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            value = ""
    else:
        try:
            value = str(json.loads((Path(root) / "state/options.json").read_text()).get("update_mirror", ""))
        except (OSError, ValueError, AttributeError):
            value = ""
    if value == "none":
        return ()
    return tuple(value.split()) if value else MIRRORS


def fetch(url, prefixes=(), limit=1 << 20, target=None, sha256=None, size=None):
    """Bytes of url (or, with target, the file written there), from GitHub or else each mirror prefix. With sha256
    and size the download must match them exactly."""
    errors, codes = [], []
    for candidate in [url] + [prefix + url for prefix in prefixes]:
        try:
            request = urllib.request.Request(candidate, headers={"User-Agent": "DashPlay-update"})
            with urllib.request.urlopen(request, timeout=30) as response:
                digest, total = hashlib.sha256(), 0
                out = open(target, "wb") if target else None
                chunks = []
                try:
                    while True:
                        chunk = response.read(1 << 16)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > (size if size is not None else limit):
                            raise UpdateError("下载的文件比预期大，已停止")
                        digest.update(chunk)
                        (out.write(chunk) if out else chunks.append(chunk))
                finally:
                    if out:
                        out.close()
            if sha256 and (digest.hexdigest() != sha256 or total != size):
                raise UpdateError("下载的文件与签名清单不符")
            return target if target else b"".join(chunks)
        except (OSError, UpdateError, ValueError, http.client.HTTPException) as error:
            if hasattr(error, "close"):   # an HTTPError holds its response open
                error.close()
            errors.append(f"{candidate.split('/')[2]}: {error}")
            codes.append(getattr(error, "code", None))
    if all(code == 404 for code in codes):
        raise NotFound("下载失败（" + "；".join(errors)[-300:] + "）")
    raise UpdateError("下载失败（" + "；".join(errors)[-300:] + "）。网络恢复后再试，或在设置里换一个镜像")


def verify(data, signature, key=KEY):
    """True when signature is the release key's Ed25519 signature of data (OpenSSL 3)."""
    with tempfile.TemporaryDirectory(prefix="dashplay-verify.") as work:
        (Path(work) / "m").write_bytes(data)
        (Path(work) / "s").write_bytes(signature)
        try:
            out = run(OPENSSL, "pkeyutl", "-verify", "-pubin", "-inkey", str(key), "-rawin",
                      "-in", str(Path(work) / "m"), "-sigfile", str(Path(work) / "s"), timeout=30)
        except FileNotFoundError:
            raise UpdateError("系统缺少 openssl，无法核对签名")
    return out.returncode == 0 and "Signature Verified Successfully" in out.stdout


def latest_manifest(root, openwrt, key=KEY):
    prefixes = mirrors(root, openwrt)
    data = fetch(LATEST + MANIFEST, prefixes, limit=1 << 20)
    signature = fetch(LATEST + SIGNATURE, prefixes, limit=4096)
    if not verify(data, signature, key):
        raise UpdateError("更新清单的签名不对，已忽略（可能是镜像或网络被篡改）")
    manifest = json.loads(data)
    if manifest.get("format") != 1 or not version_key(manifest.get("version")):
        raise UpdateError("更新清单格式不认识：请先手动升级到较新的版本")
    return manifest


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def program_matches(root, files):
    """The program under root has every file of the release ({relative path: sha256}) as released."""
    root = Path(root)
    return bool(files) and all((root / path).is_file() and file_sha256(root / path) == digest
                               for path, digest in files.items())


def system_matches(root, files):
    """The box's system files are the ones this release was made for (OneCloud / N1)."""
    return all((Path(root) / path).is_file() and file_sha256(Path(root) / path) == files[path]
               for path in SYSTEM_FILES if path in files)


def apk_version(name, listing=None):
    """'2026.10.19.1-r1' for an installed dashplay-2026.10.19.1-r1, or None. From the list of every installed
    package: `apk info -v NAME` prints the description instead (apk 3, OpenWrt 25.12)."""
    out = (listing if listing is not None else run("apk", "info", "-v", timeout=30).stdout).split()
    for word in out:
        if word.startswith(name + "-") and re.fullmatch(r"[0-9][^-]*-r\d+", word[len(name) + 1:]):
            return word[len(name) + 1:]
    return None


def bundled_version(root):
    """The release a program bundle was built for (build-bundle.sh writes version.txt for a release), or None
    for a development build and the releases before 2026.10.20."""
    try:
        return (Path(root) / VERSION_FILE).read_text().strip() or None
    except OSError:
        return None


def current_version(root, openwrt):
    if openwrt:
        full = apk_version("dashplay")
        return full.rsplit("-r", 1)[0] if full else None
    try:
        return bundled_version(root) or json.loads((Path(root) / "state/update.json").read_text()).get("version")
    except (OSError, ValueError, AttributeError):
        return None


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def compare(root, openwrt, manifest):
    """What the page shows about the installed program and this release."""
    current = current_version(root, openwrt)
    online, reason = bool(manifest.get("online")), "" if manifest.get("online") else "这一版改了系统"
    if openwrt:
        available = newer(manifest["version"], current)
    else:   # a box flashed from an image knows no version: its files tell
        files = manifest.get("debian", {}).get("files", {})
        available = not program_matches(root, files)
        current = manifest["version"] if not available else current
        if online and not system_matches(root, files):
            online, reason = False, "这台盒子的系统比这一版要求的旧"
    return {"current": current, "latest": manifest["version"], "notes": manifest.get("notes", ""),
            "available": available, "online": online, "reason": reason}


def check(root, openwrt=None, key=KEY, manifest=None):
    """Compare the installed program with the latest signed release; write and return state/update-check.json."""
    root = Path(root)
    openwrt = is_openwrt() if openwrt is None else openwrt
    result = {"checked_at": int(time.time())}
    try:
        result.update(compare(root, openwrt, manifest or latest_manifest(root, openwrt, key)))
    except UpdateError as error:
        result["error"] = str(error)
    except (ValueError, KeyError, subprocess.SubprocessError) as error:
        result["error"] = f"检查更新失败：{error}"
    save(root / "state/update-check.json", result)
    return result


def carplay_in_use(root):
    return bool(read(Path(root) / "state/receiver/phone-presence.json").get("active"))


def forbidden_in_plan(simulation):
    """Packages an apk --simulate output would touch that an online update must not (kernel, kmod, BlueZ)."""
    names = re.findall(r"^\(\d+/\d+\) (?:Installing|Upgrading|Downgrading|Replacing|Purging) (\S+)", simulation, re.M)
    return sorted({name for name in names if FORBIDDEN.match(name)})


def previous_packages(version, prefixes, work):
    """The installed release's three packages (to go back to), checked against that release's SHA256SUMS."""
    base = PREVIOUS_FEED.format(version=version)
    try:
        sums = fetch(base + "SHA256SUMS", prefixes, limit=1 << 16).decode()
    except NotFound:
        raise UpdateError(f"找不到当前版本 {version} 的安装包（它不是正式发布的版本），升级失败时无法退回，已停止。"
                          "请按装机说明手动升级") from None
    files = []
    for line in sums.splitlines():
        digest, _, name = line.partition("  ")
        if any(re.fullmatch(rf"{re.escape(package)}-[0-9][^/]*\.apk", name) for package in OPENWRT_PACKAGES):
            target = Path(work) / "previous" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            fetch(base + name, prefixes, target=target, limit=256 << 20)   # no size here: the hash below decides
            if file_sha256(target) != digest.strip():
                raise UpdateError("当前版本的安装包校验不符，无法保证能退回，已停止")
            files.append(target)
    if len(files) != len(OPENWRT_PACKAGES):
        raise UpdateError(f"找不到当前版本 {version} 的安装包，升级失败时无法退回，已停止")
    return files


def settings_port():
    port = run("uci", "-q", "get", "dashplay.main.settings_port", timeout=5).stdout.strip()
    return int(port) if port.isdigit() else 18080


def service_up(port, seconds=150, steady=15):
    """The dashplay service runs and its settings page answers (it opens even while the Wi-Fi is not up), and still
    does `steady` seconds later: on the F50 it answered 2 s after apk returned, before a crash could show."""
    def answers():
        try:
            socket.create_connection(("127.0.0.1", port), 3).close()
            return True
        except OSError:
            return False

    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if answers():
            time.sleep(steady)
            if answers():
                return True
        time.sleep(3)
    return False


def backup(root, openwrt):
    """The owner's data (pairings, certificates, identity, settings), logs left out; only the latest is kept."""
    root = Path(root)
    target = root.parent / BACKUP
    with tarfile.open(target.with_suffix(".tmp"), "w:gz") as out:
        for name in ("state", "identity"):
            if (root / name).exists():
                out.add(root / name, arcname=name,
                        filter=lambda info: None if re.match(r"state/(logs|diag)(/|$)", info.name) else info)
        for config in ("/etc/config/dashplay", str(root / "service.json")):
            if Path(config).exists():
                out.add(config, arcname="config/" + Path(config).name)
    os.chmod(target.with_suffix(".tmp"), 0o600)
    os.replace(target.with_suffix(".tmp"), target)
    return target


def install_openwrt(root, manifest, status, work):
    current = current_version(root, True)
    if not current:
        raise UpdateError("没有找到已安装的 dashplay 软件包")
    prefixes = mirrors(root, True)
    arch = Path("/etc/apk/arch").read_text().strip()
    feed = manifest["openwrt"]
    wanted = {name: feed["packages"][name] for name in OPENWRT_PACKAGES}
    if apk_version("dashplay-jre") != feed["jre"]["version"]:
        if arch not in feed["jre"]["files"]:
            raise UpdateError(f"这一版没有 {arch} 的 Java 运行环境")
        wanted["dashplay-jre"] = feed["jre"]["files"][arch]
    # The way back first: without it nothing is installed, so the new release is not fetched for nothing.
    status("downloading", f"正在下载当前版本 {current} 的安装包（升级失败时用来退回）")
    previous = previous_packages(current, prefixes, work)
    status("downloading", f"正在下载 {manifest['version']}（{sum(f['size'] for f in wanted.values()) // 1048576} MB）")
    files = []
    for entry in wanted.values():
        files.append(fetch(feed["base"] + entry["file"], prefixes, target=Path(work) / entry["file"],
                           sha256=entry["sha256"], size=entry["size"]))
    status("checking", "正在核对升级计划")
    run("apk", "update", timeout=300)   # without the index the simulation misses dependencies (F50, 2026-10-19)
    simulation = run("apk", "add", "--upgrade", "--simulate", "--allow-untrusted", *map(str, files))
    if simulation.returncode != 0:
        raise UpdateError("升级计划不成立：" + (simulation.stderr or simulation.stdout).strip()[-300:])
    blocked = forbidden_in_plan(simulation.stdout)
    if blocked:
        raise UpdateError("升级会改动内核、内核模块或系统蓝牙（" + "、".join(blocked) + "），已停止，请按说明手动升级")
    status("installing", "正在安装，CarPlay 会断开约 1 分钟")
    port = settings_port()
    result = run("apk", "add", "--upgrade", "--allow-untrusted", *map(str, files), timeout=600)
    if result.returncode == 0 and service_up(port):
        return
    status("restoring", "新版本没有正常启动，正在退回原来的版本")
    back = run("apk", "add", "--allow-untrusted", *map(str, previous), timeout=600)
    up = back.returncode == 0 and service_up(port)
    raise UpdateError(f"新版本没有正常启动，已退回 {current}" if up else
                      "新版本没有正常启动，退回也没有成功：请按说明用电脑恢复（数据备份在 /opt/" + BACKUP + "）")


def install_debian(root, manifest, status, work):
    entry = manifest["debian"]["bundle"]
    status("downloading", f"正在下载 {manifest['version']}（{entry['size'] // 1048576} MB）")
    bundle = fetch(manifest["debian"]["base"] + entry["file"], mirrors(root, False), target=Path(work) / entry["file"],
                   sha256=entry["sha256"], size=entry["size"])
    status("installing", "正在安装，CarPlay 和盒子热点会断开约 1 分钟")
    upgrader = Path(root) / "firmware/upgrade.py"
    with tarfile.open(bundle) as archive:   # the new release's upgrader, as the manual procedure does
        member = next((m for m in archive.getmembers()
                       if m.name.removeprefix("./") == "firmware/upgrade.py" and m.isfile()), None)
        if member:
            upgrader = Path(work) / "upgrade.py"
            upgrader.write_bytes(archive.extractfile(member).read())
    result = run(sys.executable, str(upgrader), str(bundle), timeout=900)
    if result.returncode != 0:
        raise UpdateError("新版本没有正常启动，已自动退回原来的程序（详见 state/upgrade.log）")
    save(Path(root) / "state/update.json", {"version": manifest["version"], "at": int(time.time())})


def install(root, openwrt=None, key=KEY):
    root = Path(root)
    openwrt = is_openwrt() if openwrt is None else openwrt
    status_file = root / "state/update-status.json"

    def status(phase, message, **extra):
        save(status_file, {"phase": phase, "message": message, "at": int(time.time()), **extra})

    status("checking", "正在检查最新版本")
    try:
        manifest = latest_manifest(root, openwrt, key)
        found = check(root, openwrt, key, manifest)
        if not found.get("available"):
            status("done", f"已是最新版本 {manifest['version']}")
            return 0
        if not found.get("online"):
            raise UpdateError(f"{found.get('reason') or '这一版改了系统'}，不能在线升级 {manifest['version']}："
                              "请按装机说明用刷机包升级")
        if carplay_in_use(root):
            raise UpdateError("正在使用 CarPlay：停车、断开后再升级")
        if shutil.disk_usage(tempfile.gettempdir()).free < 200 << 20:
            raise UpdateError("临时空间不足 200 MB，无法下载")
        status("backup", "正在备份配对、证书和设置")
        saved = backup(root, openwrt)
        with tempfile.TemporaryDirectory(prefix="dashplay-update.") as work:
            (install_openwrt if openwrt else install_debian)(root, manifest, status, work)
        status("done", f"已升级到 {manifest['version']}", version=manifest["version"], backup=str(saved))
        check(root, openwrt, key, manifest)
        return 0
    except UpdateError as error:
        status("failed", str(error))
    except Exception as error:   # whatever happened, the page must not keep saying "installing"
        status("failed", f"升级出错：{type(error).__name__}: {str(error)[-200:]}")
    return 1


def running(root):
    """An install started less than 20 minutes ago that has not finished."""
    state = read(Path(root) / "state/update-status.json")
    return state.get("phase") not in (None, "done", "failed") and time.time() - state.get("at", 0) < 1200


def launch(root, openwrt=None):
    """Start an install apart from the dashplay service (it restarts that service, and procd or systemd would end
    a child with it). Returns a message for the page. OpenWrt: a procd service made for this one run over ubus, not
    an init script, since installing a package starts every init script in it (default_postinst; the F50 began an
    update by itself when the package with one was installed, 2026-10-19)."""
    root = Path(root)
    openwrt = is_openwrt() if openwrt is None else openwrt
    if running(root):
        return "正在升级，请稍候"
    if carplay_in_use(root):
        return "正在使用 CarPlay：停车、断开后再升级"
    install = [sys.executable, str(Path(__file__).resolve()), "install", "--root", str(root)]
    if openwrt:
        service = {"name": "dashplay-update", "instances": {"update": {"command": install, "stdout": True, "stderr": True}}}
        command = ["ubus", "call", "service", "set", json.dumps(service)]
    else:
        command = ["systemd-run", "--unit=dashplay-update", "--collect", *install]
    save(root / "state/update-status.json", {"phase": "starting", "message": "正在开始升级", "at": int(time.time())})
    try:
        if openwrt:   # the finished run before, if any: procd would not start an unchanged instance again
            run("ubus", "call", "service", "delete", json.dumps({"name": "dashplay-update"}), timeout=30)
        out = run(*command, timeout=30)
        why = "" if out.returncode == 0 else (out.stderr or out.stdout).strip()[-200:] or f"退出码 {out.returncode}"
    except (OSError, subprocess.SubprocessError) as error:   # e.g. a version without the update service
        why = str(error)
    if why:   # never leave "starting" behind: it would refuse every try for 20 minutes
        save(root / "state/update-status.json", {"phase": "failed", "message": "无法启动升级：" + why,
                                                 "at": int(time.time())})
        return "无法启动升级：" + why
    return "已开始升级，约 2–5 分钟，期间 CarPlay 会断开"


def checker(root, stopped, enabled=True, first=120):
    """Supervisor thread body: check once soon after start, then daily. Never installs."""
    if not enabled or stopped.wait(first):
        return
    while True:
        try:
            check(root)
        except Exception:   # a check must never take the service down
            pass
        if stopped.wait(CHECK_EVERY):
            return


def main(argv=None):
    os.umask(0o077)   # status, check and backup files hold nothing for others; the backup holds keys
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["check", "launch", "install"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)
    if args.command == "check":
        print(json.dumps(check(args.root), ensure_ascii=False))
        return 0
    if args.command == "launch":
        print(launch(args.root))
        return 0
    return install(args.root)


if __name__ == "__main__":
    sys.exit(main())
