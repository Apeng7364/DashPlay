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
