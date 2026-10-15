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
        self.assertEqual(fields['lan.dhcp_option'], ['42,192.0.2.5', '3', '6,100.96.0.1'])
        self.assertEqual(fields['dns.noresolv'], '1')
        self.assertEqual(fields['dns.logqueries'], '1')
        self.assertIsNone(fields['dns.logfacility'])
        self.assertIsNone(fields['dns.server'])
        self.assertIn('/connman.vn.cloud.tesla.cn/100.96.0.2', fields['dns.address'])
        self.assertIn('/example.duckdns.org/100.96.0.1', fields['dns.address'])
        with self.assertRaises(ValueError):
            tesla.offline_fields(dict(SECTIONS, second={'.type':'dnsmasq'}), 'lan', '100.96.0.1/24', '')
        rules = tesla.redirect_rules('br-lan', alias, 18080)
        self.assertIn('ip daddr 100.96.0.2 tcp dport 80 redirect to :18080', rules)
        self.assertIn('iifname "br-lan"', rules)
        self.assertNotIn('uhttpd', rules)

    def test_offline_online_restore_and_crash_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Mock(return_value=Mock(stdout=json.dumps({'values': SECTIONS}), returncode=0))
            controller = tesla.OfflineChecks(directory, run)
            status = {}
            controller.update(CONFIG, NETWORK, status, online=False)
            self.assertTrue(status['tesla_offline'])
            saved = json.loads(controller.path.read_text())
            self.assertEqual(saved['before']['dns.server'], ['192.0.2.53'])
            self.assertEqual(controller.path.stat().st_mode & 0o777, 0o600)
            controller.update(CONFIG, NETWORK, status, online=True)
            self.assertFalse(status['tesla_offline'])
            self.assertFalse(controller.path.exists())
            commands = [c.args[0] for c in run.call_args_list]
            self.assertIn(['uci', 'add_list', 'dhcp.dns.server=192.0.2.53'], commands)
            self.assertIn(['nft', 'delete', 'table', 'ip', tesla.TABLE], commands)
            controller.enable(NETWORK, CONFIG['name'], 18080)
            tesla.OfflineChecks(directory, run).restore()
            self.assertFalse(controller.path.exists(), 'recovery after a supervisor crash')

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

    def test_real_wan_and_other_car_never_fake_checks_even_when_enabled(self):
        with tempfile.TemporaryDirectory() as root:
            controller = tesla.OfflineChecks(Path(root), Mock())
            with patch.object(controller, 'enable') as enable:
                for choice in ('auto', '1'):
                    status = {}
                    controller.update(dict(CONFIG, answer_tesla_checks=choice), NETWORK, status, online=True)
                    self.assertFalse(status['tesla_offline'])
                controller.update(dict(CONFIG, tesla_mode='0'), NETWORK, {}, online=False)
                controller.update(CONFIG, dict(NETWORK, mode='sta'), {}, online=False)
                enable.assert_not_called()

    def test_dns_logging_is_bounded_offline_only_and_restores_user_options(self):
        with tempfile.TemporaryDirectory() as root:
            sections = dict(SECTIONS, dns=dict(SECTIONS['dns'], logqueries='0', logfacility='/tmp/previous-dns.log'))
            run = Mock(return_value=Mock(stdout=json.dumps({'values': sections}), returncode=0))
            platform = Mock()
            platform.start.return_value.poll.return_value = None
            controller = tesla.OfflineChecks(root, run, platform)
            status = {}
            controller.update(CONFIG, NETWORK, status, online=True)
            platform.start.assert_not_called()
            controller.update(CONFIG, NETWORK, status, online=False)
            controller.update(CONFIG, NETWORK, status, online=False)
            name, argv = platform.start.call_args.args
            self.assertEqual(name, 'tesla-dns-log')
            self.assertEqual(argv, ['logread','-f','-e','dnsmasq','-F',str(Path(root)/'diag/tesla-dns.log'),'-S','1024'])
            self.assertEqual(status['tesla_dns_log'], 'recording')
            self.assertEqual(platform.start.call_count, 1)
            controller.update(CONFIG, NETWORK, status, online=True)
            self.assertEqual(status['tesla_dns_log'], 'stopped')
            platform.stop_child.assert_called_with('tesla-dns-log')
            commands = [c.args[0] for c in run.call_args_list]
            self.assertIn(['uci','set','dhcp.dns.logqueries=0'], commands)
            self.assertIn(['uci','set','dhcp.dns.logfacility=/tmp/previous-dns.log'], commands)

    def test_missing_or_exited_dns_logger_does_not_disable_offline_checks(self):
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
