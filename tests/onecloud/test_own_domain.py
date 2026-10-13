"""Own DuckDNS name with a Let's Encrypt certificate (own_domain.py): for car browsers that refuse the box's local
CA with no "continue" (2026-10-13). acme.sh is replaced by a script that hands out a synthetic certificate."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/onecloud"))
import boxnet
import own_domain
from settings_page import SettingsPage
import setup_wizard
import supervisor_openwrt

TOKEN = "0a1b2c3d-1111-2222-3333-444455556666"
FAKE_ACME = """#!/bin/sh
# Records how it was called; --install-cert copies the prepared certificate to the requested files.
echo "$*" >> "$FAKE_DIR/calls"
[ -n "$DuckDNS_Token" ] && echo "token in environment" >> "$FAKE_DIR/calls"
case " $* " in *" --issue "*) [ -n "$FAKE_SLEEP" ] && sleep "$FAKE_SLEEP"; exit "${FAKE_ISSUE:-0}" ;; esac
while [ $# -gt 0 ]; do
    case "$1" in --fullchain-file) cp "$FAKE_DIR/fullchain.pem" "$2" ;; --key-file) cp "$FAKE_DIR/key.pem" "$2" ;; esac
    shift
done
"""


def make_certificate(directory, name, days=90):
    run = lambda *a: subprocess.run(["openssl", *a], check=True, capture_output=True, cwd=directory)
    run("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", "ca.key",
        "-out", "ca.crt", "-days", "100", "-subj", "/CN=Synthetic CA")
    run("req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", "key.pem",
        "-out", "leaf.csr", "-subj", "/CN=" + name)
    (Path(directory) / "ext").write_text(f"subjectAltName=DNS:{name}\n")
    run("x509", "-req", "-in", "leaf.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial", "-days", str(days),
        "-extfile", "ext", "-out", "leaf.crt")
    (Path(directory) / "fullchain.pem").write_text((Path(directory) / "leaf.crt").read_text()
                                                   + (Path(directory) / "ca.crt").read_text())


class ConfigTests(unittest.TestCase):
    def test_sub_domain_accepts_the_full_name_and_capitals(self):
        self.assertEqual(own_domain.sub_domain(" MyCar.duckdns.org "), "mycar")
        for bad in ("", "my car", "-x", "a.b", "x" * 64):
            with self.assertRaises(ValueError):
                own_domain.sub_domain(bad)
        with self.assertRaises(ValueError):
            own_domain.check_token("short")

    def test_openwrt_reads_it_from_uci_and_ignores_a_bad_one(self):
        base = supervisor_openwrt.read_config("dashplay.main.duckdns_domain='MyCar'\n"
                                              f"dashplay.main.duckdns_token='{TOKEN}'\n")
        self.assertEqual(supervisor_openwrt.own_config(base), {"domain": "mycar", "token": TOKEN})
        self.assertIsNone(supervisor_openwrt.own_config(supervisor_openwrt.read_config("")))
        bad = supervisor_openwrt.read_config("dashplay.main.duckdns_domain='mycar'\ndashplay.main.duckdns_token='x'\n")
        self.assertIsNone(supervisor_openwrt.own_config(bad), "a wrong token turns it off, CarPlay still starts")


@unittest.skipUnless(shutil.which("openssl"), "needs the openssl command")
class MaintainerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "dashplay"; self.state = self.root / "state"
        (self.root / "acme").mkdir(parents=True); self.state.mkdir()
        self.fake = Path(tmp.name) / "fake"; self.fake.mkdir()
        acme = self.root / "acme" / "acme.sh"
        acme.write_text(FAKE_ACME); acme.chmod(0o755)
        self.config = {"domain": "mycar", "token": TOKEN}
        self.name = "mycar.duckdns.org"
        env = patch.dict(os.environ, FAKE_DIR=str(self.fake)); env.start(); self.addCleanup(env.stop)
        self.records = []
        self.sent = []   # (address, ipv6, clear) as DuckDNS was asked
        record = patch.object(own_domain, "update_record", lambda config, address, ipv6=None, clear=False: (
            self.records.append(address), self.sent.append((address, ipv6, clear)))[0] or True)
        record.start(); self.addCleanup(record.stop)
        self.stopped = threading.Event()

    def maintainer(self, address="192.168.5.80"):
        return own_domain.Maintainer(self.root, self.config, address, self.stopped, say=lambda _: None)

    def calls(self):
        return (self.fake / "calls").read_text()

    def test_obtains_installs_and_serves_the_certificate(self):
        make_certificate(self.fake, self.name)
        self.assertEqual(self.maintainer().check_certificate(), own_domain.CERT_CHECK)
        own = self.state / "tls" / "own"
        self.assertTrue(own_domain.certificate_for(self.state, self.name, own_domain.RENEW_DAYS))
        self.assertEqual((own / "key.pem").stat().st_mode & 0o777, 0o600)
        self.assertEqual(own_domain.read_status(self.state)["phase"], "ready")
        self.assertIn("--dns dns_duckdns", self.calls())
        self.assertIn("token in environment", self.calls())
        self.assertNotIn(TOKEN, self.calls(), "the token stays off acme.sh's command line")
        self.assertIn("下次开机时启用", own_domain.message(self.state, self.name))
        boxnet.ensure_leaf(self.state, [self.name], ["192.168.5.80"])   # the next start
        self.assertTrue(own_domain.serving(self.state))
        self.assertNotIn("下次开机", own_domain.message(self.state, self.name))

    def test_a_current_certificate_is_left_alone(self):
        make_certificate(self.fake, self.name)
        self.maintainer().check_certificate()
        (self.fake / "calls").unlink()
        self.assertEqual(self.maintainer().check_certificate(), own_domain.CERT_CHECK)
        self.assertFalse((self.fake / "calls").exists(), "acme.sh not run while more than 30 days remain")

    def test_a_certificate_for_another_name_is_refused(self):
        make_certificate(self.fake, "someone-else.duckdns.org")
        self.assertEqual(self.maintainer().check_certificate(), own_domain.CERT_RETRY)
        self.assertFalse((self.state / "tls" / "own" / "fullchain.pem").exists())
        self.assertEqual(own_domain.read_status(self.state)["phase"], "retry")
        self.assertIn("每小时重试", own_domain.message(self.state, self.name))

    def test_failure_is_retried_within_the_hour(self):
        with patch.dict(os.environ, FAKE_ISSUE="1"):
            self.assertEqual(self.maintainer().check_certificate(), own_domain.CERT_RETRY)

    def test_stopping_the_service_stops_acme(self):
        make_certificate(self.fake, self.name)
        with patch.dict(os.environ, FAKE_SLEEP="30"):
            worker = self.maintainer(None)
            worker.start()
            time.sleep(1)
            started = time.monotonic()
            self.stopped.set()
            worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - started, 6)
        self.assertEqual(self.records, [], "no address: the record is left as it is")

    def test_the_record_follows_the_address(self):
        make_certificate(self.fake, self.name)
        worker = self.maintainer("192.168.78.1")
        worker.start()
        for _ in range(50):
            if self.records:
                break
            time.sleep(0.1)
        self.stopped.set(); worker.join(10)
        self.assertEqual(self.records[:1], ["192.168.78.1"])
        self.assertEqual(own_domain.read_status(self.state)["record"], "ok")

    def run_record(self, config, interface="wlan0", until=lambda sent: sent):
        worker = own_domain.Maintainer(self.root, config, "192.168.5.80", self.stopped, say=lambda _: None,
                                       interface=interface)
        with patch.object(own_domain.Maintainer, "check_certificate", lambda _: own_domain.CERT_CHECK):
            worker.start()
            for _ in range(50):
                if until(self.sent):
                    break
                time.sleep(0.1)
            self.stopped.set(); worker.join(10)
        self.stopped.clear()
        return own_domain.read_status(self.state)

    def test_public_ipv6_is_published_only_behind_the_firewall(self):
        v6 = dict(self.config, ipv6=True)
        with patch.object(own_domain, "public_ipv6", lambda _: ("2408:8409:8450:9d91::5", 64)), \
                patch.object(own_domain, "guard", lambda *a: True):
            status = self.run_record(v6)
        self.assertEqual(self.sent[0], ("192.168.5.80", "2408:8409:8450:9d91::5", False))
        self.assertEqual((status["ipv6"], status["ipv6_state"]), ("2408:8409:8450:9d91::5", "ok"))
        self.assertIn("防火墙只让同一个 WiFi", own_domain.message(self.state, self.name))
        self.sent.clear()
        with patch.object(own_domain, "public_ipv6", lambda _: ("2408:8409:8450:9d91::5", 64)), \
                patch.object(own_domain, "guard", lambda *a: False):
            status = self.run_record(v6)
        self.assertEqual(self.sent[0], ("192.168.5.80", None, True), "no firewall: the old AAAA is cleared, none sent")
        self.assertEqual(status["ipv6_state"], "firewall")
        self.assertIn("没有登记公网 IPv6", own_domain.message(self.state, self.name))

    def test_no_public_ipv6_or_option_off(self):
        with patch.object(own_domain, "public_ipv6", lambda _: None):
            status = self.run_record(dict(self.config, ipv6=True))
        self.assertEqual((self.sent[0][1], status["ipv6_state"]), (None, "none"))
        self.assertIn("没有给盒子分配公网 IPv6", own_domain.message(self.state, self.name))
        self.sent.clear()
        with patch.object(own_domain, "public_ipv6", lambda _: self.fail("not looked up when off")):
            status = self.run_record(self.config)
        self.assertEqual(self.sent[0], ("192.168.5.80", None, False))
        self.assertEqual(status["ipv6_state"], "off")

    def test_box_hotspot_is_told_to_renew_at_home(self):
        make_certificate(self.fake, self.name, days=10)
        own = self.state / "tls" / "own"; own.mkdir(parents=True)
        shutil.copy(self.fake / "fullchain.pem", own); shutil.copy(self.fake / "key.pem", own)
        self.assertIn("回家插上网线", own_domain.message(self.state, self.name, box_hotspot=True))
        self.assertNotIn("回家", own_domain.message(self.state, self.name))


class Ipv6Tests(unittest.TestCase):
    def test_finds_the_stable_public_address(self):
        out = json.dumps([{"ifname": "wlan0", "addr_info": [
            {"family": "inet6", "local": "fd9e:30a4:d99::5", "prefixlen": 64},                       # ULA
            {"family": "inet6", "local": "2408:8409:1::99", "prefixlen": 64, "temporary": True},
            {"family": "inet6", "local": "2408:8409:1::7", "prefixlen": 64, "deprecated": True},
            {"family": "inet6", "local": "2408:8409:1::5", "prefixlen": 64}]}])
        run = lambda *a, **k: subprocess.CompletedProcess(a, 0, out, "")
        self.assertEqual(own_domain.public_ipv6("wlan0", run), ("2408:8409:1::5", 64))
        only_temporary = json.dumps([{"addr_info": [{"family": "inet6", "local": "2408:8409:1::99", "prefixlen": 64,
                                                     "temporary": True}]}])
        self.assertEqual(own_domain.public_ipv6("wlan0", lambda *a, **k: subprocess.CompletedProcess(a, 0, only_temporary, "")),
                         ("2408:8409:1::99", 64))
        self.assertIsNone(own_domain.public_ipv6("wlan0", lambda *a, **k: subprocess.CompletedProcess(a, 0, "[]", "")))

    def test_firewall_lets_in_only_the_wifi(self):
        rules = own_domain.guard_rules("2408:8409:8450:9d91:5084:daff:fe9d:c893", 64)
        self.assertIn("ip6 saddr 2408:8409:8450:9d91::/64 accept", rules)
        self.assertIn("ip6 saddr fe80::/10 accept", rules)
        self.assertIn("meta nfproto ipv4 accept", rules)
        self.assertTrue(rules.rstrip().endswith("}"))
        self.assertIn("counter drop", rules)

    def test_record_query(self):
        urls = []
        class Answer:
            def __init__(self, url): urls.append(url)
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n): return b"OK"
        with patch.object(own_domain.urllib.request, "urlopen", lambda url, timeout: Answer(url)):
            self.assertTrue(own_domain.update_record({"domain": "mycar", "token": TOKEN}, "192.168.5.80", ipv6="2408::5"))
            self.assertTrue(own_domain.update_record({"domain": "mycar", "token": TOKEN}, "192.168.5.80", clear=True))
        self.assertIn("ip=192.168.5.80", urls[0]); self.assertIn("ipv6=2408%3A%3A5", urls[0])
        self.assertIn("clear=true", urls[1]); self.assertNotIn("ipv6", urls[2])


class SettingsPageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "state"; self.state.mkdir()
        (self.state / "hotspot.json").write_text('{"ssid": "DashPlay", "passphrase": "installed1"}')
        setup_wizard.save(self.state, {"done": True, "mode": "box_hotspot"})
        self.status = {"mode": "ap", "box_ssid": "DashPlay", "entries": [], "home": "http://192.168.78.1/"}
        self.changes = []
        self.page = SettingsPage(self.state, lambda: self.status, self.changes.append, lambda: [])
        self.page.apply_later = self.changes.append
        self.nonce = self.page.pairing.nonce

    def save(self, **form):
        return self.page.apply("/ddns", dict(form, nonce=self.nonce))

    def test_save_keep_token_change_name_and_turn_off(self):
        self.assertIn("未设置", self.page.render(""))
        self.assertIn("mycar.duckdns.org", self.save(action="save", domain="MyCar", token=TOKEN))
        ddns = self.state / "ddns.json"
        self.assertEqual(ddns.stat().st_mode & 0o777, 0o600)
        self.assertEqual(own_domain.load(self.state), {"domain": "mycar", "token": TOKEN})
        self.assertEqual(self.changes, ["own domain saved"])
        self.assertNotIn(TOKEN, self.page.render(""), "the token is never sent back")
        own = self.state / "tls" / "own"; own.mkdir(parents=True); (own / "fullchain.pem").write_text("x")
        self.save(action="save", domain="mycar", token="")   # empty: keep it, and the certificate
        self.assertEqual(own_domain.load(self.state)["token"], TOKEN)
        self.assertTrue(own.exists())
        with self.assertRaises(ValueError):
            self.save(action="save", domain="other", token="")   # a new name needs its token
        self.save(action="save", domain="other", token=TOKEN)
        self.assertFalse(own.exists(), "the old name's certificate goes")
        self.save(action="clear")
        self.assertFalse(ddns.exists())
        with self.assertRaises(ValueError):
            self.page.apply("/ddns", {"action": "clear", "nonce": "stale"})

    def test_public_ipv6_only_where_the_box_joins_a_hotspot(self):
        self.assertNotIn("name=ipv6", self.page.render(""), "box hotspot: no public address")
        self.save(action="save", domain="mycar", token=TOKEN, ipv6="on")
        self.assertNotIn("ipv6", own_domain.load(self.state))
        setup_wizard.save(self.state, {"done": True, "mode": "mifi"})
        (self.state / "car-hotspot.json").write_text('{"ssid": "MiFi", "passphrase": "12345678"}')
        self.assertIn("name=ipv6", self.page.render(""))
        self.save(action="save", domain="mycar", token="", ipv6="on")
        self.assertEqual(own_domain.load(self.state), {"domain": "mycar", "token": TOKEN, "ipv6": True})
        self.assertIn("checked", self.page.render("").split("name=ipv6")[1][:40])
        self.save(action="save", domain="mycar", token="")
        self.assertNotIn("ipv6", own_domain.load(self.state), "unticked: off")

    def test_reset_keeps_the_name(self):
        self.save(action="save", domain="mycar", token=TOKEN)
        (self.state / "receiver").mkdir()
        self.page.apply("/reset", {"nonce": self.nonce})
        self.assertIsNotNone(own_domain.load(self.state))

    def test_openwrt_only_shows_it(self):
        page = SettingsPage(self.state, lambda: dict(self.status, own_name="mycar.duckdns.org"), self.changes.append,
                            lambda: [], managed={"by": "OpenWrt", "admin": "/cgi-bin/luci"})
        with self.assertRaises(ValueError):
            page.apply("/ddns", {"action": "save", "domain": "x", "token": TOKEN, "nonce": page.pairing.nonce})
        html = page.render("")
        self.assertIn("mycar.duckdns.org", html)
        self.assertNotIn("name=token", html)


if __name__ == "__main__":
    unittest.main()
