#!/usr/bin/env python3
"""Build an OFFLINE candidate; never flash. Requires lz4 CLI, only pinned inputs.

The existing AVB metadata is relocated, not re-signed. Modified-image acceptance
depends on separately authorized TrustOS changes and remains a hardware test.
"""
import argparse
import base64
import hashlib
import json
import stat
import struct
import subprocess
import zipfile
from pathlib import Path


def checked(condition, message):
    if not condition:
        raise ValueError(message)


def align(n, block):
    return (n + block - 1) // block * block


def unpack_cpio(blob):
    entries = []
    offset = 0
    while True:
        checked(blob[offset:offset + 6] == b"070701", "invalid cpio header")
        fields = [int(blob[offset + 6 + i * 8:offset + 14 + i * 8], 16) for i in range(13)]
        name = blob[offset + 110:offset + 110 + fields[11] - 1].decode()
        begin = align(offset + 110 + fields[11], 4)
        data = blob[begin:begin + fields[6]]
        checked(len(data) == fields[6], "truncated cpio")
        offset = align(begin + fields[6], 4)
        if name == "TRAILER!!!":
            return entries
        entries.append((name, fields, data))


def pack_cpio(entries):
    result = bytearray()
    for name, fields, data in entries + [("TRAILER!!!", [0] * 13, b"")]:
        fields = fields.copy()
        fields[6], fields[11] = len(data), len(name.encode()) + 1
        result += b"070701" + b"".join(f"{v:08x}".encode() for v in fields)
        result += name.encode() + b"\0"
        result += bytes(align(len(result), 4) - len(result))
        result += data
        result += bytes(align(len(result), 4) - len(result))
    return bytes(result)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("image", "apk", "adb_public_key", "output"):
        p.add_argument(name, type=Path)
    args = p.parse_args()
    checked(not args.output.exists(), "refusing to overwrite output")
    image, apk = args.image.read_bytes(), args.apk.read_bytes()
    checked(hashlib.sha256(image).hexdigest() == "5a5fd9d2563a45ad830e58e2c27cfbb284f0a04fa52b5e59e5ff7886ff8dc43b", "unreviewed base image")
    checked(hashlib.sha256(apk).hexdigest() == "fa7e64af14e57194e2463d959e809812e9bdd48f4cc996565706f658e78694de", "unreviewed APK")
    key = args.adb_public_key.read_text().strip().split()[0]
    checked(len(base64.b64decode(key, validate=True)) == 524, "invalid Android RSA public key")
    kernel_size, ramdisk_size = struct.unpack_from("<II", image, 8)
    checked(struct.unpack_from("<I", image, 40)[0] == 4 and struct.unpack_from("<I", image, 1580)[0] == 0, "unsupported boot layout")
    start = 4096 + align(kernel_size, 4096)
    cpio = subprocess.run(["lz4", "-d", "-c"], input=image[start:start + ramdisk_size], capture_output=True, check=True).stdout
    entries = unpack_cpio(cpio)
    # Remove only the reviewed custom service/script; retain Magisk init and backups.
    entries = [x for x in entries if x[0] not in ("overlay.d/adbip.rc", "overlay.d/sbin/start_adbip.sh")]
    def add(name, data, mode=0o755):
        fields = [0, stat.S_IFREG | mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, 0, 0]
        entries.append((name, fields, data))
    # Magisk dispatches by basename; the isolated executable must be named magisk.
    entries.append(("overlay.d/sbin/f50-tools", [0, stat.S_IFDIR | 0o755, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0], b""))
    with zipfile.ZipFile(args.apk) as z:
        add("overlay.d/sbin/f50-tools/magisk", z.read("lib/arm64-v8a/libmagisk.so"))
        for name in ("busybox", "magisk", "magiskboot", "magiskinit", "magiskpolicy"):
            add("overlay.d/sbin/f50-runtime-" + name, z.read(f"lib/arm64-v8a/lib{name}.so"))
        for name in ("util_functions.sh", "boot_patch.sh"):
            add("overlay.d/sbin/f50-runtime-" + name, z.read("assets/" + name))
    add("overlay.d/sbin/f50-adb.pub", (key + "\n").encode(), 0o600)
    add("overlay.d/sbin/f50-root-bootstrap.sh", Path(__file__).with_name("f50-root-bootstrap.sh").read_bytes())
    add("overlay.d/f50-access.rc", b"on late-start\n    start f50_access\n\nservice f50_access ${MAGISKTMP}/f50-root-bootstrap.sh\n    class main\n    user root\n    group root\n    oneshot\n    seclabel u:r:init:s0\n", 0o644)
    raw = pack_cpio(entries)
    compressed = subprocess.run(["lz4", "-l", "-9", "-c"], input=raw, capture_output=True, check=True).stdout
    decoded = subprocess.run(["lz4", "-d", "-c"], input=compressed, capture_output=True, check=True).stdout
    checked(decoded == raw and len(unpack_cpio(decoded)) == len(entries), "ramdisk roundtrip failed")
    checked(image[-64:-60] == b"AVBf", "missing AVB footer")
    old_offset, vbmeta_size = struct.unpack_from(">QQ", image, len(image) - 44)
    new_offset = align(start + len(compressed), 4096)
    checked(new_offset + vbmeta_size < len(image) - 64, "candidate exceeds partition")
    output = bytearray(len(image))
    output[:start] = image[:start]
    struct.pack_into("<I", output, 12, len(compressed))
    output[start:start + len(compressed)] = compressed
    output[new_offset:new_offset + vbmeta_size] = image[old_offset:old_offset + vbmeta_size]
    output[-64:] = image[-64:]
    struct.pack_into(">QQ", output, len(output) - 52, new_offset, new_offset)
    with args.output.open("xb") as f:
        args.output.chmod(0o600)
        f.write(output)
    print(json.dumps({"bytes": len(output), "sha256": hashlib.sha256(output).hexdigest(),
                      "ramdisk_roundtrip": True, "avb_resigned": False,
                      "hardware_validated": False}, indent=2))


if __name__ == "__main__":
    main()
