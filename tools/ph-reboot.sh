#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: any (probes state; handles BOOTED and FASTBOOT)
# env: FASTBOOT, TK_FORCE, TK_TIMEOUT
# exits: 0 ok · 1 failed · 64 bad argument
# Reboot the phone and return the INSTANT ssh answers again.
#
# This used to sleep a flat 30s after asking for the reboot and then poll every
# 5s, so a boot that really took ~20s cost 35-40s of wall clock, and a bootloader
# recovery cost another 30s on top. It now polls every 0.5s and returns as soon
# as the device is genuinely back.
#
# Device quirks this handles, none of which are optional:
#
#   - "ssh answers" != "it rebooted". The pre-reboot session can keep answering
#     for a second or two after `reboot` is issued, and a naive poll returns
#     immediately against the OLD kernel. We snapshot
#     /proc/sys/kernel/random/boot_id first and only accept a DIFFERENT one.
#   - the phone drops to the bootloader on a countdown, not at random. Nothing
#     in pmOS ever reports a successful boot (`slot-successful:b` stays `no`),
#     so the bootloader scores every boot as FAILED and decrements the 3-retry
#     counter that `set_active b` handed out; at 0 it keeps the device. So
#     every ~3rd invocation of this script WILL land in the bootloader with
#     nothing actually broken. Slot `a` has no good image, so the bootloader's
#     own fallback is useless. The fix is always the same two commands, and
#     set_active resets the counter to 3 -- which is what makes this script
#     self-sustaining rather than needing a human every third run.
#   - lsusb mislabels the running pmOS gadget as "fastboot" (18d1:d001), so USB
#     IDs can't tell a booted phone from a bootloader. `fastboot devices` only
#     prints in the real bootloader -- that is what we key off.
#   - `sudo -n reboot` over ssh gets swallowed, and on this device it can be
#     swallowed EVERY time: it exits 0, queues no job, and the phone stays up.
#     So a stuck reboot ESCALATES rather than repeating -- `systemctl reboot -i`,
#     then `reboot -f`, then sysrq -- at 12s per step. Repeating the request that
#     just failed is how this used to burn a full 180s timeout and still not
#     reboot.
#   - not every "swallowed" reboot is a hang. At the greeter phrog/greetd hold a
#     shutdown inhibitor and systemd REFUSES the request, out loud, and stays up
#     -- so the first escalation lists the inhibitors and re-asks with -i, and
#     only a device that ignores THAT is worth forcing. Going straight to
#     `reboot -f` and sysrq took this rootfs down uncleanly for what should have
#     been a normal reboot (#38).
#
# Measured on this device, host-side wall clock:
#
#   from booted pmOS, graceful   ~45s   (~31s of it is OpenRC stopping services,
#                                        only ~14s is the actual boot)
#   from booted pmOS, TK_FORCE=1 ~30s   (`reboot -f`, down in ~7s)
#   from the bootloader          ~26s   (~13s handoff + ~13s to sshd)
#
# So on a clean graceful reboot the polling saves only the old script's ~1-5s of
# granularity -- the win is on the bootloader-recovery path, where the old code
# burned two fixed 30s sleeps, and from TK_FORCE=1.
#
# Usage: ph-reboot.sh [timeout_seconds]      (default 180, or $TK_TIMEOUT)
#   env: TK_FORCE=1  skip OpenRC shutdown (~15s faster, syncs first)
# Exit:  0 back up, 1 timed out.
set -u

cd "$(dirname "$0")" || exit 1
. ./ph-lib.sh

TIMEOUT=${1:-${TK_TIMEOUT:-180}}
# Checked BEFORE anything is asked of the device. `ph-reboot.sh --help` took
# "--help" as the timeout, issued a real reboot, and then waited against an
# empty deadline -- so the phone went down and nothing was left waiting for it
# to come back. An argument this script does not understand must cost nothing.
case $TIMEOUT in
    ''|*[!0-9]*)
        echo ">> usage: ph-reboot.sh [timeout_seconds]  (default 180, or \$TK_TIMEOUT)" >&2
        echo ">>   env: TK_FORCE=1  skip service shutdown (~15s faster, syncs first)" >&2
        exit 64 ;;
esac
START=$(tk_now_ms)
DEADLINE=$(tk_deadline_ms "$TIMEOUT")

if tk_in_fastboot; then
    # Already sitting in the bootloader: nothing to ask, just re-arm and go.
    echo ">> already in the bootloader -- re-arming slot b and booting"
    tk_rearm_and_boot || { echo ">> FAILED to re-arm slot b"; exit 1; }
    OLD_ID=""
else
    OLD_ID=$(tk_boot_id) || OLD_ID=""
    if [ -n "$OLD_ID" ]; then
        echo ">> rebooting (boot_id ${OLD_ID%%-*}, up $(tk_uptime)s)"
        tk_request_reboot
    else
        echo ">> phone is not answering and not in the bootloader -- waiting for it"
    fi
fi

if NEW_ID=$(tk_wait_ssh "$OLD_ID" "$DEADLINE" tk_reboot_escalate 12); then
    echo ">> up in $(tk_since "$START")s (boot_id ${NEW_ID%%-*}, uptime $(tk_uptime)s)"
    exit 0
fi

echo ">> TIMED OUT after $(tk_since "$START")s waiting for the phone"
echo ">> if it is wedged, hold Power+VolDown to reach the bootloader, then:"
echo ">>   $FASTBOOT set_active b && $FASTBOOT reboot"
exit 1
