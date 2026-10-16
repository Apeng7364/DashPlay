#!/usr/bin/env python3
"""Download pinned installation inputs from their named upstream; refuse changed bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('group',choices=('f50','n1','onecloud','adb-mac','adb-windows','zadig','mac-tools-src'))
    p.add_argument('out',type=Path)
    a=p.parse_args()
    manifest=Path(__file__).resolve().parents[1]/'skills/dashplay-install/downloads.json'
    a.out.mkdir(parents=True,exist_ok=True,mode=0o700)
    for item in json.loads(manifest.read_text()):
        if item['group'] != a.group:continue
        target=a.out/item['file']
        if target.exists():data=target.read_bytes()
        else:
            with urllib.request.urlopen(item['url'],timeout=120) as response:data=response.read()
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise SystemExit(f"HASH_MISMATCH {item['file']}: stop; do not use this input")
        if not target.exists():
            with target.open('xb') as f:f.write(data)
        print('SHA256_OK',item['file'])


if __name__=='__main__':main()
