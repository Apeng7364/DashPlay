#!/usr/bin/env python3
"""Run the DashPlay receiver on a OneCloud box as one systemd service (Armbian/Debian).

Three ways of use, chosen in first-run setup (setup_wizard.py, docs/三种使用方式实施计划.md):
- car_hotspot: join the car hotspot (state/car-hotspot.json; STA), which the head unit's browser uses
  to reach the viewer; no box hotspot; wired CarPlay only (the car hotspot relays iPhone video far
  too slowly, 0.12 Mbit/s on an Xpeng 2026-10-12).
- mifi: join the portable Wi-Fi the same way; wired CarPlay when the iPhone is plugged in, otherwise
  the iPhone is handed the same Wi-Fi over Bluetooth (wireless).
- box_hotspot: raise the box hotspot (AP); the head unit and the iPhone join it; wired or wireless.
In the two joining modes the box hotspot is only the fallback: when the hotspot is not found within
CAR_WAIT seconds, it opens so that a phone can reach the settings page, keeps scanning while idle and
switches back once the hotspot appears. The settings page (port 80, settings_page.py) is served in
every mode.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import threading
import time

from boxnet import ensure_leaf
from mdns_name import NameResponder
from settings_page import SettingsPage
import setup_wizard
from platform_api import get_platform
import diagnostics

RUN = Path("/run/dashplay")
CAR_WAIT = 60          # head units often raise their hotspot after the box has booted
LINK_LOSS_GRACE = 30   # seconds without the car hotspot before falling back
RESCAN_EVERY = 60      # box-hotspot mode: look for the car hotspot when no station is attached
BIND_WAIT = 120        # an early receiver waits this long for its address (CAR_WAIT + DHCP)
WIRED_STATUS = RUN / "wired-status.json"   # written by the receiver, shown on the settings page


def channel_of(freq_mhz):
    freq = int(freq_mhz)
    return (freq - 2407) // 5 if freq < 3000 else (freq - 5000) // 5


def hotspot_attempts(channel, width):
    """Box hotspot (channel, width) to try in order. 5 GHz first narrows to 40 MHz on the same channel:
    the N1's BCM43455 firmware refuses 80 MHz APs (2026-10-12). Then 2.4 GHz channel 6, always allowed:
    after the radio was a client earlier in this boot, rtw88 kept every 5 GHz channel no-IR and a CN
    regulatory reset did not lift it (2026-10-11)."""
    attempts = [(channel, width)]
    if channel > 14 and width > 40 and channel != 165:
        attempts.append((channel, 40))
    if channel > 14:
        attempts.append((6, 20))
    return attempts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    config = json.loads((root / "service.json").read_text())
    hotspot = json.loads((root / "state/hotspot.json").read_text())
    # "auto" (the firmware default): the dongle's name depends on its MAC, so find it at start.
    interface, channel, width = config.get("interface") or "auto", int(config["channel"]), int(config.get("width", 80))
    address = ipaddress.IPv4Interface(config["address"])
    # Legacy single-phone installs name one phone; first-run setup adds phones in the settings page.
    phone = config.get("phone", "")
    if phone and not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", phone):
        raise ValueError("Invalid paired phone address")
    phone_args = ["--phone", phone] if phone else []
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", interface) or width not in (20, 40, 80):
        raise ValueError("Invalid interface or channel width")
    # The firmware ships the hotspot name only; the person sets the password in first-run setup.
    if not 1 <= len(hotspot["ssid"].encode()) <= 32 or (
            "passphrase" in hotspot and not 8 <= len(hotspot["passphrase"]) <= 63):
        raise ValueError("Invalid hotspot credentials")
    for relative in ["run-receiver.py", "phone-presence.py", "pair-phone.py", "pairing_window.py", "setup_wizard.py",
                     "settings_ui.py", "mdns_name.py", "status_light.py", "platform_api.py", "platform_debian.py", "diagnostics.py", "iphone-usb-bridge.py", "linux-bt-bridge/bt-bridge"]:
        if not (root / relative).is_file():
            raise RuntimeError("Missing runtime input: " + relative)
    identity = [root / "identity/offline-mfi/identity.pk8", root / "identity/offline-mfi/certificate.p7b"]

    def identity_ok():
        """The MFi identity is supplied by the person (or their AI assistant) after flashing."""
        return all(path.is_file() and path.stat().st_size > 0 for path in identity)

    if args.check:
        print("Service configuration and runtime files checked; no services changed")
        return

    os.umask(0o077)
    RUN.mkdir(mode=0o700, exist_ok=True)
    state = root / "state"
    if interface == "auto":
        for _ in range(60):   # the unit may start before udev has named the dongle
            # The kernel's "wlan0" is renamed to wlx<MAC> moments later; taking the old name made the
            # service wait 30 s for an interface that no longer existed and fail once (2026-10-12).
            subprocess.run(["udevadm", "settle", "--timeout=10"], capture_output=True)
            interface = setup_wizard.wifi_interface() or "auto"
            if interface != "auto" and Path("/sys/class/net", interface).exists():
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("No Wi-Fi interface found")
    platform = get_platform(interface, run=RUN)
    setup = setup_wizard.load(state)
    have_identity = identity_ok()
    # Until the first-run network step is done the box hotspot is open, so a new phone joins it
    # without knowing any password; the receiver then hands the iPhone an open network too.
    open_ap = setup_wizard.open_hotspot(setup)
    if not open_ap and not hotspot.get("passphrase"):
        # Set up without choosing a box-hotspot password (older flow): never fall back to an open
        # network; make one up and show it in the settings page.
        hotspot["passphrase"] = secrets.token_urlsafe(9)
        setup_wizard.save_json(state / "hotspot.json", hotspot)
    ap_passphrase = None if open_ap else hotspot["passphrase"]
    ap_security = ["--security", "open"] if open_ap else []
    platform.configure_hotspot(channel, width, hotspot["ssid"], ap_passphrase)
    stopped = threading.Event()
    reexec = []   # reason, when the configuration changed and the service should restart itself
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    children = platform.children
    status = {"mode": "starting", "entries": [], "box_ssid": hotspot["ssid"], "home": f"http://{address.ip}/",
              "identity": have_identity}

    def load_json(name):
        try:
            return json.loads((state / name).read_text())
        except (OSError, ValueError):
            return None

    def wait_for(predicate, seconds, interval=0.2):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if stopped.is_set():
                raise InterruptedError("Service stopping")
            if predicate():
                return
            stopped.wait(interval)
        raise TimeoutError("Service dependency did not become ready")

    def start(name, argv):
        if name == "receiver" and not have_identity:
            return None   # settings page and network only, until the identity files are added
        if name == "receiver":
            # Each receiver started for a (re)built network may invite the last phone once more
            # (phone-presence.py boot_key): the iPhone left the box hotspot while it was rebuilt.
            os.environ["CARPLAY_NETWORK_EPOCH"] = secrets.token_hex(4)
        return platform.start(name, argv)

    stop_child = platform.stop_child

    def mark(step):
        # Seconds since kernel start (CLOCK_MONOTONIC), comparable with systemd-analyze.
        print(f"boot {time.monotonic():.2f}s {step}", flush=True)

    def request_reexec(reason):
        reexec.append(reason)
        stopped.set()

    def join_car_hotspot(car):
        """Keep setup/lease decisions independent of the platform's association mechanism."""
        if not platform.join_wifi(car, load_json("car-lease.json") or {}, wait_for, CAR_WAIT):
            setup = setup_wizard.load(state)
            if not setup.get("done") and setup.get("car") == car["ssid"]:
                log = platform.join_log()
                setup_wizard.save(state, dict(setup, failed=setup_wizard.join_failure(log)))
            return None
        freq = platform.frequency()
        address_now = platform.ipv4_address()
        found = platform.security(car["ssid"])
        lease_text = json.dumps({"ssid": car["ssid"], "address": address_now,
                                 "freq": freq, "security": found})
        temporary = state / "car-lease.json.tmp"   # replaced whole: a power cut must not leave it empty
        temporary.write_text(lease_text)
        temporary.replace(state / "car-lease.json")
        return address_now, channel_of(freq) if freq else 0, setup_wizard.handoff_security(found)

    bluetooth_ok = []

    def power_bluetooth():
        try:
            # Early in boot the adapter is listed before it accepts "power on" (2026-10-11: refused once).
            wait_for(platform.power_bluetooth, 40, interval=0.5)
            bluetooth_ok.append(True)
            mark("bluetooth powered")
        except (TimeoutError, InterruptedError, subprocess.SubprocessError) as error:
            print(f"bluetooth power-on failed: {type(error).__name__}", flush=True)

    light = platform.status_light()
    scanner = setup_wizard.Scanner(interface, hotspot["ssid"], lambda: status["mode"], platform=platform)
    settings = SettingsPage(state, lambda: status, request_reexec, lambda: [ip.network for ip in platform.local_addresses()],
                            scanner, WIRED_STATUS)
    try:
        mark("supervisor start")
        platform.prepare(wait_for)
        light.show("starting")
        settings.start()
        # Kept on the eMMC so that a drive can be analysed after the car is switched off; started
        # before joining the car hotspot so that a failing join is recorded too.
        diagnostics.start(stopped, diagnostics.DiagLog(state / "diag"), RUN / "receiver.log", platform, status,
                          state=state)
        diagnostics.LogMirror(state / "logs", RUN, platform.boot_id()).start(stopped)
        NameResponder(setup_wizard.MDNS_NAME, stopped).start()
        # Power Bluetooth now, not after the network: an early receiver reads the adapter address while
        # it starts and stalled ~15 s waiting for it (2026-10-11).
        bluetooth = threading.Thread(target=power_bluetooth, name="bluetooth-power", daemon=True)
        bluetooth.start()
        names = [setup_wizard.MDNS_NAME]
        use = setup.get("mode")   # None until chosen in first-run setup
        status["use"] = use
        # Debugging at home (e.g. wired CarPlay): DASHPLAY_SKIP_CAR=1 skips the minute spent looking for
        # a car hotspot that is not there; the saved one stays.
        car = (load_json("car-hotspot.json") if use in setup_wizard.JOINS and os.environ.get("DASHPLAY_SKIP_CAR") != "1"
               else None)
        WIRED_STATUS.unlink(missing_ok=True)
        # The cable is always offered; wireless (Bluetooth handoff) except in car_hotspot mode.
        usb_args = ["--wired", "--wired-status", str(WIRED_STATUS)] + (
            [] if setup_wizard.wireless(setup) else ["--no-wireless"])

        def receiver_argv(plan, wait=False):
            """plan: (bind address, channel, security) of the joined hotspot, which the iPhone is handed.
            The viewer listens on every address: the head unit comes through that hotspot."""
            bind, plan_channel, security = plan
            argv = ["python3", str(root / "run-receiver.py"), "--root", str(root), "--bind", bind, *phone_args,
                    "--channel", str(plan_channel), "--security", security, "--web-host", "0.0.0.0",
                    "--interface", interface, "--hotspot", str(state / "car-hotspot.json"), *usb_args]
            return argv + (["--bind-wait", str(BIND_WAIT)] if wait else [])

        # The JVM needs ~14 s on this CPU before its first invitation; joining the car hotspot takes
        # ~11 s. Start it at once on last time's lease and let it wait for its address; a different
        # outcome restarts it (2026-10-11 boot profile).
        lease = load_json("car-lease.json") or {}
        predicted = None
        if have_identity and car and lease.get("ssid") == car["ssid"] and lease.get("address") and lease.get("freq"):
            predicted = (lease["address"], channel_of(lease["freq"]), setup_wizard.handoff_security(lease.get("security")))
            platform.allow_nonlocal_bind()
            start("receiver", receiver_argv(predicted, wait=True))
            mark(f"receiver launched early for {predicted[0]}")
        joined = join_car_hotspot(car) if car else None
        # Without wireless CarPlay (car_hotspot) the channel and security only matter for a Wi-Fi handoff
        # the receiver never makes: keep the early receiver while the address is the same. The Xpeng
        # car hotspot came up on 149 one drive and on 36 the next; restarting cost ~20 s (2026-10-12).
        keep_early = bool(predicted and joined and (joined == predicted or (
            not setup_wizard.wireless(setup) and joined[0] == predicted[0])))
        if predicted and not keep_early:
            stop_child("receiver")
            mark("early receiver stopped: " + ("network differs from last time" if joined else "hotspot not joined"))
        if joined:
            sta_address, sta_channel, _ = joined
            setup = setup_wizard.load(state)
            if setup.get("failed"):
                setup_wizard.save(state, {key: value for key, value in setup.items() if key != "failed"})
            if not keep_early:
                start("receiver", receiver_argv(joined))
            # mifi: the iPhone's video comes through the portable Wi-Fi; acknowledge at once (relay lag).
            platform.quick_ack()
            status.update(mode="sta", ssid=car["ssid"], home=f"http://{sta_address}/", channel=sta_channel, entries=[
                f"https://{sta_address}:18443/", f"http://{sta_address}/ （本设置页）"])
            ensure_leaf(state, names, [sta_address, str(address.ip)])
            mark(f"{use} hotspot joined, viewer on {sta_address}")
        else:
            status.update(mode="ap", ssid=hotspot["ssid"], open=open_ap, entries=[
                f"https://{address.ip}:18443/", f"http://{address.ip}/ （本设置页）"])
            ensure_leaf(state, names, [str(address.ip)])
            platform.set_ap_address(address)
            # The JVM needs ~6 s on this CPU (with AppCDS) before it first contacts the iPhone, while the
            # AP is up in ~4 s, so start it first (2026-10-10 boot profiling). It only needs the address
            # bound; bt-bridge waits for a powered adapter itself (BT_BRIDGE_READY_WAIT).
            def launch_receiver(ap_channel):
                start("receiver", ["python3", str(root / "run-receiver.py"), "--root", str(root),
                                   "--bind", str(address.ip), *phone_args,
                                   "--interface", interface, "--channel", str(ap_channel), *ap_security, *usb_args])

            launch_receiver(channel)
            mark("receiver launched")
            used = None
            for ap_channel, ap_mhz in hotspot_attempts(channel, width):
                if used:
                    print(f"box hotspot did not start on channel {used[0]} at {used[1]} MHz; "
                          f"trying channel {ap_channel} at {ap_mhz} MHz", flush=True)
                    stop_child("hostapd")
                    platform.configure_hotspot(ap_channel, ap_mhz, hotspot["ssid"], ap_passphrase)
                    if ap_channel != used[0]:
                        # The receiver hands the iPhone the channel, so it restarts with the one used.
                        stop_child("receiver")
                        launch_receiver(ap_channel)
                used = (ap_channel, ap_mhz)
                if platform.start_ap(wait_for):
                    break
            else:
                raise RuntimeError("box hotspot did not start")
            mark("AP enabled")
            # DHCP only: no router or DNS option. (A head unit joining it may still treat it as its
            # default network and lose internet: box_hotspot mode says so; the other modes avoid it.)
            portal = setup_wizard.captive(setup, "ap")
            platform.start_dhcp(address, portal)
            if portal:
                mark("first-run setup portal enabled")
        bluetooth.join()
        if not bluetooth_ok:
            # Only wireless CarPlay needs Bluetooth (the invitation); car_hotspot mode runs on the cable.
            if setup_wizard.wireless(setup):
                raise RuntimeError("Bluetooth adapter did not power on")
            print("Bluetooth adapter did not power on; not needed without wireless CarPlay", flush=True)
        print(f"DashPlay services started ({status['mode']})", flush=True)
        lost_since = None
        next_scan = time.monotonic() + RESCAN_EVERY
        scan_supported = True
        def phone_live():
            presence = load_json("receiver/phone-presence.json") or {}
            try:
                wired_live = json.loads(WIRED_STATUS.read_text()).get("phase") == "live"
            except (OSError, ValueError, AttributeError):
                wired_live = False
            return bool(presence.get("active")) or wired_live

        # Joining box on its own hotspot = the hotspot was not found (first-run setup excluded).
        fallback = status["mode"] == "ap" and setup.get("done") and bool(car)
        if not have_identity:
            print("MFi identity missing: settings page only, CarPlay starts once the files are added", flush=True)
        while not stopped.wait(2):
            if not have_identity and identity_ok():
                request_reexec("MFi identity added")
                continue
            light.show("live" if phone_live() else "fallback" if fallback else "waiting")
            for name, child in children:
                if child.poll() is not None:
                    raise RuntimeError(f"{name} exited with {child.returncode}")
            if status["mode"] == "sta":
                if platform.connected():
                    lost_since = None
                elif lost_since is None:
                    lost_since = time.monotonic()
                elif time.monotonic() - lost_since > LINK_LOSS_GRACE:
                    request_reexec("car hotspot lost")
            elif (car and scan_supported and time.monotonic() >= next_scan
                  and setup_wizard.load(state).get("done")):
                # During first-run setup the box hotspot stays up so the phone can fix the password.
                next_scan = time.monotonic() + RESCAN_EVERY
                # rtw88 keeps the AP up while scanning (2026-10-10): a full scan is 2.7 s off-channel,
                # one frequency 0.22 s. With a client attached (usually the iPhone, handed this AP
                # after the fallback) only the car hotspot's last frequency is probed.
                lease = load_json("car-lease.json") or {}
                frequency = None
                if platform.has_stations():
                    frequency = lease.get("freq")
                    if not lease.get("freq"):
                        continue
                supported, found, error = platform.find_hotspot(car["ssid"], frequency=frequency)
                if not supported:
                    scan_supported = False
                    print(f"AP-mode scan unsupported: {error.strip()[:120]}", flush=True)
                elif found:
                    request_reexec("car hotspot seen")
    except InterruptedError:
        pass
    finally:
        stopped.set()
        settings.stop()
        platform.stop_all()
        platform.remove_sta_network()
        platform.flush_address()
        print("DashPlay services stopped", flush=True)
    if reexec:
        print(f"restarting: {reexec[0]}", flush=True)
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), "--root", str(root)])

if __name__ == "__main__":
    main()
