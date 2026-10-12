"""OpenWrt adapter: Wi-Fi chosen from ubus/uci output, settings page without network setup."""
import ipaddress
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/onecloud'))
import platform_openwrt as ow
from settings_page import SettingsPage
import setup_wizard
from supervisor_openwrt import read_config

# Shape of `ubus call network.wireless status` on OpenWrt 25.12 (two radios, a 2.4 GHz AP listed first).
WIRELESS = {
    "radio0": {"up": True, "config": {"band": "2g", "channel": "1"}, "interfaces": [
        {"section": "default_radio0", "ifname": "phy0-ap0",
         "config": {"mode": "ap", "ssid": "Home-2G", "encryption": "psk2", "key": "secret-2g", "network": ["lan"]}}]},
    "radio1": {"up": True, "config": {"band": "5g", "channel": "auto"}, "interfaces": [
        {"section": "default_radio1", "ifname": "phy1-ap0",
         "config": {"mode": "ap", "ssid": "Car-5G", "encryption": "sae-mixed", "key": "secret-5g", "network": ["lan"]}},
        {"section": "wwan", "ifname": "phy1-sta0",
         "config": {"mode": "sta", "ssid": "Xpeng", "encryption": "psk2", "key": "car-pass", "network": ["wwan"]}}]},
}
LAN = {"up": True, "l3_device": "br-lan", "ipv4-address": [{"address": "192.168.1.1", "mask": 24}]}
FIREWALL = """firewall.@zone[0]=zone
firewall.@zone[0].name='lan'
firewall.@zone[0].network='lan'
firewall.@zone[0].input='ACCEPT'
firewall.@zone[1]=zone
firewall.@zone[1].name='wan'
firewall.@zone[1].network='wan' 'wan6' 'wwan'
firewall.@zone[1].input='REJECT'
firewall.@rule[0]=rule
firewall.@rule[0].network='wwan'
"""


class HandoffTests(unittest.TestCase):
    def test_security_types_the_iphone_can_be_handed(self):
        self.assertEqual(ow.handoff_security("none"), "open")
        self.assertEqual(ow.handoff_security("psk2+ccmp"), "wpa2")
        self.assertEqual(ow.handoff_security("psk-mixed"), "wpa2")
        self.assertEqual(ow.handoff_security("sae-mixed"), "wpa2")   # transition is announced as WPA2
        self.assertEqual(ow.handoff_security("sae"), "wpa3")
        for enterprise in ("wpa2", "wpa3", "wpa-mixed", "owe"):
            self.assertIsNone(ow.handoff_security(enterprise))

    def test_auto_prefers_an_up_5ghz_access_point(self):
        network, reason = ow.pick_network(ow.wifi_networks(WIRELESS))
        self.assertEqual((network["ssid"], network["ifname"], network["network"]), ("Car-5G", "phy1-ap0", "lan"))
        self.assertEqual(reason, "")

    def test_named_section_may_be_a_client_network(self):
        network, _ = ow.pick_network(ow.wifi_networks(WIRELESS), "wwan")
        self.assertEqual((network["mode"], network["ssid"]), ("sta", "Xpeng"))
        self.assertEqual(ow.pick_network(ow.wifi_networks(WIRELESS), "nope"), (None, "Wi-Fi section nope not found"))

    def test_waits_while_the_radio_is_down_and_skips_enterprise(self):
        down = json.loads(json.dumps(WIRELESS))
        for radio in down.values():
            radio["up"] = False
        network, reason = ow.pick_network(ow.wifi_networks(down))
        self.assertIsNone(network)
        self.assertIn("is not up yet", reason)
        enterprise = {"radio0": {"up": True, "config": {"band": "5g"}, "interfaces": [
            {"section": "x", "ifname": "phy0-ap0", "config": {"mode": "ap", "ssid": "Corp", "encryption": "wpa2",
                                                               "network": ["lan"]}}]}}
        self.assertIn("password type", ow.pick_network(ow.wifi_networks(enterprise))[1])

    def test_channel_address_and_firewall_parsing(self):
        self.assertEqual(ow.parse_channel("Interface phy1-ap0\n\tchannel 149 (5745 MHz), width: 80 MHz"), 149)
        self.assertIsNone(ow.parse_channel("Interface phy1-ap0\n\ttype AP"))
        self.assertEqual(ow.interface_address(LAN), ("br-lan", ipaddress.IPv4Interface("192.168.1.1/24")))
        self.assertEqual(ow.interface_address({"up": False}), (None, None))
        self.assertEqual(ow.zone_input(FIREWALL, "lan"), "ACCEPT")
        self.assertEqual(ow.zone_input(FIREWALL, "wwan"), "REJECT")
        self.assertIsNone(ow.zone_input(FIREWALL, "guest"))

    def test_handoff_reads_ubus_and_the_live_channel(self):
        calls = {"network.wireless": WIRELESS, "network.interface.lan": LAN}

        class Done:
            def __init__(self, stdout): self.stdout, self.returncode = stdout, 0

        def run(argv, **_):
            if argv[0] == "ubus":
                return Done(json.dumps(calls.get(argv[2], {})))
            return Done("Interface phy1-ap0\n\tchannel 36 (5180 MHz), width: 80 MHz")

        with tempfile.TemporaryDirectory() as tmp, patch.object(ow.subprocess, "run", run):
            network, reason = ow.OpenWrtPlatform(run=Path(tmp)).handoff()
        self.assertEqual(reason, "")
        self.assertEqual((network["channel"], network["security"], network["device"], str(network["address"].ip)),
                         (36, "wpa2", "br-lan", "192.168.1.1"))


class ConfigTests(unittest.TestCase):
    def test_uci_options_override_defaults(self):
        config = read_config("dashplay.main=dashplay\ndashplay.main.wifi='wwan'\ndashplay.main.settings_port='18090'\n")
        self.assertEqual((config["wifi"], config["settings_port"], config["java"]),
                         ("wwan", "18090", "/usr/lib/dashplay-jre/bin/java"))
        self.assertEqual(read_config("")["wifi"], "auto")


class ManagedPageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "state"; self.state.mkdir()
        setup_wizard.save(self.state, {"done": True, "mode": "box_hotspot"})
        self.status = {"mode": "openwrt", "ssid": "Car-5G", "home": "http://192.168.1.1:18080/", "identity": True,
                       "entries": ["https://dashplay.lan:18443/", "https://192.168.1.1:18443/"]}
        self.page = SettingsPage(self.state, lambda: self.status, lambda reason: None, lambda: [], None,
                                 Path(tmp.name) / "wired.json",
                                 managed={"by": "OpenWrt", "admin": "/cgi-bin/luci/admin/network/wireless"})

    def test_shows_phones_and_router_wifi_without_network_setup(self):
        html = self.page.render("")
        for text in ("交给 iPhone 的 WiFi", "Car-5G", "在 OpenWrt 后台设置 WiFi", "https://dashplay.lan:18443/",
                     "添加"):
            self.assertIn(text, html)
        for text in ("盒子热点", "重新进行首次设置", "使用方式", "第 1 步"):
            self.assertNotIn(text, html)

    def test_network_changes_are_refused(self):
        nonce = self.page.pairing.nonce
        for path in ("/setup", "/reset", "/car", "/hotspot", "/retry"):
            with self.assertRaises(ValueError):
                self.page.apply(path, {"nonce": nonce})

    def test_firewall_warning_is_shown(self):
        self.status["warning"] = "防火墙区域拒绝入站连接"
        self.assertIn("防火墙区域拒绝入站连接", self.page.render(""))


if __name__ == "__main__":
    unittest.main()
