#!/usr/bin/env python3
"""Build B16 TrustOS from the owner's stock TrustOS/SML. Offline only; never flash.

This deliberately lowers AVB verification. The original SML signature is retained,
not replaced with a signing key. Refuse any layout/instruction not audited for B16.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import struct

spec = importlib.util.spec_from_file_location("tos_verify", Path(__file__).with_name("verify-f50-tos-candidate.py"))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def build(tos, sml):
    length, _, _, _ = audit.inspect(tos, True)
    sml_length, header, end, _ = audit.inspect(sml, True)
    audit.require(length == 0x4CD000 and sml_length == 143184, "not audited B16 layout")
    payload = bytearray(tos[512:512 + length])
    for offset in (0x758E8, 0x774D4):
        pos = offset - 512
        audit.require(payload[pos:pos + 4] == bytes.fromhex("e003142a"), "not audited B16 instruction")
        payload[pos:pos + 4] = bytes.fromhex("00008052")
    signed = bytearray(sml[:end])
    struct.pack_into("<I", signed, 48, sml_length + length)
    for field in (24, 40):
        value = struct.unpack_from("<Q", signed, header + field)[0]
        struct.pack_into("<Q", signed, header + field, value + length)
    candidate = bytes(signed[:512]) + bytes(payload) + bytes(signed[512:])
    result = audit.verify(tos, sml, candidate)
    audit.require(len(candidate) <= len(tos), "candidate exceeds original partition")
    return candidate.ljust(len(tos), b"\0"), result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("stock_tos", type=Path)
    p.add_argument("stock_sml", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    candidate, result = build(a.stock_tos.read_bytes(), a.stock_sml.read_bytes())
    with a.output.open("xb") as out:
        a.output.chmod(0o600)
        out.write(candidate)
    result["padded_bytes"] = len(candidate)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
