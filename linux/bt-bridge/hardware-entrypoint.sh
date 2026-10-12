#!/bin/sh
# Private D-Bus and ephemeral BlueZ state, no host filesystem mounts.
set -eu
mkdir -p /run/dbus
printf '[Policy]\nAutoEnable=false\n' > /run/f50-bluez.conf
dbus-daemon --config-file=/project/tests/bluetooth/session.conf \
    --address=unix:path=/run/f50-bridge-bus --fork
bluez_pid=
cleanup() {
    if [ -n "$bluez_pid" ]; then kill "$bluez_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
for daemon in /usr/libexec/bluetooth/bluetoothd /usr/lib/bluetooth/bluetoothd; do
    if [ -x "$daemon" ]; then
        "$daemon" -n -f /run/f50-bluez.conf >&2 &
        bluez_pid=$!
        break
    fi
done
[ -n "$bluez_pid" ] || { echo 'bluetoothd not installed' >&2; exit 1; }
exec_python=/opt/bridge-venv/bin/python
"$exec_python" /project/linux/bt-bridge/probe_adapter.py "$@"
