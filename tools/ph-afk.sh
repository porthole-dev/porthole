#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: BOOTED
# env: PHONE, PORTHOLE_HOST, PORTHOLE_USER, TK_HOST, TK_RUN_TIMEOUT
# exits: 0 ok · 1 the device did not end up in the state that was asked for · 64 usage
# ph-afk.sh -- stop the phone suspending itself while nobody is at the keyboard.
#
# Usage:
#   tools/ph-afk.sh                 print the state right now  (default)
#   tools/ph-afk.sh on [duration]   mask suspend; unmask again after `duration`
#   tools/ph-afk.sh off             unmask, and cancel any pending expiry
#
#   `duration` is whatever systemd parses -- 90m, 4h, '1h 30min'. Given none,
#   the mask is INDEFINITE and survives reboots. That is the point, and it is
#   also the hazard; see below.
#
# WHY THIS EXISTS
#   brain/traps/unmasked-suspend-during-an-automated-wait-is-a-death-loop.md:
#   the compositor's own idle timer suspends the phone in the middle of an
#   unattended run. If the run is itself a suspend test, the device boots,
#   idles, suspends and dies again on a loop whose ssh window is a few tens of
#   seconds, and it ends with someone walking over to hold the power button.
#   The trap's stated fix is "keep suspend masked for the whole setup phase" --
#   and there was no tool for it, so it got retyped by hand or forgotten.
#
# WHY A MASK AND NOT systemd-inhibit
#   An inhibitor lives and dies with the process that holds it, so it cannot
#   survive the reboot in the middle of your unattended run, and an ssh session
#   dropping takes it with it. A mask is a symlink in /etc: it survives both.
#   That is exactly why it also needs an expiry -- a mask nobody removes is a
#   phone that never sleeps and a battery that explains itself badly a week
#   later. `on 4h` arms a transient systemd timer to take it off for you.
#
# WHY THREE UNITS
#   logind starts suspend.target, so masking that is the one that blocks it.
#   The other two cost nothing and close the gap: a guard on the wrong unit is
#   this repo's most expensive suspend lesson -- ph-suspend-guard-check.sh
#   exists because one was attached to a unit whose ordering let the real work
#   run first anyway.
#
# WHAT THIS DOES NOT DO
#   The screen still blanks and the session still locks; only sleep is blocked.
#   A phone that never suspends runs warm and discharges -- do not leave this
#   on past the run it was armed for.
set -u

cd "$(dirname "$0")" || exit 1
# shellcheck source=ph-lib.sh
. ./ph-lib.sh

UNITS="sleep.target suspend.target systemd-suspend.service"
EXPIRE=porthole-afk-expire

usage() { sed -n '8,17p' "./${0##*/}" | sed 's/^# \?//'; exit 64; }

# Read the state back off the device rather than trusting the write. Every
# tunable in this repo that was "set" and never re-read turned out not to be:
# brain/laws/shipped-configuration-is-not-running-configuration.md
report() {
    tk_run '
        for u in '"$UNITS"'; do
            printf "  %-26s %s\n" "$u" "$(systemctl is-enabled "$u" 2>&1)"
        done
        # list-timers, not `show -p NextElapseUSecRealtime`: --on-active makes a
        # MONOTONIC timer, whose realtime elapse property is "n/a" forever.
        systemctl list-timers --no-legend --all '"$EXPIRE"'.timer 2>/dev/null |
            sed "s/^/  unmask: /"
    '
}

masked_count() { tk_run "systemctl is-enabled $UNITS 2>&1 | grep -c '^masked'" | tr -d '\r'; }

case ${1:-status} in
status)
    echo ">> suspend on $PHONE:"
    report
    if [ "$(masked_count)" = 3 ]; then
        echo ">> AFK: suspend is MASKED. \`tools/ph-afk.sh off\` puts it back."
    fi
    ;;
on)
    DUR=${2:-}
    tk_run "sudo -n systemctl stop $EXPIRE.timer 2>/dev/null
            sudo -n systemctl mask $UNITS" >/dev/null
    if [ -n "$DUR" ]; then
        # --collect so a transient unit that fails does not linger in the
        # failed state and block the next arm under the same name.
        tk_run "sudo -n systemd-run --collect --unit=$EXPIRE --on-active='$DUR' \
                --timer-property=AccuracySec=30s \
                systemctl unmask $UNITS" >/dev/null ||
            echo ">> WARNING: the expiry timer was NOT armed -- the mask is indefinite"
    fi
    report
    if [ "$(masked_count)" != 3 ]; then
        echo ">> FAILED: suspend is not masked. The phone can still sleep."
        exit 1
    fi
    if [ -n "$DUR" ]; then
        echo ">> AFK for $DUR. It unmasks itself; nothing has to remember."
    else
        echo ">> AFK indefinitely, ACROSS REBOOTS. Nothing will undo this but you."
        echo ">> Prefer \`tools/ph-afk.sh on 4h\` if you know when you are back."
    fi
    ;;
off)
    tk_run "sudo -n systemctl stop $EXPIRE.timer $EXPIRE.service 2>/dev/null
            sudo -n systemctl unmask $UNITS" >/dev/null
    report
    if [ "$(masked_count)" != 0 ]; then
        echo ">> FAILED: something is still masked."
        exit 1
    fi
    echo ">> suspend is back to normal."
    ;;
*)  usage ;;
esac
