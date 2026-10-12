#!/bin/sh
# Copy the prepared build box's root partition and pack it into a USB Burning Tool image.
# Run on the Mac/Linux host (needs e2fsprogs and python3; no root):
#   SSHPASS=… sh pack-image.sh BUILD_BOX_ADDRESS BASE.burn.img OUT.burn.img [JUMP_HOST]
# The partition is read under fsfreeze, so it is consistent; it is then checked, shrunk to its
# contents and stored as the sparse rootfs item, keeping the base image's bootloader and kernel.
set -eu
box=${1:?build box address}
base=${2:?base .burn.img}
out=${3:?output .burn.img}
jump=${4:-}
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/dashplay-pack.XXXXXX")
trap 'rm -rf "$work"' EXIT
ssh_box() { sshpass -e ssh -o StrictHostKeyChecking=no ${jump:+-J "$jump"} "root@$box" "$@"; }

device=$(ssh_box 'findmnt -n -o SOURCE /')
echo "copying $device from $box"
ssh_box "sync; fsfreeze -f /; trap 'fsfreeze -u /' EXIT; dd if=$device bs=4M status=none | gzip -1" > "$work/rootfs.ext4.gz"
gzip -d "$work/rootfs.ext4.gz"

e2fsck -fy "$work/rootfs.ext4" || [ $? -le 1 ]
# Start from an empty journal: the old one can still hold fragments of deleted files.
tune2fs -O ^has_journal "$work/rootfs.ext4" >/dev/null
tune2fs -O has_journal "$work/rootfs.ext4" >/dev/null
resize2fs -M "$work/rootfs.ext4"
blocks=$(dumpe2fs -h "$work/rootfs.ext4" 2>/dev/null | awk -F: '/^Block count/{print $2}')
bsize=$(dumpe2fs -h "$work/rootfs.ext4" 2>/dev/null | awk -F: '/^Block size/{print $2}')
truncate -s $((blocks * bsize)) "$work/rootfs.ext4"
e2fsck -fn "$work/rootfs.ext4"

python3 "$here/burnimg.py" sparse "$work/rootfs.ext4" "$work/rootfs.simg"
python3 "$here/burnimg.py" replace "$base" rootfs "$work/rootfs.simg" "$out"
python3 "$here/burnimg.py" info "$out"
echo "written $out"
