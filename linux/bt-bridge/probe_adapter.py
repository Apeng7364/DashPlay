"""Bounded, unpaired discovery probe. Explicit approval required on real hardware."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

from dbus_next import BusType, Variant
from bridge import ManagedBus, ADAPTER, DEVICE, call, choose_adapter, properties


def usb_identity(adapter):
    controller = Path('/sys/class/bluetooth') / adapter.rsplit('/', 1)[-1]
    for parent in controller.resolve().parents:
        if (parent / 'idVendor').is_file() and (parent / 'idProduct').is_file():
            return (parent / 'idVendor').read_text().strip() + ':' + (parent / 'idProduct').read_text().strip()
    return None


async def probe(args):
    bus = await asyncio.wait_for(ManagedBus(bus_type=BusType.SYSTEM).connect(), 5)
    adapter = None
    powered = started = changed_power = False
    try:
        for attempt in range(30):
            try:
                objects = (await call(bus, '/', 'org.freedesktop.DBus.ObjectManager', 'GetManagedObjects', timeout=1))[0]
                break
            except Exception:
                if attempt == 29:
                    raise
                await asyncio.sleep(0.1)
        adapter = choose_adapter(objects, args.adapter)
        usb_id = usb_identity(adapter)
        if usb_id != args.expected_usb_id.lower():
            raise RuntimeError('adapter USB ID mismatch; refusing to change another adapter')
        props = properties(objects, adapter, ADAPTER)
        powered = bool(props.get('Powered'))
        if props.get('Discovering'):
            raise RuntimeError('discovery already active; refusing to interfere')
        if not powered:
            changed_power = True
            await call(bus, adapter, 'org.freedesktop.DBus.Properties', 'Set', 'ssv',
                       [ADAPTER, 'Powered', Variant('b', True)])
        await call(bus, adapter, ADAPTER, 'SetDiscoveryFilter', 'a{sv}',
                   [{'Transport': Variant('s', 'bredr')}])
        started = True
        await call(bus, adapter, ADAPTER, 'StartDiscovery')
        await asyncio.sleep(args.seconds)
        objects = (await call(bus, '/', 'org.freedesktop.DBus.ObjectManager', 'GetManagedObjects'))[0]
        count = sum(properties(objects, path, DEVICE).get('Adapter') == adapter for path in objects)
        report = dict(adapter=args.adapter, usb_id=usb_id, powered_before=powered,
                      classic_discovery_started=True, observed_devices=count,
                      pairing_tested=False, rfcomm_tested=False, carplay_tested=False)
    finally:
        cleanup_errors = []
        operations = []
        if started:
            operations.append((ADAPTER, 'StopDiscovery', '', []))
        if changed_power:
            operations.append(('org.freedesktop.DBus.Properties', 'Set', 'ssv',
                               [ADAPTER, 'Powered', Variant('b', False)]))
        try:
            for interface, member, signature, body in operations:
                try:
                    await call(bus, adapter, interface, member, signature, body, timeout=3)
                except Exception as error:
                    cleanup_errors.append(str(error))
        finally:
            bus.disconnect()
            await bus.wait_for_disconnect()
        if cleanup_errors:
            raise RuntimeError('cleanup incomplete: ' + '; '.join(cleanup_errors))
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--adapter', default='hci0')
    parser.add_argument('--expected-usb-id', required=True)
    parser.add_argument('--seconds', type=int, choices=range(1, 31), default=10)
    args = parser.parse_args()
    try:
        asyncio.run(probe(args))
    except Exception as error:
        print(f'adapter-probe: {error}', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
