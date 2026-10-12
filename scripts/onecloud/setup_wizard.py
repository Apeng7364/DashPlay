"""First-run setup: choose how the box is used, set up its network, then the iPhone.

Three ways of use (user decision 2026-10-12, docs/三种使用方式实施计划.md), state/setup.json "mode":
- "car_hotspot": the box joins the car hotspot, which the head unit's browser uses. The iPhone uses
  the USB cable only: the car hotspot relayed iPhone-to-box video at 0.12 Mbit/s (Xpeng, 2026-10-12).
- "mifi": the box, the head unit and the iPhone all join a portable Wi-Fi (MiFi); iPhone on the cable
  if plugged in, otherwise wireless through the MiFi.
- "box_hotspot": the box raises its own hotspot and the head unit joins it (the head unit then has no
  internet); iPhone on the cable or wireless on the box hotspot.
state/setup.json: {"done": bool, "mode": ..., "car": <ssid being set up>, "failed": reason, "box": bool}.
Steps: mode -> car (car_hotspot/mifi: pick the hotspot) or box (box_hotspot: name/password of the box
hotspot) -> phone -> done. Until that network step is done the box hotspot is open (no password) and
DNS answers every name with the box, so a phone joins without a password and opens this page by
itself (captive portal). The daily box hotspot is always WPA2 and never captive. Older files migrate:
a box with a saved car hotspot becomes "car_hotspot".
"""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time

MDNS_NAME = "dashplay.local"
SCAN_CACHE_SECONDS = 20
# One scan with the box hotspot up misses hotspots, a different few each time (2026-10-12, N1: 9, 8, 11 and 11
# names in four scans, 12 together; the OneCloud listed 7-10). The list adds scans up and keeps a hotspot until
# it was not seen for this long.
SCAN_KEEP_SECONDS = 120


MODES = ("car_hotspot", "mifi", "box_hotspot")
JOINS = ("car_hotspot", "mifi")   # modes in which the box joins a hotspot (state/car-hotspot.json)


def load(state: Path):
    try:
        value = json.loads((state / "setup.json").read_text())
    except (OSError, ValueError):
        value = None
    car_saved = (state / "car-hotspot.json").exists()
    if not isinstance(value, dict):
        # Installed before the wizard: set up exactly when a car hotspot is saved.
        value = {"done": car_saved}
    value = {key: item for key, item in value.items() if key not in ("role", "hotspot")}
    if value.get("car") == "skip":   # the old "use the box hotspot" choice
        value.pop("car")
    if value.get("mode") not in MODES:
        value.pop("mode", None)
        # Before 2026-10-12 there was one way of use: the box joined the car hotspot.
        if car_saved or value.get("car"):
            value["mode"] = "car_hotspot"
    if value.get("done") and (value.get("mode") is None or value["mode"] in JOINS and not car_saved):
        value["done"] = False
    return value


def save_json(path: Path, value):
    """Replace a small state file so that a power cut leaves the old or the new content."""
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(value, out)
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(path)
    os.sync()   # the root fs commits only every 120 s; setup progress must survive switching the car off


def save(state: Path, value):
    save_json(state / "setup.json", value)


def step(setup, network):
    """mode -> car (box joins a hotspot) or box (box hotspot) -> phone -> done.
    network: "sta" once the box has joined the chosen hotspot, else "ap"/"starting"."""
    if setup.get("done"):
        return "done"
    mode = setup.get("mode")
    if mode not in MODES:
        return "mode"
    if mode == "box_hotspot":
        return "phone" if setup.get("box") else "box"
    return "phone" if network == "sta" else "car"


def open_hotspot(setup):
    """The box hotspot has no password only until the network step of first-run setup is done."""
    return step(setup, "ap") in ("mode", "car", "box")


def captive(setup, network):
    """Hijack DNS only on the box hotspot and only while it is open."""
    return network == "ap" and open_hotspot(setup)


def wireless(setup):
    """Whether the iPhone may use wireless CarPlay: not in car_hotspot mode (cable only)."""
    return setup.get("mode") != "car_hotspot"


from platform_api import get_platform


def wifi_interface(net=Path("/sys/class/net")):
    return get_platform().wifi_interface(net)


def security(scan_text, ssid):
    """How the car hotspot is secured, for the Wi-Fi handed to the iPhone: open | wpa2 | wpa3-transition | wpa3."""
    best = None
    for block in re.split(r"^BSS ", scan_text, flags=re.M)[1:]:
        name = re.search(r"^\s*SSID: ?(.*)$", block, re.M)
        if not name or _unescape_ssid(name.group(1)) != ssid:
            continue
        suites = " ".join(re.findall(r"Authentication suites: (.*)", block))
        if "SAE" in suites:
            found = "wpa3-transition" if "PSK" in suites else "wpa3"
        elif suites or "WPA:" in block or "RSN:" in block:
            found = "wpa2"
        else:
            found = "open"
        if "associated" in block.split("\n", 1)[0] or best is None:
            best = found
    return best


def handoff_security(found):
    """Security announced to the iPhone. WPA2/WPA3 transition is announced as WPA2: announced as
    transition (iAP2 value 3), iPhone XS accepted the invitation three times and never joined
    (2026-10-11), while WPA2 always worked; iOS then only warns about the mode when joined by hand."""
    return {"wpa3-transition": "wpa2", None: "wpa2"}.get(found, found)


def _unescape_ssid(text):
    """iw prints non-printable SSID bytes as \\xNN (UTF-8 names arrive that way)."""
    raw = bytearray()
    index = 0
    while index < len(text):
        if text.startswith("\\x", index) and re.fullmatch(r"[0-9a-fA-F]{2}", text[index + 2:index + 4]):
            raw.append(int(text[index + 2:index + 4], 16))
            index += 4
        elif text.startswith("\\\\", index):
            raw.append(0x5c)
            index += 2
        else:
            raw.extend(text[index].encode())
            index += 1
    return raw.decode("utf-8", errors="replace")


def parse_scan(text, own_ssid=None):
    """Strongest entry per visible SSID, strongest first: [{ssid, signal, band}]."""
    best = {}
    for block in re.split(r"^BSS ", text, flags=re.M)[1:]:
        ssid = re.search(r"^\s*SSID: ?(.*)$", block, re.M)
        signal = re.search(r"^\s*signal: (-?[\d.]+) dBm", block, re.M)
        freq = re.search(r"^\s*freq: ([\d.]+)", block, re.M)
        if not ssid or not signal:
            continue
        name = _unescape_ssid(ssid.group(1))
        if not name or name == own_ssid or "\x00" in name:
            continue
        entry = {"ssid": name, "signal": round(float(signal.group(1))),
                 "band": "5 GHz" if freq and float(freq.group(1)) > 3000 else "2.4 GHz"}
        if name not in best or entry["signal"] > best[name]["signal"]:
            best[name] = entry
    return sorted(best.values(), key=lambda item: -item["signal"])


class Scanner:
    """On-demand scan; rtw88 scans off-channel briefly while keeping the box hotspot up. Not in the background:
    every scan takes the box hotspot off its channel for a moment, under the phone doing the setup."""

    def __init__(self, interface, own_ssid, mode, platform=None):
        self.platform = platform or get_platform(interface)
        self.interface, self.own_ssid, self.mode = interface, own_ssid, mode
        self.lock = threading.Lock()
        self.seen = {}   # ssid -> (entry, last seen)
        self.at = None

    def scan_once(self):
        try:
            found = self.platform.scan_networks(self.own_ssid, ap=self.mode() == "ap")
        except (OSError, subprocess.TimeoutExpired):
            return   # keep what was just seen
        now = time.monotonic()
        for entry in found:
            self.seen[entry["ssid"]] = (entry, now)

    def results(self, fresh=False):
        with self.lock:
            now = time.monotonic()
            if self.at is None:
                self.scan_once()   # the first list: two scans, as one misses hotspots
                self.scan_once()
                self.at = time.monotonic()
            elif fresh or now - self.at >= SCAN_CACHE_SECONDS:
                self.scan_once()
                self.at = time.monotonic()
            self.seen = {ssid: kept for ssid, kept in self.seen.items() if self.at - kept[1] <= SCAN_KEEP_SECONDS}
            return sorted((entry for entry, _ in self.seen.values()), key=lambda item: -item["signal"])


def join_failure(log_text):
    """Classify a failed join from the wpa_supplicant log without exposing any of it."""
    # WPA2: the 4-way handshake fails. WPA3/SAE (a transition hotspot is joined with SAE): the AP rejects
    # the SAE exchange with status 15 (2026-10-11, Xiaomi hotspot with a wrong password).
    if re.search(r"WRONG_KEY|pre-shared key may be incorrect|4-Way Handshake failed"
                 r"|AUTH-REJECT .*auth_type=3 .*status_code=15", log_text):
        return "wrong_password"
    if re.search(r"CTRL-EVENT-CONNECTED", log_text):
        return "no_address"
    return "not_found"


FAILURES = {
    "wrong_password": "密码不对，请重新输入。",
    "not_found": "没有找到这个热点。请确认热点已打开、名称正确，并且盒子离它不远。",
    "no_address": "连上了热点，但没有拿到网络地址。请重试。",
}
