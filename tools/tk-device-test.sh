#!/bin/bash
# scope: generic
# One check: two concurrent tk-device.sh callers must not interleave.
# If the lock is broken, the two "start" lines land next to each other.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out=$(mktemp); lock=$(mktemp -u)
trap 'rm -f "$out" "$lock" "$lock.holder"' EXIT

run() {
    TK_DEVICE_LOCK="$lock" TK_AGENT="$1" "$here/tk-device.sh" \
        bash -c "echo start-$1 >>'$out'; sleep 0.3; echo end-$1 >>'$out'"
}
run A & run B & wait

mapfile -t l <"$out"
[ "${#l[@]}" -eq 4 ] || { echo "FAIL: expected 4 lines, got ${#l[@]}: ${l[*]}"; exit 1; }
for pair in 0 2; do
    s=${l[$pair]} e=${l[$((pair+1))]}
    [ "${s%%-*}" = start ] && [ "${e%%-*}" = end ] && [ "${s#*-}" = "${e#*-}" ] || {
        echo "FAIL: interleaved: ${l[*]}"; exit 1; }
done
echo "PASS: tk-device.sh serialises concurrent callers"

# --- state gate (opt-in flags). TK_DEVICE_STATE stands in for the probe, so
# --- this runs with no phone attached.
lock2=$(mktemp -u)
trap 'rm -f "$out" "$lock" "$lock.holder" "$lock2" "$lock2.holder"' EXIT
dev() { TK_DEVICE_LOCK="$lock2" TK_AGENT=T "$here/tk-device.sh" "$@"; }

set +e
TK_DEVICE_STATE=FASTBOOT dev --need-booted true >/dev/null 2>&1;   a=$?
TK_DEVICE_STATE=BOOTED   dev --need-fastboot true >/dev/null 2>&1; b=$?
TK_DEVICE_STATE=BOOTED   dev --need-booted true;                   c=$?
TK_DEVICE_STATE=FASTBOOT dev true;                                 d=$?   # no flag: unchanged
TK_DEVICE_STATE=BOOTED   dev false;                                e=$?   # status still the command's
h=$(TK_DEVICE_STATE=FROZEN dev bash -c "cat '$lock2.holder'")
set -e

[ "$a" -eq 76 ] && [ "$b" -eq 76 ] || { echo "FAIL: wrong state must exit 76, got $a/$b"; exit 1; }
[ "$c" -eq 0 ]  || { echo "FAIL: matching state must run, got $c"; exit 1; }
[ "$d" -eq 0 ]  || { echo "FAIL: no flag must ignore state, got $d"; exit 1; }
[ "$e" -eq 1 ]  || { echo "FAIL: command status must propagate, got $e"; exit 1; }
case $h in *state=FROZEN*agent=T*|*agent=T*state=FROZEN*) ;;
    *) echo "FAIL: holder must record the state: $h"; exit 1 ;; esac
echo "PASS: tk-device.sh state gate"

# --- tk_boot_id must treat a transient failure as "unknown", not as a new
# --- boot_id. A fake ssh that fails once and then succeeds stands in for the
# --- ~7.9 s sshd stall that ate a baseline on 2026-08-19; no phone needed.
bindir=$(mktemp -d)
trap 'rm -f "$out" "$lock" "$lock.holder" "$lock2" "$lock2.holder"; rm -rf "$bindir"' EXIT
cat > "$bindir/ssh" <<'FAKE'
#!/bin/sh
n=$(cat "$FAKE_SSH_COUNT" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$FAKE_SSH_COUNT"
[ "$n" -le "$FAKE_SSH_FAILS" ] && exit 255
echo 11111111-2222-3333-4444-555555555555
FAKE
chmod +x "$bindir/ssh"
export FAKE_SSH_COUNT="$bindir/count"
PATH="$bindir:$PATH"
# shellcheck source=tk-lib.sh
. "$here/tk-lib.sh"

: > "$FAKE_SSH_COUNT"; export FAKE_SSH_FAILS=1
id=$(tk_boot_id) || true
[ "$id" = "11111111-2222-3333-4444-555555555555" ] || {
    echo "FAIL: tk_boot_id gave up on a transient failure, got '$id'"; exit 1; }
[ "$(cat "$FAKE_SSH_COUNT")" -eq 2 ] || {
    echo "FAIL: expected exactly 2 attempts, got $(cat "$FAKE_SSH_COUNT")"; exit 1; }

# genuinely unreachable: empty AND non-zero, so callers can tell "unknown"
: > "$FAKE_SSH_COUNT"; export FAKE_SSH_FAILS=99
set +e; id=$(tk_boot_id); rc=$?; set -e
[ -z "$id" ] && [ "$rc" -ne 0 ] || {
    echo "FAIL: unreachable must be empty+non-zero, got '$id' rc=$rc"; exit 1; }
echo "PASS: tk_boot_id retries a transient failure, empty only when truly unreachable"
