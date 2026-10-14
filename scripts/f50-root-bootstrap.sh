#!/system/bin/sh
# Candidate only: first-stage authenticated access and matching Magisk runtime.
# Execution changes the F50's root environment and requires explicit approval.
set -eu
BASE=${0%/*}
LOG=/data/local/tmp/f50-root-bootstrap.log
test ! -L "$LOG"
umask 077
printf 'bootstrap-v3 started\n' > "$LOG"
TARGET=/data/adb/magisk
test -d /data/adb || mkdir -m 0700 /data/adb
test ! -L /data/adb
test ! -L "$TARGET"
mkdir -p "$TARGET"
chmod 0755 "$TARGET"
runtime_matches=1
for name in busybox magisk magiskboot magiskinit magiskpolicy util_functions.sh boot_patch.sh; do
    source="$BASE/f50-runtime-$name"
    test -f "$source"
    if test -e "$TARGET/$name" || test -L "$TARGET/$name"; then
        test ! -L "$TARGET/$name"
        if ! cmp -s "$source" "$TARGET/$name"; then
            printf 'existing runtime differs: %s (not overwritten)\n' "$name" >> "$LOG"
            runtime_matches=0
        fi
    fi
done
if test "$runtime_matches" = 1; then
  for name in busybox magisk magiskboot magiskinit magiskpolicy util_functions.sh boot_patch.sh; do
    cp "$BASE/f50-runtime-$name" "$TARGET/$name"
    chmod 0755 "$TARGET/$name"
    cmp -s "$BASE/f50-runtime-$name" "$TARGET/$name" || exit 1
  done
  chown -R 0:0 "$TARGET"
  restorecon -R /data/adb
fi
test ! -L /data/misc/adb
mkdir -p /data/misc/adb
KEYS=/data/misc/adb/adb_keys
test ! -L "$KEYS"
touch "$KEYS"
key=$(cat "$BASE/f50-adb.pub")
grep -Fqx "$key" "$KEYS" || printf '%s\n' "$key" >> "$KEYS"
chown 1000:2000 /data/misc/adb "$KEYS"
chmod 2750 /data/misc/adb
chmod 0640 "$KEYS"
restorecon -R /data/misc/adb
grep -Fqx "$key" "$KEYS" || exit 1
printf 'runtime and host key ready\n' >> "$LOG"
# These are boot-session properties, not persist.* settings. Authentication stays on.
"$BASE/f50-tools/magisk" resetprop -n ro.adb.secure 1
"$BASE/f50-tools/magisk" resetprop -n ro.secure 0
"$BASE/f50-tools/magisk" resetprop -n ro.debuggable 1
test "$(getprop ro.adb.secure)" = 1
printf 'authentication ready; waiting for Android boot\n' >> "$LOG"
i=0
while test "$(getprop sys.boot_completed)" != 1 && test "$i" -lt 120; do
    sleep 1
    i=$((i + 1))
done
sleep 5
# The init service survives adbd restarts; issue one atomic control request.
test "$(getprop ro.adb.secure)" = 1
setprop service.adb.tcp.port 5555
setprop ctl.restart adbd
sleep 2
printf 'boot_completed=%s adbd=%s tcp=%s authenticated=%s\n' \
    "$(getprop sys.boot_completed)" "$(getprop init.svc.adbd)" \
    "$(getprop service.adb.tcp.port)" "$(getprop ro.adb.secure)" >> "$LOG"
sync
