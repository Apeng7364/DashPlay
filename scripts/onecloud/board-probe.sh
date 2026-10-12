#!/bin/sh
# Read-only check of a new board (first written for the Phicomm N1) before installing DashPlay:
# everything the three ways of use rely on. Changes nothing; run as root on the board:
#   sh board-probe.sh > probe.txt 2>&1
# What to look for is in docs/斐讯N1移植预研.md ("到货检查").
section() { printf '\n== %s\n' "$1"; }
run() { printf '$ %s\n' "$*"; timeout 10 "$@" 2>&1 | head -n "${LINES_MAX:-60}"; }   # never hang (bluetoothctl without an adapter)

section "board"
run cat /proc/device-tree/model; echo
tr '\0' ' ' < /proc/device-tree/compatible 2>/dev/null; echo
run uname -a
run cat /etc/os-release
run free -m
run nproc

section "storage (eMMC / USB stick)"
run lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,MODEL
run df -h /

section "Java (the receiver needs 21)"
run java -version

section "Wi-Fi"
wifi=""
for dev in /sys/class/net/*; do [ -e "$dev/phy80211" ] && wifi=$(basename "$dev"); done
echo "interface: ${wifi:-none}"
[ -n "$wifi" ] && run readlink -f "/sys/class/net/$wifi/device/driver"
run iw reg get
# Modes (AP needed for the box hotspot), combinations (STA + AP), bands and which 5 GHz channels are
# usable without radar detection (no-IR / radar).
LINES_MAX=400 run iw list | grep -E "Supported interface modes|\* (AP|managed|P2P)|valid interface combinations|#\{|Band [0-9]|MHz \[[0-9]+\]"
run sh -c "dmesg | grep -iE 'brcmf|43455|firmware' | tail -20"
run ls /lib/firmware/brcm/

section "Bluetooth (classic, for wireless CarPlay invitations)"
run sh -c "dmesg | grep -iE 'bluetooth|hci|bcm43|BCM4345' | tail -20"
run ls /sys/class/bluetooth/
run bluetoothctl show
run sh -c "ls /lib/firmware/brcm/ | grep -i hcd"

section "USB (wired CarPlay: an iPhone on a host port)"
run lsusb
run lsusb -t
run sh -c "ls /sys/class/usb_role/ 2>/dev/null; for f in /proc/device-tree/soc/usb*/dr_mode /proc/device-tree/soc/*/usb*/dr_mode; do [ -e \"\$f\" ] && echo \"\$f: \$(tr -d '\\0' < \$f)\"; done"
run sh -c "lsmod | grep -iE 'ipheth|cdc_ncm|tun|dwc'"

section "network management"
run sh -c "systemctl is-active NetworkManager systemd-networkd 2>&1"
run ls /etc/netplan /etc/systemd/network /etc/NetworkManager/system-connections
run ip -br link
run ip -br addr

section "packages the service uses"
run sh -c "for p in hostapd wpa_supplicant dnsmasq bluetoothctl iw python3 udevadm networkctl; do printf '%s: ' \$p; command -v \$p || echo missing; done"

section "LEDs"
run ls /sys/class/leds/

section "CPU frequency"
run sh -c "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_frequencies 2>&1"
run sh -c "cat /sys/class/thermal/thermal_zone0/temp 2>&1"
