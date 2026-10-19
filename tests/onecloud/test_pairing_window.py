import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts/onecloud'))
from pairing_window import PairingWindow, save_json, read_json, attempt_deadline, ATTEMPT_SECONDS, WINDOW_LIMIT_SECONDS
from settings_page import SettingsPage

class PairingTests(unittest.TestCase):
    def setUp(self):
        boot = patch('platform_debian.DebianPlatform.boot_id', return_value='synthetic-boot')
        boot.start(); self.addCleanup(boot.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.page=SettingsPage(self.root/'state',lambda:{},lambda _:None,lambda:[])
        self.window=self.page.pairing
        # Bluetooth pairing is offered where wireless CarPlay is (mifi, box_hotspot; not car_hotspot).
        save_json(self.root/'state/setup.json',{'done':True,'mode':'mifi'})
        save_json(self.root/'state/car-hotspot.json',{'ssid':'Car','passphrase':'synthetic1'})
    def test_start_rejected_while_someone_is_using_carplay(self):
        save_json(self.root/'state/receiver/phone-presence.json',{'active':'synthetic-A'})
        with patch('pairing_window.subprocess.Popen') as launch:
            with self.assertRaises(ValueError):self.window.start()
            launch.assert_not_called()
    def test_no_start_or_confirmation_without_page_nonce(self):
        with patch.object(self.window,'start') as start:
            with self.assertRaises(ValueError):self.page.apply('/pair',{'action':'start','nonce':'wrong'})
            start.assert_not_called()
    def test_pairing_requires_current_request_and_explicit_confirmation(self):
        self.window.process=Mock();self.window.process.poll.return_value=None
        save_json(self.window.status_file,{'phase':'confirm','request_id':'current','passkey':'123456','name':'Synthetic phone'})
        self.assertFalse(self.window.confirm_file.exists())
        with self.assertRaises(ValueError):self.window.confirm('old',True)
        self.window.confirm('current',False)
        self.assertFalse(read_json(self.window.confirm_file)['accepted'])
        self.window.confirm('current',True)
        self.assertTrue(read_json(self.window.confirm_file)['accepted'])
        self.assertEqual(self.window.confirm_file.stat().st_mode & 0o777,0o600)
    def test_phone_name_is_escaped_on_confirmation_page(self):
        self.window.process=Mock();self.window.process.poll.return_value=None
        save_json(self.window.status_file,{'phase':'confirm','request_id':'current','passkey':'123456','name':'<script>bad</script>'})
        page=self.page.render('')
        self.assertNotIn('<script>bad</script>',page)
        self.assertIn('&lt;script&gt;',page)
        self.assertIn('value=yes',page)

    def test_manual_use_never_preempts_and_requires_a_paired_phone(self):
        grant=self.root/'state/receiver/pairing-request.json'
        with patch.object(self.window,'devices',return_value={'02:00:00:00:00:02':'Synthetic B'}):
            with self.assertRaises(ValueError):self.window.use('02:00:00:00:00:09')
            save_json(self.root/'state/receiver/phone-presence.json',{'active':'02:00:00:00:00:01'})
            with self.assertRaises(ValueError):self.window.use('02:00:00:00:00:02')
            self.assertFalse(grant.exists())
            save_json(self.root/'state/receiver/phone-presence.json',{})
            self.window.use('02:00:00:00:00:02')
        self.assertEqual(read_json(grant)['phone'],'02:00:00:00:00:02')
        self.assertEqual(grant.stat().st_mode & 0o777,0o600)
    def test_idle_page_offers_paired_phones_but_hides_them_while_busy(self):
        with patch.object(self.window,'devices',return_value={'02:00:00:00:00:01':'A','02:00:00:00:00:02':'<B>'}):
            page=self.page.render('')
            self.assertIn('<b>&lt;B&gt;</b><span>蓝牙 …00:02</span>',page)
            self.assertEqual(page.count('value=use'),2)
            save_json(self.root/'state/receiver/phone-presence.json',{'active':'02:00:00:00:00:01','last_phone':'02:00:00:00:00:01'})
            page=self.page.render('')
        self.assertIn('<b>A</b><span>蓝牙 …00:01</span></span><span class=value>正在使用</span>',page)
        self.assertEqual(page.count('value=remove'),1)
        self.assertIn('删除「&lt;B&gt;」',page)
        self.assertNotIn('value=use',page)

    def test_pick_only_listed_phone_while_idle_and_waiting(self):
        self.window.process=Mock();self.window.process.poll.return_value=None
        save_json(self.window.status_file,{'phase':'waiting','found':[{'address':'02:00:00:00:00:02','name':'B'}]})
        with self.assertRaises(ValueError):self.window.pick('02:00:00:00:00:09')
        save_json(self.root/'state/receiver/phone-presence.json',{'active':'02:00:00:00:00:01'})
        with self.assertRaises(ValueError):self.window.pick('02:00:00:00:00:02')
        self.assertFalse(self.window.pick_file.exists())
        save_json(self.root/'state/receiver/phone-presence.json',{})
        self.page.apply('/pair',{'action':'pick','phone':'02:00:00:00:00:02','nonce':self.window.nonce})
        self.assertEqual(read_json(self.window.pick_file)['address'],'02:00:00:00:00:02')
    def test_waiting_page_lists_found_phones_escaped(self):
        self.window.process=Mock();self.window.process.poll.return_value=None
        save_json(self.window.status_file,{'phase':'waiting','found':[{'address':'02:00:00:00:00:02','name':'<i>B</i>'}],'error':'x'})
        page=self.page.render('')
        self.assertIn('<b>&lt;i&gt;B&lt;/i&gt;</b><span>蓝牙 …00:02</span>',page)
        self.assertIn('value=pick',page)
        self.assertIn('http-equiv=refresh',page)
        self.assertNotIn('<i>B</i>',page)
    def test_only_phone_major_class_is_listed(self):
        from pairing_window import is_phone
        class V:
            def __init__(self,v):self.value=v
        self.assertTrue(is_phone({'Class':V(0x7a020c)}))
        self.assertFalse(is_phone({'Class':V(0x240404)}))
        self.assertFalse(is_phone({}))

    def test_remove_clears_box_side_records_but_never_the_active_phone(self):
        A,B='02:00:00:00:00:01','02:00:00:00:00:02'
        state=self.root/'state/receiver/phone-presence.json'
        grant=self.root/'state/receiver/pairing-request.json'
        save_json(state,{'active':A,'last_phone':B,'paired':[A,B],'phones':{A:{},B:{'networks':{}}},'declined':{B:{'count':1,'until':9}}})
        save_json(grant,{'phone':B})
        removed=[]
        async def fake_remove(root,phone):removed.append(phone)
        with patch.object(self.window,'devices',return_value={A:'A',B:'B'}),patch('pairing_window.remove_device',fake_remove):
            with self.assertRaises(ValueError):self.window.remove(A)
            with self.assertRaises(ValueError):self.window.remove('02:00:00:00:00:09')
            self.page.apply('/pair',{'action':'remove','phone':B,'nonce':self.window.nonce})
        self.assertEqual(removed,[B])
        left=read_json(state)
        self.assertEqual(list(left['phones']),[A])
        self.assertEqual(left['active'],A)
        self.assertNotIn('last_phone',left)
        self.assertEqual(left['declined'],{})
        self.assertEqual(left['paired'],[A])
        self.assertFalse(grant.exists())

class AttemptDeadlineTests(unittest.TestCase):
    # 2026-10-19 (F50): a retry late in the 180 s window was cut off while the iPhone was confirming.
    def test_each_attempt_gets_its_own_time_from_when_it_begins(self):
        self.assertEqual(attempt_deadline(180, now=170, opened=0), 170 + ATTEMPT_SECONDS)
        self.assertEqual(attempt_deadline(400, now=10, opened=0), 400)   # never shortened

    def test_the_window_stays_bounded(self):
        self.assertEqual(attempt_deadline(180, now=WINDOW_LIMIT_SECONDS - 5, opened=0), WINDOW_LIMIT_SECONDS)

if __name__=='__main__':unittest.main()
