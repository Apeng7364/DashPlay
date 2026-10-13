"""OpenWrt adapter: Wi-Fi chosen from ubus/uci output, settings page without network setup."""
import ipaddress
import json
import re
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/onecloud'))
import platform_openwrt as ow
from settings_page import SettingsPage
import setup_wizard
from supervisor_openwrt import name_changes, read_config, trusted_networks

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


class TrustedNetworkTests(unittest.TestCase):
    def test_only_the_network_of_the_wifi_handed_to_the_iphone(self):
        # Every interface used to count, the WAN too (F50: sipa_eth0 10.3.178.29/8, the whole 10/8);
        # only the firewall kept it out (audit 2026-10-13, A5).
        lan = {"network": "lan", "address": ipaddress.IPv4Interface("192.168.77.1/24")}
        self.assertEqual(trusted_networks(lan), [ipaddress.IPv4Network("192.168.77.0/24")])
        self.assertNotIn(ipaddress.ip_address("10.3.178.30"), trusted_networks(lan)[0])
        car = {"network": "wwan", "address": ipaddress.IPv4Interface("192.168.43.123/24")}
        self.assertEqual(trusted_networks(car), [ipaddress.IPv4Network("192.168.43.0/24")])


# `ip -6 -o addr show scope global` and `ip -6 -o route show default` on the F50 (2026-10-13): the public
# address is on the mobile modem; the LAN has a unique local prefix and hands the modem's /64 on by relay.
F50_ADDR = """13: sipa_eth0    inet6 2408:8409:8500:5dd4:3439:f3ff:fe2a:e97f/64 scope global dynamic mngtmpaddr \\       valid_lft forever preferred_lft forever
34: br-lan    inet6 fd9e:30a4:d99::1/60 scope global noprefixroute \\       valid_lft forever preferred_lft forever
"""
F50_ROUTE = """default via fe80::1 dev sipa_eth0 proto ra metric 1024 expires 62108sec pref medium
default via fe80::1 dev sipa_eth0 proto static metric 4096 pref medium
"""


class PublicIpv6Tests(unittest.TestCase):
    def test_the_modems_address_when_the_lan_has_only_a_local_prefix(self):
        self.assertEqual(ow.public_ipv6(F50_ADDR, F50_ROUTE, "br-lan"), "2408:8409:8500:5dd4:3439:f3ff:fe2a:e97f")

    def test_an_address_on_the_lan_comes_first(self):
        delegated = F50_ADDR + "34: br-lan    inet6 240e:3b0:1234:5600::1/64 scope global dynamic noprefixroute \\ valid_lft 3000sec\n"
        self.assertEqual(ow.public_ipv6(delegated, F50_ROUTE, "br-lan"), "240e:3b0:1234:5600::1")

    def test_local_temporary_and_unready_addresses_do_not_count(self):
        addr = """13: wan@eth0    inet6 2408:8409::aa/64 scope global temporary dynamic \\ valid_lft 3000sec
13: wan@eth0    inet6 2408:8409::bb/64 scope global deprecated dynamic \\ valid_lft 3000sec
13: wan@eth0    inet6 2408:8409::cc/64 scope global tentative \\ valid_lft 3000sec
34: br-lan    inet6 fd9e:30a4:d99::1/60 scope global noprefixroute \\ valid_lft forever
"""
        self.assertIsNone(ow.public_ipv6(addr, "default via fe80::1 dev wan proto ra\n", "br-lan"))
        self.assertEqual(ow.public_ipv6(addr + "13: wan@eth0    inet6 2408:8409::dd/64 scope global dynamic\n",
                                        "default via fe80::1 dev wan proto ra\n", "br-lan"), "2408:8409::dd")

    def test_none_without_an_ipv6_route_out(self):
        self.assertIsNone(ow.public_ipv6(F50_ADDR, "", "br-lan"))
        self.assertIsNone(ow.public_ipv6("", "", "br-lan"))


DHCP = """dhcp.cfg01411c=dnsmasq
dhcp.cfg01411c.domain='lan'
dhcp.cfg01411c.local='/lan/'
dhcp.lan=dhcp
"""
NAME, LAN_IP, PUBLIC = "car.duckdns.org", "192.168.77.1", "2408:8409:8500:5dd4:3439:f3ff:fe2a:e97f"


def applied(uci, changes):
    """The `uci show dhcp` that the changes leave behind (enough of uci for these records)."""
    lines = uci.splitlines()
    for change in changes:
        verb, arg = change[0], change[1] if len(change) > 1 else ""
        key, _, value = arg.partition("=")
        if verb == "set":
            lines = [l for l in lines if not l.startswith(key + "=")]
            lines.append(f"{key}={value}" if "." not in key[5:] else f"{key}='{value}'")
        elif verb == "delete":
            lines = [l for l in lines if not (l.startswith(key + "=") or l.startswith(key + "."))]
        elif verb in ("add_list", "del_list"):
            old = next((l for l in lines if l.startswith(key + "=")), None)
            values = re.findall(r"'([^']*)'", old) if old else []
            values = values + [value] if verb == "add_list" else [v for v in values if v != value]
            lines = [l for l in lines if l != old] + ([key + "=" + " ".join(f"'{v}'" for v in values)] if values else [])
    return "\n".join(lines) + "\n"


class NameRecordTests(unittest.TestCase):
    """uci dashplay.main.public_ipv6: the name also (1) or only (2) answers with the router's public IPv6."""

    def test_the_lan_address_by_default(self):
        changes = name_changes(DHCP, NAME, LAN_IP)
        after = applied(DHCP, changes)
        self.assertIn(f"dhcp.dashplay.ip='{LAN_IP}'", after)
        self.assertNotIn("dashplay6", after)
        self.assertEqual(name_changes(after, NAME, LAN_IP), [], "nothing to do when current")

    def test_beside_the_lan_address(self):
        after = applied(DHCP, name_changes(DHCP, NAME, LAN_IP, PUBLIC))
        self.assertIn(f"dhcp.dashplay.ip='{LAN_IP}'", after)
        self.assertIn(f"dhcp.dashplay6.ip='{PUBLIC}'", after)
        self.assertNotIn(f"/{NAME}/", after, "both types are answered locally")

    def test_instead_of_the_lan_address_and_back(self):
        start = applied(DHCP, name_changes(DHCP, NAME, LAN_IP))
        alone = applied(start, name_changes(start, NAME, LAN_IP, PUBLIC, only_public=True))
        self.assertNotIn("dhcp.dashplay=", alone)
        self.assertIn(f"dhcp.dashplay6.ip='{PUBLIC}'", alone)
        self.assertIn(f"dhcp.cfg01411c.server='/{NAME}/'", alone, "no A record from upstream")
        self.assertEqual(name_changes(alone, NAME, LAN_IP, PUBLIC, only_public=True), [])
        back = applied(alone, name_changes(alone, NAME, LAN_IP))
        self.assertIn(f"dhcp.dashplay.ip='{LAN_IP}'", back)
        self.assertNotIn("dashplay6", back)
        self.assertNotIn(f"/{NAME}/", back)

    def test_only_public_without_a_public_address_keeps_the_lan_address(self):
        after = applied(DHCP, name_changes(DHCP, NAME, LAN_IP, None, only_public=True))
        self.assertIn(f"dhcp.dashplay.ip='{LAN_IP}'", after)
        self.assertNotIn(f"/{NAME}/", after)

    def test_a_new_prefix_or_name_replaces_the_old_records(self):
        alone = applied(DHCP, name_changes(DHCP, NAME, LAN_IP, PUBLIC, only_public=True))
        moved = applied(alone, name_changes(alone, NAME, LAN_IP, "2408:8409:8500:77::1", only_public=True))
        self.assertIn("dhcp.dashplay6.ip='2408:8409:8500:77::1'", moved)
        renamed = applied(moved, name_changes(moved, "dashplay.lan", LAN_IP, "2408:8409:8500:77::1", only_public=True))
        self.assertNotIn(f"/{NAME}/", renamed)
        self.assertIn("'/dashplay.lan/'", renamed)


class ConfigTests(unittest.TestCase):
    def test_idle_release_defaults_to_five_minutes_and_can_be_turned_off(self):
        self.assertEqual(read_config("")["idle_release"], "300")
        self.assertEqual(read_config("dashplay.main.idle_release='0'\n")["idle_release"], "0")

    def test_the_public_ipv6_address_is_off_by_default(self):
        self.assertEqual(read_config("")["public_ipv6"], "0")

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
        # The settings page runs on 18080; a relative link stayed there and got a 404 (review 2026-10-12).
        self.assertIn('href="http://192.168.1.1/cgi-bin/luci/admin/network/wireless"', html)

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
