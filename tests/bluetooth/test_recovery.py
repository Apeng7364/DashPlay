"""Boundaries for opt-in stale-controller recovery; no actual Bluetooth is changed."""
import os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'linux/bt-bridge'))
import bridge
from dbus_next import Variant
A='/org/bluez/hci0';D=A+'/dev_12_34_56_78_9A_BC'
class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.stamp=str(Path(self.tmp.name)/'recovery')
        self.data={A:{bridge.ADAPTER:{'Powered':Variant('b',True)}},D:{bridge.DEVICE:{
            'Adapter':Variant('o',A),'Paired':Variant('b',True),'Connected':Variant('b',True)}}}
        self.power=[]
    async def call(self,bus,path,interface,member,*args,**kwargs):
        if member=='GetManagedObjects':return [self.data]
        self.assertEqual((path,member),(A,'Set'));self.power.append(args[1][2].value);return []
    async def test_disabled_by_default(self):
        with patch.dict(os.environ,{},clear=True),patch.object(bridge,'call',side_effect=self.call):
            self.assertFalse(await bridge.recover_stale_link(None,A,D))
        self.assertEqual(self.power,[])
    async def test_dedicated_adapter_recovers_once_and_keeps_pairing(self):
        with patch.dict(os.environ,{'BT_BRIDGE_RECOVERY_STATE':self.stamp}),patch.object(bridge,'call',side_effect=self.call):
            self.assertTrue(await bridge.recover_stale_link(None,A,D))
            self.assertFalse(await bridge.recover_stale_link(None,A,D))
        self.assertEqual(self.power,[False,True]);self.assertTrue(self.data[D][bridge.DEVICE]['Paired'].value)
        self.assertEqual(Path(self.stamp).stat().st_mode&0o777,0o600)
    async def test_other_connected_device_prevents_reset(self):
        self.data[A+'/dev_other']={bridge.DEVICE:{'Adapter':Variant('o',A),'Connected':Variant('b',True)}}
        with patch.dict(os.environ,{'BT_BRIDGE_RECOVERY_STATE':self.stamp}),patch.object(bridge,'call',side_effect=self.call):
            self.assertFalse(await bridge.recover_stale_link(None,A,D))
        self.assertEqual(self.power,[])
    async def test_scanning_or_unpaired_prevents_reset(self):
        with patch.dict(os.environ,{'BT_BRIDGE_RECOVERY_STATE':self.stamp}),patch.object(bridge,'call',side_effect=self.call):
            self.data[A][bridge.ADAPTER]['Discovering']=Variant('b',True)
            self.assertFalse(await bridge.recover_stale_link(None,A,D))
            self.data[A][bridge.ADAPTER]['Discovering']=Variant('b',False)
            self.data[D][bridge.DEVICE]['Paired']=Variant('b',False)
            self.assertFalse(await bridge.recover_stale_link(None,A,D))
        self.assertEqual(self.power,[])
if __name__=='__main__':unittest.main()
