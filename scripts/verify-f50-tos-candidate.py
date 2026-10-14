#!/usr/bin/env python3
"""Read-only comparison of the audited B16 TOS candidate to this device's backups.

Requires cryptography. Never writes an image or contacts a device. Passing proves
the described packaging/signatures, not boot compatibility or patch semantics.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def require(condition, message):
    if not condition:
        raise ValueError(message)


def inspect(data, ordinary):
    require(len(data) >= 512 and data[:8] == b"DHTB\x01\0\0\0", "invalid DHTB")
    length = struct.unpack_from("<I", data, 48)[0]
    header = 512 + length
    require(header + 96 <= len(data), "signature header outside image")
    require(data[header:header + 8] == b"SIMGHDR\0", "invalid signature header")
    size, offset, cert_size, cert_offset = struct.unpack_from("<4Q", data, header + 16)
    require(512 <= offset and offset + size <= header, "signed payload outside body")
    require(cert_size == 1080 and cert_offset == header + 96, "unexpected certificate layout")
    require(cert_offset + cert_size <= len(data), "certificate outside image")
    cert = data[cert_offset:cert_offset + cert_size]
    require(cert[:8] == bytes.fromhex("0000000000100000"), "unexpected certificate type/key size")
    require(cert[8:12] == bytes.fromhex("00010001"), "unexpected exponent")
    digest = hashlib.sha256(data[offset:offset + size]).digest()
    require(digest == cert[524:556] == data[8:40], "signed payload digest mismatch")
    if ordinary:
        require(offset == 512 and size == length, "stock payload is not ordinary DHTB")
    key = rsa.RSAPublicNumbers(65537, int.from_bytes(cert[12:524], "big")).public_key()
    key.verify(cert[568:], cert[524:568], padding.PSS(
        mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.AUTO), hashes.SHA256())
    return length, header, cert_offset + cert_size, cert


def verify(stock_tos, stock_sml, candidate):
    tos_length, _, _, _ = inspect(stock_tos, True)
    sml_length, sml_header, sml_end, sml_cert = inspect(stock_sml, True)
    _, _, _, candidate_cert = inspect(candidate, False)
    require(candidate_cert[:568] == sml_cert[:568], "candidate changes stock certificate/key/message")
    require(tos_length == 0x4CD000 and sml_length == 143184, "not the audited B16 layout")
    patched = bytearray(stock_tos[512:512 + tos_length])
    for offset in (0x758E8, 0x774D4):
        position = offset - 512
        require(patched[position:position + 4] == bytes.fromhex("e003142a"), "unexpected stock instruction")
        patched[position:position + 4] = bytes.fromhex("00008052")
    # Reproduce the reviewed packer in memory; never invoke its input-mutating chsize().
    sml = bytearray(stock_sml[:sml_end])
    struct.pack_into("<I", sml, 48, sml_length + tos_length)
    for field in (24, 40):
        value = struct.unpack_from("<Q", sml, sml_header + field)[0]
        struct.pack_into("<Q", sml, sml_header + field, value + tos_length)
    expected = bytes(sml[:512]) + bytes(patched) + bytes(sml[512:])
    require(len(expected) == len(candidate), "unexpected candidate size")
    # PSS signatures may differ due to randomized salt. Both were verified above.
    require(expected[:-512] == candidate[:-512], "unexplained bytes outside RSA-PSS signature")
    return {
        "packaging_matches_device_backups": True,
        "stock_and_candidate_rsa_pss_verified": True,
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
        "instruction_changes": ["0x758e8", "0x774d4"],
        "remaining_gate": "patch semantics and actual device boot compatibility not verified",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock_tos", type=Path)
    parser.add_argument("stock_sml", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.stock_tos.read_bytes(), args.stock_sml.read_bytes(),
                            args.candidate.read_bytes()), indent=2))
