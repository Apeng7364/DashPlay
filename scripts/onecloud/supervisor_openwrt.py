#!/usr/bin/env python3
"""Run DashPlay on OpenWrt as one procd service (/etc/init.d/dashplay, package openwrt/dashplay).

The router owns the network: the person sets up Wi-Fi in LuCI. This supervisor reads the Wi-Fi to hand
the iPhone (uci dashplay.main.wifi: a wifi-iface section, or "auto" = the first access point, 5 GHz
first), starts the receiver on that network's address and the settings page (iPhones only) on
settings_port, and restarts itself when that Wi-Fi changes. Without a Bluetooth adapter it runs
wired CarPlay only. Compared with supervisor.py: no box hotspot, no joining, no first-run setup.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time

from boxnet import ensure_leaf
from settings_page import SettingsPage
import setup_wizard
import diagnostics
from platform_openwrt import OpenWrtPlatform, RUN

DEFAULTS = {"enabled": "1", "wifi": "auto", "settings_port": "18080",
            "java": "/usr/lib/dashplay-jre/bin/java", "name": "dashplay.lan",
            # Boards whose BlueZ cannot come from the system (ZTE F50: board_zte_f50.py): a command that
            # brings up the controller and its own D-Bus, and that bus's address.
            "bluetooth_service": "", "dbus_address": ""}
RECHECK_EVERY = 10      # seconds between looks at the Wi-Fi handed to the iPhone
WIRED_STATUS = RUN / "wired-status.json"


def read_config(uci_text):
    """`uci -q show dashplay` -> options of the main section, with defaults."""
    config = dict(DEFAULTS)
    for line in (uci_text or "").splitlines():
        match = re.match(r"^dashplay\.main\.(\w+)='?(.*?)'?$", line)
        if match:
            config[match.group(1)] = match.group(2)
    return config


def point_name(name, host):
    """Keep our uci dhcp record (section "dashplay") answering name -> the router's address, so the head
    unit can open https://dashplay.lan:18443/ whatever the LAN address. Returns True when it changed."""
    def get(key):
        return subprocess.run(["uci", "-q", "get", key], capture_output=True, text=True).stdout.strip()

    if get("dhcp.dashplay") == "domain" and get("dhcp.dashplay.name") == name and get("dhcp.dashplay.ip") == host:
        return False
    for change in (["set", "dhcp.dashplay=domain"], ["set", f"dhcp.dashplay.name={name}"],
                   ["set", f"dhcp.dashplay.ip={host}"], ["commit", "dhcp"]):
        subprocess.run(["uci", *change], check=True, capture_output=True)
    def reload():
        try:
            # No pipes: a daemon started by the init script would hold them open (OpenWrt container,
            # 2026-10-12, where the reload also never finished). The name is a convenience: in the
            # background, so CarPlay never waits for it.
            subprocess.run(["/etc/init.d/dnsmasq", "reload"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        except subprocess.TimeoutExpired:
            print("dnsmasq reload timed out", flush=True)

    threading.Thread(target=reload, name="dnsmasq-reload", daemon=True).start()
    return True


def plan_of(network):
    """What the receiver depends on: a change in any of these restarts it."""
    return (network["ssid"], network["key"], network["security"], network["channel"], str(network["address"].ip))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    config = read_config(subprocess.run(["uci", "-q", "show", "dashplay"], capture_output=True, text=True).stdout)
    port = int(config["settings_port"])
    if not re.fullmatch(r"[A-Za-z0-9_@\[\]-]{1,64}", config["wifi"]) or not 1024 <= port <= 65535 or port == 18443:
        raise ValueError("Invalid dashplay.main.wifi or settings_port")
    if not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,62})(\.[a-z0-9-]{1,63})+", config["name"]):
        raise ValueError("Invalid dashplay.main.name")
    for relative in ["run-receiver.py", "phone-presence.py", "pair-phone.py", "pairing_window.py", "settings_page.py",
                     "settings_ui.py", "platform_openwrt.py", "diagnostics.py", "iphone-usb-bridge.py",
                     "linux-bt-bridge/bt-bridge"]:
        if not (root / relative).is_file():
            raise RuntimeError("Missing runtime input: " + relative)
    if not Path(config["java"]).is_file():
        raise RuntimeError(f"Java not found at {config['java']}: install the dashplay-jre package")
    identity = [root / "identity/offline-mfi/identity.pk8", root / "identity/offline-mfi/certificate.p7b"]

    def identity_ok():
        return all(path.is_file() and path.stat().st_size > 0 for path in identity)

    if args.check:
        print("Service configuration and runtime files checked; no services changed")
        return

    os.umask(0o077)
    RUN.mkdir(mode=0o700, parents=True, exist_ok=True)
    # A board helper may put its own bluetoothctl in RUN/bin; every child inherits PATH and the bus.
    os.environ["PATH"] = f"{RUN / 'bin'}:{os.environ.get('PATH', '/usr/sbin:/usr/bin:/sbin:/bin')}"
    if config["dbus_address"]:
        os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = config["dbus_address"]
    state = root / "state"
    state.mkdir(mode=0o700, exist_ok=True)
    platform = OpenWrtPlatform(run=RUN)
    stopped = threading.Event()
    reexec = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    have_identity = identity_ok()
    status = {"mode": "openwrt", "entries": [], "identity": have_identity, "home": f"http://{config['name']}:{port}/"}

    def wait_for(predicate, seconds, interval=0.2):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if stopped.is_set():
                raise InterruptedError("Service stopping")
            if predicate():
                return
            stopped.wait(interval)
        raise TimeoutError("Service dependency did not become ready")

    def request_reexec(reason):
        reexec.append(reason)
        stopped.set()

    settings = SettingsPage(state, lambda: status, request_reexec,
                            lambda: [ip.network for ip in platform.local_addresses()], None, WIRED_STATUS,
                            managed={"by": "OpenWrt", "admin": "/cgi-bin/luci/admin/network/wireless"})
    def say(text):
        """procd sends stdout to logread, which the F50's Wi-Fi driver floods within seconds: keep our
        own lines in RUN/supervisor.log too (mirrored to state/logs like every RUN/*.log)."""
        print(text, flush=True)
        try:
            with (RUN / "supervisor.log").open("a") as log:
                log.write(text + "\n")
        except OSError:
            pass

    def mark(step):
        say(f"boot {time.monotonic():.2f}s {step}")   # seconds since kernel start

    handoff_file = RUN / "handoff.json"

    def launch(network, wireless, wait=False):
        setup_wizard.save_json(handoff_file, {"ssid": network["ssid"], "passphrase": network["key"]})
        os.environ["CARPLAY_NETWORK_EPOCH"] = secrets.token_hex(4)
        platform.start("receiver", [
            sys.executable, str(root / "run-receiver.py"), "--root", str(root), "--bind", str(network["address"].ip),
            "--channel", str(network["channel"]), "--security", network["security"],
            "--hotspot", str(handoff_file), "--interface", network["device"], "--web-host", "0.0.0.0",
            "--java", config["java"], "--settings-port", str(port), "--wired", "--wired-status", str(WIRED_STATUS)]
            + ([] if wireless else ["--no-wireless"]) + (["--bind-wait", "120"] if wait else []))

    try:
        mark("supervisor start")
        # Full logs on the SD card / flash, so that a drive can be analysed after the power is cut.
        diagnostics.LogMirror(state / "logs", RUN, platform.boot_id(), journal=False).start(stopped)

        # Bluetooth at once, beside the Wi-Fi: the F50's controller takes ~10 s to come up, about as
        # long as its Wi-Fi (supervisor.py does the same). A router without BlueZ runs wired only.
        if config["bluetooth_service"]:
            platform.start("bluetooth", shlex.split(config["bluetooth_service"]))
        has_bluez = bool(config["bluetooth_service"] or shutil.which("bluetoothctl"))
        bluetooth = []

        def power_bluetooth():
            try:   # bluetoothd (or the board helper's bluetoothctl) may come up after us
                return platform.power_bluetooth()
            except (subprocess.SubprocessError, FileNotFoundError):
                return False

        def bring_up_bluetooth():
            if not has_bluez:
                say("No BlueZ (bluez-daemon not installed): wired CarPlay only")
                return bluetooth.append(False)
            try:
                wait_for(power_bluetooth, 60 if config["bluetooth_service"] else 40, interval=0.5)
                mark("bluetooth powered")
                bluetooth.append(True)
            except TimeoutError:
                say("Bluetooth adapter did not power on: wired CarPlay only")
                bluetooth.append(False)
            except InterruptedError:
                bluetooth.append(False)

        bluetooth_thread = threading.Thread(target=bring_up_bluetooth, name="bluetooth-power", daemon=True)
        bluetooth_thread.start()

        # The JVM needs several seconds before it first invites the iPhone: start it on what the Wi-Fi
        # configuration says while the radio comes up, and check it against the live Wi-Fi below.
        early = None
        if have_identity:
            early, _ = platform.handoff(config["wifi"], live=False)
            if early:
                ensure_leaf(state, [config["name"], setup_wizard.MDNS_NAME], [str(early["address"].ip)])
                launch(early, has_bluez, wait=True)
                mark(f"receiver launched early for {early['ssid']}")

        # The Wi-Fi comes up some seconds after procd starts us; wait for it rather than fail.
        reason = ""
        network = None
        while not stopped.is_set():
            network, why = platform.handoff(config["wifi"])
            if network:
                break
            if why != reason:
                say(f"waiting: {why}")
                reason = why
            stopped.wait(1)
        if stopped.is_set():
            raise InterruptedError("Service stopping")
        mark(f"Wi-Fi {network['ssid']} up on channel {network['channel']}")
        address = network["address"]
        platform.interface, platform.ap_interface = network["device"], network["ifname"]
        host = str(address.ip)
        # The name resolves through the router's own DNS, i.e. for devices on its access point only; on a
        # client network (car hotspot, portable Wi-Fi) the head unit asks that network's DNS instead.
        named = network["mode"] == "ap"
        if named and point_name(config["name"], host):
            say(f"{config['name']} now points at {host}")
        status.update(ssid=network["ssid"], home=f"http://{host}:{port}/",
                      entries=([f"https://{config['name']}:18443/"] if named else []) + [f"https://{host}:18443/"])
        if network["mode"] == "sta" and platform.zone_input(network["network"]) not in ("ACCEPT", None):
            # A client network usually sits in the wan zone, which rejects the iPhone's AirPlay connections.
            status["warning"] = (f"「{network['ssid']}」所在的防火墙区域拒绝入站连接，iPhone 连不上 DashPlay。"
                                 f"请在 OpenWrt 后台把网络 {network['network']} 放进允许入站的区域，或改用路由器自己的 WiFi。")
            say(f"warning: firewall zone of {network['network']} rejects input")
        ensure_leaf(state, [config["name"], setup_wizard.MDNS_NAME], [host])
        settings.start(port)
        diagnostics.start(stopped, diagnostics.DiagLog(state / "diag"), RUN / "receiver.log", platform, status,
                          state=state)

        bluetooth_thread.join()
        wireless = bool(bluetooth and bluetooth[0])
        # setup.json carries the way of use the settings page shows: wireless (handoff) or cable only.
        setup_wizard.save(state, {"done": True, "mode": "box_hotspot" if wireless else "car_hotspot"})
        if have_identity:
            if early and plan_of(early) == plan_of(network) and wireless == has_bluez:
                mark("early receiver kept")
            else:
                if early:
                    platform.stop_child("receiver")
                    mark("early receiver stopped: the live Wi-Fi or Bluetooth differs")
                launch(network, wireless)
        else:
            say("MFi identity missing: settings page only, CarPlay starts once the files are added")
        say(f"DashPlay services started on {network['ssid']} ({host}, channel {network['channel']})")
        plan = plan_of(network)
        next_check = time.monotonic() + RECHECK_EVERY
        while not stopped.wait(2):
            if not have_identity and identity_ok():
                request_reexec("MFi identity added")
                continue
            for name, child in platform.children:
                if child.poll() is not None:
                    raise RuntimeError(f"{name} exited with {child.returncode}")
            if time.monotonic() >= next_check:
                next_check = time.monotonic() + RECHECK_EVERY
                now, why = platform.handoff(config["wifi"])
                if now is None or plan_of(now) != plan:
                    request_reexec("Wi-Fi changed" if now else why)
    except InterruptedError:
        pass
    finally:
        stopped.set()
        settings.stop()
        platform.stop_all()
        say("DashPlay services stopped")
    if reexec:
        say(f"restarting: {reexec[0]}")
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), "--root", str(root)])


if __name__ == "__main__":
    main()
