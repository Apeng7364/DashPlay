"""Optional: a DuckDNS name with a Let's Encrypt certificate, for car browsers that refuse the box's local CA
and offer no way past the warning (2026-10-13: a head unit's Chromium showed ERR_CERT_AUTHORITY_INVALID with
no "continue"). Off unless the owner enters a DuckDNS sub-domain and token: on the box in the settings page
(state/ddns.json), on OpenWrt in LuCI (uci dashplay.main.duckdns_domain / duckdns_token).

- The public DuckDNS record points the name at the address the head unit opens: the box on the car hotspot or
  portable Wi-Fi, 192.168.78.1 on the box hotspot, the router's own address on OpenWrt. DuckDNS takes a
  private address; a DNS server that filters those ("rebind protection") will not resolve the name.
- The certificate comes from Let's Encrypt through acme.sh (bundled in acme/) with a DNS-01 challenge: only
  outgoing HTTPS, nothing has to reach the box, so it works behind the car hotspot. It is installed in
  state/tls/own/, which boxnet serves while valid; a new one loads on the next start, never mid-drive.
  Renewed when less than RENEW_DAYS remain, whenever the box is online (box hotspot: no internet in the car,
  so at home on the Ethernet cable).
- The token reaches acme.sh by its environment only (not its command line); it is never logged or shown.
- Optional, on the car hotspot or portable Wi-Fi ("ipv6": true): the record also carries the box's own public
  IPv6 address, for car browsers that refuse a LAN address (the picture still comes straight over the Wi-Fi). The
  head unit asks that hotspot, which asks DuckDNS, so the address is public: it is published only behind an IPv6
  firewall (nftables, guard_rules) that lets in nothing but the Wi-Fi's own prefix and link-local addresses; a
  box where it cannot be loaded never publishes it. OpenWrt does this differently (uci public_ipv6): the router answers the
  name itself, so nothing is published.
"""
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request

SUFFIX = ".duckdns.org"
DOMAIN = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
TOKEN = re.compile(r"[A-Za-z0-9-]{16,64}")   # DuckDNS shows a UUID
RENEW_DAYS = 30            # Let's Encrypt certificates last 90 days
RECORD_REFRESH = 1800      # seconds between DuckDNS updates once one succeeded
RECORD_RETRY = 60
CERT_CHECK = 12 * 3600
CERT_RETRY = 3600
ACME_TIMEOUT = 600
DNS_SLEEP = 60             # wait for the TXT record instead of acme.sh's DoH check (blocked on some networks)
ACME_SERVER = os.environ.get("DASHPLAY_ACME_SERVER", "letsencrypt")   # tests: letsencrypt_test


def sub_domain(text):
    """'MyCar', 'mycar.duckdns.org' -> 'mycar'; ValueError when not a DuckDNS sub-domain."""
    domain = (text or "").strip().lower().removesuffix(SUFFIX)
    if not DOMAIN.fullmatch(domain):
        raise ValueError("DuckDNS 子域名只能包含小写字母、数字和连字符。")
    return domain


def check_token(token):
    token = (token or "").strip()
    if not TOKEN.fullmatch(token):
        raise ValueError("DuckDNS token 格式不对：在 duckdns.org 登录后页面上方的 token 一栏复制。")
    return token


def load(state):
    """state/ddns.json -> {"domain": sub-domain, "token": ..., ["ipv6": True]}, or None when off or unreadable."""
    try:
        config = json.loads((Path(state) / "ddns.json").read_text())
        loaded = {"domain": sub_domain(config["domain"]), "token": check_token(config["token"])}
        if config.get("ipv6") is True:
            loaded["ipv6"] = True
        return loaded
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def name_of(config):
    return config["domain"] + SUFFIX if config else None


def forget_certificate(state):
    """When the name changes or the feature is turned off: the certificate is for the old name."""
    shutil.rmtree(Path(state) / "tls" / "own", ignore_errors=True)
    (Path(state) / "own-domain.json").unlink(missing_ok=True)


def update_record(config, address, ipv6=None, clear=False, timeout=15):
    """Point the name at address (private addresses allowed) and, when given, its AAAA at ipv6. clear: drop both
    records first (DuckDNS has no way to drop the AAAA alone). True when DuckDNS answered OK."""
    def call(fields):
        query = urllib.parse.urlencode(dict(fields, domains=config["domain"], token=config["token"]))
        with urllib.request.urlopen("https://www.duckdns.org/update?" + query, timeout=timeout) as response:
            return response.read(16).strip() == b"OK"
    if clear and not call({"clear": "true"}):
        return False
    return call(dict({"ip": address}, **({"ipv6": ipv6} if ipv6 else {})))


NFT_TABLE = "dashplay_v6"


def public_ipv6(interface, run=subprocess.run):
    """(address, prefix length) of the interface's public IPv6 address, a stable one before a temporary one;
    None when it has none (the hotspot hands out no IPv6, or only ULA)."""
    try:
        out = run(["ip", "-6", "-j", "addr", "show", "dev", interface, "scope", "global"],
                  capture_output=True, text=True, timeout=5)
        links = json.loads(out.stdout or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    found = []
    for link in links if isinstance(links, list) else []:
        for info in link.get("addr_info", []):
            try:
                address = ipaddress.IPv6Address(info.get("local", ""))
            except ValueError:
                continue
            if info.get("family") != "inet6" or not address.is_global or any(
                    info.get(flag) for flag in ("deprecated", "tentative", "dadfailed")):
                continue
            found.append((bool(info.get("temporary")), str(address), int(info.get("prefixlen", 64))))
    return min(found)[1:] if found else None


def guard_rules(address, prefix_length):
    """nftables: IPv6 into the box only from its own Wi-Fi (the address's prefix) and link-local addresses, plus
    ICMPv6 and DHCPv6 replies; IPv4 is left alone. Replaces the table as one transaction."""
    network = ipaddress.IPv6Network(f"{address}/{min(max(prefix_length, 48), 128)}", strict=False)
    return (f"table inet {NFT_TABLE}\ndelete table inet {NFT_TABLE}\n"
            f"table inet {NFT_TABLE} {{\n  chain input {{\n    type filter hook input priority filter; policy accept;\n"
            "    meta nfproto ipv4 accept\n    iifname \"lo\" accept\n    ct state established,related accept\n"
            "    meta l4proto ipv6-icmp accept\n    ip6 saddr fe80::/10 accept\n"
            f"    ip6 saddr {network} accept\n    udp dport 546 accept\n    counter drop\n  }}\n}}\n")


def nft(commands, run=subprocess.run):
    """Run nftables commands: with the nft program, or else through libnftables, which the box images carry (as a
    library of their other packages) without the program. False when neither is there or they were refused."""
    if shutil.which("nft"):
        try:
            return run(["nft", "-f", "-"], input=commands, capture_output=True, text=True, timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        import ctypes
        lib = ctypes.CDLL("libnftables.so.1")
    except OSError:
        return False
    lib.nft_ctx_new.restype = ctypes.c_void_p
    lib.nft_ctx_free.argtypes = [ctypes.c_void_p]
    lib.nft_ctx_buffer_output.argtypes = lib.nft_ctx_buffer_error.argtypes = [ctypes.c_void_p]
    lib.nft_run_cmd_from_buffer.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    ctx = lib.nft_ctx_new(0)
    if not ctx:
        return False
    try:
        lib.nft_ctx_buffer_output(ctx); lib.nft_ctx_buffer_error(ctx)   # quiet: nothing on the service's output
        return lib.nft_run_cmd_from_buffer(ctx, commands.encode()) == 0
    finally:
        lib.nft_ctx_free(ctx)


def guard(address, prefix_length, run=subprocess.run):
    """Load the IPv6 firewall. False when it could not be loaded: then the address is not published."""
    return nft(guard_rules(address, prefix_length), run)


def unguard(run=subprocess.run):
    """Drop the IPv6 firewall (the option turned off); nothing when it is not there."""
    nft(f"table inet {NFT_TABLE}\ndelete table inet {NFT_TABLE}\n", run)


def _openssl(*args):
    return subprocess.run(["openssl", *args], capture_output=True, text=True, timeout=30)


def certificate_for(state, name, days=0):
    """The installed certificate is for name and stays valid `days` more."""
    chain = Path(state) / "tls" / "own" / "fullchain.pem"
    if not chain.exists():
        return False
    host = _openssl("x509", "-noout", "-checkhost", name, "-in", str(chain))
    return "does match" in host.stdout and _openssl(
        "x509", "-noout", "-checkend", str(days * 86400), "-in", str(chain)).returncode == 0


def end_date(state):
    """'2027-01-11' for the installed certificate, or None."""
    out = _openssl("x509", "-noout", "-enddate", "-in", str(Path(state) / "tls" / "own" / "fullchain.pem"))
    if out.returncode:
        return None
    try:
        return time.strftime("%Y-%m-%d", time.strptime(out.stdout.strip().split("=", 1)[1], "%b %d %H:%M:%S %Y %Z"))
    except (IndexError, ValueError):
        return None


def write_status(state, **fields):
    """Fixed, public fields only (phase, record, address): never an error text, which may carry the token."""
    path = Path(state) / "own-domain.json"
    try:
        old = json.loads(path.read_text())
    except (OSError, ValueError):
        old = {}
    value = dict(old if isinstance(old, dict) else {}, **fields)
    fd, temporary = tempfile.mkstemp(prefix=".own-domain.", dir=state)
    with os.fdopen(fd, "w") as out:
        json.dump(value, out)
    Path(temporary).replace(path)


def read_status(state):
    try:
        value = json.loads((Path(state) / "own-domain.json").read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


class Maintainer(threading.Thread):
    """Keeps the DuckDNS record on `address` (None: leave it) and the certificate current, until stopped.
    interface: the Wi-Fi joined on the car hotspot or portable Wi-Fi, whose public IPv6 address the record also
    carries when the owner asked for it (config "ipv6") and the firewall is up; looked at every minute, so a new
    prefix (the hotspot reconnected) is followed."""

    def __init__(self, root, config, address, stopped, say=print, interface=None, tesla=False):
        super().__init__(name="own-domain", daemon=True)
        self.root, self.state = Path(root), Path(root) / "state"
        self.config, self.name, self.address = config, name_of(config), address
        if tesla and address:
            from setup_wizard import tesla_reaches
            if not tesla_reaches(address):
                self.address = None
                write_status(self.state, name=self.name, record="blocked", warning="特斯拉无法打开当前地址，已保留原域名记录")
                say("DuckDNS record kept: Tesla cannot reach the current address")
        self.stopped, self.say, self.interface = stopped, say, interface
        self.sent, self.sent_at = None, 0.0   # (address, ipv6) last accepted by DuckDNS, and when

    def ipv6(self):
        """(address or None, state for the settings page): ok, none (no public IPv6), firewall (no nft), off."""
        if not (self.interface and self.config.get("ipv6")):
            return None, "off"
        found = public_ipv6(self.interface)
        if not found:
            return None, "none"
        return (found[0], "ok") if guard(*found) else (None, "firewall")

    def run(self):
        next_record = next_cert = time.monotonic()
        published = read_status(self.state).get("ipv6") or None   # an AAAA left from before must go
        while not self.stopped.is_set():
            now = time.monotonic()
            if self.address and now >= next_record:
                ipv6, v6state = self.ipv6()
                if (self.address, ipv6) == self.sent and now < self.sent_at + RECORD_REFRESH:
                    ok = True   # nothing changed since DuckDNS took it
                else:
                    try:
                        ok = update_record(self.config, self.address, ipv6=ipv6, clear=bool(published and not ipv6))
                    except (OSError, ValueError):   # no internet yet; URLError is an OSError
                        ok = False
                    if ok:
                        self.sent, self.sent_at, published = (self.address, ipv6), now, ipv6
                    self.say(f"duckdns {self.name} -> {self.address}{' ' + ipv6 if ipv6 else ''}: "
                             f"{'OK' if ok else 'failed'}")
                write_status(self.state, name=self.name, record="ok" if ok else "failed", address=self.address,
                             ipv6=published or "", ipv6_state=v6state)
                next_record = now + (RECORD_RETRY if not ok or v6state != "off" else RECORD_REFRESH)
            if now >= next_cert:
                next_cert = now + self.check_certificate()
            if not self.address:
                next_record = float("inf")
            self.stopped.wait(max(1.0, min(next_record, next_cert) - time.monotonic()))

    def check_certificate(self):
        """Seconds until the next look."""
        if certificate_for(self.state, self.name, RENEW_DAYS):
            write_status(self.state, name=self.name, phase="ready")
            return CERT_CHECK
        renewing = certificate_for(self.state, self.name)
        write_status(self.state, name=self.name, phase="renewing" if renewing else "issuing")
        try:
            self.obtain()
        except InterruptedError:
            return CERT_CHECK
        except Exception as error:   # no internet, DuckDNS or Let's Encrypt refused; texts may carry the token
            write_status(self.state, name=self.name, phase="retry")
            self.say(f"certificate for {self.name} deferred: {type(error).__name__}")
            return CERT_RETRY
        write_status(self.state, name=self.name, phase="ready")
        self.say(f"certificate for {self.name} installed until {end_date(self.state)}; used from the next start")
        return CERT_CHECK

    def obtain(self):
        home = self.state / "acme"
        home.mkdir(mode=0o700, exist_ok=True)
        log = home / "acme.log"
        log.unlink(missing_ok=True)   # one run's log only (private, 0600)
        # One worker at a time, also across a supervisor restart while acme.sh is still running.
        fd = os.open(home / "dashplay.lock", os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # 2: acme.sh still has a certificate it considers current; install that one.
            if self.acme(["--issue", "--dns", "dns_duckdns", "--dnssleep", str(DNS_SLEEP), "-d", self.name,
                          "--keylength", "ec-256"], log) not in (0, 2):
                raise RuntimeError("acme.sh could not obtain the certificate")
            with tempfile.TemporaryDirectory(dir=home) as scratch:
                chain, key = Path(scratch) / "fullchain.pem", Path(scratch) / "key.pem"
                if self.acme(["--install-cert", "-d", self.name, "--ecc", "--fullchain-file", str(chain),
                              "--key-file", str(key)], log) != 0:
                    raise RuntimeError("acme.sh could not export the certificate")
                self.install(chain, key)

    def install(self, chain, key):
        """Only a certificate for this name whose key matches replaces the installed one."""
        pub = lambda *args: _openssl(*args).stdout
        if "does match" not in pub("x509", "-noout", "-checkhost", self.name, "-in", str(chain)) or (
                pub("x509", "-noout", "-pubkey", "-in", str(chain)) != pub("pkey", "-pubout", "-in", str(key))):
            raise ValueError("certificate does not match")
        own = self.state / "tls" / "own"
        own.mkdir(mode=0o700, parents=True, exist_ok=True)
        key.chmod(0o600)
        key.replace(own / "key.pem")
        chain.replace(own / "fullchain.pem")

    def acme(self, arguments, log):
        """acme.sh in its own process group, cancelled when the service stops; the token only in its environment."""
        home = self.state / "acme"
        command = [str(self.root / "acme" / "acme.sh"), "--home", str(self.root / "acme"), "--config-home", str(home),
                   "--cert-home", str(home / "certs"), *arguments, "--server", ACME_SERVER, "--no-color"]
        env = dict(os.environ, DuckDNS_Token=self.config["token"])
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as out, subprocess.Popen(
                command, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                start_new_session=True) as process:
            deadline = time.monotonic() + ACME_TIMEOUT
            try:
                while True:
                    if self.stopped.is_set():
                        raise InterruptedError("service stopping")
                    if time.monotonic() > deadline:
                        raise TimeoutError("acme.sh took too long")
                    try:
                        return process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()


def message(state, name, box_hotspot=False):
    """The settings page's line about the name and its certificate."""
    if not name:
        return ""
    status = read_status(state)
    if status.get("name") != name:
        status = {}
    until = end_date(state) if certificate_for(state, name) else None
    if until:
        text = f"正式证书有效期至 {until}。"
        if not serving(state):
            text += "新证书在下次开机时启用。"
        if box_hotspot and not certificate_for(state, name, RENEW_DAYS):
            text += "车上没有网络无法续签：请回家插上网线开机一次，盒子会自动续签。"
    else:
        text = {"issuing": "正在申请正式证书，通常几分钟；完成后下次开机启用。",
                "retry": "正式证书暂未申请到：请确认盒子能上网、子域名和 token 正确；盒子每小时重试。",
                }.get(status.get("phase"), "等待申请正式证书：盒子联网后自动开始。")
        if box_hotspot:
            text += "盒子热点方式在车上没有网络：请在家插上网线开机申请。"
    if status.get("record") == "blocked":
        text += status.get("warning", "已保留原域名记录。")
    if status.get("record") == "failed":
        text += f"更新 {name} 的解析失败，盒子会继续重试。"
    text += {"ok": f"公网 IPv6 {status.get('ipv6')} 已登记，防火墙只让同一个 WiFi 里的设备连进盒子。",
             "none": "连着的热点没有给盒子分配公网 IPv6，暂时只登记了局域网地址。",
             "firewall": "盒子的防火墙没能启用，为安全起见没有登记公网 IPv6；请把诊断记录交给 AI 助手或开发者。",
             }.get(status.get("ipv6_state"), "")
    return text


def serving(state):
    """The web server's keystore holds the installed certificate of one's own (boxnet's marker: its hash)."""
    try:
        marker = json.loads((Path(state) / "tls" / "leaf.san.json").read_text())
        chain = (Path(state) / "tls" / "own" / "fullchain.pem").read_bytes()
        return marker.get("own") == hashlib.sha256(chain).hexdigest()[:16]
    except (OSError, ValueError, TypeError, AttributeError):
        return False
