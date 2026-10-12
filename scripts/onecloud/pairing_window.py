"""Bounded, operator-confirmed incoming pairing; never accepts a passkey automatically."""
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from platform_api import get_platform
import sys
import threading
import time

PHONE = re.compile(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}')
GRANT_SECONDS = 90


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream)
    temporary.replace(path)


class PairingWindow:
    def __init__(self, root):
        self.root = root
        self.directory = root / 'state/receiver/pairing-window'
        self.status_file = self.directory / 'status.json'
        self.confirm_file = self.directory / 'confirm.json'
        self.pick_file = self.directory / 'pick.json'
        self.process = None
        self.lock = threading.Lock()
        self.nonce = secrets.token_urlsafe(24)

    def status(self):
        if self.process:
            status = read_json(self.status_file)
            if self.process.poll() is None or status.get('phase') == 'paired':
                return status
        return {'phase': 'closed'}

    def start(self):
        with self.lock:
            if read_json(self.root / 'state/receiver/phone-presence.json').get('active'):
                raise ValueError('当前有人使用 CarPlay，请先断开后再添加手机。')
            if self.process and self.process.poll() is None:
                return
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.confirm_file.unlink(missing_ok=True)
            self.pick_file.unlink(missing_ok=True)
            save_json(self.status_file, {'phase': 'starting'})
            self.process = subprocess.Popen(['python3', str(self.root / 'pair-phone.py'), '--root', str(self.root),
                                             '--incoming', '--timeout', '180'],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def confirm(self, request_id, accepted):
        with self.lock:
            status = self.status()
            if status.get('phase') != 'confirm' or not request_id or status.get('request_id') != request_id:
                raise ValueError('配对请求已变化，请刷新页面后重新核对。')
            save_json(self.confirm_file, {'request_id': request_id, 'accepted': accepted})

    def pick(self, address):
        """Box-initiated pairing with a phone seen while its Bluetooth settings page is open."""
        with self.lock:
            status = self.status()
            if self.presence().get('active'):
                raise ValueError('当前有人使用 CarPlay，请先断开后再添加手机。')
            if status.get('phase') != 'waiting' or address not in {d['address'] for d in status.get('found', [])}:
                raise ValueError('这部手机已不在列表中，请刷新页面。')
            save_json(self.pick_file, {'address': address})

    def presence(self):
        return read_json(self.root / 'state/receiver/phone-presence.json')

    def devices(self):
        """Paired, unblocked BlueZ devices as {address: name}; empty when BlueZ is unavailable."""
        import asyncio   # only the settings page and pairing need it; keeps it off the boot path
        try:
            return asyncio.run(asyncio.wait_for(paired_devices(self.root), 3))
        except Exception:
            return {}

    def use(self, phone):
        """Give one already-paired phone the next bounded invitation, e.g. on a new hotspot."""
        with self.lock:
            if self.presence().get('active'):
                raise ValueError('当前有人使用 CarPlay，不会自动抢占；请对方先断开 Wi-Fi 或 CarPlay。')
            if not PHONE.fullmatch(phone) or phone not in self.devices():
                raise ValueError('这部手机未配对，请先添加新手机。')
            save_json(self.root / 'state/receiver/pairing-request.json', {
                'phone': phone, 'boot': get_platform().boot_id(),
                'expires': time.monotonic() + GRANT_SECONDS})

    def remove(self, phone):
        """Forget a phone on the box too: BlueZ pairing, Wi-Fi identities, back-off and any grant."""
        with self.lock:
            if not PHONE.fullmatch(phone) or phone not in self.devices():
                raise ValueError('这部手机已不在已配对列表中，请刷新页面。')
            if self.presence().get('active') == phone:
                raise ValueError('这部手机正在使用 CarPlay，请先断开后再删除。')
            import asyncio
            asyncio.run(asyncio.wait_for(remove_device(self.root, phone), 5))
            state_path = self.root / 'state/receiver/phone-presence.json'
            with state_path.with_suffix('.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                state = read_json(state_path)
                state.get('phones', {}).pop(phone, None)
                state.get('declined', {}).pop(phone, None)
                if state.get('last_phone') == phone:
                    state.pop('last_phone')
                if phone in state.get('paired', []):
                    state['paired'].remove(phone)
                save_json(state_path, state)
            grant = self.root / 'state/receiver/pairing-request.json'
            if read_json(grant).get('phone') == phone:
                grant.unlink(missing_ok=True)
            os.sync()

    def reset_all(self):
        """First-run reset: forget every phone on the box, whoever is using it."""
        self.stop()
        with self.lock:
            import asyncio
            for phone in self.devices():
                asyncio.run(asyncio.wait_for(remove_device(self.root, phone), 5))
            state_path = self.root / 'state/receiver/phone-presence.json'
            with state_path.with_suffix('.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                # Keep only the boot marker, so the reset does not grant another boot-time invitation.
                save_json(state_path, {key: value for key, value in read_json(state_path).items() if key == 'boot'})
            (self.root / 'state/receiver/pairing-request.json').unlink(missing_ok=True)
            os.sync()

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)


async def remove_device(root, phone):
    sys.path.insert(0, str(root / 'linux-bt-bridge/deps'))
    from dbus_next import BusType
    from dbus_next.aio import MessageBus
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        node = await bus.introspect('org.bluez', '/')
        manager = bus.get_proxy_object('org.bluez', '/', node).get_interface('org.freedesktop.DBus.ObjectManager')
        suffix = '/dev_' + phone.replace(':', '_')
        for path, interfaces in (await manager.call_get_managed_objects()).items():
            if path.endswith(suffix) and 'org.bluez.Device1' in interfaces:
                adapter_path = path[:-len(suffix)]
                node = await bus.introspect('org.bluez', adapter_path)
                adapter = bus.get_proxy_object('org.bluez', adapter_path, node).get_interface('org.bluez.Adapter1')
                await adapter.call_remove_device(path)
    finally:
        bus.disconnect()


async def paired_devices(root):
    sys.path.insert(0, str(root / 'linux-bt-bridge/deps'))
    from dbus_next import BusType
    from dbus_next.aio import MessageBus
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        node = await bus.introspect('org.bluez', '/')
        manager = bus.get_proxy_object('org.bluez', '/', node).get_interface('org.freedesktop.DBus.ObjectManager')
        result = {}
        for interfaces in (await manager.call_get_managed_objects()).values():
            props = interfaces.get('org.bluez.Device1', {})
            if props.get('Paired') and props['Paired'].value and not (props.get('Blocked') and props['Blocked'].value):
                address = props['Address'].value.upper()
                result[address] = props['Alias'].value if 'Alias' in props else address
        return result
    finally:
        bus.disconnect()


def is_phone(props):
    """Major device class Phone (iPhones report 0x7a020c); hides headsets, TVs and computers."""
    value = props.get('Class')
    return value is not None and (value.value >> 8) & 0x1f == 2


async def run_incoming(root, timeout):
    """One bounded pairing window.

    Either direction ends in the same numeric comparison: the box pairs with a phone picked on the
    page (verified path: iPhones are discoverable while Settings > Bluetooth is open), or a phone
    that lists the box initiates. Every passkey still needs explicit confirmation on the page.
    """
    import asyncio
    import signal
    from dbus_next import BusType, Variant
    from dbus_next.aio import MessageBus
    from dbus_next.errors import DBusError
    from dbus_next.service import ServiceInterface, method

    os.umask(0o077)
    directory = root / 'state/receiver/pairing-window'
    status_file, confirm_file, pick_file = directory / 'status.json', directory / 'confirm.json', directory / 'pick.json'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stopped.set)
    loop.add_signal_handler(signal.SIGINT, stopped.set)
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    selected = None
    confirmed = False
    pairing = None
    error = ''
    deadline = time.monotonic() + min(timeout, 180)

    def busy():
        return bool(read_json(root / 'state/receiver/phone-presence.json').get('active'))

    async def proxy(path, interface):
        node = await bus.introspect('org.bluez', path)
        return bus.get_proxy_object('org.bluez', path, node).get_interface(interface)

    object_manager = await proxy('/', 'org.freedesktop.DBus.ObjectManager')
    objects = await object_manager.call_get_managed_objects()
    adapters = [path for path, interfaces in objects.items() if 'org.bluez.Adapter1' in interfaces]
    if len(adapters) != 1:
        bus.disconnect()
        raise RuntimeError('Expected a dedicated adapter')
    adapter_path = adapters[0]
    adapter = await proxy(adapter_path, 'org.bluez.Adapter1')
    props = await proxy(adapter_path, 'org.freedesktop.DBus.Properties')
    previous = {key:(await props.call_get('org.bluez.Adapter1', key)).value for key in ('Pairable','Discoverable')}

    class Agent(ServiceInterface):
        def __init__(self):
            super().__init__('org.bluez.Agent1')

        @method()
        async def RequestConfirmation(self, device: 'o', passkey: 'u'):
            nonlocal selected, confirmed
            if (not device.startswith(adapter_path + '/dev_') or stopped.is_set() or
                    (selected is not None and selected != device) or busy()):
                raise DBusError('org.bluez.Error.Rejected', 'Receiver busy')
            selected = device
            request_id = secrets.token_urlsafe(20)
            device_props = await proxy(device, 'org.bluez.Device1')
            name = await device_props.get_alias()
            save_json(status_file, {'phase':'confirm', 'name':name, 'passkey':f'{passkey:06d}', 'request_id':request_id})
            while time.monotonic() < deadline and not stopped.is_set():
                reply = read_json(confirm_file)
                if reply.get('request_id') == request_id:
                    if reply.get('accepted') is True:
                        confirmed = True
                        save_json(status_file, {'phase':'finishing'})
                        return
                    break
                await asyncio.sleep(0.25)
            selected = None
            raise DBusError('org.bluez.Error.Rejected', 'Passkey not confirmed')

        @method()
        def RequestAuthorization(self, device: 'o'):
            raise DBusError('org.bluez.Error.Rejected', 'Numeric confirmation required')

        @method()
        def AuthorizeService(self, device: 'o', uuid: 's'):
            if device != selected or not confirmed or uuid.lower() != '00000000-deca-fade-deca-deafdecacafe':
                raise DBusError('org.bluez.Error.Rejected', 'Unexpected service')

        @method()
        def Cancel(self):
            pass

        @method()
        def Release(self):
            stopped.set()

    async def pair(path):
        nonlocal selected, confirmed, error
        try:
            await (await proxy(path, 'org.bluez.Device1')).call_pair()
        except Exception:
            try:
                done = await (await proxy(path, 'org.bluez.Device1')).get_paired()
            except Exception:
                done = False
            if not done:
                # Also when the page already confirmed: the iPhone never answered (it only shows the
                # request while Settings > Bluetooth is open). Go back to the list instead of waiting.
                selected, confirmed = None, False
                error = '手机没有确认配对。请让 iPhone 停在「设置 → 蓝牙」页面，再点一次它的名字。'
                save_json(status_file, {'phase': 'waiting', 'found': [], 'error': error})
                confirm_file.unlink(missing_ok=True)

    agent_path = '/onecloud/incoming_pair_agent'
    manager = await proxy('/org/bluez', 'org.bluez.AgentManager1')
    bus.export(agent_path, Agent())
    registered = discovering = False
    try:
        await manager.call_register_agent(agent_path, 'DisplayYesNo')
        registered = True
        await manager.call_request_default_agent(agent_path)
        for key in previous:
            # A Barrot USB dongle (33fa:0001) answers the first change with "Authentication Failed"
            # and accepts the next one (2026-10-12).
            for attempt in range(5):
                try:
                    await props.call_set('org.bluez.Adapter1', key, Variant('b', True))
                    break
                except DBusError:
                    await asyncio.sleep(1)   # the failed change may still be "in progress"
                    if (await props.call_get('org.bluez.Adapter1', key)).value:
                        break
                    if attempt == 4:
                        raise
        for attempt in range(5):   # same dongle: "Operation already in progress" right after the above
            try:
                await adapter.call_start_discovery()
                break
            except DBusError:
                await asyncio.sleep(1)
                if (await props.call_get('org.bluez.Adapter1', 'Discovering')).value:
                    break
                if attempt == 4:
                    raise
        discovering = True
        while time.monotonic() < deadline and not stopped.is_set():
            if selected and confirmed:
                device = await proxy(selected, 'org.bluez.Device1')
                if await device.get_paired():
                    await device.set_trusted(True)
                    # BlueZ keeps link keys under /var/lib/bluetooth; flush them before the car is switched off.
                    os.sync()
                    phone = (await device.get_address()).upper()
                    save_json(root / 'state/receiver/pairing-request.json', {
                        'phone':phone, 'boot':get_platform().boot_id(),
                        'expires':time.monotonic() + GRANT_SECONDS})
                    save_json(status_file, {'phase':'paired'})
                    return
            elif selected is None:
                objects = await object_manager.call_get_managed_objects()
                found = sorted(({'address': d['Address'].value.upper(), 'name': d['Alias'].value}
                                for path, interfaces in objects.items()
                                for d in [interfaces.get('org.bluez.Device1')]
                                if d and path.startswith(adapter_path + '/dev_') and is_phone(d)
                                and not d['Paired'].value and 'Name' in d), key=lambda item: item['name'])
                pick = read_json(pick_file).get('address')
                pick_file.unlink(missing_ok=True)
                if pick and not busy() and pick in {item['address'] for item in found}:
                    selected, error = f"{adapter_path}/dev_{pick.replace(':', '_')}", ''
                    save_json(status_file, {'phase':'pairing', 'name':next(i['name'] for i in found if i['address'] == pick)})
                    pairing = asyncio.ensure_future(pair(selected))
                else:
                    save_json(status_file, {'phase':'waiting', 'found':found, 'error':error})
            await asyncio.sleep(1)
        save_json(status_file, {'phase':'closed'})
    finally:
        if pairing and not pairing.done():
            pairing.cancel()
        if discovering:
            try:
                await adapter.call_stop_discovery()
            except Exception:
                pass
        for key,value in previous.items():
            try:
                await props.call_set('org.bluez.Adapter1', key, Variant('b', value))
            except Exception:
                pass
        if registered:
            try:
                await manager.call_unregister_agent(agent_path)
            except Exception:
                pass
        confirm_file.unlink(missing_ok=True)
        pick_file.unlink(missing_ok=True)
        bus.disconnect()
