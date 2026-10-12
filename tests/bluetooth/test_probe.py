"""Safety regression checks for the optional, approval-gated hardware probe."""
import argparse
import asyncio
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux/bt-bridge'))
import probe_adapter
from dbus_next import Variant

PATH = '/org/bluez/hci0'

class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, usb_id, powered=False, discovering=False, fail_scan=False):
        self.calls = []
        bus = AsyncMock()
        bus.disconnect = lambda: None
        factory = type('Factory', (), {'connect': lambda self: asyncio.sleep(0, result=bus)})
        data = {PATH: {'org.bluez.Adapter1': {
            'Powered': Variant('b', powered), 'Discovering': Variant('b', discovering)}}}
        async def call(bus, path, interface, member, *args, **kwargs):
            self.calls.append((member, args))
            if member == 'GetManagedObjects':
                return [data]
            if member == 'StartDiscovery' and fail_scan:
                raise RuntimeError('synthetic scan failure')
            return []
        args = argparse.Namespace(adapter='hci0', expected_usb_id='0bda:8771', seconds=0)
        with patch.object(probe_adapter, 'ManagedBus', lambda **kwargs: factory()), \
             patch.object(probe_adapter, 'usb_identity', lambda adapter: usb_id), \
             patch.object(probe_adapter, 'call', call), contextlib.redirect_stdout(io.StringIO()):
            await probe_adapter.probe(args)

    async def test_mismatched_usb_never_changes_power(self):
        with self.assertRaisesRegex(RuntimeError, 'USB ID mismatch'):
            await self.check('1111:2222')
        self.assertEqual([name for name, _ in self.calls], ['GetManagedObjects'])

    async def test_existing_discovery_is_not_stopped(self):
        with self.assertRaisesRegex(RuntimeError, 'discovery already active'):
            await self.check('0bda:8771', discovering=True)
        self.assertEqual([name for name, _ in self.calls], ['GetManagedObjects'])

    async def test_power_state_restored_after_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'synthetic scan failure'):
            await self.check('0bda:8771', fail_scan=True)
        self.assertEqual([name for name, _ in self.calls][-2:], ['StopDiscovery', 'Set'])
        powers = [args[1][2].value for name, args in self.calls if name == 'Set']
        self.assertEqual(powers, [True, False])

    async def test_already_powered_adapter_stays_powered(self):
        await self.check('0bda:8771', powered=True)
        self.assertNotIn('Set', [name for name, _ in self.calls])
        self.assertEqual(self.calls[-1][0], 'StopDiscovery')
