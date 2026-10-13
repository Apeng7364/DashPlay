#!/usr/bin/env python3
"""Run DashPlay on OpenWrt as one procd service (/etc/init.d/dashplay, package openwrt/dashplay).

The router owns the network: the person sets up Wi-Fi in LuCI. This supervisor reads the Wi-Fi to hand
the iPhone (uci dashplay.main.wifi: a wifi-iface section, or "auto" = the first access point, 5 GHz
first), starts the receiver on that network's address and the settings page (iPhones only) on
settings_port, and restarts itself when that Wi-Fi changes. Without a Bluetooth adapter it runs
wired CarPlay only. Compared with supervisor.py: no box hotspot, no joining, no first-run setup.
Optional: a DuckDNS name with a Let's Encrypt certificate (uci duckdns_domain / duckdns_token,
own_domain.py) for car browsers that refuse the local CA; it then replaces the name.
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
import own_domain
from platform_openwrt import OpenWrtPlatform, RUN

DEFAULTS = {"enabled": "1", "wifi": "auto", "settings_port": "18080",
            "java": "/usr/lib/dashplay-jre/bin/java", "name": "dashplay.lan",
            # Boards whose BlueZ cannot come from the system (ZTE F50: board_zte_f50.py): a command that
            # brings up the controller and its own D-Bus, and that bus's address.
            "bluetooth_service": "", "dbus_address": "",
            # Seconds without a viewer page before wireless CarPlay is ended, so an iPhone near a box left on
            # does not keep sending its screen (0: never; then a reopened page shows the picture at once).
            "idle_release": "300",
            # The name also answers with the router's own public IPv6 address, for car browsers that refuse a
            # private one: 0 no, 1 beside the LAN address, 2 instead of it (LAN address while there is none).
            "public_ipv6": "0",
            # A DuckDNS sub-domain and its token: the name becomes <sub-domain>.duckdns.org, with a Let's Encrypt
            # certificate the router obtains and renews itself (own_domain.py). Empty: off.
            "duckdns_domain": "", "duckdns_token": ""}
RECHECK_EVERY = 10      # seconds between looks at the Wi-Fi handed to the iPhone
WIRED_STATUS = RUN / "wired-status.json"


def trusted_networks(network):
    """Who may use the settings page: the network of the Wi-Fi handed to the iPhone, where the head unit is,
    not every interface of the router (the WAN or a guest network too; audit 2026-10-13, A5)."""
    return [network["address"].network]


def read_config(uci_text):
    """`uci -q show dashplay` -> options of the main section, with defaults."""
    config = dict(DEFAULTS)
    for line in (uci_text or "").splitlines():
        match = re.match(r"^dashplay\.main\.(\w+)='?(.*?)'?$", line)
        if match:
            config[match.group(1)] = match.group(2)
    return config


def name_changes(uci_dhcp, name, host, public=None, only_public=False):
    """`uci -q show dhcp` -> the uci commands that make the router's DNS answer name -> host (its LAN address,
    record "dashplay") and name -> public (its own public IPv6 address, record "dashplay6", uci
    dashplay.main.public_ipv6). only_public leaves the LAN address out, and the name is then answered
    locally only: dnsmasq asks upstream for a type the hosts records lack, and a public record of the name
    may point at the LAN (F50, 2026-10-13). Without a public address the LAN address stays. [] when current."""
    kinds, options = {}, {}
    for line in (uci_dhcp or "").splitlines():
        match = re.match(r"^dhcp\.([^.=]+)=(\w+)$", line)
        if match:
            kinds[match.group(1)] = match.group(2)
        match = re.match(r"^dhcp\.([^.=]+)\.(\w+)=(.*)$", line)
        if match:
            options.setdefault(match.group(1), {})[match.group(2)] = re.findall(r"'([^']*)'", match.group(3)) or [match.group(3)]
    alone = bool(only_public and public)
    changes = []
    for section, ip in (("dashplay", None if alone else host), ("dashplay6", public)):
        have = options.get(section, {})
        if ip is None:
            if section in kinds:
                changes.append(["delete", f"dhcp.{section}"])
        elif kinds.get(section) != "domain" or have.get("name") != [name] or have.get("ip") != [ip]:
            changes += [["set", f"dhcp.{section}=domain"], ["set", f"dhcp.{section}.name={name}"],
                        ["set", f"dhcp.{section}.ip={ip}"]]
    dns = next((section for section, kind in kinds.items() if kind == "dnsmasq"), None)
    if dns:
        servers = options.get(dns, {}).get("server", [])
        ours = {f"/{old}/" for section in ("dashplay", "dashplay6") for old in options.get(section, {}).get("name", [])}
        local = f"/{name}/" if alone else None
        for entry in sorted((ours | {f"/{name}/"}) - {local}):
            if entry in servers:
                changes.append(["del_list", f"dhcp.{dns}.server={entry}"])
        if local and local not in servers:
            changes.append(["add_list", f"dhcp.{dns}.server={local}"])
    return changes + [["commit", "dhcp"]] if changes else []


def point_name(name, host, public=None, only_public=False):
    """Keep our uci dhcp records answering name -> the router (name_changes), so the head unit can open
    https://dashplay.lan:18443/ whatever the LAN address. Returns True when they changed."""
    shown = subprocess.run(["uci", "-q", "show", "dhcp"], capture_output=True, text=True).stdout
    changes = name_changes(shown, name, host, public, only_public)
    if not changes:
        return False
    for change in changes:
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


def own_config(config):
    """The DuckDNS sub-domain and token from uci, or None when off or invalid (then said once, never fatal)."""
    if not config["duckdns_domain"]:
        return None
    try:
        return {"domain": own_domain.sub_domain(config["duckdns_domain"]),
                "token": own_domain.check_token(config["duckdns_token"])}
    except ValueError:
        print("dashplay.main.duckdns_domain/duckdns_token invalid: own name off", flush=True)
        return None


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
    ddns = own_config(config)
    if ddns:
        config["name"] = own_domain.name_of(ddns)
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
    status = {"mode": "openwrt", "entries": [], "identity": have_identity, "home": f"http://{config['name']}:{port}/",
              "own_name": config["name"] if ddns else None}

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

    trusted = []   # the network the settings page serves, once the Wi-Fi is known (it starts after that)
    settings = SettingsPage(state, lambda: status, request_reexec, lambda: list(trusted), None, WIRED_STATUS,
                            managed={"by": "OpenWrt", "admin": "/cgi-bin/luci/admin/network/wireless"})
    maintainer = []   # own_domain.Maintainer, when a DuckDNS name is set

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
            "--java", config["java"], "--settings-port", str(port), "--wired", "--wired-status", str(WIRED_STATUS),
            "--idle-release", str(int(config["idle_release"]) if config["idle_release"].isdigit() else 300)]
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
        public_mode = config["public_ipv6"] if config["public_ipv6"] in ("1", "2") else "0"

        def point(public):
            if point_name(config["name"], host, public, public_mode == "2"):
                say(f"{config['name']} now points at "
                    + " and ".join(a for a in ([] if public and public_mode == "2" else [host]) + [public] if a))

        public = platform.public_ipv6(network["device"]) if named and public_mode != "0" else None
        if named:
            point(public)
        renewed = ensure_leaf(state, [config["name"], setup_wizard.MDNS_NAME], [host])
        # The DuckDNS name also works on a client network (the head unit asks the public record), once its
        # certificate is in use.
        status.update(ssid=network["ssid"], home=f"http://{host}:{port}/",
                      entries=([f"https://{config['name']}:18443/"] if named or (ddns and own_domain.serving(state)) else [])
                      + [f"https://{host}:18443/"])
        if ddns:
            maintainer.append(own_domain.Maintainer(root, ddns, host, stopped, say))
            maintainer[0].start()
        if network["mode"] == "sta" and platform.zone_input(network["network"]) not in ("ACCEPT", None):
            # A client network usually sits in the wan zone, which rejects the iPhone's AirPlay connections.
            status["warning"] = (f"「{network['ssid']}」所在的防火墙区域拒绝入站连接，iPhone 连不上 DashPlay。"
                                 f"请在 OpenWrt 后台把网络 {network['network']} 放进允许入站的区域，或改用路由器自己的 WiFi。")
            say(f"warning: firewall zone of {network['network']} rejects input")
        trusted[:] = trusted_networks(network)
        settings.start(port)
        diagnostics.start(stopped, diagnostics.DiagLog(state / "diag"), RUN / "receiver.log", platform, status,
                          state=state)

        bluetooth_thread.join()
        wireless = bool(bluetooth and bluetooth[0])
        # setup.json carries the way of use the settings page shows: wireless (handoff) or cable only.
        setup_wizard.save(state, {"done": True, "mode": "box_hotspot" if wireless else "car_hotspot"})
        if have_identity:
            # A renewed certificate (or a new CA) needs a new receiver: it reads the keystore once (audit A6).
            if early and plan_of(early) == plan_of(network) and wireless == has_bluez and not renewed:
                mark("early receiver kept")
            else:
                if early:
                    platform.stop_child("receiver")
                    mark("early receiver stopped: the live Wi-Fi, Bluetooth or certificate differs")
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
                elif named and public_mode != "0":
                    # The mobile network may hand out a new prefix when it reconnects.
                    latest = platform.public_ipv6(network["device"])
                    if latest != public:
                        public = latest
                        point(public)
    except InterruptedError:
        pass
    finally:
        stopped.set()
        for worker in maintainer:
            worker.join(10)   # acme.sh is stopped first: no signer left running into the restarted service
        settings.stop()
        platform.stop_all()
        say("DashPlay services stopped")
    if reexec:
        say(f"restarting: {reexec[0]}")
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), "--root", str(root)])


if __name__ == "__main__":
    main()
