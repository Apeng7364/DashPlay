"""Offline checks use a real local HTTP server, never the car or DuckDNS."""
import http.client
import ipaddress
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/onecloud'))
import setup_wizard as wizard
from settings_page import SettingsPage
from platform_debian import DebianPlatform
import supervisor


class TeslaOfflineTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        (self.state / 'setup.json').write_text('{"done":true,"mode":"box_hotspot"}')
        (self.state / 'options.json').write_text('{"tesla":true}')

    def test_defaults_and_completed_setup_never_reopens(self):
        for mode, iphone in [('car_hotspot', 'wired'), ('mifi', 'any'), ('box_hotspot', 'any')]:
            (self.state / 'setup.json').write_text(json.dumps({'done': True, 'mode': mode}))
            loaded = wizard.load(self.state)
            self.assertTrue(loaded['done'])
            self.assertEqual(loaded['iphone'], iphone)
        self.assertEqual(wizard.options(self.state)['car_internet'], 'none')
        self.assertTrue(wizard.wireless({'mode': 'box_hotspot', 'iphone': 'wireless'},
                                      {'car_internet': 'phone_hotspot', 'uplink_phone': 'same'}))
        self.assertTrue(wizard.wireless({'mode': 'box_hotspot', 'iphone': 'any'}, {}))

    def test_dns_answer_scope(self):
        names = supervisor.hotspot_names(self.state, 'example.duckdns.org', 'box_hotspot')
        self.assertEqual(set(names), set(wizard.TESLA_CHECK_NAMES) | {'example.duckdns.org'})
        self.assertEqual(supervisor.hotspot_names(self.state, 'example.duckdns.org', 'mifi'), [])
        platform = DebianPlatform('wlan-test', self.state)
        with patch.object(platform, 'start') as start:
            platform.start_dhcp(ipaddress.ip_interface('100.96.0.1/24'), False, names)
            argv = start.call_args.args[1]
        for flag in ('--dhcp-option=3', '--dhcp-option=6,100.96.0.1', '--no-hosts', '--no-resolv'):
            self.assertIn(flag, argv)
        self.assertNotIn('--server', ' '.join(argv))

    def test_http_exact_reply_precedes_access_and_captive_checks(self):
        status = {'mode': 'ap', 'home': 'http://100.96.0.1/'}
        page = SettingsPage(self.state, lambda: status, lambda _: None, lambda: [])
        page.start(0); self.addCleanup(page.stop)
        port = page.server.server_address[1]
        def get(host):
            conn = http.client.HTTPConnection('127.0.0.1', port)
            conn.request('GET', '/online/status.html', headers={'Host': host})
            response = conn.getresponse(); data = response.read(); headers = dict(response.getheaders()); conn.close()
            return response.status, headers, data
        with patch.object(page, 'captive_redirect', side_effect=AssertionError('must precede captive')):
            code, headers, body = get('CONNMAN.VN.CLOUD.TESLA.CN:80')
        self.assertEqual(code, 200)
        self.assertEqual(headers['X-ConnMan-Status'], 'online')
        self.assertEqual(headers['Content-Type'], 'text/html')
        self.assertEqual(body, b'<html>\n<head>\n</head>\n<body>\n</body>\n</html>\n\n')
        for name in wizard.TESLA_CHECK_NAMES[1:]:
            self.assertEqual(get(name)[0], 200)
        self.assertEqual(get('other.example')[0], 404)
        status['uplink'] = {'phase':'online'}
        self.assertEqual(get(wizard.TESLA_CHECK_NAMES[0])[0], 404, 'real upstream must never receive a fake HTTP check')
        status.pop('uplink')
        (self.state / 'options.json').write_text('{"tesla":false}')
        self.assertEqual(get(wizard.TESLA_CHECK_NAMES[0])[0], 404)
