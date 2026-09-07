#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: FASTBOOT
# env: FASTBOOT, PORTHOLE_SLOT, PORTHOLE_TIMEOUT
# exits: 0 ok · 1 failed · 2 usage
# Flash a boot image to slot b and come back up in pmOS, end to end, unattended.
#
#   validate image -> bootloader -> flash boot_b -> set_active b -> reboot -> ssh
#
# Every phase is timed and polled at 0.5s; nothing here sleeps for a fixed
# worst case. A total of ~40s is normal, and the script returns as soon as ssh
# actually answers rather than after a padded timeout.
#
# The image is validated BEFORE the device is touched -- it must exist, be
# non-empty, and start with the `ANDROID!` magic. Flashing a truncated or
# wrong-format file to boot_b costs a recovery session, and the bootloader will
# happily accept garbage, so this is checked on the host where a mistake is
# free.
#
# Device facts that shape the steps:
#
#   - slot `a` has no good image. Everything goes to boot_b and set_active b.
#     NEVER set_active a.
#   - `fastboot set_active b` also grants exactly 3 boot retries and clears the
#     unbootable flag. Nothing in pmOS ever marks the slot successful, so this
#     is both "select the slot" and "re-arm the counter", and it must come
#     AFTER the flash.
#   - because of that retry counter, the first boot after a flash can still
#     land back in the bootloader on its own. The final wait auto-recovers from
#     that (see ph-lib.sh) instead of reporting a failure.
#   - lsusb mislabels the running pmOS gadget as "fastboot", so `fastboot
#     devices` -- not USB IDs -- is what tells bootloader from booted OS.
#   - getting into the bootloader is unreliable enough to need its own retry
#     logic; that lives in ph-to-fastboot.sh and is reused here.
#
# Usage: ph-flash-boot.sh IMAGE [timeout_seconds]   (default 180, or $PORTHOLE_TIMEOUT)
#   env: PORTHOLE_SLOT       partition to flash (default boot_b)
# Exit:  0 flashed and back up, 2 bad arguments/image, non-zero otherwise.
set -u

cd "$(dirname "$0")" || exit 1
. ./ph-lib.sh

IMG=${1:-}
TIMEOUT=${2:-${PORTHOLE_TIMEOUT:-180}}
SLOT=${PORTHOLE_SLOT:-boot_b}

[ -n "$IMG" ] || { echo "usage: ph-flash-boot.sh IMAGE [timeout_seconds]" >&2; exit 2; }

# ------------------------------------------------------- validate the image --
[ -f "$IMG" ] || { echo "!! no such boot image: $IMG" >&2; exit 2; }
[ -s "$IMG" ] || { echo "!! boot image is empty: $IMG" >&2; exit 2; }

magic=$(head -c 8 "$IMG" 2>/dev/null)
if [ "$magic" != "ANDROID!" ]; then
    echo "!! $IMG is not an Android boot image" >&2
    echo "!! expected magic 'ANDROID!', got '$magic'" >&2
    echo "!! refusing to touch $SLOT" >&2
    exit 2
fi
echo ">> image ok: $IMG ($(stat -c %s "$IMG") bytes, ANDROID! magic)"

# The image is fine; is the TOOL? A $FASTBOOT that cannot run exits 127 with
# empty stdout, which every probe below reads as "not in the bootloader" -- so
# without this the flash spends its whole timeout blaming the phone. Checked
# once, here, rather than on every poll.
ph_need_fastboot

TOTAL_START=$(tk_now_ms)

# ------------------------------------------------------ 1: to the bootloader --
echo ">> [1/4] getting to the bootloader"
phase=$(tk_now_ms)
if ! ./ph-to-fastboot.sh "$TIMEOUT"; then
    echo "!! could not reach the bootloader -- nothing was flashed" >&2
    exit 1
fi
echo ">> [1/4] bootloader in $(tk_since "$phase")s"

# ---------------------------------------------------------------- 2: flash --
echo ">> [2/4] flashing $SLOT"
phase=$(tk_now_ms)
if ! "$FASTBOOT" flash "$SLOT" "$IMG"; then
    echo "!! FLASH FAILED -- $SLOT may be in an unusable state" >&2
    echo "!! the phone is still in the bootloader; retry the flash by hand:" >&2
    echo "!!   $FASTBOOT flash $SLOT $IMG" >&2
    exit 1
fi
echo ">> [2/4] flashed in $(tk_since "$phase")s"

# ----------------------------------------------------------- 3: arm slot b --
echo ">> [3/4] set_active b (selects the slot and resets the 3-retry counter)"
phase=$(tk_now_ms)
if ! "$FASTBOOT" set_active b; then
    echo "!! set_active b FAILED -- the new image will not boot" >&2
    echo "!!   $FASTBOOT set_active b" >&2
    exit 1
fi
echo ">> [3/4] armed in $(tk_since "$phase")s"

# ----------------------------------------------------------- 4: boot it up --
echo ">> [4/4] rebooting and waiting for ssh"
phase=$(tk_now_ms)
if ! "$FASTBOOT" reboot; then
    echo "!! fastboot reboot FAILED" >&2
    exit 1
fi

DEADLINE=$(tk_deadline_ms "$TIMEOUT")
# The old boot_id is irrelevant here: the phone came from the bootloader, so any
# ssh answer at all is the new image talking.
if ! NEW_ID=$(tk_wait_ssh "" "$DEADLINE"); then
    echo "!! [4/4] TIMED OUT after $(tk_since "$phase")s -- $SLOT was flashed but did not come up" >&2
    echo "!! this usually means the new image bootloops. Hold Power+VolDown to reach" >&2
    echo "!! the bootloader and flash a known-good image." >&2
    exit 1
fi
echo ">> [4/4] up in $(tk_since "$phase")s (boot_id ${NEW_ID%%-*})"

echo ">> DONE: $IMG live on $SLOT, total $(tk_since "$TOTAL_START")s"
exit 0
