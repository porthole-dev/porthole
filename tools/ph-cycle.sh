#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: any (probes state; handles BOOTED and FASTBOOT)
# env: HOST, PHONE, PORTHOLE_USER, PORTHOLE_WORKDIR, TK_IMG
# exits: 0 ok · 1 failed
# One display bring-up experiment: bootloader -> RAM boot -> modprobe -> capture.
#
# Why this exists: bringing the display up means running the same loop dozens of
# times with different module parameters, and every step has a trap.
#
#   - the phone must be in fastboot, but if the last experiment *survived* it is
#     still booted, and msm.ko is pinned by fbcon so it cannot be swapped.
#     `reboot bootloader` from the phone gets there without touching the flashed
#     (display-enabled, therefore bootlooping) slot.
#   - a crashed experiment burns a slot retry, and at 0 the bootloader refuses
#     to hand off at all -- `fastboot set_active` resets the counter. Without
#     this the boot silently fails and looks like a bad kernel.
#   - the SoC can die with nothing in pstore, so dmesg must be *streamed* to the
#     host before the trigger is armed; the last line that arrived is the
#     result. See the debug-stream-dmesg-before-crash notes.
#   - it RAM boots, so nothing is written to flash: a bad module costs a
#     power cycle, not a recovery session.
#
# The image should be one with `modprobe.blacklist=ipa,msm` in its cmdline (see
# bootimg-cmdline.py) so the phone comes up headless and msm can be loaded by
# hand with the parameters under test.
#
# Usage: ph-cycle.sh LOGFILE [modprobe args ...]
#    eg: ph-cycle.sh notc.log no_tearcheck=1
#        ph-cycle.sh base.log
set -u

# shellcheck source=../lib/porthole.sh
. "$(dirname "${BASH_SOURCE[0]:-$0}")/ph-lib.sh"

REPO=$PORTHOLE_WORKDIR
IMG=${TK_IMG:-$REPO/boot-headless.img}
PHONE=${PHONE:-$PORTHOLE_USER@$HOST}
LOG="${1:?usage: ph-cycle.sh LOGFILE [modprobe args ...]}"; shift
ARGS="$*"

[ -f "$IMG" ] || { echo "no boot image at $IMG (set TK_IMG)"; exit 1; }

# If the last experiment survived, the phone is still up: send it back to the
# bootloader rather than power cycling by hand.
if ping -c1 -W2 $HOST >/dev/null 2>&1; then
    echo "### phone is up, rebooting to bootloader"
    timeout 20 ssh "${TK_SSH_OPTS[@]}" "$PHONE" 'sudo reboot bootloader' \
        >/dev/null 2>&1
fi

for _ in $(seq 1 30); do
    timeout 2 fastboot devices 2>/dev/null | grep -q fastboot && break
    sleep 3
done
timeout 2 fastboot devices 2>/dev/null | grep -q fastboot || {
    echo "### phone never reached fastboot"; exit 1; }

# Failed boots decrement the retry counter; at 0 the bootloader will not hand
# off to a RAM-booted image either.
timeout 20 fastboot set_active a >/dev/null 2>&1
timeout 60 fastboot boot "$IMG" >/dev/null 2>&1 || {
    echo "### fastboot boot failed"; exit 1; }

for i in $(seq 1 15); do
    sleep 6
    timeout 5 ssh "${TK_SSH_OPTS[@]}" \
        "$PHONE" true 2>/dev/null && { echo "### booted (~$((i * 6))s)"; break; }
done

# Stream first, trigger second -- a wedge takes the whole SoC down and anything
# still sitting in the ring buffer is lost.
rm -f "$LOG"
ssh "${TK_SSH_OPTS[@]}" \
    "$PHONE" 'sudo dmesg -w' > "$LOG" 2>&1 &
sleep 4

echo "### modprobe msm $ARGS"
timeout 90 ssh "${TK_SSH_OPTS[@]}" "$PHONE" \
    "sudo modprobe msm $ARGS; echo rc=\$?" 2>&1 | tail -1

sleep 8
ping -c1 -W2 $HOST >/dev/null 2>&1 && echo "### ALIVE" || echo "### DEAD"
sleep 8
ping -c1 -W2 $HOST >/dev/null 2>&1 \
    && echo "### still alive at +16s" || echo "### dead by +16s"

echo "### frame-done timeouts: $(grep -ic 'frame done timeout' "$LOG" 2>/dev/null)"
echo "### log: $LOG"
