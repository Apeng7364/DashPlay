#!/bin/sh
# Install DashPlay on a fresh Armbian, as root: OneCloud (Armbian-unofficial 25.11 trixie minimal) or
# Phicomm N1 (ophub amlogic-s9xxx-armbian, Debian trixie; see docs/斐讯N1移植预研.md).
# Usage: sh install.sh dashplay-bundle.tar
# Replays exactly what differs between that base image and the working box (2026-10-11 diff):
# four packages, the CPU governor, a 20 MB journal, no boot-time update checks, the service; plus
# the rebuilt rtw88 (box hotspot beside the car hotspot).
set -eu
bundle=${1:?Usage: install.sh dashplay-bundle.tar}
root=/opt/dashplay
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
board=other
grep -qa 'phicomm,n1' /proc/device-tree/compatible 2>/dev/null && board=n1
grep -qai 'onecloud' /proc/device-tree/compatible /proc/device-tree/model 2>/dev/null && board=onecloud
echo "board: $board ($(uname -m), $(uname -r))"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends bluez dnsmasq-base hostapd openjdk-21-jre-headless

mkdir -p "$root"
tar -xpf "$bundle" -C "$root"
chmod 700 "$root/state" "$root/identity" "$root/identity/offline-mfi"

# The receiver needs Java 21 (Debian trixie / Ubuntu noble; bookworm only has 17).
java -version 2>&1 | grep -q 'version "21' || { echo "Java 21 missing: use a trixie (or noble) image" >&2; exit 1; }
# The startup cache is tied to the JVM and CPU it was made on (the bundle's comes from a OneCloud).
[ "$(uname -m)" = armv7l ] || rm -f "$root/state/receiver/app-cds.jsa" "$root/state/receiver/app-cds.classpath"

# meson8b clock switching under ondemand stalled the CPU clock notifier (see supervisor.py); the
# service also sets the governor at start, this only keeps Armbian's own boot setting in line.
[ -f /etc/default/cpufrequtils ] && sed -i 's/^GOVERNOR=.*/GOVERNOR=performance/' /etc/default/cpufrequtils
# The service gets its address on the car hotspot from systemd-networkd (a .network file in /run).
# Images that manage the network with NetworkManager must leave Wi-Fi, the box hotspot interface and
# the wired-CarPlay TAP to DashPlay.
systemctl enable systemd-networkd
if [ -d /etc/NetworkManager ]; then
    mkdir -p /etc/NetworkManager/conf.d
    printf '# DashPlay manages these itself.\n[keyfile]\nunmanaged-devices=interface-name:wl*;interface-name:dpap0;interface-name:dpncm\n' \
        > /etc/NetworkManager/conf.d/dashplay-unmanaged.conf
fi
# Keep the persistent journal small on the 8 GB eMMC.
if grep -q '^#\?SystemMaxUse=' /etc/systemd/journald.conf; then
    sed -i 's/^#\?SystemMaxUse=.*/SystemMaxUse=20M/' /etc/systemd/journald.conf
else
    echo 'SystemMaxUse=20M' >> /etc/systemd/journald.conf
fi
# The box is not updated after delivery; these checks took a full core during every boot.
rm -f /etc/cron.d/armbian-updates
# Masked, not just disabled: on a first boot with an empty machine-id systemd may apply presets
# and re-enable disabled units.
systemctl disable --now apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
systemctl mask apt-daily.timer apt-daily-upgrade.timer

# rtw88 rebuilt with a station + AP combination for the 8821C (scripts/onecloud/wifi/): the box joins
# the car hotspot and raises its own for the iPhone. updates/ wins over the stock modules, which stay.
kernel=6.12.43-current-meson
if [ -d "$root/drivers/rtw88" ] && [ "$(uname -r)" = "$kernel" ]; then
    install -d /lib/modules/$kernel/updates/rtw88
    install -m 644 "$root"/drivers/rtw88/*.ko /lib/modules/$kernel/updates/rtw88/
    depmod -a $kernel
    # Armbian also autoloads Realtek's vendor 8821cu for this dongle. It never binds (rtw88 does), but
    # while it is loaded rtw88's wiphy reports no interface combinations (2026-10-11), so no box hotspot
    # can run beside the car hotspot. Keep it from loading; the module file stays.
    printf "# DashPlay: rtw88 drives the RTL8821CU; the vendor driver hides rtw88's STA + AP combination.\nblacklist 8821cu\n" \
        > /etc/modprobe.d/dashplay-rtw88.conf
elif [ "$board" = onecloud ]; then
    echo "rtw88 for $kernel not installed (running $(uname -r)); not needed by the three ways of use" >&2
fi
# N1: the built-in BCM43455's Bluetooth needs its patch file, or classic Bluetooth (wireless CarPlay
# invitations) does not work.
if [ "$board" = n1 ] && ! ls /lib/firmware/brcm/BCM4345C0*.hcd >/dev/null 2>&1; then
    echo "warning: /lib/firmware/brcm/BCM4345C0*.hcd missing: Bluetooth (wireless CarPlay) will not work" >&2
fi

install -m 644 "$root/dashplay.service" /etc/systemd/system/dashplay.service
install -m 644 "$here/dashplay-firstboot.service" /etc/systemd/system/dashplay-firstboot.service
systemctl daemon-reload
systemctl enable dashplay.service dashplay-firstboot.service
python3 "$root/supervisor.py" --root "$root" --check
echo "DashPlay installed in $root; reboot, or run prepare-image.sh to make a firmware image."
