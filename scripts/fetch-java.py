#!/usr/bin/env python3
"""Fetch pinned official test runtimes and validate their SHA-256 sums."""
import hashlib
import pathlib
import sys
import urllib.request

ASSETS = {
    'jdk25-linux.tar.gz': (
        'https://github.com/adoptium/temurin25-binaries/releases/download/jdk-25.0.4.1%2B1/OpenJDK25U-jdk_x64_linux_hotspot_25.0.4.1_1.tar.gz',
        'dbb698396d478e7fa2b1e50f4103324b2a99b90569ee27c33f2261f9215cf41e'),
    'jre21-musl.tar.gz': (
        'https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/OpenJDK21U-jre_x64_alpine-linux_hotspot_21.0.12.1_1.tar.gz',
        '7bc7c2f9ba5ffea5f727e14581964bd54294b8edc2be9a79ea6393f8d0799200'),
}

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def main():
    target = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else 'artifacts')
    target.mkdir(parents=True, exist_ok=True)
    for name, (url, expected) in ASSETS.items():
        path = target / name
        if path.exists():
            if digest(path) != expected:
                raise SystemExit(f'Existing file hash mismatch; preserved: {path}')
        else:
            partial = path.with_suffix(path.suffix + '.part')
            with urllib.request.urlopen(url, timeout=60) as response, partial.open('xb') as stream:
                while chunk := response.read(1024 * 1024):
                    stream.write(chunk)
            if digest(partial) != expected:
                raise SystemExit(f'Download hash mismatch; preserved for inspection: {partial}')
            partial.rename(path)
        print(f'PASS {name} {expected}')

if __name__ == '__main__':
    main()
