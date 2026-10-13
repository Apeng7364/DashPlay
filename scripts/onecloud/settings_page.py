"""Plain-HTTP settings page on port 80: first-run setup, phones, hotspots, status, CA, own DuckDNS name.

Reachable in both modes (box hotspot: http://192.168.78.1/, car hotspot: http://<box address>/, and
http://dashplay.local/ in either). Only clients on the box's own LAN subnets may use it. Secrets are
written to 0600 files under state/ and never sent back to the browser; leaving a password field
empty keeps the stored value. Until setup is done, the page is the step-by-step setup flow.
On OpenWrt (managed=...) the router's admin owns the network: no setup flow, only iPhones and status.
Markup lives in settings_ui.py; this module holds the behaviour.
"""
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
import tempfile
from pathlib import Path
import secrets
import threading
import urllib.parse

import own_domain
from pairing_window import PairingWindow
import settings_ui as ui
import setup_wizard
import diagnostics

MAX_BODY = 4096
APPLY_DELAY = 2.0   # let the browser receive the reply before the service restarts its network


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


_SAVING = threading.Lock()


def _save(path: Path, value):
    # Requests run in threads: one save at a time, each with its own temporary file (a double-click on save
    # wrote both into one .tmp and failed the second rename; review 2026-10-12).
    with _SAVING:
        fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump(value, out)
            out.flush()
            os.fsync(out.fileno())
        Path(name).replace(path)
        os.sync()   # the root fs commits only every 120 s; saved settings must survive switching the car off


def _hotspot_credentials(form, old):
    ssid = (form.get("ssid") or "").strip()
    if not 1 <= len(ssid.encode()) <= 32 or any(ord(c) < 32 or ord(c) == 127 for c in ssid):
        raise ValueError("热点名称为 1–32 字节，不能含换行等控制字符。")
    password = form.get("password", "")
    if not password:
        password = old.get("passphrase", "")
    # WPA: 8–63 printable ASCII characters. A newline also became its own hostapd line (review 2026-10-12).
    if not (8 <= len(password) <= 63 and all(32 <= ord(c) <= 126 for c in password)):
        raise ValueError("热点密码为 8–63 个英文字母、数字或符号（可含空格）。")
    return ssid, password


def _car_credentials(form, old):
    ssid = (form.get("ssid") or "").strip()
    if not ssid or ssid == "__manual__":
        ssid = form.get("ssid_manual", "").strip()
    if not 1 <= len(ssid.encode()) <= 32:
        raise ValueError("请选择热点，或输入 1–32 字节的热点名称。")
    password = form.get("password", "")
    if not password and old.get("ssid") == ssid:
        password = old.get("passphrase", "")
    if not 8 <= len(password) <= 63:
        raise ValueError("热点密码为 8–63 个字符。")
    return ssid, password


# First-run step 1 (user decision 2026-10-12): (key, icon, title, text, note).
MODES = [
    ("car_hotspot", "car", "方式一：车机热点", "盒子连车机热点，车机照常上网。iPhone 用数据线插盒子。",
     "不能用无线 CarPlay：车机热点在设备之间转发很慢（实测 0.12 Mbit/s），画面会卡。"),
    ("mifi", "hotspot", "方式二：随身 WiFi", "盒子、车机和 iPhone 都连随身 WiFi，车机经它上网。插了数据线用有线 CarPlay，没插用无线。",
     "有的随身 WiFi 不让连上的设备互相访问，那时无线会卡或连不上，请用数据线。"),
    ("box_hotspot", "box", "方式三：车机连盒子热点", "盒子自己开热点，车机和 iPhone 都连它。插了数据线用有线 CarPlay，没插用无线。",
     "车机连上盒子热点后不能上网。"),
]
MODE_TITLES = {key: title for key, _, title, _, _ in MODES}


def network_label(mode):
    """What the box joins in this way of use."""
    return "随身 WiFi" if mode == "mifi" else "车机热点"


def read_wired(path):
    """Phase of wired CarPlay written by the receiver (WiredBootstrap): cable/trust/starting/live, or None."""
    value = _load(path) if path else None
    return value.get("phase") if isinstance(value, dict) else None


class SettingsPage:
    def __init__(self, state: Path, status, on_change, allowed_networks, scanner=None, wired_status=None,
                 managed=None):
        """status() -> dict for display; on_change(reason) re-applies the configuration;
        wired_status: the receiver's wired CarPlay status file; managed: {"by": "OpenWrt", "admin": URL of
        its Wi-Fi settings} when the system, not this page, sets up the network."""
        self.managed = managed
        self.state = state
        self.pairing = PairingWindow(state.parent)
        self.car_file = state / "car-hotspot.json"
        self.wired_status = wired_status
        self.hotspot_file = state / "hotspot.json"
        self.ddns_file = state / "ddns.json"
        self.ca_file = state / "tls" / "ca.crt"
        self.status, self.on_change, self.allowed = status, on_change, allowed_networks
        self.scanner = scanner
        self.server = None

    def setup(self):
        return setup_wizard.load(self.state)

    def apply_later(self, reason):
        threading.Timer(APPLY_DELAY, self.on_change, args=(reason,)).start()

    def start(self, port=80):
        page = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):   # keep tokens/passwords out of the journal
                pass

            def _allowed(self):
                client = ipaddress.ip_address(self.client_address[0].removeprefix("::ffff:"))
                return client.is_loopback or any(client in net for net in page.allowed())

            def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
                data = body if isinstance(body, bytes) else body.encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if not self._allowed():
                    return self._send(403, "forbidden")
                target = page.captive_redirect(self.headers.get("Host", ""), self.path)
                if target:
                    return self._send(302, "", extra={"Location": target})
                if self.path == "/ca.crt" and page.ca_file.exists():
                    return self._send(200, page.ca_file.read_bytes(), "application/x-x509-ca-cert",
                                      {"Content-Disposition": 'attachment; filename="dashplay-ca.crt"'})
                if self.path == "/diag.txt":
                    return self._send(200, diagnostics.DiagLog(page.state / "diag").text(), "text/plain; charset=utf-8",
                                      {"Content-Disposition": 'attachment; filename="dashplay-diag.txt"'})
                if self.path.split("?")[0] != "/":
                    return self._send(404, "not found")
                self._send(200, page.render(self.path.partition("?")[2]))

            def do_POST(self):
                if not self._allowed():
                    return self._send(403, "forbidden")
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    return self._send(413, "too large")
                form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(length).decode(),
                                                                 keep_blank_values=True).items()}
                back = {"/car": "/?view=car&", "/hotspot": "/?view=hotspot&"}.get(self.path, "/?")
                try:
                    result = page.apply(self.path, form)
                except ValueError as error:
                    result = "错误：" + str(error)
                if isinstance(result, tuple) and result[0] == "go":
                    return self._send(303, "", extra={"Location": result[1]})
                if isinstance(result, tuple):   # a full page: the network is about to change
                    return self._send(200, result[1])
                self._send(303, "", extra={"Location": back + urllib.parse.urlencode({"m": result})})

        self.server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        threading.Thread(target=self.server.serve_forever, name="settings-page", daemon=True).start()

    def stop(self):
        self.pairing.stop()
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def home(self):
        return self.status().get("home", "/")

    def captive_redirect(self, host, path):
        """While captive, any other host or path (OS connectivity probes) is sent to the setup page."""
        if not setup_wizard.captive(self.setup(), self.status().get("mode")):
            return None
        own = urllib.parse.urlsplit(self.home()).hostname
        name = host.split(":")[0].lower()
        if name in (own, setup_wizard.MDNS_NAME) and path.split("?")[0] in ("/", "/ca.crt"):
            return None
        return self.home()

    def check_nonce(self, form):
        if not secrets.compare_digest(form.get("nonce", ""), self.pairing.nonce):
            raise ValueError("页面已过期，请刷新后重试。")

    # Behaviour -------------------------------------------------------------------------------

    def apply(self, path, form):
        if self.managed and path != "/pair":
            raise ValueError(f"网络在 {self.managed['by']} 后台设置。")
        if path == "/pair":
            self.check_nonce(form)
            action = form.get("action")
            if action == "start":
                self.pairing.start()
                return ""
            if action == "cancel":
                self.pairing.stop()
                return "已取消添加。"
            if action == "pick":
                self.pairing.pick(form.get("phone", ""))
                return ""
            if action == "remove":
                self.pairing.remove(form.get("phone", ""))
                return "已从盒子上删除。这部手机以后要用，需要重新添加；也可以在它的「设置 → 通用 → CarPlay」里忘记此车辆。"
            if action == "use":
                self.pairing.use(form.get("phone", ""))
                return "已邀请这部手机接入，约 1 分钟内有效。请保持它的蓝牙打开。"
            if action in ("yes", "no"):
                self.pairing.confirm(form.get("request_id", ""), action == "yes")
                return "" if action == "yes" else "已拒绝这次配对。"
            raise ValueError("未知操作。")
        if path == "/setup":
            self.check_nonce(form)
            return self.apply_setup(form)
        if path == "/reset":
            self.check_nonce(form)
            self.pairing.reset_all()
            # The Lockdown pair record too: the next iPhone on the cable asks "Trust this computer?" again.
            for name in ("car-hotspot.json", "car-lease.json", "network-mode.json"):
                (self.state / name).unlink(missing_ok=True)
            for record in (self.state / "receiver").glob("lockdown-pair-record*.properties"):   # one per iPhone
                record.unlink(missing_ok=True)
            # And the receiver's CarPlay pairings, else the box still knew the old iPhones after a reset.
            (self.state / "receiver/pairings.properties").unlink(missing_ok=True)
            setup_wizard.save(self.state, {"done": False})
            self.apply_later("setup reset")
            return ("page", self.notice_page(
                "正在恢复首次设置", ("off", "热点", "已清除"), ("off", "iPhone", "已清除"),
                ["盒子约 1 分钟后打开自己的热点。",
                 f"手机连接「{self.status().get('box_ssid', '')}」，不需要密码，会自动弹出首次设置。",
                 f"没有弹出时，用浏览器打开 http://{setup_wizard.MDNS_NAME}/ 。"]))
        if path == "/car":
            self.check_nonce(form)
            action = form.get("action")
            if action == "rescan":
                if self.scanner:
                    self.scanner.results(fresh=True)
                return ""
            ssid, password = _car_credentials(form, _load(self.car_file) or {})
            _save(self.car_file, {"ssid": ssid, "passphrase": password})
            self.apply_later("car hotspot saved")
            return ("page", self.switching_page(ssid))
        if path == "/hotspot":
            self.check_nonce(form)
            ssid = self.save_hotspot(form)
            if self.status().get("mode") != "ap":
                return f"已保存。盒子下次打开自己的热点时使用「{ssid}」。"
            self.apply_later("box hotspot changed")
            return ("page", self.reconnect_page(ssid, "正在更改盒子热点"))
        if path == "/ddns":
            self.check_nonce(form)
            return self.save_ddns(form)
        if path == "/retry":
            self.check_nonce(form)
            self.on_change("retry requested")
            return f"正在重新连接{network_label(self.setup().get('mode'))}。"
        raise ValueError("未知操作。")

    def save_ddns(self, form):
        """Own DuckDNS name (own_domain.py); a new name or turning it off drops the old name's certificate."""
        old = own_domain.load(self.state)
        if form.get("action") == "clear":
            self.ddns_file.unlink(missing_ok=True)
            own_domain.forget_certificate(self.state)
            self.apply_later("own domain cleared")
            return "已关闭自己的域名，盒子约 1 分钟后改回自带证书。"
        domain = own_domain.sub_domain(form.get("domain"))
        token = (form.get("token") or "").strip()
        if not token and old and old["domain"] == domain:
            token = old["token"]   # left empty: keep the saved one
        token = own_domain.check_token(token)
        if not old or old["domain"] != domain:
            own_domain.forget_certificate(self.state)
        # Public IPv6 only where the box joins a hotspot: on its own hotspot it has no public address.
        ipv6 = form.get("ipv6") == "on" and self.setup().get("mode") in setup_wizard.JOINS
        _save(self.ddns_file, {"domain": domain, "token": token, **({"ipv6": True} if ipv6 else {})})
        self.apply_later("own domain saved")
        return (f"已保存 {domain}{own_domain.SUFFIX}。盒子约 1 分钟后重启服务，联网时自动更新解析并申请正式证书"
                "（几分钟），下次开机起用。")

    def ddns_block(self, nonce, mode):
        """Advanced: the own DuckDNS name. On OpenWrt LuCI sets it; this page only tells how it is going."""
        name = self.status().get("own_name")
        if self.managed:
            return (f'<div class=cell><span class=main><b>自己的域名</b><span>{escape(own_domain.message(self.state, name))}'
                    f'</span></span><span class=value>{escape(name)}</span></div>') if name else ""
        ddns = own_domain.load(self.state) or {}
        form = (f'<form method=post action=/ddns>{ui.hidden(nonce)}<div class=group>'
                f'<label class=cell><span class=main><b>子域名</b></span><input type=text name=domain '
                f'value="{escape(ddns.get("domain", ""))}" placeholder="如 mycar" required autocapitalize=off '
                'style="text-align:right"></label>'
                '<label class=cell><span class=main><b>Token</b></span><input type=password name=token '
                f'placeholder="{"留空表示不修改" if ddns else "必填"}" autocomplete=off style="text-align:right"></label>'
                + ('<label class=cell><span class=main><b>同时登记公网 IPv6</b><span>车机不让打开 192.168.x.x 这类局域网地址时才勾选</span>'
                   f'</span><input type=checkbox name=ipv6 value=on{" checked" if ddns.get("ipv6") else ""}></label>'
                   if mode in setup_wizard.JOINS else "") + '</div>'
                '<button class="btn plain" name=action value=save>保存</button></form>'
                + (f'<form method=post action=/ddns>{ui.hidden(nonce)}<button class="btn plain" name=action value=clear '
                   'onclick="return confirm(\'关闭后车机改用盒子自带证书的地址，确定？\')">关闭</button></form>' if ddns else ""))
        state = own_domain.message(self.state, name, mode == "box_hotspot") if ddns else ""
        ipv6 = ('勾选「同时登记公网 IPv6」后，盒子把自己的公网 IPv6 地址也登记到这个域名：车机拿到的是公网地址，'
                '画面仍在同一个 WiFi 里直接传。需要车机热点或随身 WiFi 给设备分配 IPv6；这个地址在 DuckDNS 上谁都查得到，'
                '所以盒子同时开启防火墙，只让同一个 WiFi 里的设备连进来。' if mode in setup_wizard.JOINS else
                '盒子热点方式没有公网地址，车机不让打开局域网地址的问题无法用这个办法解决。')
        return (f'<details><summary class=cell><span class=main><b>自己的域名</b><span>证书警告点不过去，或不让打开局域网地址</span></span>'
                f'<span class=value>{escape(name or "未设置")}</span><span class=chev></span></summary>'
                f'<div class=inner style="padding:0 0 12px">{form}'
                + (f'<p class=foot>{escape(state)}</p>' if state else "") +
                '<p class=foot>在 duckdns.org 免费注册并建一个子域名，把子域名和页面上的 token 填在这里。盒子会把这个域名指向自己，'
                '并向 Let’s Encrypt 申请免费的正式证书、到期前自动续签；车机改用 https://子域名.duckdns.org:18443/ 打开，'
                f'不再有证书警告。不需要开放任何端口。{ipv6}'
                + ('盒子热点方式在车上没有网络，申请和续签要在家插上网线开机。' if mode == "box_hotspot" else "") + '</p></div></details>')

    def save_hotspot(self, form):
        old = _load(self.hotspot_file) or {}
        ssid, password = _hotspot_credentials(form, old)
        _save(self.hotspot_file, dict(old, ssid=ssid, passphrase=password))
        return ssid

    def apply_setup(self, form):
        action = form.get("action")
        setup = self.setup()
        if action == "rescan":
            if self.scanner:
                self.scanner.results(fresh=True)
            return ""
        if action == "mode":
            mode = form.get("mode")
            if mode not in setup_wizard.MODES:
                raise ValueError("请选择一种使用方式。")
            setup_wizard.save(self.state, {"done": False, "mode": mode})
            return ""
        if action == "back":   # choose the way of use again; nothing on the air has changed yet
            setup_wizard.save(self.state, {"done": False})
            return ""
        step = setup_wizard.step(setup, self.status().get("mode"))
        if action == "car" and step == "car":
            ssid, password = _car_credentials(form, _load(self.car_file) or {})
            box = _load(self.hotspot_file) or {}
            if not box.get("passphrase"):
                box_password = form.get("box_password", "")
                if not 8 <= len(box_password) <= 63:
                    raise ValueError("请为盒子自己的热点设置 8–63 位密码。")
                _save(self.hotspot_file, dict(box, passphrase=box_password))
            _save(self.car_file, {"ssid": ssid, "passphrase": password})
            setup_wizard.save(self.state, {"done": False, "mode": setup["mode"], "car": ssid})
            self.apply_later("setup car hotspot chosen")
            return ("page", self.switching_page(ssid, first=True))
        if action == "hotspot" and step == "box":
            ssid = self.save_hotspot(form)
            (self.state / "car-lease.json").unlink(missing_ok=True)
            setup_wizard.save(self.state, {"done": False, "mode": "box_hotspot", "box": True})
            self.apply_later("setup box hotspot set")
            return ("page", self.reconnect_page(ssid, "正在打开盒子热点", first=True))
        if action == "finish" and step == "phone":
            setup_wizard.save(self.state, dict(setup, done=True))
            return "设置完成。"
        raise ValueError("未知操作。")

    # Pages -----------------------------------------------------------------------------------

    def notice_page(self, title, hotspot, phone, steps, warn=""):
        items = "".join(f"<li>{escape(step)}</li>" for step in steps)
        return ui.page(title, f'<h1>{escape(title)}</h1>{ui.chain(hotspot, phone)}'
                              f'<p class=group-title>接下来</p><ol class=steps>{items}</ol>{ui.banner(warn, "wait")}')

    def reconnect_page(self, ssid, title, first=False):
        steps = ["盒子约 1 分钟后用新的设置重新打开热点，手机会先断开。",
                 f"在手机和车机的「无线局域网」里连接「{ssid}」，输入你设置的密码。",
                 f"用浏览器打开 http://{setup_wizard.MDNS_NAME}/" + ("，继续连接 iPhone。" if first else "。")]
        return self.notice_page(title, ("wait", ssid, "约 1 分钟"), ("off", "iPhone", "稍后"), steps)

    def switching_page(self, ssid, first=False):
        box = self.status().get("box_ssid", "")
        label = network_label(self.setup().get("mode"))
        address = f"http://{setup_wizard.MDNS_NAME}/"
        steps = [f"盒子约 1 分钟后连上{label}「{ssid}」，并关掉自己的热点，手机会先断开。",
                 f"手机也连接「{ssid}」。",
                 f"用浏览器打开 {address}" + ("，继续连接 iPhone。" if first else "。")]
        return self.notice_page(f"正在连接「{ssid}」", ("wait", ssid, "约 1 分钟"), ("off", "iPhone", "稍后"), steps,
                                f"2 分钟后仍打不开：盒子没连上「{ssid}」，会重新打开自己的热点「{box}」，"
                                "手机连它后打开上面的地址，页面会写明原因。")

    def phone_node(self, devices, active, phase, wired=None):
        if wired == "live":
            return ("on", "iPhone", "有线连接")
        if active and active in devices:
            return ("on", devices[active], "正在使用")
        if phase != "closed":
            return ("wait", "iPhone", "正在添加")
        if wired in ("trust", "starting"):
            return ("wait", "iPhone", "数据线已插上")
        if self.setup().get("mode") == "car_hotspot":
            return ("off", "iPhone", "插数据线使用")
        return ("off", "iPhone", f"已添加 {len(devices)} 部" if devices else "未添加")

    def hotspot_node(self):
        status = self.status()
        if self.managed:
            return ("on", status.get("ssid", ""), "路由器 WiFi") if status.get("ssid") else ("wait", "WiFi", "等待 WiFi")
        if status.get("mode") == "sta":
            return ("on", status.get("ssid", ""), network_label(self.setup().get("mode")))
        if status.get("mode") == "ap":
            return ("on", status.get("box_ssid", ""), "盒子热点")
        return ("wait", "热点", "正在启动")

    def chain(self, devices, presence, phase, wired):
        mode = self.setup().get("mode")
        first = "car" if mode == "car_hotspot" and self.status().get("mode") == "sta" else "hotspot"
        return ui.chain(self.hotspot_node(), self.phone_node(devices, presence.get("active"), phase, wired), first)

    def phones_block(self, nonce, first=False):
        """Pairing flow while it runs; otherwise the device list and the add button."""
        pair = self.pairing.status()
        panel, refresh = ui.pairing_panel(pair, nonce)
        presence = self.pairing.presence()
        devices = self.pairing.devices()
        if panel:
            return panel, refresh, devices
        # "paired" stays in the status file after the window closes; it only matters until someone connects.
        connecting = pair.get("phase") == "paired" and not presence.get("active")
        notice = ui.banner("配对完成，正在引导手机连接热点并打开 CarPlay。") if connecting else ""
        rows = ui.phone_rows(devices, presence.get("active"), presence.get("last_phone"), nonce, not presence.get("active"))
        add = (f'<form method=post action=/pair>{ui.hidden(nonce)}'
               f'<button class="{"btn plain" if devices else "btn"}" name=action value=start>添加 iPhone</button></form>')
        foot = ("配对一次后，手机连上同一个热点就能接入；有人正在使用时，其他手机不会把它挤掉。"
                if not first else "第一次使用需要用蓝牙配对一次，之后连上同一个热点就能接入。")
        return notice + ui.group("我的手机", rows, foot) + add, connecting, devices

    def render(self, query):
        params = urllib.parse.parse_qs(query)
        notice = ui.flash(params.get("m", [""])[0])
        if self.status().get("identity") is False:
            host = urllib.parse.urlsplit(self.home()).hostname or ""
            notice = ui.banner(f"还缺 CarPlay 认证文件。把「AI 安装说明」交给你的 AI 助手，并告诉它：盒子地址 {host}，"
                               "用户名 root，密码 1234。文件放好后盒子会自动开始工作，网络和添加手机可以先设置。", "wait") + notice
        if self.managed:
            return self.render_full(notice)
        setup = self.setup()
        current = setup_wizard.step(setup, self.status().get("mode"))
        if current == "mode":
            return self.render_mode_step(notice)
        if current == "car":
            return self.render_car_step(setup, notice)
        if current == "box":
            return self.render_box_step(notice)
        if current == "phone":
            return self.render_phone_step(notice)
        view = params.get("view", [""])[0]
        if view == "car" and setup.get("mode") in setup_wizard.JOINS:
            return self.render_car_change(notice)
        if view == "hotspot":
            return self.render_hotspot_change(notice)
        return self.render_full(notice)

    def back_link(self):
        return (f'<form method=post action=/setup>{ui.hidden(self.pairing.nonce)}'
                '<button class=back name=action value=back style="border:0;background:none;font:inherit">‹ 换一种使用方式</button></form>')

    def render_mode_step(self, notice):
        body = (f'<h1>使用方式</h1><p class=sub>首次设置：第 1 步，共 3 步</p>{notice}'
                '<p class=foot style="margin:8px 4px 0">选择车机、盒子和 iPhone 怎样连接。以后可以在「高级 → 重新进行首次设置」里更换。</p>'
                + ui.mode_cards(self.pairing.nonce, MODES, self.setup().get("mode")))
        return ui.page("首次设置", body)

    def hotspot_foot(self):
        mode = self.setup().get("mode")
        if mode == "box_hotspot":
            return ("车机和 iPhone 都连这个热点。车机连上后不能上网（盒子热点没有互联网）；"
                    "CarPlay 画面地址固定为 https://192.168.78.1:18443/ 。")
        return f"连不上{network_label(mode)}时，盒子临时打开这个热点，手机连它进入设置。"

    def render_hotspot_change(self, notice):
        nonce = self.pairing.nonce
        box = _load(self.hotspot_file) or {}
        body = (f'<a class=back href="/">‹ 盒子设置</a><h1>盒子热点</h1>{notice}'
                + ui.hotspot_form(nonce, "/hotspot", box.get("ssid", ""), box.get("passphrase", ""), "保存", self.hotspot_foot()))
        return ui.page("盒子热点", body)

    def render_box_step(self, notice):
        box = _load(self.hotspot_file) or {}
        body = (f'{self.back_link()}<h1>盒子热点</h1><p class=sub>首次设置：第 2 步，共 3 步</p>{notice}'
                f'{ui.chain(("wait", box.get("ssid", ""), "请设置"), ("off", "iPhone", "下一步"), "hotspot")}'
                + ui.hotspot_form(self.pairing.nonce, "/setup", box.get("ssid", ""), box.get("passphrase", ""),
                                  "打开热点", self.hotspot_foot()))
        return ui.page("首次设置", body)

    def render_car_step(self, setup, notice):
        nonce = self.pairing.nonce
        label = network_label(setup.get("mode"))
        failure = ""
        trying = setup.get("car") not in (None, "skip")
        if trying:
            reason = setup_wizard.FAILURES.get(setup.get("failed"), setup_wizard.FAILURES["not_found"])
            failure = ui.banner(f"没有连上「{setup['car']}」。{reason}", "bad")
        networks = self.scanner.results() if self.scanner else []
        box = _load(self.hotspot_file) or {}
        extra = "" if box.get("passphrase") else ui.group(
            "盒子自己的热点", '<label class=cell>' + ui.LOCK + '<input type=password name=box_password placeholder="设置密码，至少 8 位" '
            'autocomplete=new-password required minlength=8 maxlength=63 aria-label=盒子热点密码></label>',
            f"找不到{label}时，盒子临时打开自己的热点「{escape(box.get('ssid', ''))}」，手机连它进入设置。")
        foot = ("盒子连车机热点，车机浏览器经它打开 CarPlay 画面。请先在车机上打开它的热点。"
                if setup.get("mode") == "car_hotspot" else
                "盒子、车机和 iPhone 都连这个随身 WiFi。请先打开随身 WiFi。")
        picker = ui.network_picker(networks, setup.get("car") if trying else None, nonce, "car", "/setup", "连接",
                                   foot, extra, label)
        body = (f'{self.back_link()}<h1>连接{label}</h1><p class=sub>首次设置：第 2 步，共 3 步</p>{notice}'
                f'{ui.chain(("wait", label, "请选择"), ("off", "iPhone", "下一步"), "car" if label == "车机热点" else "hotspot")}'
                f'{failure}{picker}')
        return ui.page("首次设置", body)

    def wired_phase(self):
        return read_wired(self.wired_status)

    def iphone_blocks(self, nonce, first=False):
        """Cable first; Bluetooth pairing for wireless where this way of use has it.
        Returns (html, refresh, devices, wired phase)."""
        mode = self.setup().get("mode")
        wired = self.wired_phase()
        waiting = wired in ("trust", "starting") or first and mode == "car_hotspot" and wired != "live"
        if mode == "car_hotspot":
            return ui.wired_block(wired, "iPhone 用数据线", "这种使用方式只能用数据线（有线 CarPlay）。"), waiting, {}, wired
        foot = ("插着数据线时用有线；拔掉后改用下面的无线。"
                + ("无线卡顿或连不上时请用数据线：有的随身 WiFi 不让设备互相访问。" if mode == "mifi" else ""))
        block, refresh, devices = self.phones_block(nonce, first)
        # Pairing is done on another screen (the head unit): the iPhone stays on its Bluetooth page.
        intro = ui.other_screen(self.home()) if first and not devices and not refresh else ""
        return ui.wired_block(wired, "用数据线（推荐）", foot) + intro + block, refresh or waiting, devices, wired

    def entries_block(self, status, refresh):
        entries = "".join(f'<a class=cell href="{escape(u)}"><span class=main><b>{escape(u)}</b></span><span class=chev></span></a>'
                          for u in status.get("entries", []) if u.startswith("https"))
        if not entries or refresh:
            return ""
        mode = self.setup().get("mode")
        if self.managed:
            foot = f"车机连接 WiFi「{escape(status.get('ssid', ''))}」后，在车机浏览器打开上面的地址。"
        elif status.get("mode") == "ap" and mode == "box_hotspot":
            foot = f"车机连接盒子热点「{escape(status.get('box_ssid', ''))}」后，在车机浏览器打开上面的地址。车机连着它时不能上网。"
        elif status.get("mode") == "ap":
            foot = "当前通过盒子热点连接。在连着盒子热点的设备上打开上面的地址。"
        else:
            foot = f"当前通过{network_label(mode)}连接。在车机浏览器打开上面的地址。"
        return ui.group("CarPlay 画面", entries, foot)

    def render_phone_step(self, notice):
        nonce = self.pairing.nonce
        block, refresh, devices, wired = self.iphone_blocks(nonce, first=True)
        presence = self.pairing.presence()
        phase = self.pairing.status().get("phase", "closed")
        ready = bool(devices) or wired == "live"
        finish = (f'<form method=post action=/setup>{ui.hidden(nonce)}'
                  f'<button class="{"btn" if ready else "btn plain"}" name=action value=finish>'
                  f'{"完成设置" if ready else "稍后连接，先完成设置"}</button></form>')
        pairing = phase != "closed"
        body = (f'<h1>连接 iPhone</h1><p class=sub>首次设置：第 3 步，共 3 步</p>{notice}'
                f'{self.chain(devices, presence, phase, wired)}'
                f'{block}'
                + self.entries_block(self.status(), pairing)
                + (finish if ready or not pairing else ""))
        return ui.page("首次设置", body, refresh)

    def render_car_change(self, notice):
        nonce = self.pairing.nonce
        car = _load(self.car_file) or {}
        label = network_label(self.setup().get("mode"))
        networks = self.scanner.results() if self.scanner else []
        picker = ui.network_picker(networks, car.get("ssid"), nonce, "save", "/car", "连接",
                                   "保存后盒子会切换过去；连不上时临时打开盒子自己的热点。" +
                                   ("密码留空表示沿用已保存的密码。" if car else ""), label=label)
        body = f'<a class=back href="/">‹ 盒子设置</a><h1>{label}</h1>{notice}{picker}'
        page = ui.page(label, body)
        # A saved password may be kept, so the field is optional on this page only.
        return page.replace(" required minlength=8", " minlength=8") if car else page

    def render_full(self, notice):
        nonce = self.pairing.nonce
        status = self.status()
        mode = self.setup().get("mode")
        label = network_label(mode)
        car = _load(self.car_file) or {}
        block, refresh, devices, wired = self.iphone_blocks(nonce)
        presence = self.pairing.presence()
        phase = self.pairing.status().get("phase", "closed")
        joins = mode in setup_wizard.JOINS
        fallback = (ui.banner(f"没有连上{label}「{car.get('ssid', '')}」，暂时打开了盒子自己的热点。{label}打开后会自动切换。", "wait")
                    if status.get("mode") == "ap" and joins and car else "")
        network = (f'<div class=cell><span class=main><b>使用方式</b></span><span class=value>{escape(MODE_TITLES.get(mode, ""))}</span></div>')
        if joins:
            network += (f'<a class=cell href="/?view=car"><span class=main><b>{label}</b></span>'
                        f'<span class=value>{escape(car.get("ssid", "未设置"))}</span><span class=chev></span></a>')
        network += (f'<a class=cell href="/?view=hotspot"><span class=main><b>盒子热点</b></span>'
                    f'<span class=value>{escape(status.get("box_ssid", ""))}</span><span class=chev></span></a>'
                    f'<div class=cell><span class=main><b>设置页</b></span><span class=value>http://{setup_wizard.MDNS_NAME}/</span></div>')
        if status.get("mode") == "ap" and joins and car:
            network += (f'<form method=post action=/retry>{ui.hidden(nonce)}'
                        f'<button class="cell action">立即重试{label}</button></form>')
        if self.managed:
            admin = self.managed["admin"]
            if admin.startswith("/"):
                # On the router's own web server (port 80), not on this page's port (a 404 there).
                host = urllib.parse.urlsplit(status.get("home", "")).hostname
                admin = f"http://{host}{admin}" if host else admin
            network = (f'<div class=cell><span class=main><b>交给 iPhone 的 WiFi</b></span>'
                       f'<span class=value>{escape(status.get("ssid", "") or "等待 WiFi")}</span></div>'
                       f'<a class=cell href="{escape(admin)}"><span class=main><b>在 {self.managed["by"]} 后台设置 WiFi</b>'
                       '<span>WiFi 名称、密码或信道改了以后，DashPlay 会自动跟着改</span></span><span class=chev></span></a>')
            if status.get("warning"):
                fallback = ui.banner(escape(status["warning"]), "bad")
        # Why the head unit may not open the picture, and what to do, in the order to try (2026-10-13).
        reach = (('<a class=cell href="/ca.crt"><span class=main><b>下载盒子 CA 证书</b><span>装到车机上，证书警告就没了</span></span><span class=chev></span></a>'
                  if self.ca_file.exists() else "") + self.ddns_block(nonce, mode))
        reach_foot = ("" if self.managed else
                      "一般用不到。按顺序试：① 车机提示证书不安全，点「继续访问」即可。② 没有「继续访问」：在车机上装盒子 CA 证书；"
                      "车机不让装，就设置「自己的域名」。③ 车机不让打开 192.168.x.x 这类局域网地址：设置「自己的域名」并勾选"
                      "「同时登记公网 IPv6」（方式一、方式二）。")
        advanced = ('<a class=cell href="/diag.txt"><span class=main><b>下载诊断记录</b><span>画面卡顿、断线时，把它交给 AI 助手或开发者分析</span></span><span class=chev></span></a>'
                    + ("" if self.managed else
                    f'<form method=post action=/reset>{ui.hidden(nonce)}<button class="cell destructive" '
                    'onclick="return confirm(\'清除使用方式、热点和所有已添加的手机，回到首次设置？\')">重新进行首次设置</button></form>'))
        body = (f'<h1 class=span>盒子设置</h1>{notice}{self.chain(devices, presence, phase, wired)}'
                f'{fallback}<div>'
                + self.entries_block(status, False)
                + block + '</div><div>'
                + ui.group("网络", network)
                + (ui.group("车机打不开画面时", reach, reach_foot) if reach else "")
                + ui.group("高级", advanced, "" if self.managed else "重新进行首次设置可以更换使用方式，会保留盒子证书、自己的域名和盒子热点密码。")
                + '</div>')
        return ui.page("盒子设置", body, refresh, wide=True)
