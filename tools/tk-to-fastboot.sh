#!/bin/bash
# scope: soc:qcom
# Get the phone INTO the bootloader, and return the instant it lands (~9s).
#
# THE IMPORTANT FACT: `sudo -n reboot bootloader` does NOT reach the bootloader
# on this device, and never did. /usr/sbin/reboot is busybox, whose reboot
# applet is `reboot [-d DELAY] [-nf]` -- no mode argument exists, so the word
# "bootloader" is silently thrown away and you get an ordinary reboot. Every
# time it "worked" it was really the A/B retry counter hitting 0 and the
# bootloader keeping the device by itself. That is why it looked flaky.
#
# The real mechanism, and what this script uses (verified: bootloader in 9s,
# first try):
#
#   reboot(LINUX_REBOOT_CMD_RESTART2, "bootloader")
#
# The kernel plumbing was present all along -- pon@800 is qcom,pm8998-pon with
# `mode-bootloader = <2>` and pm8916-pon is bound to it, so the reboot-mode
# framework writes 2 to the PMIC PON_SOFT_RB_SPARE register that the Pixel 2
# bootloader reads on the next boot. Only userspace was missing: nothing in this
# rootfs issues RESTART2 with a mode string, so tk-lib.sh makes the raw syscall
# through python3. See tk_request_bootloader() there for the constants.
#
# This path does NOT burn a boot retry -- the bootloader stops deliberately, so
# the slot keeps the retries it had. Contrast the fallback below.
#
# FALLBACK (only if python3 is missing, or the PMIC magic is somehow ignored):
# burn boot retries until the counter runs out. `fastboot set_active b` arms
# slot b with exactly 3 retries; nothing in pmOS ever reports a successful boot,
# so the bootloader scores every boot as FAILED and decrements; at 0 it stops
# handing off. From a freshly armed slot that is up to FOUR reboots at ~40s
# each, hence TK_TRIES=4. This countdown is also exactly why the phone drops to
# the bootloader on its own every ~3rd boot during ordinary work -- it is a
# countdown, not a glitch.
#
# The fallback deliberately never calls set_active: re-arming mid-loop would
# reset the counter to 3 and the loop would never converge. Recovery belongs on
# the way OUT (tk-reboot.sh), never on the way in.
#
# Corollary: if you got here via the fallback the counter is at 0, so a bare
# `fastboot reboot` drops straight back into the bootloader. Always
# `set_active b` before rebooting out -- tk-reboot.sh and tk-flash-boot.sh do.
#
# Outcomes are told apart like this:
#   - bootloader: `fastboot devices` prints a line. The ONLY reliable tell --
#     lsusb mislabels the running pmOS gadget as "fastboot" (18d1:d001), so USB
#     IDs cannot distinguish a booted phone from a bootloader.
#   - back in pmOS: ssh answers with a *different* boot_id than the one we
#     snapshotted. Comparing boot_ids rather than "does ssh answer" matters,
#     because the pre-reboot session keeps answering for a second or two.
#
# Usage: tk-to-fastboot.sh [timeout_seconds]   (default 180, or $TK_TIMEOUT)
#   env: TK_ATTEMPT   seconds to wait per reboot        (default 60)
#        TK_TRIES     fallback reboots before giving up (default 4)
#        TK_NO_SYSCALL=1  skip the fast path, force the retry-burn fallback
# Exit:  0 in the bootloader, 1 overall deadline hit, 3 gave up.
set -u

cd "$(dirname "$0")" || exit 1
. ./tk-lib.sh

TIMEOUT=${1:-${TK_TIMEOUT:-180}}
ATTEMPT_S=${TK_ATTEMPT:-60}
TRIES=${TK_TRIES:-4}
SWALLOWED_MAX=${TK_SWALLOWED_MAX:-3}

START=$(tk_now_ms)
DEADLINE=$(tk_deadline_ms "$TIMEOUT")

done_msg() { echo ">> in the bootloader after $(tk_since "$START")s"; }

if tk_in_fastboot; then
    echo ">> already in the bootloader"
    done_msg
    exit 0
fi

OLD_ID=$(tk_boot_id) || OLD_ID=""

# ----------------------------------------------- fast path: RESTART2 syscall --
if [ "${TK_NO_SYSCALL:-0}" != "1" ] && [ -n "$OLD_ID" ] && tk_have_python; then
    echo ">> reboot(RESTART2, \"bootloader\") via the PMIC reboot-mode register"
    tk_request_bootloader

    att_deadline=$(tk_deadline_ms "$ATTEMPT_S")
    [ "$att_deadline" -gt "$DEADLINE" ] && att_deadline=$DEADLINE

    while ! tk_expired "$att_deadline"; do
        if tk_in_fastboot; then
            done_msg
            exit 0
        fi
        if id=$(tk_boot_id) && [ -n "$id" ] && [ "$id" != "$OLD_ID" ]; then
            echo ">> came back up in pmOS -- PMIC mode was not honoured,"
            echo ">> falling back to burning boot retries"
            OLD_ID=$id
            break
        fi
        sleep "$TK_POLL"
    done
else
    echo ">> fast path unavailable (no python3 or TK_NO_SYSCALL=1) -- burning boot retries"
fi

tk_in_fastboot && { done_msg; exit 0; }

# ------------------------------------------ fallback: drain the retry counter --
# `burned` counts reboots that actually happened, i.e. retries consumed. A
# swallowed request costs no retry, so it is counted separately and does not eat
# the budget.
burned=0
swallowed=0

while [ "$burned" -lt "$TRIES" ] && [ "$swallowed" -lt "$SWALLOWED_MAX" ]; do
    tk_expired "$DEADLINE" && break

    if [ -n "$OLD_ID" ]; then
        echo ">> reboot $((burned + 1))/$TRIES: burning a boot retry (boot_id ${OLD_ID%%-*})"
        tk_request_reboot
    else
        echo ">> phone not answering -- watching for the bootloader"
    fi

    att_deadline=$(tk_deadline_ms "$ATTEMPT_S")
    [ "$att_deadline" -gt "$DEADLINE" ] && att_deadline=$DEADLINE

    outcome=timeout
    while ! tk_expired "$att_deadline"; do
        if tk_in_fastboot; then
            echo ">> retry counter hit 0 -- the bootloader kept the device"
            done_msg
            exit 0
        fi
        if id=$(tk_boot_id) && [ -n "$id" ] && [ "$id" != "$OLD_ID" ]; then
            echo ">> back up in pmOS (boot_id ${id%%-*}) -- one retry consumed"
            OLD_ID=$id
            outcome=burned
            break
        fi
        sleep "$TK_POLL"
    done

    if [ "$outcome" = burned ]; then
        burned=$(( burned + 1 ))
        continue
    fi

    tk_expired "$DEADLINE" && break
    swallowed=$(( swallowed + 1 ))
    echo ">> nothing happened in ${ATTEMPT_S}s -- request swallowed, no retry consumed" \
         "($swallowed/$SWALLOWED_MAX)"
    OLD_ID=$(tk_boot_id) || OLD_ID=""
done

if tk_expired "$DEADLINE"; then
    echo ">> TIMED OUT after $(tk_since "$START")s -- never reached the bootloader"
    echo ">> ($burned retries burned; a full counter needs ~4 boots, raise the timeout)"
elif [ "$swallowed" -ge "$SWALLOWED_MAX" ]; then
    echo ">> GAVE UP after $(tk_since "$START")s -- $swallowed reboot requests were swallowed"
    echo ">> the phone is up but not acting on reboot requests"
else
    echo ">> GAVE UP after $burned reboots / $(tk_since "$START")s -- never reached the bootloader"
    echo ">> the retry counter should have hit 0 by now; something is re-arming slot b"
fi
echo ">>"
echo ">> DO THIS BY HAND: power the phone off, then hold Power + Volume-Down"
echo ">> until the bootloader screen appears. Verify with:"
echo ">>   $FASTBOOT devices"
tk_expired "$DEADLINE" && exit 1
exit 3
