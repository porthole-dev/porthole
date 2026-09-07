#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: BOOTED
# env: HOST, PHONE, PORTHOLE_USER, PORTHOLE_THERMAL_CEILING (mC, default 82000),
#      PORTHOLE_THERMAL_FLOOR (mC, default 55000), PORTHOLE_THERMAL_BRIGHTNESS (default 4)
# exits: 0 ok · 64 usage · 1 the die never reached the floor
#
# Keep a phone from being cooked by back-to-back measurement arms.
#
# WHY THIS EXISTS: an agent running arms unattended will happily play 4K60 for
# ten minutes at a time, leave the browser running afterwards, and start the
# next arm on a die that never came back down. On 2026-09-04 that put taimen at
# 80 C for most of two hours, and a phone that sits hot is a phone being worn
# out -- quite apart from every thermal number after the first being taken on a
# saturated die and therefore meaningless.
#
# The three verbs are meant to bracket every arm:
#
#   tools/ph-thermal.sh prep          dim the panel, note the starting die temp
#   tools/ph-thermal.sh down          kill the usual suspects, dim, report
#   tools/ph-thermal.sh cool [mC]     block until the die is under the floor
#   tools/ph-thermal.sh guard N [mC]  background watchdog: for N seconds, kill
#                                     the workload if the die passes the ceiling
#
# `cool` is the one that matters for back-to-back arms, and `guard` is the one
# that matters when nobody is watching.
set -uo pipefail
# shellcheck source=ph-lib.sh
. "$(dirname "$0")/ph-lib.sh"

CEIL=${PORTHOLE_THERMAL_CEILING:-82000}
FLOOR=${PORTHOLE_THERMAL_FLOOR:-55000}
BRIGHT=${PORTHOLE_THERMAL_BRIGHTNESS:-4}

# The hottest zone, because which one leads depends on the workload: a GPU arm
# leads on gpu-*-thermal, a CPU arm on cpu*-thermal.
maxtemp() { tk_run 'cat /sys/class/thermal/thermal_zone*/temp | sort -n | tail -1'; }

dim() {
    tk_run 'for b in /sys/class/backlight/*/; do sudo -n sh -c "echo '"$BRIGHT"' > $b/brightness"; done' >/dev/null 2>&1 || true
}

# Anything an arm might have left running. Split strings so the pkill pattern
# does not match the ssh command line carrying it -- that self-kill costs an
# afternoon the first time it happens.
#
# `porthole tools audit` flags this anyway, correctly: the contract's real fix
# is stopping each workload's systemd --user scope/slice, not string-splitting
# a pkill pattern, and that fix also covers the gap this still has --
# grandchildren pkill -f never sees. Every OTHER arm in this tree already
# names its epiphany scope (app-gnome-org.gnome.Epiphany-$$.scope under
# app.slice, stopped by unit glob) and tears down through that; killarms()
# is the generic catch-all called by tools that do not know which of those
# per-arm scope names, if any, is still standing, and I have not verified on
# a device that MiniBrowser and glmark2 are ever placed in a stoppable scope
# at all (nothing in this tree launches either one that way). Guessing here
# risks a silent no-op teardown on an overheating phone, which is worse than
# the known gap this already has -- so it stays pkill for now, exempted
# rather than "fixed" on faith.
killarms() {
    tk_run 'pkill -f epipha""ny; pkill -f MiniBrow""ser; pkill -f glmark""2; true' >/dev/null 2>&1 || true  # contract: pkill-ok scope names for MiniBrowser/glmark2 are unverified on-device; see comment above
}

usage() { echo "usage: tools/ph-thermal.sh prep|down|cool [mC]|guard SECONDS [mC]" >&2; exit 64; }
[ $# -ge 1 ] || usage

case "$1" in
prep)
    dim
    echo "thermal: start $(maxtemp) mC, panel dimmed to $BRIGHT"
    ;;
down)
    killarms
    dim
    # The temp/freq reading that follows is only meaningful once the killed
    # workload has actually exited -- poll for that instead of guessing how
    # long it takes, bounded so a process that ignores the kill still lets
    # teardown finish and report.
    for _ in $(seq 1 10); do
        tk_run 'pgrep -x epiphany >/dev/null || pgrep -x MiniBrowser >/dev/null || pgrep -x glmark2 >/dev/null' >/dev/null 2>&1 || break
        sleep 1
    done
    echo "thermal: after teardown $(maxtemp) mC, gpu $(tk_run 'cat /sys/class/devfreq/*gpu/cur_freq' 2>/dev/null)"
    ;;
cool)
    target=${2:-$FLOOR}
    killarms
    dim
    for _ in $(seq 1 90); do
        t=$(maxtemp)
        [ -n "$t" ] || break
        [ "$t" -le "$target" ] 2>/dev/null && { echo "thermal: cooled to $t mC"; exit 0; }
        sleep 10
    done
    echo "thermal: still $(maxtemp) mC after 15 min, not cooling" >&2
    exit 1
    ;;
guard)
    [ $# -ge 2 ] || usage
    secs=$2; ceil=${3:-$CEIL}
    end=$(( $(date +%s) + secs ))
    while [ "$(date +%s)" -lt "$end" ]; do
        t=$(maxtemp)
        if [ -n "$t" ] && [ "$t" -ge "$ceil" ] 2>/dev/null; then
            echo "thermal: GUARD TRIPPED at $t mC (ceiling $ceil) -- killing the workload" >&2
            killarms
            exit 0
        fi
        sleep 5
    done
    echo "thermal: guard finished, peak stayed under $ceil mC"
    ;;
*) usage ;;
esac
