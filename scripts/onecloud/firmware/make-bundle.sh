#!/bin/sh
# Build dashplay-bundle.tar (the /opt/dashplay tree for install.sh) from the repository plus the parts
# that are only on the reference box: the launcher jar, the Bluetooth bridge's private Python deps
# and the receiver jars together with their AppCDS startup cache (the cache is tied to the
# jars' size and mtime, so both come from the box; their hashes must equal this repository's build).
# RTW88_MODULES (OneCloud only, optional) names the output of scripts/onecloud/wifi/build-rtw88-concurrent.sh
# (rtw88 with a station + AP combination for the 8821C; kept for the same-channel box hotspot, which the
# three ways of use no longer need).
# BOARD=n1 builds the bundle for a Phicomm N1 (aarch64): the reference box may still be a OneCloud, since
# everything taken from it is architecture independent, but its AppCDS cache is left out.
#   SSHPASS=… [BOARD=onecloud|n1] [RTW88_MODULES=DIR] sh scripts/onecloud/firmware/make-bundle.sh REFERENCE_BOX OUT.tar [JUMP_HOST]
set -eu
box=${1:?reference box address}
out=${2:?output tar}
jump=${3:-}
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
board=${BOARD:-onecloud}
modules=${RTW88_MODULES:-}
if [ -n "$modules" ]; then
    for module in rtw88_core rtw88_usb rtw88_8821c rtw88_8821cu; do
        [ -s "$modules/$module.ko" ] || { echo "missing $modules/$module.ko" >&2; exit 1; }
    done
fi
cache="state/receiver/app-cds.jsa state/receiver/app-cds.classpath"
[ "$board" = onecloud ] || cache=""
stage=$(mktemp -d "${TMPDIR:-/tmp}/dashplay-bundle.XXXXXX")
trap 'rm -rf "$stage"' EXIT
ssh_box() { sshpass -e ssh -o StrictHostKeyChecking=no ${jump:+-J "$jump"} "root@$box" "$@"; }

ssh_box "cd /opt/dashplay && tar -cpf - --exclude=__pycache__ launcher linux-bt-bridge tls.security playport/server $cache" |
    tar -xpf - -C "$stage"

lib="$repo/research/playport/server/build/install/server/lib"
for jar in "$lib"/*.jar; do
    name=$(basename "$jar")
    cmp -s "$jar" "$stage/playport/server/build/install/server/lib/$name" ||
        { echo "box jar differs from repository build: $name (deploy first)" >&2; exit 1; }
done

for file in "$repo"/scripts/onecloud/*.py "$repo/scripts/onecloud/dashplay.service"; do
    cp "$file" "$stage/"
done
mkdir -p "$stage/playport/web" "$stage/identity/offline-mfi" "$stage/firmware"
if [ -n "$modules" ]; then
    mkdir -p "$stage/drivers/rtw88"
    cp "$modules"/rtw88_core.ko "$modules"/rtw88_usb.ko "$modules"/rtw88_8821c.ko "$modules"/rtw88_8821cu.ko "$stage/drivers/rtw88/"
fi
mkdir -p "$stage/state/receiver"
# The bridge's vendored dbus-next is MIT: its notice must travel with it.
cp "$repo/licenses/dbus-next-0.2.3-MIT.txt" "$stage/linux-bt-bridge/deps/dbus_next-LICENSE.txt"
cp -R "$repo/research/playport/web/dist" "$stage/playport/web/dist"
rm -rf "$stage/playport/web/dist/receiver-access.js" "$stage/playport/web/dist/experiments"   # private tablet tests
cp "$repo"/scripts/onecloud/firmware/install.sh "$repo"/scripts/onecloud/firmware/prepare-image.sh \
   "$repo"/scripts/onecloud/firmware/dashplay-firstboot.service "$stage/firmware/"
printf '{\n  "interface": "auto",\n  "address": "192.168.78.1/24",\n  "channel": 149,\n  "width": 80\n}\n' > "$stage/service.json"
printf '{"ssid": "DashPlay"}\n' > "$stage/state/hotspot.json"

# Nothing personal may ship: no identity, pairing, token, Wi-Fi password or certificate key.
leaks=$(cd "$stage" && find . \( -name '*.pk8' -o -name '*.p7b' -o -name '*.p12' -o -name '*.key' -o -name 'browser-token' \
    -o -name 'airplay-identity.properties' -o -name 'pairings.properties' -o -name 'car-hotspot.json' -o -name 'ddns.json' \
    -o -name 'account.conf' \) -print)
[ -z "$leaks" ] || { echo "refusing to bundle personal files: $leaks" >&2; exit 1; }
tar -C "$stage" -cpf "$out" .
echo "bundle $out: $(tar -tf "$out" | wc -l) entries, $(du -h "$out" | cut -f1)"
