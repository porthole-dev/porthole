#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: soc:qcom
# needs: any (probes state; handles BOOTED and FASTBOOT)
# env: FASTBOOT, PORTHOLE_USB_FASTBOOT_ID, PORTHOLE_USB_GADGET_ID, TK_ATTEMPT,
#      TK_NO_SYSCALL, TK_POLL, TK_SWALLOWED_MAX, TK_TIMEOUT, TK_TRIES
# exits: 0 ok · 1 failed · 3 see source
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
# rootfs issues RESTART2 with a mode string, so ph-lib.sh makes the raw syscall
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
# the way OUT (ph-reboot.sh), never on the way in.
#
# Corollary: if you got here via the fallback the counter is at 0, so a bare
# `fastboot reboot` drops straight back into the bootloader. Always
# `set_active b` before rebooting out -- ph-reboot.sh and ph-flash-boot.sh do.
#
# Outcomes are told apart like this:
#   - bootloader: `fastboot devices` prints a line. The ONLY reliable tell --
#     lsusb mislabels the running pmOS gadget as "fastboot" (18d1:d001), so USB
#     IDs cannot distinguish a booted phone from a bootloader.
#   - back in pmOS: ssh answers with a *different* boot_id than the one we
#     snapshotted. Comparing boot_ids rather than "does ssh answer" matters,
#     because the pre-reboot session keeps answering for a second or two.
#   - IN THE BOOTLOADER BUT NOT ON THE BUS: neither of the above ever becomes
#     true, because the phone is not enumerated at all. This is a real state
#     and it is indistinguishable from the two failures above unless something
#     reads the bus -- ph_usb_state does, and every message below switches on
#     it. Observed 2026-09-01: the phone reached the bootloader screen and its
#     USB did not come up on this host until the cable was replugged, 26
#     minutes of which were spent following advice for a different fault.
#
# Usage: ph-to-fastboot.sh [timeout_seconds]   (default 180, or $TK_TIMEOUT)
#   env: TK_ATTEMPT   seconds to wait per reboot        (default 60)
#        TK_TRIES     fallback reboots before giving up (default 4)
#        TK_NO_SYSCALL=1  skip the fast path, force the retry-burn fallback
# Exit:  0 in the bootloader, 1 overall deadline hit, 3 gave up.
set -u

cd "$(dirname "$0")" || exit 1
. ./ph-lib.sh

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
        echo ">> phone not answering -- watching for the bootloader (usb: $(ph_usb_state))"
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

    # "Swallowed" is a claim that the phone IGNORED the request and kept
    # running. A phone that has left the USB bus ignored nothing -- it
    # rebooted, and the host simply cannot see where it landed. Spending the
    # give-up budget on that reads as "the phone is up but not acting on
    # reboot requests", which is the opposite of what is happening, and it
    # ends in advice for the wrong fault.
    if [ "$(ph_usb_state)" = absent ]; then
        echo ">> the phone is GONE FROM THE USB BUS -- it did reboot, and nothing has"
        echo ">> enumerated since. Not a swallowed request, so no budget spent."
        OLD_ID=""
        continue
    fi

    swallowed=$(( swallowed + 1 ))
    echo ">> nothing happened in ${ATTEMPT_S}s -- request swallowed, no retry consumed" \
         "($swallowed/$SWALLOWED_MAX)"
    OLD_ID=$(tk_boot_id) || OLD_ID=""
done

if tk_expired "$DEADLINE"; then
    # NOT "never reached the bootloader" -- that is a claim about the phone,
    # and this script cannot make it. What it can say is that the bootloader
    # never answered here, which is true whichever of the three states holds.
    echo ">> TIMED OUT after $(tk_since "$START")s -- the bootloader never answered"
    echo ">> ($burned retries burned; a full counter needs ~4 boots, raise the timeout)"
elif [ "$swallowed" -ge "$SWALLOWED_MAX" ]; then
    echo ">> GAVE UP after $(tk_since "$START")s -- $swallowed reboot requests were swallowed"
    echo ">> the phone is up but not acting on reboot requests"
else
    echo ">> GAVE UP after $burned reboots / $(tk_since "$START")s -- never reached the bootloader"
    echo ">> the retry counter should have hit 0 by now; something is re-arming slot b"
fi
# The advice depends entirely on which of the three states the bus is in, and
# for two of them "power the phone off and hold Power + Volume-Down" is wrong
# -- in one case actively destructive, since it throws away a bootloader the
# phone had already reached.
echo ">>"
case $(ph_usb_state) in
absent)
    echo ">> THE USB BUS IS EMPTY: no ${PORTHOLE_USB_FASTBOOT_ID:-bootloader} and no"
    echo ">> ${PORTHOLE_USB_GADGET_ID:-gadget} device is enumerated on this host. If the"
    echo ">> phone is showing the bootloader screen then it GOT THERE and its USB"
    echo ">> never came up -- which is exactly what this looks like from here."
    echo ">>"
    echo ">> DO THIS FIRST: unplug the cable and plug it back in. That is the"
    echo ">> whole fix, it costs seconds, and it keeps the bootloader you have."
    echo ">> Then confirm with:"
    echo ">>   $FASTBOOT devices"
    ;;
fastboot)
    echo ">> BUT $PORTHOLE_USB_FASTBOOT_ID IS ON THE BUS: the phone IS in the bootloader"
    echo ">> and \`$FASTBOOT devices\` still lists nothing, so fastboot cannot OPEN"
    echo ">> the device. That is a host problem, not a phone problem: a missing"
    echo ">> udev rule for that ID, or another process holding the interface."
    echo ">> Replugging the cable clears a stale claim. Do NOT reboot the phone."
    ;;
gadget)
    echo ">> $PORTHOLE_USB_GADGET_ID IS STILL ON THE BUS: the phone never left pmOS, so"
    echo ">> the reboot request is being dropped rather than lost in transit."
    echo ">>"
    echo ">> DO THIS BY HAND: power the phone off, then hold Power + Volume-Down"
    echo ">> until the bootloader screen appears. Verify with:"
    echo ">>   $FASTBOOT devices"
    ;;
*)
    echo ">> DO THIS BY HAND: power the phone off, then hold Power + Volume-Down"
    echo ">> until the bootloader screen appears. Verify with:"
    echo ">>   $FASTBOOT devices"
    echo ">>"
    echo ">> (This profile names no PORTHOLE_USB_FASTBOOT_ID / _GADGET_ID, so"
    echo ">> porthole cannot tell you whether the phone is on the bus at all."
    echo ">> Filling those in is what makes this message specific -- and the"
    echo ">> difference between replugging a cable and rebooting a phone that"
    echo ">> was already where you wanted it.)"
    ;;
esac
tk_expired "$DEADLINE" && exit 1
exit 3
