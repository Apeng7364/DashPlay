import ipaddress
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/onecloud'))
import tesla_openwrt as tesla

SECTIONS = {'dns': {'.type': 'dnsmasq', 'server': ['192.0.2.53'], 'address': ['/old.example/192.0.2.1']},
            'lan': {'.type': 'dhcp', 'interface': 'lan', 'dhcp_option': ['3,192.0.2.1', '42,192.0.2.5']}}
NETWORK = {'network': 'lan', 'device': 'br-lan', 'address': ipaddress.ip_interface('100.96.0.1/24'), 'mode': 'ap'}
CONFIG = {'name': 'example.duckdns.org', 'settings_port': '18080', 'tesla_mode': '1'}


class OpenWrtOfflineTests(unittest.TestCase):
    def test_dns_and_dhcp_plan(self):
        alias, fields = tesla.offline_fields(SECTIONS, 'lan', '100.96.0.1/24', CONFIG['name'])
        self.assertEqual(alias, '100.96.0.2')
        # The box is the router: Teslas left a network without a default route ~30 s after joining (2026-10-16).
        self.assertEqual(fields['lan.dhcp_option'], ['42,192.0.2.5', '3,100.96.0.1', '6,100.96.0.1'])
        # Only the check names are local; the real upstream settings must stay untouched (2026-10-25).
        for key in ('dns.server', 'dns.noresolv', 'dns.nohosts', 'dns.resolvfile'):
            self.assertNotIn(key, fields)
        self.assertEqual(fields['dns.logqueries'], '1')
        self.assertIsNone(fields['dns.logfacility'])
        self.assertIn('/connman.vn.cloud.tesla.cn/100.96.0.2', fields['dns.address'])
        self.assertIn('/example.duckdns.org/100.96.0.1', fields['dns.address'])
        with self.assertRaises(ValueError):
            tesla.offline_fields(dict(SECTIONS, second={'.type':'dnsmasq'}), 'lan', '100.96.0.1/24', '')
        rules = tesla.redirect_rules('br-lan', alias, 18080)
        self.assertIn('ip daddr 100.96.0.2 tcp dport 80 redirect to :18080', rules)
        self.assertIn('iifname "br-lan"', rules)
        self.assertNotIn('uhttpd', rules)

    def test_lan_once_upstream_switch_and_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Mock(return_value=Mock(stdout=json.dumps({'values': SECTIONS}), returncode=0))
            controller = tesla.OfflineChecks(directory, run)
            status = {}
            controller.update(CONFIG, NETWORK, status, online=False)
            self.assertTrue(controller.active)
            self.assertTrue(status['tesla_offline'])
            self.assertEqual(status['tesla_upstream'], 'offline')
            saved = json.loads(controller.path.read_text())
            self.assertEqual(saved['before']['dns.address'], ['/old.example/192.0.2.1'])
            self.assertNotIn('dns.server', saved['before'])
            self.assertEqual(controller.path.stat().st_mode & 0o777, 0o600)
            built = len(run.call_args_list)
            controller.update(CONFIG, NETWORK, status, online=True)
            # Upstream changes only update the status: the LAN, DNS and DHCP stay as they are.
            self.assertTrue(controller.active)
            self.assertTrue(status['tesla_offline'])
            self.assertEqual(status['tesla_upstream'], 'online')
            self.assertTrue(controller.path.exists())
            self.assertEqual([c.args[0] for c in run.call_args_list[built:]
                              if c.args[0][0] in ('uci', '/etc/init.d/dnsmasq', 'ip', 'nft')], [])
            # Real network only: no LAN baseline at all.
            controller.update(dict(CONFIG, answer_tesla_checks='0'), NETWORK, status)
            self.assertFalse(controller.active)
            self.assertFalse(status['tesla_offline'])
            self.assertFalse(controller.path.exists())
            commands = [c.args[0] for c in run.call_args_list]
            self.assertIn(['uci', 'add_list', 'dhcp.dns.address=/old.example/192.0.2.1'], commands)
            self.assertIn(['nft', 'delete', 'table', 'ip', tesla.TABLE], commands)
            controller.enable(NETWORK, CONFIG['name'], 18080)
            tesla.OfflineChecks(directory, run).restore()
            self.assertFalse(controller.path.exists(), 'recovery after a supervisor crash')

    def test_status_has_no_reconnect_advice(self):
        with tempfile.TemporaryDirectory() as root:
            run = Mock(return_value=Mock(stdout=json.dumps({'values': SECTIONS}), returncode=0))
            controller = tesla.OfflineChecks(root, run)
            status = {}
            controller.update(CONFIG, NETWORK, status, online=False)
            self.assertNotIn('tesla_network_change', status)
            self.assertIn('局域网 CarPlay', status['tesla_internet'])
            controller.update(CONFIG, NETWORK, status, online=True)
            self.assertEqual(status['tesla_upstream'], 'online')
            self.assertIn('上游在线', status['tesla_internet'])

    def test_baseline_failure_keeps_the_carplay(self):
        with tempfile.TemporaryDirectory() as root:
            run = Mock(return_value=Mock(stdout=json.dumps({'values': SECTIONS}), returncode=0))
            controller = tesla.OfflineChecks(root, run)
            with patch.object(controller, 'enable', side_effect=OSError('synthetic failure')):
                status = {}
                controller.update(CONFIG, NETWORK, status)   # must not raise: CarPlay keeps running
            self.assertFalse(controller.active)
            self.assertIn('tesla_error', status)

    def test_failure_restores_saved_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Mock(return_value=Mock(stdout=json.dumps({'values': SECTIONS}), returncode=0))
            controller = tesla.OfflineChecks(directory, run)
            original = controller.command
            def command(argv, **kw):
                if argv[:2] == ['nft', '-f']:
                    raise OSError('synthetic failure')
                return original(argv, **kw)
            with patch.object(controller, 'command', side_effect=command), self.assertRaises(OSError):
                controller.enable(NETWORK, CONFIG['name'], 18080)
            self.assertFalse(controller.path.exists())
            self.assertFalse(controller.active)

    def test_auto_wan_probe_never_uses_offline_dns_or_tailscale(self):
        interface = {'interface':'wan', 'up':True, 'route':[{'target':'0.0.0.0','mask':0}],
                     'ipv4-address':[{'address':'192.0.2.9'}]}
        run = Mock(return_value=Mock(stdout=json.dumps({'interface':[interface]})))
        connect = Mock(); connect.return_value.__enter__ = Mock(); connect.return_value.__exit__ = Mock()
        self.assertTrue(tesla.wan_available(run, connect))
        self.assertEqual(connect.call_args.kwargs['source_address'], ('192.0.2.9', 0))
        connect.side_effect = OSError('offline')
        self.assertFalse(tesla.wan_available(run, connect))
        interface['interface'] = 'tailscale'
        run.return_value.stdout = json.dumps({'interface':[interface]})
        connect.reset_mock()
        self.assertFalse(tesla.wan_available(run, connect)); connect.assert_not_called()

    def test_wan_counts_as_up_when_only_a_domestic_target_answers(self):
        # 2026-10-15, F50 with a working SIM: the carrier dropped 1.1.1.1:443 (and 8.8.8.8:443) while 223.5.5.5:443
        # answered, so a probe of 1.1.1.1 alone kept the box answering Tesla's checks with no upstream DNS.
        interface = {'interface':'wan', 'up':True, 'route':[{'target':'0.0.0.0','mask':0}],
                     'ipv4-address':[{'address':'192.0.2.9'}]}
        run = Mock(return_value=Mock(stdout=json.dumps({'interface':[interface]})))
        def connect(target, timeout, source_address):
            if target[0] in ('1.1.1.1', '8.8.8.8'):
                raise OSError('timed out')
            conn = Mock(); conn.__enter__ = Mock(return_value=conn); conn.__exit__ = Mock(return_value=False)
            return conn
        self.assertTrue(tesla.wan_available(run, connect))

    def test_auto_and_always_build_the_lan_even_with_upstream(self):
        with tempfile.TemporaryDirectory() as root:
            controller = tesla.OfflineChecks(Path(root), Mock())
            with patch.object(controller, 'enable',
                              side_effect=lambda *a, **k: setattr(controller, 'active', True)) as enable:
                for choice in ('auto', '1'):
                    controller.active = False   # a fresh controller for each choice
                    status = {}
                    controller.update(dict(CONFIG, answer_tesla_checks=choice), NETWORK, status, online=True)
                    self.assertTrue(status['tesla_offline'])
                    self.assertTrue(enable.called)
                    enable.reset_mock()
                controller.update(dict(CONFIG, answer_tesla_checks='0'), NETWORK, {}, online=True)
                controller.update(dict(CONFIG, tesla_mode='0'), NETWORK, {}, online=False)
                controller.update(CONFIG, dict(NETWORK, mode='sta'), {}, online=False)
                enable.assert_not_called()

    def test_dns_logging_is_bounded_and_restores_user_options(self):
        with tempfile.TemporaryDirectory() as root:
            sections = dict(SECTIONS, dns=dict(SECTIONS['dns'], logqueries='0', logfacility='/tmp/previous-dns.log'))
            run = Mock(return_value=Mock(stdout=json.dumps({'values': sections}), returncode=0))
            platform = Mock()
            platform.start.return_value.poll.return_value = None
            controller = tesla.OfflineChecks(root, run, platform)
            status = {}
            controller.update(CONFIG, NETWORK, status, online=True)
            self.assertEqual(status['tesla_dns_log'], 'recording')
            name, argv = platform.start.call_args.args
            self.assertEqual(name, 'tesla-dns-log')
            self.assertEqual(argv, ['logread','-f','-e','dnsmasq','-F',str(Path(root)/'diag/tesla-dns.log'),'-S','1024'])
            controller.update(CONFIG, NETWORK, status, online=False)
            self.assertEqual(platform.start.call_count, 1)
            controller.restore()
            self.assertEqual(platform.stop_child.call_args.args, ('tesla-dns-log',))
            commands = [c.args[0] for c in run.call_args_list]
            self.assertIn(['uci','set','dhcp.dns.logqueries=0'], commands)
            self.assertIn(['uci','set','dhcp.dns.logfacility=/tmp/previous-dns.log'], commands)

    def test_missing_or_exited_dns_logger_does_not_disable_the_lan(self):
        with tempfile.TemporaryDirectory() as root:
            run = Mock(return_value=Mock(stdout=json.dumps({'values': SECTIONS}), returncode=0))
            platform = Mock(); platform.start.side_effect = FileNotFoundError('synthetic missing logger')
            controller = tesla.OfflineChecks(root, run, platform)
            status = {}
            controller.update(CONFIG, NETWORK, status, online=False)
            self.assertTrue(controller.active)
            self.assertEqual(status['tesla_dns_log'], 'error: FileNotFoundError')
            platform.start.side_effect = None
            platform.start.return_value.poll.return_value = None
            controller.update(CONFIG, NETWORK, status, online=False)
            self.assertEqual(status['tesla_dns_log'], 'recording')
            calls = platform.start.call_count
            platform.start.return_value.poll.return_value = 1
            controller.update(CONFIG, NETWORK, status, online=False)
            self.assertEqual(platform.start.call_count, calls + 1)
            self.assertTrue(controller.active)
            controller.restore()


if __name__ == '__main__':
    unittest.main()
