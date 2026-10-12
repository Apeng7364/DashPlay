"""Adapter contract tests: only temporary paths and mocked system commands, never live networking."""
import ipaddress
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/onecloud'))
from platform_api import Platform, get_platform
from platform_debian import AP_VIF, DebianPlatform, ap_width, derived_address, hostapd_config, machine_key, supports_sta_ap, wifi_driver
from setup_wizard import Scanner


class PlatformTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.platform = get_platform('wlan-test', run=self.root, sta_network=self.root / 'sta.network')

    def test_adapter_implements_contract_without_import_side_effects(self):
        self.assertIsInstance(self.platform, DebianPlatform)
        for name in Platform.__dict__:
            if not name.startswith('_'):
                self.assertTrue(callable(getattr(self.platform, name)), name)
        self.assertEqual(self.platform.children, [])

    @patch('platform_debian.subprocess.run')
    def test_scan_and_scanner_cache(self, run):
        run.return_value = Mock(returncode=0, stdout='BSS 00\n\tSSID: Test\n\tsignal: -40 dBm\n\tfreq: 5180\n')
        scan = Scanner('wlan-test', 'Box', lambda: 'ap', platform=self.platform)
        self.assertEqual(scan.results()[0]['ssid'], 'Test')
        scan.results()
        # The first list takes two scans (one misses hotspots, see the next test), later ones come from the cache.
        self.assertEqual(run.call_args_list, [call(['iw', 'dev', 'wlan-test', 'scan', 'ap-force'], capture_output=True,
                                                   text=True, timeout=30)] * 2)
        self.platform.scan(ap=True, frequency=5180)
        self.assertEqual(run.call_args.args[0][-3:], ['freq', '5180', 'ap-force'])
        run.side_effect = subprocess.TimeoutExpired('iw', 30)
        self.assertEqual([n['ssid'] for n in scan.results(fresh=True)], ['Test'], 'a failed scan keeps what was just seen')

    def test_scanner_adds_up_scans_that_each_miss_some_hotspots(self):
        # 2026-10-12, N1 with its own hotspot up: four scans in a row listed 9, 8, 11 and 11 names, 12 together.
        clock = [1000.0]
        results = [[{'ssid': 'Car', 'signal': -50, 'band': '5 GHz'}, {'ssid': 'A', 'signal': -70, 'band': '2.4 GHz'}],
                   [{'ssid': 'B', 'signal': -60, 'band': '5 GHz'}, {'ssid': 'A', 'signal': -65, 'band': '2.4 GHz'}],
                   [{'ssid': 'Car', 'signal': -45, 'band': '5 GHz'}]]
        platform = Mock(scan_networks=Mock(side_effect=lambda own, ap=False: results.pop(0)))
        scan = Scanner('wlan-test', 'Box', lambda: 'ap', platform=platform)
        with patch('setup_wizard.time.monotonic', side_effect=lambda: clock[0]):
            self.assertEqual([(n['ssid'], n['signal']) for n in scan.results()], [('Car', -50), ('B', -60), ('A', -65)])
            clock[0] += 30
            self.assertEqual([(n['ssid'], n['signal']) for n in scan.results(fresh=True)], [('Car', -45), ('B', -60), ('A', -65)])
            # Not seen for over two minutes: gone from the list.
            results.append([{'ssid': 'Car', 'signal': -44, 'band': '5 GHz'}])
            clock[0] += 125
            self.assertEqual([n['ssid'] for n in scan.results(fresh=True)], ['Car'])

    @patch('platform_debian.subprocess.run')
    def test_bluetooth_and_link_observation(self, run):
        run.return_value = Mock(returncode=0, stdout='Connected to synthetic\n freq: 5180\n')
        self.assertTrue(self.platform.power_bluetooth())
        self.assertEqual([c.args[0] for c in run.call_args_list],
                         [['btmgmt', '--index', '0', 'info'], ['bluetoothctl', 'power', 'on'], ['bluetoothctl', 'devices', 'Connected'],
                          ['bluetoothctl', 'system-alias', 'DashPlay']])
        self.assertTrue(self.platform.connected())
        self.assertEqual(self.platform.frequency(), 5180)
        run.return_value = Mock(returncode=1, stdout='Not connected.')
        self.assertFalse(self.platform.power_bluetooth())
        self.assertFalse(self.platform.connected())
        self.assertIsNone(self.platform.frequency())

    @patch('platform_debian.subprocess.run')
    def test_bluetooth_start_clears_a_leftover_phone_link(self, run):
        run.side_effect = lambda argv, **_: Mock(returncode=0, stdout=(
            'Device 02:00:00:00:00:55 Phone\n' if argv[1:] == ['devices', 'Connected'] else ''))
        self.assertTrue(self.platform.power_bluetooth())
        self.assertEqual([c.args[0][1:] for c in run.call_args_list],
                         [['--index', '0', 'info'], ['power', 'on'], ['devices', 'Connected'], ['power', 'off'], ['power', 'on'],
                          ['system-alias', 'DashPlay']])

    def test_machine_key_prefers_the_emmc_over_machine_id(self):
        mmc = self.root / 'mmc'
        for name, kind, cid in (('mmc0:0001', 'SDIO', ''), ('mmc2:0001', 'MMC', 'cid-of-the-emmc')):
            (mmc / name).mkdir(parents=True)
            (mmc / name / 'type').write_text(kind + '\n')
            (mmc / name / 'cid').write_text(cid + '\n')
        (self.root / 'machine-id').write_text('machine-id-test\n')
        self.assertEqual(machine_key(mmc, self.root / 'machine-id'), 'cid-of-the-emmc')
        self.assertEqual(machine_key(self.root / 'none', self.root / 'machine-id'), 'machine-id-test')
        a, b = derived_address('k1', 'wifi'), derived_address('k1', 'bluetooth')
        self.assertRegex(a, r'^[0-9a-f]{2}(:[0-9a-f]{2}){5}$')
        self.assertEqual(int(a[:2], 16) & 0x03, 0x02, 'locally administered unicast')
        self.assertNotEqual(a, b)
        self.assertEqual(a, derived_address('k1', 'wifi'), 'the same box keeps its address')
        self.assertNotEqual(a, derived_address('k2', 'wifi'), 'another box gets another one')

    @patch('platform_debian.subprocess.run')
    def test_vendor_default_addresses_become_unique_per_box(self, run):
        # Phicomm N1: every unit has the BCM43455 nvram MAC and the BCM4345C0 Bluetooth default.
        net = self.root / 'net/wlan-test'
        net.mkdir(parents=True)
        (net / 'address').write_text('b8:27:eb:74:f2:6c\n')
        # The kernel's own view (btmgmt) works before bluetoothd is up early in boot. BlueZ drops the adapter
        # when its address changes and lists it again under the new one a moment later.
        shown = ['No default controller available\n', f'Controller {derived_address("box1", "bluetooth").upper()} (public)\n']
        def fake(argv, **_):
            if argv == ['btmgmt', '--index', '0', 'info']:
                return Mock(returncode=0, stdout='hci0:\tPrimary controller\n\taddr 43:45:C0:00:1F:AC version 9\n')
            if argv == ['bluetoothctl', 'show']:
                return Mock(returncode=0, stdout=shown.pop(0) if len(shown) > 1 else shown[0])
            return Mock(returncode=0, stdout='')
        run.side_effect = fake
        with patch('platform_debian.SYS_NET', self.root / 'net'), patch('platform_debian.machine_key', return_value='box1'), \
             patch('platform_debian.time.sleep'):
            self.platform.unique_wifi_address()
            self.assertTrue(self.platform.power_bluetooth())
        wifi, bluetooth = derived_address('box1', 'wifi'), derived_address('box1', 'bluetooth')
        argv = [c.args[0] for c in run.call_args_list]
        self.assertEqual(argv[:2], [['ip', 'link', 'set', 'dev', 'wlan-test', 'down'],
                                    ['ip', 'link', 'set', 'dev', 'wlan-test', 'address', wifi]])
        self.assertEqual(argv[2:8], [['btmgmt', '--index', '0', 'info'], ['btmgmt', '--index', '0', 'power', 'off'],
                                     ['btmgmt', '--index', '0', 'public-addr', bluetooth],
                                     ['bluetoothctl', 'show'], ['bluetoothctl', 'show'], ['bluetoothctl', 'power', 'on']])
        self.assertTrue(all(c.kwargs.get('input') == '' for c in run.call_args_list if c.args[0][0] == 'btmgmt'),
                        'btmgmt hangs with stdin on /dev/null, as in the service')
        # A failing step leaves the address as it is but never stops Bluetooth from starting.
        run.reset_mock()
        run.side_effect = lambda argv, **_: (_ for _ in ()).throw(subprocess.TimeoutExpired(argv, 10)) \
            if argv[0] == 'btmgmt' else Mock(returncode=0, stdout='')
        with patch('platform_debian.machine_key', return_value='box1'):
            self.assertTrue(self.platform.power_bluetooth())
        with patch('platform_debian.SYS_NET', self.root / 'net'), patch('platform_debian.machine_key', side_effect=OSError):
            (net / 'address').write_text('b8:27:eb:74:f2:6c\n')
            self.platform.unique_wifi_address()   # no key to derive from: left as it is, no exception
        # A unique address from the hardware (an OneCloud dongle) is left as it is.
        run.reset_mock()
        (net / 'address').write_text('00:00:5e:00:53:01\n')
        run.side_effect = lambda argv, **_: Mock(returncode=0, stdout=(
            'hci0:\tPrimary controller\n\taddr 00:00:5E:00:53:02 version 9\n' if argv[0] == 'btmgmt' else ''))
        with patch('platform_debian.SYS_NET', self.root / 'net'):
            self.platform.unique_wifi_address()
            self.assertTrue(self.platform.power_bluetooth())
        self.assertNotIn(['btmgmt', '--index', '0', 'power', 'off'], [c.args[0] for c in run.call_args_list])
        self.assertNotIn('ip', [c.args[0][0] for c in run.call_args_list])

    @patch('platform_debian.subprocess.run')
    def test_quick_ack_on_the_car_subnet(self, run):
        run.return_value = Mock(returncode=0, stdout='10.239.116.0/24 proto kernel src 10.239.116.212 metric 2048\n')
        self.platform.quick_ack()
        self.assertEqual(run.call_args.args[0], ['ip', 'route', 'change', '10.239.116.0/24', 'proto', 'kernel', 'src',
                                                  '10.239.116.212', 'metric', '2048', 'dev', 'wlan-test', 'quickack', '1'])

    def test_interface_and_optional_leds(self):
        (self.root / 'wlan-test' / 'wireless').mkdir(parents=True)
        self.assertEqual(self.platform.wifi_interface(self.root), 'wlan-test')
        # The box-hotspot interface created beside the client is never taken for the dongle itself.
        (self.root / 'dpap0' / 'phy80211').mkdir(parents=True)
        self.assertEqual(self.platform.wifi_interface(self.root), 'wlan-test')
        self.platform.status_light(self.root / 'missing').show('live')

    def test_brcmfmac_hotspot_keeps_the_driver_regulatory_domain(self):
        # Phicomm N1 (2026-10-12): once hostapd sets CN, brcmfmac disables 36-48 (CN marks them DFS),
        # so a car hotspot there is never seen; its own domain allows 36-48 and an AP on 149.
        with patch('platform_debian.wifi_driver', return_value='brcmfmac'):
            self.platform.configure_hotspot(149, 40, 'Box', 'synthetic-password')
        config = (self.root / 'hostapd.conf').read_text()
        self.assertNotIn('country_code', config)
        self.assertNotIn('ieee80211d', config)
        with patch('platform_debian.wifi_driver', return_value='rtw88_8821cu'):
            self.platform.configure_hotspot(149, 40, 'Box', 'synthetic-password')
        self.assertIn('country_code=CN', (self.root / 'hostapd.conf').read_text())

    def test_wifi_driver_from_sysfs(self):
        (self.root / 'drivers' / 'brcmfmac').mkdir(parents=True)
        (self.root / 'wlan-test' / 'device').mkdir(parents=True)
        (self.root / 'wlan-test' / 'device' / 'driver').symlink_to(self.root / 'drivers' / 'brcmfmac')
        self.assertEqual(wifi_driver('wlan-test', self.root), 'brcmfmac')
        self.assertIsNone(wifi_driver('missing', self.root))

    @patch('platform_debian.subprocess.run')
    def test_ap_address_config_and_dhcp_modes(self, run):
        self.platform.configure_hotspot(149, 80, 'Box', 'synthetic-password')
        config = (self.root / 'hostapd.conf').read_text()
        self.assertIn('vht_oper_centr_freq_seg0_idx=155', config)
        self.assertIn('wpa_passphrase=synthetic-password', config)
        self.platform.configure_hotspot(6, 20, 'Box', None)
        self.assertNotIn('wpa=', (self.root / 'hostapd.conf').read_text())
        address = ipaddress.ip_interface('192.0.2.1/24')
        self.platform.set_ap_address(address)
        self.assertEqual([c.args[0][1:3] for c in run.call_args_list], [['addr', 'flush'], ['link', 'set'], ['addr', 'add']])
        with patch.object(self.platform, 'start') as start:
            self.platform.start_dhcp(address, True)
            self.assertIn('--address=/#/192.0.2.1', start.call_args.args[1])
            self.assertIn('--dhcp-range=192.0.2.10,192.0.2.100,255.255.255.0,12h', start.call_args.args[1])
            self.platform.start_dhcp(address, False)
            self.assertIn('--port=0', start.call_args.args[1])
            self.assertIn('--dhcp-option=3', start.call_args.args[1])

    def test_station_plus_ap_detection_from_iw_list(self):
        rebuilt = ('\tvalid interface combinations:\n\t\t * #{ managed } <= 1, #{ AP } <= 1,\n'
                   '\t\t   total <= 2, #channels <= 1\n\tHT Capability overrides:\n')
        vendor = ('\tvalid interface combinations:\n\t\t * #{ managed, P2P-client } <= 2, #{ AP, P2P-GO } <= 1,\n'
                  '\t\t   total <= 2, #channels <= 1\n')
        self.assertTrue(supports_sta_ap(rebuilt))
        self.assertTrue(supports_sta_ap(vendor))
        mt7921 = ('\tvalid interface combinations:\n\t\t * #{ managed, P2P-client } <= 2, #{ AP, P2P-GO } <= 1,\n'
                  '\t\t   total <= 2, #channels <= 2\n')
        self.assertEqual((supports_sta_ap(rebuilt), supports_sta_ap(mt7921)), (1, 2))
        self.assertFalse(supports_sta_ap('\tinterface combinations are not supported\n'))   # stock rtw88
        self.assertFalse(supports_sta_ap(''))
        self.assertFalse(supports_sta_ap('\tvalid interface combinations:\n\t\t * #{ managed } <= 2,\n\t\t   total <= 2\n'))

    def test_box_hotspot_width_follows_the_car_channel(self):
        self.assertEqual(ap_width(149, 80), 80)
        self.assertEqual(ap_width(36, 80), 80)
        self.assertEqual(ap_width(165, 80), 20)   # no 40/80 MHz partner
        self.assertEqual(ap_width(6, 80), 20)
        self.assertNotIn('vht_oper', hostapd_config('ap', 165, 80, 'Box', 'synthetic-password'))
        self.assertNotIn('HT40', hostapd_config('ap', 165, 80, 'Box', 'synthetic-password'))
        self.assertIn('hw_mode=g', hostapd_config('ap', 11, 80, 'Box', 'synthetic-password'))
        self.assertIn('vht_oper_centr_freq_seg0_idx=42', hostapd_config('ap', 44, 80, 'Box', 'synthetic-password'))

    @patch('platform_debian.subprocess.run')
    def test_second_interface_carries_the_box_hotspot(self, run):
        net = self.root / 'net'
        (net / 'wlan-test').mkdir(parents=True)
        (net / 'wlan-test' / 'address').write_text('00:00:5e:00:53:01\n')
        real = Path
        def fake_path(*parts):
            if parts and parts[0] == '/sys/class/net':
                return real(net, *parts[1:])
            return real(*parts)
        run.return_value = Mock(returncode=0, stdout='')
        with patch('platform_debian.Path', side_effect=fake_path):
            self.assertTrue(self.platform.add_ap_interface())
        commands = [c.args[0] for c in run.call_args_list]
        self.assertIn(['iw', 'dev', 'wlan-test', 'interface', 'add', AP_VIF, 'type', '__ap'], commands)
        self.assertIn(['ip', 'link', 'set', AP_VIF, 'address', '02:00:5e:00:53:01'], commands)   # local bit flipped
        self.assertEqual(self.platform.ap_interface, AP_VIF)
        self.platform.configure_hotspot(149, 80, 'Box', 'synthetic-password')
        self.assertIn(f'interface={AP_VIF}', (self.root / 'hostapd.conf').read_text())
        run.reset_mock()
        self.platform.set_ap_address(ipaddress.ip_interface('192.0.2.1/24'))
        self.assertTrue(all(AP_VIF in c.args[0] for c in run.call_args_list))
        with patch.object(self.platform, 'start') as start:
            self.platform.start_dhcp(ipaddress.ip_interface('192.0.2.1/24'), False)
            self.assertIn(f'--interface={AP_VIF}', start.call_args.args[1])
        run.reset_mock()
        self.platform.has_stations()
        self.assertEqual(run.call_args.args[0][:3], ['iw', 'dev', AP_VIF])
        # The driver refuses: nothing is left behind and the hotspot stays on the dongle itself.
        run.reset_mock()
        run.return_value = Mock(returncode=1, stdout='')
        with patch('platform_debian.Path', side_effect=fake_path):
            self.assertFalse(self.platform.add_ap_interface())
        self.assertEqual(self.platform.ap_interface, 'wlan-test')

    @patch('platform_debian.subprocess.run')
    def test_join_scans_last_frequency_first_then_everything(self, run):
        run.return_value = Mock(returncode=0, stdout='Connected to synthetic\nfreq: 5805')
        car = {'ssid': 'Car', 'passphrase': 'synthetic1'}
        lease = {'ssid': 'Car', 'address': '192.0.2.9', 'freq': 5805}
        confs, waits = [], []
        def wait(predicate, seconds, interval):
            waits.append(seconds)
            confs.append((self.root / 'wpa.conf').read_text())
            if seconds == 8:   # not found on the old channel: the car moved
                raise TimeoutError
        with patch.object(self.platform, 'start'), patch.object(self.platform, 'ipv4_address', return_value='192.0.2.9'):
            self.assertTrue(self.platform.join_wifi(car, lease, wait, 60))
        self.assertEqual(waits, [30, 8, 52, 30])
        self.assertIn('scan_freq=5805', confs[1])
        self.assertNotIn('scan_freq', confs[2])
        self.assertIn(['wpa_cli', '-p', str(self.root / 'wpa'), '-i', 'wlan-test', 'reconfigure'],
                      [c.args[0] for c in run.call_args_list])
        # Another car hotspot than last time: no frequency hint.
        with patch.object(self.platform, 'start'), patch.object(self.platform, 'ipv4_address', return_value='192.0.2.9'):
            self.platform.join_wifi(dict(car, ssid='Other'), lease, lambda *a, **k: None, 60)
        self.assertNotIn('scan_freq', (self.root / 'wpa.conf').read_text())

    @patch('platform_debian.subprocess.run')
    def test_join_preserves_lease_escaping_and_timeout_cleanup(self, run):
        run.return_value = Mock(returncode=0, stdout='Connected to synthetic\nfreq: 5180')
        car = {'ssid': 'Test"\\', 'passphrase': 'synthetic"\\'}
        waits = []
        def wait(predicate, seconds, interval):
            waits.append((seconds, interval))
            self.assertTrue(predicate())
        with patch.object(self.platform, 'start') as start, patch.object(self.platform, 'ipv4_address', return_value='192.0.2.9'):
            self.assertTrue(self.platform.join_wifi(car, {'ssid': car['ssid'], 'address': '192.0.2.9'}, wait, 60))
        self.assertEqual(waits, [(30, .25), (60, .25), (30, .2)])
        self.assertIn('RequestAddress=192.0.2.9', self.platform.sta_network.read_text())
        self.assertEqual(self.platform.sta_network.stat().st_mode & 0o777, 0o644)
        self.assertIn('ssid="Test\\"\\\\"', (self.root / 'wpa.conf').read_text())
        self.assertIn('key_mgmt=WPA-PSK SAE', (self.root / 'wpa.conf').read_text())
        with patch.object(self.platform, 'start'), patch.object(self.platform, 'stop_child') as stop:
            self.assertFalse(self.platform.join_wifi(car, {}, Mock(side_effect=TimeoutError), 60))
            stop.assert_called_once_with('wpa_supplicant')
        self.assertFalse(self.platform.sta_network.exists())
        self.assertEqual(run.call_args.args[0], ['ip', 'addr', 'flush', 'dev', 'wlan-test'])

    @patch('platform_debian.os.killpg')
    @patch('platform_debian.subprocess.Popen')
    def test_service_stop_escalation_and_receiver_shutdown_allowance(self, popen, kill):
        child = popen.return_value
        child.poll.return_value = None
        child.pid = 123
        self.assertIs(self.platform.start('receiver', ['synthetic']), child)
        self.assertTrue(popen.call_args.kwargs['start_new_session'])
        child.wait.side_effect = [subprocess.TimeoutExpired('synthetic', 8), None]
        self.platform.stop_child('receiver')
        self.assertEqual(kill.call_args_list, [call(123, signal.SIGTERM), call(123, signal.SIGKILL)])
        self.assertEqual(self.platform.children, [])
        child.wait.side_effect = None
        self.platform.start('receiver', ['synthetic'])
        self.platform.stop_all()
        child.wait.assert_called_with(timeout=40)

    def test_ap_exit_or_ready(self):
        child = Mock()
        child.poll.return_value = None
        with patch.object(self.platform, 'start', return_value=child), patch.object(self.platform, 'ap_ready', return_value=True):
            def wait(predicate, seconds):
                self.assertEqual(seconds, 30)
                self.assertTrue(predicate())
            self.assertTrue(self.platform.start_ap(wait))
            child.poll.return_value = 1
            self.assertFalse(self.platform.start_ap(wait))

    @patch('platform_debian.subprocess.run')
    def test_ap_ready_trusts_hostapd_when_driver_reports_no_channel(self, run):
        # Realtek 8821cu concurrent build: an AP without a busy station interface shows no channel.
        run.return_value = Mock(returncode=0, stdout='Interface wlan-test\n\tssid DashPlay\n\ttype AP\n')
        (self.platform.run / 'hostapd.log').write_text('wlan-test: interface state COUNTRY_UPDATE->HT_SCAN\n')
        self.assertFalse(self.platform.ap_ready())
        (self.platform.run / 'hostapd.log').write_text('wlan-test: AP-ENABLED \n')
        self.assertTrue(self.platform.ap_ready())

    @patch('platform_debian.subprocess.run')
    def test_ap_ready_ignores_driver_while_hostapd_starts(self, run):
        # Phicomm N1 brcmfmac: type AP with a channel as soon as hostapd starts, before it fails to set
        # an 80 MHz channel and logs AP-DISABLED.
        run.return_value = Mock(returncode=0, stdout='Interface wlan-test\n\ttype AP\n\tchannel 153 (5765 MHz)\n')
        (self.platform.run / 'hostapd.log').write_text('wlan-test: interface state COUNTRY_UPDATE->HT_SCAN\n')
        self.assertFalse(self.platform.ap_ready())
        (self.platform.run / 'hostapd.log').write_text(
            'nl80211: kernel reports: (extension) channel is disabled\nwlan-test: AP-DISABLED \n')
        self.assertFalse(self.platform.ap_ready())

    def test_hotspot_attempts_narrow_before_leaving_5ghz(self):
        import supervisor
        self.assertEqual(supervisor.hotspot_attempts(149, 80), [(149, 80), (149, 40), (6, 20)])
        self.assertEqual(supervisor.hotspot_attempts(149, 40), [(149, 40), (6, 20)])
        self.assertEqual(supervisor.hotspot_attempts(165, 80), [(165, 80), (6, 20)])
        self.assertEqual(supervisor.hotspot_attempts(6, 20), [(6, 20)])

class KnownFrequencyTests(unittest.TestCase):
    def test_car_moved_channel_is_seen_from_one_scan(self):
        import platform_debian
        header = "bssid / frequency / signal level / flags / ssid\n"
        other = "02:00:00:00:00:01\t5745\t-60\t[WPA2-PSK-CCMP][ESS]\tneighbour\n"
        car = "02:00:00:00:00:02\t5745\t-40\t[WPA2-PSK-CCMP][ESS]\t\\xe5\\xb0\\x8f\\xe9\\xb9\\x8f\\xe6\\xb1\\xbd\\xe8\\xbd\\xa6\n"
        self.assertTrue(platform_debian.missed_on_known_freq(header + other, "小鹏汽车"))
        self.assertFalse(platform_debian.missed_on_known_freq(header + other + car, "小鹏汽车"))
        self.assertFalse(platform_debian.missed_on_known_freq(header, "小鹏汽车"))   # no scan finished yet

if __name__ == '__main__':
    unittest.main()
