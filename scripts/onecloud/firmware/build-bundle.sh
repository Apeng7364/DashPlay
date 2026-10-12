#!/bin/sh
# Build dashplay-bundle.tar (the /opt/dashplay tree for install.sh) from this repository alone, without a
# reference box: for a new board (Phicomm N1) or any fresh install. make-bundle.sh is the OneCloud
# variant that also copies the AppCDS startup cache from a running box.
#   sh scripts/onecloud/firmware/build-bundle.sh OUT.tar
# Needs on the host: the receiver built (PlayPort patched, :server:installDist; see PLAYPORT below), javac 21, pip,
# unzip. Downloads one pinned, hash-checked input: dbus-next 0.2.3 (the Bluetooth bridge's private
# dependency, MIT).
set -eu
out=${1:?output tar}
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
# The PlayPort checkout with the patches applied and :server:installDist run: PLAYPORT, or playport/ (the
# public README's layout), or research/playport (the private workspace).
playport=${PLAYPORT:-}
if [ -z "$playport" ]; then
    for candidate in "$repo/playport" "$repo/research/playport"; do
        [ -d "$candidate/server/build/install/server/lib" ] && { playport=$candidate; break; }
    done
fi
lib="$playport/server/build/install/server/lib"
[ -s "$lib/server-0.1.0.jar" ] || { echo "build the receiver first (:server:installDist)" >&2; exit 1; }
stage=$(mktemp -d "${TMPDIR:-/tmp}/dashplay-bundle.XXXXXX")
work=$(mktemp -d "${TMPDIR:-/tmp}/dashplay-build.XXXXXX")
trap 'rm -rf "$stage" "$work"' EXIT

# Program and service.
# Committed files only: work in progress in the tree must not ship.
for file in $(git -C "$repo" ls-files 'scripts/onecloud/*.py') scripts/onecloud/dashplay.service scripts/onecloud/tls.security; do
    case "$file" in scripts/onecloud/*/*) continue ;; esac   # the top level only, as on the box
    cp "$repo/$file" "$stage/"
done
mkdir -p "$stage/playport/server/build/install/server" "$stage/playport/web" "$stage/identity/offline-mfi" \
    "$stage/firmware" "$stage/state/receiver" "$stage/launcher" "$stage/linux-bt-bridge/deps"
cp -R "$lib" "$stage/playport/server/build/install/server/lib"
cp -R "$playport/web/dist" "$stage/playport/web/dist"
rm -rf "$stage/playport/web/dist/receiver-access.js" "$stage/playport/web/dist/experiments"   # private tablet tests

# Launcher: warms the handshake crypto once AirPlay listens (docs/玩客云移植预研.md).
javac --release 21 -cp "$lib/*" -d "$work/launcher" "$repo/scripts/onecloud/launcher/onecloud/Launcher.java"
jar --create --file "$stage/launcher/onecloud-launcher.jar" -C "$work/launcher" .

# Bluetooth bridge with its private dbus-next (never installed into the system Python).
for file in bt-bridge bridge.py relay.py probe_adapter.py; do cp "$repo/linux/bt-bridge/$file" "$stage/linux-bt-bridge/"; done
chmod 755 "$stage/linux-bt-bridge/bt-bridge"
python3 -m pip download --quiet --no-deps --only-binary=:all: --require-hashes \
    -r "$repo/linux/bt-bridge/requirements.txt" -d "$work/wheels"
unzip -q "$work"/wheels/dbus_next-0.2.3-*.whl 'dbus_next/*' -d "$stage/linux-bt-bridge/deps"
cp "$repo/licenses/dbus-next-0.2.3-MIT.txt" "$stage/linux-bt-bridge/deps/dbus_next-LICENSE.txt"

cp "$repo"/scripts/onecloud/firmware/install.sh "$repo"/scripts/onecloud/firmware/prepare-image.sh \
   "$repo"/scripts/onecloud/firmware/dashplay-firstboot.service "$repo/scripts/onecloud/board-probe.sh" "$stage/firmware/"
printf '{\n  "interface": "auto",\n  "address": "192.168.78.1/24",\n  "channel": 149,\n  "width": 80\n}\n' > "$stage/service.json"
printf '{"ssid": "DashPlay"}\n' > "$stage/state/hotspot.json"
find "$stage" -name __pycache__ -type d -prune -exec rm -rf {} +

# Nothing personal may ship: no identity, pairing, token, Wi-Fi password or certificate key.
leaks=$(cd "$stage" && find . \( -name '*.pk8' -o -name '*.p7b' -o -name '*.p12' -o -name '*.key' -o -name 'browser-token' \
    -o -name 'airplay-identity.properties' -o -name 'pairings.properties' -o -name 'car-hotspot.json' -o -name 'ddns.json' \
    -o -name 'account.conf' -o -name 'lockdown-pair-record*' \) -print)
[ -z "$leaks" ] || { echo "refusing to bundle personal files: $leaks" >&2; exit 1; }
# macOS tar would add Apple extended attributes that GNU tar on the box warns about.
xattrs=""
tar --version 2>/dev/null | grep -q bsdtar && xattrs="--no-xattrs --no-mac-metadata"
COPYFILE_DISABLE=1 tar $xattrs -C "$stage" -cpf "$out" .
echo "bundle $out: $(tar -tf "$out" | wc -l | tr -d ' ') entries, $(du -h "$out" | cut -f1)"
