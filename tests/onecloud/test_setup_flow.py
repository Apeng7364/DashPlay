import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/onecloud'))
from settings_page import SettingsPage
from setup_flow import FirstSetup
import setup_wizard
import supervisor

class SetupFlowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        (self.state / 'hotspot.json').write_text('{"ssid":"DashPlay"}')
        self.changes = []
        self.status = {'mode':'ap', 'box_ssid':'DashPlay', 'home':'http://192.0.2.1/'}
        self.page = SettingsPage(self.state, lambda:self.status, self.changes.append, lambda:[])
        self.page.apply_later = self.changes.append
        self.flow = FirstSetup(self.page)

    def answer(self, action, **fields):
        return self.page.apply('/setup-flow', dict(fields, action=action, nonce=self.page.pairing.nonce))

    def until_iphone(self, internet='none', same=True):
        self.answer('vehicle', value='tesla')
        self.assertNotIn('value="car_hotspot"', self.page.render(''))
        self.answer('mode', value='no')
        self.answer('mifi', value='box_hotspot')
        self.answer('network', ssid='Box', password='synthetic-box')
        self.answer('internet', value=internet)
        if internet == 'phone_hotspot':
            self.answer('uplink', ssid='Phone', password='synthetic-uplink', uplink_phone='same' if same else 'other')
        self.answer('sound', value='no')

    def test_network_step_shows_at_once_while_the_scan_runs_and_its_fields_are_visible(self):
        # N1 2026-10-17: "next" to the car-hotspot step took seconds (the first list is two scans), and the box's
        # fallback password row looked like a label (borderless input, no placeholder).
        import threading
        release, calls = threading.Event(), []
        class Radio:
            def scan_networks(self, own, ap=False):
                calls.append(1); release.wait(5)
                return [{'ssid': 'XPeng G6', 'band': '5', 'signal': -50}]
        self.page.scanner = setup_wizard.Scanner('wlan0', 'DashPlay', lambda: 'ap', platform=Radio())
        self.answer('vehicle', value='other')
        self.answer('mode', value='car_hotspot')
        html = self.page.render('')
        self.assertIn('正在扫描附近的车机热点', html)
        self.assertIn('<meta http-equiv=refresh content=2>', html)
        self.assertNotIn('name=box_password', html, 'no form to lose what was typed on the reload')
        self.assertIn('正在扫描', self.page.render(''), 'one scan at a time')
        release.set()
        for _ in range(100):
            if not self.page.scanner.busy: break
            threading.Event().wait(0.02)
        self.assertEqual(len(calls), 2, 'the first list is still two scans')
        html = self.page.render('')
        self.assertIn('value="XPeng G6"', html)
        self.assertNotIn('http-equiv=refresh', html)
        self.assertIn('placeholder="至少 8 位" required minlength=8 maxlength=63 autocomplete=new-password', html,
                      'no box password saved yet: a visible, required field')
        # Rescan answers at once too; the page shows "scanning" until it is done.
        # A list older than 20 s stays shown while a new scan runs for the next page.
        release.clear(); self.page.scanner.at -= setup_wizard.SCAN_CACHE_SECONDS
        self.assertIn('value="XPeng G6"', self.page.render(''))
        self.assertTrue(self.page.scanner.busy)
        self.assertIn('value="XPeng G6"', self.page.render(''), 'not "scanning" for a scan nobody asked for')
        release.set()
        for _ in range(100):
            if not self.page.scanner.busy: break
            threading.Event().wait(0.02)
        # Rescan answers at once too; the page shows "scanning" until it is done.
        release.clear()
        self.assertEqual(self.answer('network-rescan'), ('go', '/?view=setup'))
        self.assertIn('正在扫描', self.page.render(''))
        release.set()

    def test_a_radio_without_5ghz_says_so_under_the_hotspot_list(self):
        # Snapdragon 410 sticks (WCN3620): a 5 GHz car hotspot is never in the list, so the page says why.
        class Listing:
            def results(self):
                return [{'ssid': 'Car 2.4', 'band': '2.4', 'signal': -50}]
        self.page.scanner = Listing()
        self.answer('vehicle', value='other')
        self.answer('mode', value='car_hotspot')
        self.assertNotIn('只支持 2.4 GHz', self.page.render(''))
        self.status['only_24ghz'] = True
        html = self.page.render('')
        self.assertIn('value="Car 2.4"', html)
        self.assertIn('只支持 2.4 GHz，5 GHz 的车机热点不会出现在列表里', html)

    def test_car_hotspot_offers_the_direct_wireless_way_only_when_the_box_can(self):
        # 2026-10-17: the direct iPhone hotspot is a normal choice; whether it is offered depends on the radio only.
        def until_iphone():
            self.answer('vehicle', value='other'); self.answer('mode', value='car_hotspot')
            self.answer('network', ssid='Car', password='synthetic-car', box_password='synthetic-box')
            self.answer('sound', value='no')
        self.status['direct_capable'] = True
        until_iphone()
        html = self.page.render('')
        self.assertIn('value="wireless"', html); self.assertIn('无线（iPhone 直连盒子热点）', html)
        self.assertIn('36–48 或 149–165', html)
        self.assertNotIn('只能用数据线', html)
        self.answer('iphone', value='any')
        self.assertIn('都行', self.page.render(''))
        self.answer('summary', value='confirm')
        self.assertIs(setup_wizard.options(self.state)['direct_phone'], True)
        self.assertEqual(setup_wizard.load(self.state)['iphone'], 'any')
        # A radio that cannot: the cable only, and why
        (self.state / 'setup-draft.json').unlink(missing_ok=True)
        setup_wizard.save(self.state, {'done': False, 'wizard_version': 2})
        self.status['direct_capable'] = False
        until_iphone()
        html = self.page.render('')
        self.assertNotIn('value="wireless"', html)
        self.assertIn('不能同时连车机热点又开自己的热点', html)
        with self.assertRaises(ValueError):
            self.answer('iphone', value='wireless')

    def test_settings_page_switches_for_the_direct_way_and_the_cars_internet(self):
        setup_wizard.save(self.state, {'done': True, 'wizard_version': 2, 'confirmed': True, 'mode': 'car_hotspot',
                                       'iphone': 'any', 'car': 'Car'})
        self.status.update(direct_capable=True, direct_phone='iPhone 直连盒子热点（信道 149）')
        html = self.page.iphone_preference(self.page.pairing.nonce)
        self.assertIn('name=direct_phone value=1 checked', html, 'on by default on the car hotspot')
        self.assertIn('name=direct_phone_internet value=1>', html, 'sharing the car\'s internet off by default')
        self.assertIn('会抢带宽', html)
        self.assertIn('class="banner ok', html)
        self.page.apply('/direct', {'nonce': self.page.pairing.nonce, 'direct_phone': '1', 'direct_phone_internet': '1'})
        chosen = setup_wizard.options(self.state)
        self.assertEqual((chosen['direct_phone'], chosen['direct_phone_internet']), (True, True))
        self.page.apply('/direct', {'nonce': self.page.pairing.nonce})
        self.assertEqual(setup_wizard.options(self.state)['direct_phone'], False)
        self.status.pop('direct_phone')
        html = self.page.iphone_preference(self.page.pairing.nonce)
        self.assertIn('name=direct_phone value=1>', html, 'turned off stays off')
        self.assertIn('打开上面的「无线时 iPhone 直连盒子热点」就能用无线', html)
        self.status['direct_capable'] = False
        with self.assertRaises(ValueError):
            self.page.apply('/direct', {'nonce': self.page.pairing.nonce, 'direct_phone': '1'})
        self.assertNotIn('name=direct_phone', self.page.iphone_preference(self.page.pairing.nonce))

    def test_the_box_can_go_direct_by_its_radio_and_an_n1_only_on_the_new_firmware(self):
        from unittest.mock import Mock, patch
        radio = Mock(); radio.concurrent_ap.return_value = 0
        self.assertEqual(supervisor.direct_capable(radio)[0], False)
        self.assertIn('不能同时连车机热点又开自己的热点', supervisor.direct_capable(radio)[1])
        radio.concurrent_ap.return_value = 1
        with patch.object(supervisor.board_phicomm_n1, 'direct_ready', return_value=False):
            capable, why = supervisor.direct_capable(radio)
        self.assertFalse(capable); self.assertIn('新无线固件', why)
        with patch.object(supervisor.board_phicomm_n1, 'direct_ready', return_value=True):
            self.assertEqual(supervisor.direct_capable(radio), (True, None))

    def test_a_box_without_cable_or_direct_hotspot_is_not_offered_the_car_hotspot(self):
        # Snapdragon 410 stick: no station + AP at once, no USB port for the iPhone. Way one would carry no CarPlay.
        self.status.update(direct_capable=False, wired_capable=False)
        self.answer('vehicle', value='other')
        html = self.page.render('')
        self.assertNotIn('value="car_hotspot"', html)
        self.assertIn('也没有接 iPhone 的数据线口', html)
        with self.assertRaises(ValueError):
            self.answer('mode', value='car_hotspot')
        self.answer('mode', value='no')
        self.answer('mifi', value='box_hotspot')
        self.answer('network', ssid='Box', password='synthetic-box')
        self.answer('internet', value='none')
        self.answer('sound', value='no')
        html = self.page.render('')
        self.assertIn('没有接 iPhone 的数据线口，只用无线', html)
        self.assertNotIn('value="wired"', html)
        with self.assertRaises(ValueError):
            self.answer('iphone', value='wired')
        self.answer('iphone', value='any')
        self.answer('summary', value='confirm')
        self.assertEqual(setup_wizard.load(self.state)['mode'], 'box_hotspot')
        self.assertTrue(setup_wizard.wireless(setup_wizard.load(self.state), setup_wizard.options(self.state)))
        from unittest.mock import Mock
        radio = Mock(); radio.concurrent_ap.return_value = 0; radio.wired_capable.return_value = False
        self.assertIn('出不了 CarPlay', supervisor.direct_capable(radio)[1])

    def test_box_hotspot_password_fields_say_what_an_empty_one_means(self):
        self.answer('vehicle', value='other'); self.answer('mode', value='no'); self.answer('mifi', value='box_hotspot')
        html = self.page.render('')
        self.assertIn('<b>热点密码</b>', html); self.assertIn('placeholder="至少 8 位" required', html)

    def test_summary_is_the_only_apply_and_passwords_never_render(self):
        self.assertIn('什么车', self.page.render(''))
        self.until_iphone('phone_hotspot')
        html = self.page.render('')
        self.assertIn('value="wireless"', html)
        self.assertNotIn('只能用数据线', html)
        self.answer('iphone', value='any')
        html = self.page.render('')
        self.assertIn('配置总结', html)
        self.assertIn('未实车验证', html)
        self.assertNotIn('synthetic-uplink', html)
        self.assertNotIn('synthetic-box', html)
        self.assertFalse((self.state/'options.json').exists())
        self.assertEqual(json.loads((self.state/'hotspot.json').read_text()), {'ssid':'DashPlay'})
        self.assertEqual(self.changes, [])
        self.answer('summary', value='confirm')
        self.assertEqual(self.changes, ['setup confirmed'])
        self.assertEqual((self.state/'uplink-hotspot.json').stat().st_mode & 0o777, 0o600)
        self.assertTrue(setup_wizard.wireless(self.page.setup(), setup_wizard.options(self.state)))
        self.assertEqual(supervisor.car_audio_args(self.state), ['--tesla-browser'])
        self.assertIn('添加 iPhone', self.page.render(''))

    def test_back_retains_draft_but_invalidates_dependent_answers(self):
        self.until_iphone()
        self.answer('back', target='vehicle')
        self.assertEqual(self.flow.current(self.flow.load()), 'vehicle')
        self.answer('vehicle', value='other')
        self.answer('mode', value='no')
        self.answer('mifi', value='mifi')
        self.answer('network', ssid='Travel', password='synthetic-travel', box_password='synthetic-box')
        self.assertEqual(self.flow.current(self.flow.load()), 'sound', 'Q5 skipped for MiFi')
        self.answer('sound', value='yes')
        self.answer('iphone', value='any')
        self.answer('summary', value='confirm')
        self.assertEqual(self.page.setup()['mode'], 'mifi')

    def test_wireless_requires_pairing_but_wired_can_finish(self):
        self.until_iphone()
        self.answer('iphone', value='wireless'); self.answer('summary', value='confirm')
        with self.assertRaises(ValueError):
            self.page.apply('/setup', {'action':'finish','nonce':self.page.pairing.nonce})
        self.page.apply('/iphone', {'iphone':'wired','nonce':self.page.pairing.nonce})
        self.page.apply('/setup', {'action':'finish','nonce':self.page.pairing.nonce})
        html = self.page.render('')
        for group in ('车', '网络', '车机上网', 'iPhone', '声音', '自己的网址'):
            self.assertIn('>'+group+'<', html)
        self.assertNotIn('name=local_dns', html)

    def test_same_phone_uplink_keeps_wireless_preference_without_confirmation(self):
        setup_wizard.save(self.state, {'done':True,'mode':'box_hotspot','iphone':'wireless'})
        form = {'car_internet':'phone_hotspot','uplink_phone':'same','ssid':'Phone','password':'synthetic-secret',
                'nonce':self.page.pairing.nonce}
        self.page.apply('/internet', form)
        self.assertEqual(self.page.setup()['iphone'], 'wireless')
        self.assertNotIn('synthetic-secret', self.page.render(''))
        self.assertIn('Phone', self.page.render(''))
        self.page.apply('/iphone', {'iphone':'wireless','nonce':self.page.pairing.nonce})

    def test_capability_questions_follow_video_tree_and_back_recomputes_branch(self):
        self.answer('vehicle', value='other')
        self.assertIn('车机能自己开热点吗', self.page.render(''))
        self.assertNotIn('value="mifi"', self.page.render(''))
        self.answer('mode', value='no')
        self.assertIn('有没有随身 WiFi', self.page.render(''))
        self.answer('mifi', value='box_hotspot')
        self.answer('network', ssid='Box', password='synthetic-box')
        self.assertIn('车机自己还要能上网吗', self.page.render(''))
        self.assertNotIn('特斯拉自带音乐', self.page.render(''))
        self.answer('back', target='mode')
        self.answer('mode', value='car_hotspot')
        self.assertEqual(self.flow.current(self.flow.load()), 'network')
        self.answer('network', ssid='Car', password='synthetic-car', box_password='synthetic-box')
        self.assertEqual(self.flow.current(self.flow.load()), 'sound')
        self.answer('sound', value='no')
        self.assertNotIn('value="wireless"', self.page.render(''))

    def test_new_flow_cannot_be_bypassed_by_old_endpoint_or_out_of_order_post(self):
        with self.assertRaises(ValueError): self.answer('summary', value='confirm')
        with self.assertRaises(ValueError):
            self.page.apply('/setup', {'action':'mode','mode':'box_hotspot','nonce':self.page.pairing.nonce})
        self.answer('vehicle', value='tesla')
        with self.assertRaises(ValueError): self.answer('mode', value='car_hotspot')
