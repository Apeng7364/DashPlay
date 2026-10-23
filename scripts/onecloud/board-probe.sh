#!/bin/sh
# Read-only check of a new board (first written for the Phicomm N1) before installing DashPlay:
# everything the three ways of use rely on. Changes nothing; run as root on the board:
#   sh board-probe.sh > probe.txt 2>&1
# What to look for is in docs/斐讯N1移植预研.md ("到货检查"); for a Snapdragon 410 stick also
# docs/高通410随身WiFi移植预研.md (the "msm8916" section at the end).
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
run sh -c "lsmod | grep -iE 'ipheth|cdc_ncm|tun|dwc|ci_hdrc'"

section "network management"
run sh -c "systemctl is-active NetworkManager systemd-networkd 2>&1"
run ls /etc/netplan /etc/systemd/network /etc/NetworkManager/system-connections
run ip -br link
run ip -br addr

section "packages the service uses"
run sh -c "for p in hostapd wpa_supplicant dnsmasq bluetoothctl iw python3 udevadm networkctl; do printf '%s: ' \$p; command -v \$p || echo missing; done"

section "LEDs"
run ls /sys/class/leds/

section "ports the service needs (53 DNS, 67 DHCP, 80/443 web) and who holds them"
run sh -c "ss -lntup 2>/dev/null | grep -E ':(53|67|80|443) ' || echo 'none in use'"

section "CPU frequency"
run sh -c "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_frequencies 2>&1"
run sh -c "cat /sys/class/thermal/thermal_zone0/temp 2>&1"

# Snapdragon 410 (MSM8916) 4G sticks: Wi-Fi and Bluetooth are the WCNSS core with an external WCN36xx radio
# (wcn36xx, btqcomsmd); the modem keeps part of the 512 MB.
if tr '\0' '\n' < /proc/device-tree/compatible 2>/dev/null | grep -qx 'qcom,msm8916'; then
    section "msm8916: radio chip (qcom,wcn3620 = 2.4 GHz only), firmware and addresses"
    run sh -c "for f in /proc/device-tree/soc*/remoteproc*/iris/compatible /proc/device-tree/soc*/*/wcnss*/iris/compatible; do [ -e \"\$f\" ] && echo \"\$f: \$(tr '\\0' ' ' < \$f)\"; done"
    # local-mac-address / local-bd-address: without them Wi-Fi takes the NV default and Bluetooth stays unconfigured
    run sh -c "find /proc/device-tree -name 'local-mac-address' -o -name 'local-bd-address' | while read f; do echo \"\$f: \$(od -An -tx1 \$f)\"; done"
    run sh -c "dmesg | grep -iE 'wcn36xx|wcnss|btqcomsmd|qcom_wcnss|remoteproc|mpss|modem' | tail -30"
    run sh -c "ls -la /lib/firmware/wlan/prima/ /lib/firmware/qcom/msm8916/ 2>&1 | head -40"
    run cat /sys/class/net/wlan0/address
    section "msm8916: Bluetooth controller state (unconfigured = no address yet)"
    run btmgmt config
    run btmgmt info
    section "msm8916: memory kept by the firmware, swap/zram"
    run sh -c "grep -E 'MemTotal|MemAvailable|SwapTotal' /proc/meminfo"
    run sh -c "cat /proc/swaps; ls /sys/block | grep zram"
    run sh -c "dmesg | grep -iE 'reserved|Memory:' | head -10"
    section "msm8916: USB role (device = the plug; wired CarPlay needs host and power out)"
    run sh -c "for r in /sys/class/usb_role/*/role; do echo \"\$r: \$(cat \$r)\"; done"
    run sh -c "ls /sys/kernel/config/usb_gadget/ 2>&1; cat /sys/class/udc/*/state 2>&1"
    section "msm8916: 4G modem and SIM"
    run sh -c "command -v mmcli && mmcli -L"
    run sh -c "ip -br link | grep -iE 'wwan|rmnet|qmi'"
    section "msm8916: the image's own hotspot and services (they would hold wlan0, 53 or 67)"
    run sh -c "nmcli -t -f NAME,TYPE,DEVICE,AUTOCONNECT connection show 2>&1"
    run sh -c "systemctl list-unit-files --state=enabled 2>/dev/null | grep -iE 'hostapd|dnsmasq|openstick|rmtfs|qrtr|ModemManager|usb|gadget'"
    run sh -c "cat /etc/openstick-board 2>&1; command -v openstick-board"
    run sh -c "cat /boot/extlinux/extlinux.conf 2>&1 | head -20"
fi
