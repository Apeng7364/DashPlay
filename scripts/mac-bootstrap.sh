#!/bin/sh
# Ready a Mac for the installation guide without installing anything on it: a pinned standalone
# Python 3.12 (astral-sh/python-build-standalone) under WORK/python and, on Apple Silicon, the prebuilt
# spd_dump and lz4 of tools/mac-arm64 checked against their SHA256SUMS. Uses only what macOS ships
# (curl, tar, shasum). Prints the PATH line for the following commands, then MAC_READY.
#   sh scripts/mac-bootstrap.sh "$WORK"
set -eu
work=${1:?usage: mac-bootstrap.sh WORK}
here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ "$(uname -s)" = Darwin ] || { echo "STOP: not macOS" >&2; exit 1; }
release=20261003
case $(uname -m) in
    arm64) arch=aarch64 sum=316a463172740e71d8dca1f2730784e325f3f720941137b5d674d5801a632213 ;;
    x86_64) arch=x86_64 sum=a8fd7a91852f19b6d959793ef41fad048631ccb2a334a9ecdf573255298f7978 ;;
    *) echo "STOP: unknown Mac processor $(uname -m)" >&2; exit 1 ;;
esac
name="cpython-3.12.15+$release-$arch-apple-darwin-install_only.tar.gz"
archive="$work/$name"
mkdir -p "$work"
if [ ! -f "$archive" ]; then
    curl -fL --retry 3 -o "$archive.part" \
        "https://github.com/astral-sh/python-build-standalone/releases/download/$release/cpython-3.12.15%2B$release-$arch-apple-darwin-install_only.tar.gz"
    mv "$archive.part" "$archive"
fi
echo "$sum  $archive" | shasum -a 256 -c - >/dev/null || { echo "STOP: Python download does not match its SHA-256" >&2; exit 1; }
if [ ! -x "$work/python/bin/python3" ]; then
    tar -xzf "$archive" -C "$work"
fi
"$work/python/bin/python3" -c 'import sys, ssl, venv; assert sys.version_info[:2] == (3, 12)'
path="$work/python/bin"
if [ "$arch" = aarch64 ]; then
    (cd "$here/tools/mac-arm64" && shasum -a 256 -c SHA256SUMS >/dev/null) || { echo "STOP: tools/mac-arm64 does not match SHA256SUMS" >&2; exit 1; }
    "$here/tools/mac-arm64/lz4" -V >/dev/null
    "$here/tools/mac-arm64/spd_dump" --help >/dev/null 2>&1 || true   # prints usage; the checksum above is the check
    path="$path:$here/tools/mac-arm64"
fi
echo "export PATH=\"$path:\$PATH\""
echo MAC_READY
