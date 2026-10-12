#!/usr/bin/env python3
"""Read and rebuild Amlogic USB Burning Tool images (v2), as used for the OneCloud Armbian image.

Layout (checked against Armbian-unofficial_25.11.0-trunk_Onecloud_trixie_current_6.12.43_minimal):
a 64-byte header (CRC-32 of everything after its first 4 bytes, stored as ~crc32; version 2; magic
0x27b51956; total size; alignment; item count), then one 576-byte record per item (id, file type,
offset, size, main type, sub type, verify flag ...), then the item data, each aligned. Every
PARTITION item is followed by a VERIFY item holding "sha1sum <sha1 of that partition's bytes>".
The rootfs partition is an Android sparse image of an ext4 filesystem.

    burnimg.py info IMAGE
    burnimg.py extract IMAGE SUBTYPE OUT            # raw item bytes (e.g. the sparse rootfs)
    burnimg.py replace IMAGE SUBTYPE NEW_ITEM OUT   # new image with that partition (and its VERIFY) replaced
    burnimg.py unsparse SPARSE RAW / burnimg.py sparse RAW SPARSE
"""
import hashlib
import struct
import sys
import zlib

HEADER = 64
RECORD = 576
MAGIC = 0x27B51956
SPARSE_MAGIC = 0xED26FF3A
RAW, FILL, DONT_CARE, CRC = 0xCAC1, 0xCAC2, 0xCAC3, 0xCAC4


def parse(data):
    crc, version, magic, size, align, count = struct.unpack_from("<IIIQII", data, 0)
    if magic != MAGIC or version != 2:
        raise ValueError("not an Amlogic v2 burn image")
    if crc != (~zlib.crc32(data[4:]) & 0xFFFFFFFF):
        raise ValueError("header CRC mismatch")
    items = []
    for index in range(count):
        record = data[HEADER + index * RECORD:HEADER + (index + 1) * RECORD]
        offset, length = struct.unpack_from("<QQ", record, 16)
        items.append({
            "record": record,
            "main": record[32:288].split(b"\0")[0].decode(),
            "sub": record[288:544].split(b"\0")[0].decode(),
            "data": data[offset:offset + length],
        })
    return {"header": data[:HEADER], "align": align, "items": items}


def build(image):
    """Lay the items out again (same order and alignment) and recompute offsets, sizes and CRC."""
    align = image["align"]
    count = len(image["items"])
    position = HEADER + count * RECORD
    records, blobs = [], []
    for item in image["items"]:
        position += -position % align
        record = bytearray(item["record"])
        struct.pack_into("<QQ", record, 16, position, len(item["data"]))
        records.append(bytes(record))
        blobs.append((position, item["data"]))
        position += len(item["data"])
    out = bytearray(position)
    header = bytearray(image["header"])
    struct.pack_into("<Q", header, 12, position)
    out[:HEADER] = header
    for index, record in enumerate(records):
        out[HEADER + index * RECORD:HEADER + (index + 1) * RECORD] = record
    for offset, blob in blobs:
        out[offset:offset + len(blob)] = blob
    struct.pack_into("<I", out, 0, ~zlib.crc32(out[4:]) & 0xFFFFFFFF)
    return bytes(out)


def replace(image, sub, new_data):
    found = False
    for item in image["items"]:
        if item["main"] == "PARTITION" and item["sub"] == sub:
            item["data"] = new_data
            found = True
        elif item["main"] == "VERIFY" and item["sub"] == sub:
            item["data"] = b"sha1sum " + hashlib.sha1(new_data).hexdigest().encode()
    if not found:
        raise KeyError(sub)
    return image


def unsparse(data):
    magic, major, _minor, file_header, chunk_header, block, blocks, chunks, _crc = struct.unpack_from("<IHHHHIIII", data, 0)
    if magic != SPARSE_MAGIC or major != 1:
        raise ValueError("not an Android sparse image")
    out = bytearray(block * blocks)
    position, offset = 0, file_header
    for _ in range(chunks):
        kind, _reserved, count, total = struct.unpack_from("<HHII", data, offset)
        body = data[offset + chunk_header:offset + total]
        length = count * block
        if kind == RAW:
            out[position:position + length] = body
        elif kind == FILL:
            out[position:position + length] = body[:4] * (length // 4)
        elif kind not in (DONT_CARE, CRC):
            raise ValueError(f"unknown sparse chunk {kind:#x}")
        if kind != CRC:
            position += length
        offset += total
    return bytes(out)


def sparse(raw, block=4096):
    """Encode raw bytes: runs of zero blocks become DONT_CARE (the filesystem image is all-zero there)."""
    if len(raw) % block:
        raise ValueError("size must be a multiple of the block size")
    zero = bytes(block)
    chunks, index, blocks = [], 0, len(raw) // block
    while index < blocks:
        start = index
        empty = raw[index * block:(index + 1) * block] == zero
        while index < blocks and (raw[index * block:(index + 1) * block] == zero) == empty:
            index += 1
        count = index - start
        if empty:
            chunks.append(struct.pack("<HHII", DONT_CARE, 0, count, 12))
        else:
            chunks.append(struct.pack("<HHII", RAW, 0, count, 12 + count * block) + raw[start * block:index * block])
    header = struct.pack("<IHHHHIIII", SPARSE_MAGIC, 1, 0, 28, 12, block, blocks, len(chunks), 0)
    return header + b"".join(chunks)


def main(argv):
    command = argv[0]
    if command == "info":
        image = parse(open(argv[1], "rb").read())
        for index, item in enumerate(image["items"]):
            print(index, item["main"], item["sub"], len(item["data"]))
    elif command == "extract":
        image = parse(open(argv[1], "rb").read())
        item = next(i for i in image["items"] if i["main"] == "PARTITION" and i["sub"] == argv[2])
        open(argv[3], "wb").write(item["data"])
    elif command == "replace":
        image = replace(parse(open(argv[1], "rb").read()), argv[2], open(argv[3], "rb").read())
        open(argv[4], "wb").write(build(image))
    elif command == "unsparse":
        open(argv[2], "wb").write(unsparse(open(argv[1], "rb").read()))
    elif command == "sparse":
        open(argv[2], "wb").write(sparse(open(argv[1], "rb").read()))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
