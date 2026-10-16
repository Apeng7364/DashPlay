#!/usr/bin/env python3
"""Run the DashPlay receiver on a OneCloud box as one systemd service (Armbian/Debian).

Three ways of use, chosen in first-run setup (setup_wizard.py, docs/三种使用方式实施计划.md):
- car_hotspot: join the car hotspot (state/car-hotspot.json; STA), which the head unit's browser uses
  to reach the viewer; no box hotspot; wired CarPlay only (the car hotspot relays iPhone video far
  too slowly, 0.12 Mbit/s on an Xpeng 2026-10-12).
- mifi: join the portable Wi-Fi the same way; wired CarPlay when the iPhone is plugged in, otherwise
  the iPhone is handed the same Wi-Fi over Bluetooth (wireless).
- box_hotspot: raise the box hotspot (AP); the head unit and the iPhone join it; wired or wireless.
The opt-in direct_phone validation path instead hands iPhone the box AP alongside either joined
network; it keeps the viewer and domain on the upstream station address. By default,
in the two joining modes the box hotspot is only the fallback: when the hotspot is not found within
CAR_WAIT seconds, it opens so that a phone can reach the settings page, keeps scanning while idle and
switches back once the hotspot appears. The settings page (port 80, settings_page.py) is served in
every mode. Optional: a DuckDNS name with a Let's Encrypt certificate (state/ddns.json, own_domain.py)
for car browsers that refuse the box's local CA. Remote debugging (debug_uplink.py): for a window opened on the
settings page, any mode joins the owner's phone hotspot instead, wired CarPlay only, until the window ends or a reboot.
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
import debug_uplink
from phone_uplink import PhoneUplink, JoinedPhoneAP
from mdns_name import NameResponder
import own_domain
from settings_page import SettingsPage, network_label
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


options = setup_wizard.options


def idle_release(state, default=300):
    """The owner's choice in state/options.json, or 5 minutes."""
    try:
        return max(0, int(options(state).get("idle_release", default)))
    except (ValueError, TypeError):
        return default


def tesla_mode(state):
    """Settings page, state/options.json "tesla": the box hotspot in SHARED_LAN, music, calls and Siri over the
    car's Bluetooth, the Tesla browser's fullscreen."""
    return options(state).get("tesla") is True


def handed_hotspot(state, debug, direct=False):
    """The Wi-Fi file the receiver started on a joined hotspot reads (run-receiver.py --hotspot): the car hotspot
    handed to the iPhone. Remote debugging hands nothing (--no-wireless) and may start in box-hotspot mode, which
    has no car-hotspot.json: there the receiver exited at once and the service restarted every 25 s (2026-10-15)."""
    return state / ("hotspot.json" if debug or direct else "car-hotspot.json")


def joined_receiver_network(state, plan, interface, debug=False, direct_address=None, ap_interface='ap0'):
    """AirPlay follows the iPhone; the viewer stays reachable from the upstream car side."""
    bind, channel, security = plan
    direct = direct_address is not None
    return ['--bind', str(direct_address) if direct else bind, '--channel', str(channel),
            '--security', 'wpa2' if direct else security, '--web-host', '0.0.0.0',
            '--interface', ap_interface if direct else interface,
            '--hotspot', str(handed_hotspot(state, debug, direct))]


def hotspot_names(state, own_name, use):
    """Names the box answers on its own hotspot (settings page, state/options.json "local_dns"): the own domain in
    box-hotspot mode, so that a car whose browser gets no other DNS there (no internet, or it does not fall back to
    its LTE, as the Xpeng did 2026-10-12) still opens it with the trusted certificate."""
    chosen = options(state)
    if use != "box_hotspot":
        return []
    checks = list(setup_wizard.TESLA_CHECK_NAMES) if chosen.get("tesla") else []
    return checks + ([own_name] if own_name and (chosen.get("tesla") or chosen.get("local_dns")) else [])


def hotspot_router(state, use):
    """Whether the box hotspot hands out the box as default gateway: only while answering a Tesla's online checks.
    Two N1 users' Teslas asked for a router in DHCP and, without one, left the hotspot ~30 s after every join
    (2026-10-16); with the box as router they stayed. Other cars keep no router, or a head unit could make the
    box its default network and lose its own LTE."""
    return use == "box_hotspot" and tesla_mode(state)


def car_audio_args(state):
    """run-receiver.py options for what goes over the car's own Bluetooth (settings page, state/options.json
    "car_music" / "car_voice"; found in a Tesla, 2026-10-14); Tesla mode is all of them."""
    chosen = options(state)
    tesla = tesla_mode(state)
    if tesla and all(chosen.get(key, True) is True for key in ("car_music", "car_voice")):
        return ["--tesla"]
    return (["--tesla-browser"] if tesla else []) + [flag for key, flag in (
        ("car_music", "--car-music"), ("car_voice", "--car-voice")) if chosen.get(key, tesla) is True]


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
                     "settings_ui.py", "mdns_name.py", "debug_uplink.py", "status_light.py", "platform_api.py", "platform_debian.py", "diagnostics.py", "iphone-usb-bridge.py", "linux-bt-bridge/bt-bridge"]:
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
    platform.bluetooth_state = state
    platform.cleanup_phone_uplink()  # restore MAC/NAT after an interrupted previous run
    setup = setup_wizard.load(state)
    # Tesla mode: the box hotspot moves to setup_wizard.SHARED_LAN, which a Tesla's browser opens (2026-10-14).
    tesla = tesla_mode(state)
    address, tesla_warning = setup_wizard.box_hotspot_address(address, tesla, platform.local_addresses())
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
        # A stop wins over a restart asked for meanwhile (a LAN change under way when procd stops us, 2026-10-14).
        signal.signal(sig, lambda *_: (reexec.clear(), reexec.append(None), stopped.set()))
    children = platform.children
    status = {"mode": "starting", "entries": [], "box_ssid": hotspot["ssid"], "home": f"http://{address.ip}/",
              "identity": have_identity, "ap_ip": str(address.ip), "tesla": tesla}

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

    def join_car_hotspot(car, remember=True):
        """Keep setup/lease decisions independent of the platform's association mechanism. remember: keep the
        lease for the next start's early receiver (not for the phone hotspot of remote debugging)."""
        if not platform.join_wifi(car, (load_json("car-lease.json") or {}) if remember else {}, wait_for, CAR_WAIT):
            setup = setup_wizard.load(state)
            if not setup.get("done") and setup.get("car") == car["ssid"]:
                log = platform.join_log()
                setup_wizard.save(state, dict(setup, failed=setup_wizard.join_failure(log)))
            return None
        freq = platform.frequency()
        address_now = platform.ipv4_address()
        found = platform.security(car["ssid"])
        if not remember:
            return address_now, channel_of(freq) if freq else 0, setup_wizard.handoff_security(found)
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
                            scanner, WIRED_STATUS, run=RUN)
    uplink = None
    direct_ap = None
    maintainer = []   # own_domain.Maintainer, when a DuckDNS name is set
    try:
        mark("supervisor start")
        platform.prepare(wait_for)
        platform.ensure_receiver_ipv6()
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
        ddns = own_domain.load(state)
        own_name = own_domain.name_of(ddns)
        status["own_name"] = own_name
        names = [setup_wizard.MDNS_NAME] + ([own_name] if own_name else [])

        def with_name(entries):
            # The name first once its certificate is in use: the head unit then opens it without a warning.
            return ([f"https://{own_name}:18443/"] if own_name and own_domain.serving(state) else []) + entries

        def keep_name(target, wifi=None):
            """DuckDNS record -> target (None: leave it) and the certificate, in the background. wifi: the joined
            Wi-Fi, whose public IPv6 address goes into the record too when the owner asked for it."""
            if ddns:
                maintainer.append(own_domain.Maintainer(root, ddns, target, stopped, interface=wifi, tesla=tesla))
                maintainer[0].start()
        if not (ddns and ddns.get("ipv6")):
            own_domain.unguard()   # the public IPv6 option was turned off: its firewall goes with it
        use = setup.get("mode")   # None until chosen in first-run setup
        status["use"] = use
        # Debugging at home (e.g. wired CarPlay): DASHPLAY_SKIP_CAR=1 skips the minute spent looking for
        # a car hotspot that is not there; the saved one stays.
        car = (load_json("car-hotspot.json") if use in setup_wizard.JOINS and os.environ.get("DASHPLAY_SKIP_CAR") != "1"
               else None)
        # Remote debugging: the owner's phone hotspot replaces the network of every mode for the window.
        debug = debug_uplink.active(state, RUN)
        status["debug"] = bool(debug)
        if debug:
            car = debug
            print(f"remote debugging: joining the phone hotspot for {debug_uplink.remaining(RUN) / 60:.0f} min", flush=True)
        # Opt-in validation path until real car-hotspot tests pass (design 6).
        direct_requested = bool(car and not debug and options(state).get('direct_phone') is True)
        direct_active = False
        WIRED_STATUS.unlink(missing_ok=True)
        # The cable is always offered; wireless (Bluetooth handoff) except in car_hotspot mode.
        # Remote debugging hands the iPhone no Wi-Fi: the phone hotspot is the owner's, not the car's.
        usb_args = ["--wired", "--wired-status", str(WIRED_STATUS)] + (
            [] if setup_wizard.wireless(setup, options(state)) and not debug else ["--no-wireless"]) + [
            # state/options.json "idle_release": seconds without a viewer before wireless CarPlay ends (0: never);
            # in state/, which program updates keep (they replace service.json).
            "--idle-release", str(idle_release(state)), *car_audio_args(state)]

        def receiver_argv(plan, wait=False):
            """plan: (bind address, channel, security) of the joined hotspot, which the iPhone is handed.
            The viewer listens on every address: the head unit comes through that hotspot."""
            argv = ["python3", str(root / "run-receiver.py"), "--root", str(root), *phone_args,
                    *joined_receiver_network(state, plan, interface, debug,
                                             address.ip if direct_active else None, platform.ap_interface),
                    *([a for a in usb_args if a != '--no-wireless'] if direct_active and setup.get('iphone') != 'wired' else usb_args)]
            return argv + (["--bind-wait", str(BIND_WAIT)] if wait else [])

        # The JVM needs ~14 s on this CPU before its first invitation; joining the car hotspot takes
        # ~11 s. Start it at once on last time's lease and let it wait for its address; a different
        # outcome restarts it (2026-10-11 boot profile).
        lease = load_json("car-lease.json") or {}
        predicted = None
        if have_identity and car and not debug and not direct_requested and lease.get("ssid") == car["ssid"] and lease.get("address") and lease.get("freq"):
            predicted = (lease["address"], channel_of(lease["freq"]), setup_wizard.handoff_security(lease.get("security")))
            platform.allow_nonlocal_bind()
            start("receiver", receiver_argv(predicted, wait=True))
            mark(f"receiver launched early for {predicted[0]}")
        if direct_requested:
            direct_ap = JoinedPhoneAP(platform, address, hotspot, wait_for)
            try:
                if not direct_ap.prepare():
                    status['direct_phone'] = '无线驱动不支持同时连接和开热点，保留原连接方式'
            except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
                direct_ap.close()
                status['direct_phone'] = '直连热点准备失败（' + type(error).__name__ + '），保留原连接方式'
        joined = join_car_hotspot(car, remember=not debug) if car else None
        if direct_ap and direct_ap.prepared:
            if joined:
                try:
                    direct_ap.start()
                    direct_active = True
                    status['phone_interface'] = platform.ap_interface
                    status['direct_phone'] = 'iPhone 直连盒子热点（待实车验证）；车机仍用上游地址'
                except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
                    direct_ap.close()
                    status['direct_phone'] = '直连热点失败（' + type(error).__name__ + '），保留原连接方式'
                    joined = join_car_hotspot(car)
            else:
                direct_ap.close()
        # Without wireless CarPlay (car_hotspot) the channel and security only matter for a Wi-Fi handoff
        # the receiver never makes: keep the early receiver while the address is the same. The Xpeng
        # car hotspot came up on 149 one drive and on 36 the next; restarting cost ~20 s (2026-10-12).
        keep_early = bool(predicted and joined and (joined == predicted or (
            not setup_wizard.wireless(setup, options(state)) and joined[0] == predicted[0])))
        if predicted and not keep_early:
            stop_child("receiver")
            mark("early receiver stopped: " + ("network differs from last time" if joined else "hotspot not joined"))
        if joined:
            sta_address, sta_channel, _ = joined
            setup = setup_wizard.load(state)
            if setup.get("failed"):
                setup_wizard.save(state, {key: value for key, value in setup.items() if key != "failed"})
            # The certificate before the receiver: the JVM reads the keystore once at start, and one started
            # first kept the old certificate without the new address (audit 2026-10-13, A6).
            renewed = ensure_leaf(state, names, [sta_address, str(address.ip)])
            if keep_early and renewed:
                # Same address, but renewed or signed by a new CA (rare): the early receiver has the old one.
                stop_child("receiver")
                keep_early = False
                mark("early receiver stopped: the web certificate was renewed")
            if not keep_early:
                start("receiver", receiver_argv(joined))
            # mifi: the iPhone's video comes through the portable Wi-Fi; acknowledge at once (relay lag).
            platform.quick_ack()
            status.update(mode="sta", ssid=car["ssid"], home=f"http://{sta_address}/", channel=sta_channel, entries=with_name([
                f"https://{sta_address}:18443/", f"http://{sta_address}/ （本设置页）"]))
            mark(f"{'debug' if debug else use} hotspot joined, viewer on {sta_address}")
            if tesla and not debug and not setup_wizard.tesla_reaches(sta_address):
                # A joined hotspot is numbered by its owner: only its own settings can move it.
                status["tesla_warning"] = (f"{network_label(use)}分给盒子的地址是 {sta_address}，特斯拉浏览器打不开这类局域网地址。"
                                           f"在{network_label(use)}自己的后台把局域网地址改成 {setup_wizard.SHARED_LAN.ip}"
                                           "（100.64–100.127 开头的网段都行），再重新连接。")
            # The DuckDNS record stays where the car finds the box: the phone hotspot is only for the owner.
            keep_name(None if debug else sta_address, None if debug else interface)
        else:
            ensure_leaf(state, names, [str(address.ip)])
            if tesla_warning:
                status["tesla_warning"] = tesla_warning
                print(f"Tesla mode: {tesla_warning}", flush=True)
            status.update(mode="ap", ssid=hotspot["ssid"], open=open_ap, entries=with_name([
                f"https://{address.ip}:18443/", f"http://{address.ip}/ （本设置页）"]))
            # Box hotspot: the head unit opens the box at its fixed address; online through the Ethernet cable
            # (at home) the box renews the certificate. The fallback hotspot of the joining modes leaves the record.
            keep_name(str(address.ip) if use == "box_hotspot" else None)
            platform.set_ap_address(address)
            # The JVM needs ~6 s on this CPU (with AppCDS) before it first contacts the iPhone, while the
            # AP is up in ~4 s, so start it first (2026-10-10 boot profiling). It only needs the address
            # bound; bt-bridge waits for a powered adapter itself (BT_BRIDGE_READY_WAIT).
            def launch_receiver(ap_channel):
                start("receiver", ["python3", str(root / "run-receiver.py"), "--root", str(root),
                                   "--bind", str(address.ip), *phone_args,
                                   "--interface", platform.ap_interface, "--channel", str(ap_channel), *ap_security, *usb_args])

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
            platform.start_dhcp(address, portal, hotspot_names(state, own_name, use),
                                query_log=state / "diag/tesla-dns.log" if tesla and use == "box_hotspot" else None,
                                router=hotspot_router(state, use))
            status["tesla_offline"] = setup_wizard.tesla_offline(setup, options(state))
            if portal:
                mark("first-run setup portal enabled")
        if use == "box_hotspot" and (setup.get("done") or setup.get("confirmed")) and not debug and options(state)["car_internet"] == "phone_hotspot":
            uplink = PhoneUplink(platform, state, address, hotspot, used[0], used[1], wait_for,
                                 launch_receiver, status)
        bluetooth.join()
        if not bluetooth_ok:
            # Only wireless CarPlay needs Bluetooth (the invitation); car_hotspot mode runs on the cable.
            if setup_wizard.wireless(setup, options(state)) or direct_active and setup.get('iphone') != 'wired':
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
            if debug and not debug_uplink.remaining(RUN):
                request_reexec("remote debugging ended")
                continue
            if not have_identity and identity_ok():
                request_reexec("MFi identity added")
                continue
            if uplink:
                uplink.tick()
            if direct_ap and direct_ap.changed():
                request_reexec('direct phone AP lost upstream/channel; rebuild')
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
        for worker in maintainer:
            worker.join(10)   # acme.sh is stopped first: no signer left running into the restarted service
        settings.stop()
        platform.stop_all()
        if uplink:
            uplink.close()
        if direct_ap:
            direct_ap.close()
        platform.remove_sta_network()
        platform.flush_address()
        print("DashPlay services stopped", flush=True)
    if reexec and reexec[0] is not None:
        print(f"restarting: {reexec[0]}", flush=True)
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), "--root", str(root)])

if __name__ == "__main__":
    main()
