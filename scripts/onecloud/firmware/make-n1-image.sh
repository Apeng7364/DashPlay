#!/bin/sh
# Turn the ophub Armbian image for the Phicomm N1 into a DashPlay image, on an N1 (aarch64, so the
# image's packages install natively in a chroot). The result boots from a USB stick like the ophub
# image; armbian-install then copies it to the eMMC. Only the MFi identity is left to the user.
#   (on the N1, as root) sh make-n1-image.sh BASE.img dashplay-bundle.tar
# BASE.img (the unpacked ophub trixie image) is modified in place; compress it afterwards.
# The N1 must be online (apt installs Java, BlueZ, hostapd and dnsmasq into the image).
set -eu
img=$(realpath "${1:?base image}")
bundle=$(realpath "${2:?dashplay-bundle.tar}")
mnt=/mnt/dashplay-image
loop=$(losetup -f --show -P "$img")
cleanup() {
    for path in dev/pts dev proc sys run; do umount "$mnt/$path" 2>/dev/null || true; done
    umount "$mnt" 2>/dev/null || true
    losetup -d "$loop" 2>/dev/null || true
}
trap cleanup EXIT
mkdir -p "$mnt"
mount "${loop}p2" "$mnt"
for path in dev dev/pts proc sys; do mount --bind "/$path" "$mnt/$path"; done
mount -t tmpfs tmpfs "$mnt/run"

# Name resolution for apt inside the image, and no service starts from package scripts.
resolv_saved=""
if [ -L "$mnt/etc/resolv.conf" ] || [ -e "$mnt/etc/resolv.conf" ]; then
    mv "$mnt/etc/resolv.conf" "$mnt/etc/resolv.conf.dashplay"; resolv_saved=1
fi
cat /etc/resolv.conf > "$mnt/etc/resolv.conf"
printf '#!/bin/sh\nexit 101\n' > "$mnt/usr/sbin/policy-rc.d"; chmod 755 "$mnt/usr/sbin/policy-rc.d"

mkdir -p "$mnt/root/dashplay-install"
cp "$bundle" "$mnt/root/dashplay-install/dashplay-bundle.tar"
tar -xf "$bundle" -C "$mnt/root/dashplay-install" ./firmware
chroot "$mnt" sh /root/dashplay-install/firmware/install.sh /root/dashplay-install/dashplay-bundle.tar
# prepare-image.sh also empties /root (the bundle copy), resets root's password to 1234, removes
# host keys and the machine id, and zeroes free space so the image compresses.
chroot "$mnt" sh /opt/dashplay/firmware/prepare-image.sh --yes

rm -f "$mnt/usr/sbin/policy-rc.d" "$mnt/etc/resolv.conf"
[ -z "$resolv_saved" ] || mv "$mnt/etc/resolv.conf.dashplay" "$mnt/etc/resolv.conf"
echo "DashPlay installed into $img; compress it (xz) and write it to a USB stick to flash an N1."
