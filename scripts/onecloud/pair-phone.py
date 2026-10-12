#!/usr/bin/env python3
"""Pair the iPhone with the OneCloud BlueZ adapter after a human checks the passkey.

The passkey is printed and written to /run/dashplay/pair-passkey. Pairing proceeds only
after someone compares it with the iPhone and writes "yes" to /run/dashplay/pair-confirm.
Nothing is auto-accepted. Usage: pair-phone.py --root /opt/dashplay [--timeout 300] [--confirm-timeout 300]
"""
import argparse
import asyncio
import json
from pathlib import Path
import re
import sys

RUN = Path("/run/dashplay")
IAP2_UUID = "00000000-deca-fade-deca-deafdecacafe"


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--incoming", action="store_true", help="Accept a new phone after web passkey confirmation")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--confirm-timeout", type=int, default=300)
    args = parser.parse_args()
    sys.path.insert(0, str(args.root / "linux-bt-bridge/deps"))
    from dbus_next import BusType, Variant
    from dbus_next.aio import MessageBus
    from dbus_next.errors import DBusError
    from dbus_next.service import ServiceInterface, method

    if args.incoming:
        from pairing_window import run_incoming
        await run_incoming(args.root, args.timeout)
        return

    phone = json.loads((args.root / "service.json").read_text())["phone"].upper()
    if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", phone):
        raise SystemExit("Invalid phone address in service.json")
    RUN.mkdir(mode=0o700, exist_ok=True)
    confirm = RUN / "pair-confirm"
    confirm.unlink(missing_ok=True)
    loop = asyncio.get_running_loop()

    class Agent(ServiceInterface):
        def __init__(self):
            super().__init__("org.bluez.Agent1")

        async def _ask(self, passkey):
            text = f"{passkey:06d}"
            (RUN / "pair-passkey").write_text(text + "\n")
            print(f"PASSKEY {text} — compare with the iPhone, then write yes to {confirm}", flush=True)
            until = loop.time() + args.confirm_timeout
            while loop.time() < until:
                if confirm.exists():
                    answer = confirm.read_text().strip().lower()
                    confirm.unlink(missing_ok=True)
                    if answer == "yes":
                        print("Passkey confirmed by operator", flush=True)
                        return
                    break
                await asyncio.sleep(0.5)
            raise DBusError("org.bluez.Error.Rejected", "Passkey not confirmed")

        @method()
        async def RequestConfirmation(self, device: "o", passkey: "u"):
            await self._ask(passkey)

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
            print(f"PASSKEY {passkey:06d} (display only)", flush=True)

        @method()
        async def RequestAuthorization(self, device: "o"):
            raise DBusError("org.bluez.Error.Rejected", "Only confirmed passkey pairing is allowed")

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):
            return

        @method()
        def Cancel(self):
            print("Pairing cancelled by BlueZ", flush=True)

        @method()
        def Release(self):
            return

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    async def proxy(path, interface):
        node = await bus.introspect("org.bluez", path)
        return bus.get_proxy_object("org.bluez", path, node).get_interface(interface)

    agent_path = "/onecloud/pair_agent"
    bus.export(agent_path, Agent())
    manager = await proxy("/org/bluez", "org.bluez.AgentManager1")
    await manager.call_register_agent(agent_path, "KeyboardDisplay")
    await manager.call_request_default_agent(agent_path)
    objects = await (await proxy("/", "org.freedesktop.DBus.ObjectManager")).call_get_managed_objects()
    adapters = [p for p, i in objects.items() if "org.bluez.Adapter1" in i]
    if len(adapters) != 1:
        raise SystemExit("Expected exactly one Bluetooth adapter")
    adapter_path = adapters[0]
    props = await proxy(adapter_path, "org.freedesktop.DBus.Properties")
    adapter = await proxy(adapter_path, "org.bluez.Adapter1")
    for name in ("Powered", "Pairable", "Discoverable"):
        await props.call_set("org.bluez.Adapter1", name, Variant("b", True))
    device_path = f"{adapter_path}/dev_{phone.replace(':', '_')}"
    try:
        if device_path not in objects:
            print("Scanning for the iPhone (keep Settings > Bluetooth open)…", flush=True)
            await adapter.call_start_discovery()
            until = loop.time() + args.timeout
            while device_path not in objects and loop.time() < until:
                await asyncio.sleep(1)
                objects = await (await proxy("/", "org.freedesktop.DBus.ObjectManager")).call_get_managed_objects()
            await adapter.call_stop_discovery()
            if device_path not in objects:
                raise SystemExit("iPhone not found during discovery")
        device = await proxy(device_path, "org.bluez.Device1")
        if not await device.get_paired():
            print("Pairing…", flush=True)
            await device.call_pair()
        await device.set_trusted(True)
        # dbus-next maps the "UUIDs" property to get_uui_ds(); read it by name instead.
        device_props = await proxy(device_path, "org.freedesktop.DBus.Properties")
        uuids = (await device_props.call_get("org.bluez.Device1", "UUIDs")).value
        print(json.dumps({"paired": await device.get_paired(), "bonded": await device.get_bonded(),
                          "trusted": await device.get_trusted(), "iap2_uuid": IAP2_UUID in uuids}), flush=True)
    finally:
        await props.call_set("org.bluez.Adapter1", "Discoverable", Variant("b", False))
        await manager.call_unregister_agent(agent_path)
        (RUN / "pair-passkey").unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
