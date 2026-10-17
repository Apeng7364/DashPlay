#!/usr/bin/env python3
"""Download pinned installation inputs from their named upstream; refuse changed bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from install_progress import Progress  # noqa: E402


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
            # in pieces with progress (WORK/progress/download-<file>.json beside the output folder, install_progress.py)
            with urllib.request.urlopen(item['url'],timeout=120) as response, \
                    Progress(a.out.parent,f"download-{item['file']}",f"下载 {item['file']}",
                             total=int(response.headers.get('Content-Length') or 0) or None,expected=300) as p:
                parts=[]
                while chunk:=response.read(1<<20):parts.append(chunk);p.advance(len(chunk))
                data=b''.join(parts)
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise SystemExit(f"HASH_MISMATCH {item['file']}: stop; do not use this input")
        if not target.exists():
            with target.open('xb') as f:f.write(data)
        print('SHA256_OK',item['file'])


if __name__=='__main__':main()
