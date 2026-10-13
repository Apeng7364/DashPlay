#!/bin/sh
# Turn the build box (fresh Armbian + install.sh) into a clean "new device", ready to be copied
# into a firmware image. Run as root on the build box; afterwards copy the root partition with
# pack-image.sh. Never run on a box in use: it deletes pairings, hotspots, logs and keys.
set -eu
root=/opt/dashplay
[ "${1:-}" = "--yes" ] || { echo "This wipes all user data on this box. Re-run with --yes." >&2; exit 2; }

systemctl stop dashplay.service || true

# DashPlay: keep the program and the startup cache only; the hotspot ships with a name, no password.
cache=$(mktemp -d)
cp -p "$root/state/receiver/app-cds.jsa" "$root/state/receiver/app-cds.classpath" "$cache/" 2>/dev/null || true
rm -rf "$root/state" "$root"/backup-* "$root"/stage-* "$root/identity/offline-mfi/"*
find "$root" -name __pycache__ -type d -prune -exec rm -rf {} +
mkdir -p -m 700 "$root/state/receiver"
chmod 700 "$root/state"   # mkdir -p -m sets the mode of the last component only
cp -p "$cache"/* "$root/state/receiver/" 2>/dev/null || true
rm -rf "$cache"
printf '{"ssid": "DashPlay"}\n' > "$root/state/hotspot.json"
chmod 600 "$root/state/hotspot.json"
rm -f "$root/playport/web/dist/receiver-access.js" "$root/playport/web/dist/receiver-access.json" "$root/playport/web/dist/temporary-metrics.js"
rm -rf "$root/playport/web/dist/experiments"   # browser tests from development, not part of the product

# System: no Bluetooth pairings, no per-box identity, no logs, no package caches.
rm -rf /var/lib/bluetooth/*
rm -f /etc/ssh/ssh_host_*            # dashplay-firstboot.service creates new ones
: > /etc/machine-id                  # systemd creates a new one on first boot
rm -f /var/lib/dbus/machine-id
rm -f /etc/netplan/20-eth-fixed-mac.yaml /etc/armbian-leds.conf
rm -f /etc/systemd/system/getty@.service.d/override.conf /etc/systemd/system/serial-getty@.service.d/override.conf
apt-get clean
rm -rf /var/lib/apt/lists/*
journalctl --rotate >/dev/null 2>&1 || true
journalctl --vacuum-time=1s >/dev/null 2>&1 || true
for dir in /var/log /var/log.hdd; do
    [ -d "$dir" ] && find "$dir" -type f \( -name '*.gz' -o -name '*.[0-9]' -o -name '*.old' \) -delete && \
        find "$dir" -type f -exec truncate -s 0 {} +
done
rm -f /root/.bash_history /root/.lesshst /root/.not_logged_in_yet
rm -rf /root/.ssh /tmp/* /var/tmp/*
# Anything else left in /root (e.g. backups of the car hotspot made while testing) holds personal
# data; only the shell's own dotfiles stay.
find /root -mindepth 1 -maxdepth 1 ! -name .bashrc ! -name .profile ! -name .bash_aliases -exec rm -rf {} +

# root keeps the agreed password (1234) so the owner's AI assistant can log in after flashing.
echo 'root:1234' | chpasswd
chage -d "$(date +%F)" -M 99999 root

# Updates stay off even if the first boot applies presets (empty machine-id), see install.sh.
systemctl mask apt-daily.timer apt-daily-upgrade.timer
# Grow the filesystem to the whole eMMC partition on the first boot (Armbian's own service).
# (ophub images for the N1 grow it their own way; the service may not exist there.)
systemctl enable armbian-resize-filesystem.service 2>/dev/null || echo "armbian-resize-filesystem not present" >&2

# Zero free space so the image compresses and the sparse rootfs stays small.
dd if=/dev/zero of=/zero.fill bs=1M status=none 2>/dev/null || true
rm -f /zero.fill
sync
echo "Clean. Now copy the root partition (pack-image.sh), then power the box off without booting it again."
