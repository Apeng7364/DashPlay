#!/bin/sh
# Build the Windows x64 helpers shipped in tools/windows-x64/, from the same pinned sources as the Mac ones
# (scripts/build-mac-tools.sh): spd_dump with patches/spreadtrum-read-user-area.patch, spreadtrum-windows-libusb.patch
# and libusb 1.0.29 linked in
# (the F50 in download mode then needs the WinUSB driver, installed with Zadig), and the lz4 command line tool.
# Cross-compiled with MinGW-w64 in a debian:trixie container (not privileged, no host network); not needed by users:
#   sh scripts/build-windows-tools.sh
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
out="$here/tools/windows-x64"
build=$(mktemp -d "${TMPDIR:-/tmp}/dashplay-windows-tools.XXXXXX")
trap 'rm -rf "$build"' EXIT
python3 "$here/scripts/fetch-install-assets.py" mac-tools-src "$build/src"
cp "$here/patches/spreadtrum-read-user-area.patch" "$here/patches/spreadtrum-windows-libusb.patch" "$build/"
docker run --rm --security-opt no-new-privileges -e OWNER="$(id -u):$(id -g)" -v "$build:/build" -w /build debian:trixie sh -c '
trap '"'"'chown -R "$OWNER" /build'"'"' EXIT
set -eu
apt-get update -qq >/dev/null && apt-get install -qq -y --no-install-recommends gcc-mingw-w64-x86-64 make bzip2 git ca-certificates >/dev/null
host=x86_64-w64-mingw32
flags="-O2 -ffile-prefix-map=/build=."
tar -xjf src/libusb-1.0.29.tar.bz2
(cd libusb-1.0.29 && CFLAGS="$flags" ./configure --host=$host --disable-shared --enable-static --prefix=/build/usb >configure.log \
    && make -j8 >make.log && make install >install.log)
spd=spreadtrum_flash-d24c21a0c93a545c0130668caa1826d59ceebe48
tar -xzf src/spreadtrum-source.tar.gz
(cd $spd && git apply --unidiff-zero ../spreadtrum-read-user-area.patch)
# Windows only: --kickto by polling the device list (libusb has no hotplug there), unbuffered output on pipes,
# no "pause" on errors
(cd $spd && git apply ../spreadtrum-windows-libusb.patch)
printf "#define GIT_VER \"pinned-raw-read\"\n#define GIT_SHA1 \"d24c21a0c93a545c0130668caa1826d59ceebe48\"\n" > $spd/GITVER.h
# common.h names Windows.h and Dbt.h as on Windows; the MinGW headers are lower case
mkdir -p shim && echo "#include <windows.h>" > shim/Windows.h && echo "#include <dbt.h>" > shim/Dbt.h
usb_libs=$(sed -n "s/^Libs.private: //p" usb/lib/pkgconfig/libusb-1.0.pc)
(cd $spd && $host-gcc-posix $flags -DUSE_LIBUSB=1 -I/build/shim -I/build/usb/include spd_dump.c common.c \
    /build/usb/lib/libusb-1.0.a $usb_libs -static -lpthread -lm -Wl,--no-insert-timestamp,--build-id=none -o spd_dump.exe)
tar -xzf src/lz4-1.10.0.tar.gz
make -C lz4-1.10.0/programs lz4 CC=$host-gcc CFLAGS="$flags" LDFLAGS="-static -Wl,--no-insert-timestamp,--build-id=none" EXT=.exe >lz4.log
mkdir -p bin
cp $spd/spd_dump.exe lz4-1.10.0/programs/lz4.exe bin/
$host-strip bin/*.exe
# Only Windows system DLLs: nothing for the user to install.
for exe in bin/*.exe; do
    $host-objdump -p $exe | sed -n "s/^\tDLL Name: //p" | tr A-Z a-z | grep -v -x -E "kernel32.dll|msvcrt.dll|user32.dll|advapi32.dll|api-ms-win-.*\.dll|ucrtbase.dll|shell32.dll|ole32.dll|setupapi.dll" \
        && { echo "$exe links a non-system DLL" >&2; exit 1; } || true
done
'
mkdir -p "$out"
for tool in spd_dump.exe lz4.exe; do
    if strings "$build/bin/$tool" | grep -F -e "$build" -e "$HOME" >/dev/null; then
        echo "$tool contains a build path" >&2; exit 1
    fi
    cp "$build/bin/$tool" "$out/$tool"
done
cd "$out"
sha256sum spd_dump.exe lz4.exe > SHA256SUMS
cat SHA256SUMS
