#!/bin/sh
# Build the Apple Silicon helpers shipped in tools/mac-arm64/ so the installation guide needs nothing
# installed on the user's Mac: spd_dump (TomKing062/spreadtrum_flash at the pinned commit, plus
# patches/spreadtrum-read-user-area.patch, with libusb 1.0.29 linked in) and the lz4 command line tool.
# Sources are the pinned "mac-tools-src" group of skills/dashplay-install/downloads.json.
# Run on an Apple Silicon Mac with the Xcode command line tools (not needed by users):
#   sh scripts/build-mac-tools.sh
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ "$(uname -s)/$(uname -m)" = Darwin/arm64 ] || { echo "run on an Apple Silicon Mac" >&2; exit 1; }
out="$here/tools/mac-arm64"
# A neutral build directory: paths in debug info would otherwise carry the builder's user name.
build=$(mktemp -d /tmp/dashplay-mac-tools.XXXXXX)
trap 'rm -rf "$build"' EXIT
export MACOSX_DEPLOYMENT_TARGET=11.0
# Calls newer than the deployment target (libusb picks up pipe2 from a macOS 27 SDK) would crash on older
# macOS: refuse them, and tell libusb to use pipe() instead.
flags="-O2 -ffile-prefix-map=$build=. -Werror=unguarded-availability-new"
python3 "$here/scripts/fetch-install-assets.py" mac-tools-src "$build/src"
cd "$build"

tar -xjf src/libusb-1.0.29.tar.bz2
(cd libusb-1.0.29 && CFLAGS="$flags" ac_cv_func_pipe2=no ./configure --disable-shared --enable-static --prefix="$build/usb" >configure.log \
    && make -j8 >make.log && make install >install.log)
usb_libs=$(sed -n 's/^Libs.private: //p' usb/lib/pkgconfig/libusb-1.0.pc)

spd=spreadtrum_flash-d24c21a0c93a545c0130668caa1826d59ceebe48
tar -xzf src/spreadtrum-source.tar.gz
(cd "$spd" && git apply --unidiff-zero "$here/patches/spreadtrum-read-user-area.patch")
printf '#define GIT_VER "pinned-raw-read"\n#define GIT_SHA1 "d24c21a0c93a545c0130668caa1826d59ceebe48"\n' > "$spd/GITVER.h"
# common.h includes <libusb-1.0/libusb.h>
(cd "$spd" && cc $flags -DUSE_LIBUSB=1 -I"$build/usb/include" spd_dump.c common.c \
    "$build/usb/lib/libusb-1.0.a" $usb_libs -lpthread -lm -o spd_dump)

tar -xzf src/lz4-1.10.0.tar.gz
make -C lz4-1.10.0/programs lz4 CFLAGS="$flags" >lz4.log

mkdir -p "$out"
for tool in "$spd/spd_dump" lz4-1.10.0/programs/lz4; do
    name=$(basename "$tool")
    strip -x "$tool"
    codesign -s - -f "$tool" 2>/dev/null
    # Only the system's own libraries: nothing for the user to install.
    if otool -L "$tool" | tail -n +2 | grep -v -E '^[[:space:]]+(/usr/lib/|/System/Library/)'; then
        echo "$name links a library outside the system" >&2; exit 1
    fi
    if strings "$tool" | grep -F -e "$build" -e "$HOME" >/dev/null; then
        echo "$name contains a build path" >&2; exit 1
    fi
    cp "$tool" "$out/$name"
    chmod 755 "$out/$name"
done
cd "$out"
shasum -a 256 spd_dump lz4 > SHA256SUMS
cat SHA256SUMS
