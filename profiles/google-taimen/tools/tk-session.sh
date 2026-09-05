#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: device:google-taimen
# needs: BOOTED; tk-key.py and tk-touch.py in /tmp on the device; magick on the host
# env: HOST, PHONE
# exits: 0 ok · 64 usage · 1 could not reach the requested state
#
# Wake, unlock and blank the phosh session, reliably enough to drive the phone
# unattended.
#
# WHY: an agent working overnight finds the panel blanked and the session
# locked, and every "proper" API lies about the latter -- logind LockedHint,
# org.gnome.ScreenSaver.GetActive and lswt all report unlocked while phosh's
# lockscreen (a layer-shell surface) is plainly on screen, and SetActive false
# returns success and changes nothing. So: press the power key to wake, swipe to
# unlock, and *look at the screen* to decide whether it worked.
#
#   tk-session.sh state     panel on/off, locked/unlocked
#   tk-session.sh wake      power key if the panel is off
#   tk-session.sh ensure    wake + swipe until unlocked (what arms call)
#   tk-session.sh blank     power key if the panel is on
#
# The PIN is disabled on this device, so the swipe IS the whole unlock and is
# harmless when nothing was locked. If a PIN is ever set, pipe it to tk-key.py
# type - rather than passing it as an argument (it would land in journald).
set -uo pipefail
# shellcheck source=../../../lib/porthole.sh
. "$(dirname "$0")/../../../tools/tk-lib.sh"

PROBE=/tmp/tk-session-lockprobe.png

panel_on() { tk_run '. /tmp/sess.sh; timeout 8 grim -s 0.1 /tmp/panelprobe.png >/dev/null 2>&1'; }
press_power() { tk_run 'sudo -n python3 /tmp/tk-key.py power' >/dev/null 2>&1; }
swipe_up() { tk_run 'sudo -n python3 /tmp/tk-touch.py swipe 720 2600 720 1000 350' >/dev/null 2>&1; }

# 0 = locked. NOT a template match: RMSE between a smooth green gradient and
# green-text-on-green comes in under any useful threshold, so the banner
# template in tools/repro/a5xx-gmem/ false-positives on the unlocked wallpaper
# (measured 2026-09-05). What separates cleanly is the LUMA of the band where
# the lockscreen's big white clock sits: mean ~196 locked, ~104 on the
# wallpaper. grim reads the composited framebuffer, so the backlight setting
# does not affect this.
#
# It is a heuristic and it knows it: a bright app filling the screen would read
# as "locked". Callers should treat a positive as "swipe and carry on" rather
# than as ground truth, and arms should still assert their own page state.
LOCK_LUMA=${TK_SESSION_LOCK_LUMA:-150}
is_locked() {
    tk_run ". /tmp/sess.sh; grim -g '0,300 480x100' $PROBE" >/dev/null 2>&1 || return 1
    scp "${TK_SSH_OPTS[@]}" "$PHONE:$PROBE" /tmp/tk-session-probe.png >/dev/null 2>&1 || return 1
    local mean
    mean=$(magick /tmp/tk-session-probe.png -colorspace Gray -format '%[fx:mean*255]' info: 2>/dev/null)
    [ -n "$mean" ] || return 1
    awk -v m="$mean" -v t="$LOCK_LUMA" 'BEGIN{exit !(m > t)}'
}

usage() { echo "usage: tk-session.sh state|wake|ensure|blank" >&2; exit 64; }
[ $# -eq 1 ] || usage

case "$1" in
state)
    if panel_on; then
        is_locked && echo "panel: on, session: LOCKED" || echo "panel: on, session: unlocked"
    else
        echo "panel: off"
    fi
    ;;
wake)
    panel_on && { echo "panel already on"; exit 0; }
    press_power; sleep 3
    panel_on && { echo "woke"; exit 0; }
    press_power; sleep 4
    panel_on && { echo "woke on second press"; exit 0; }
    echo "panel still off after two power presses" >&2; exit 1
    ;;
ensure)
    panel_on || { press_power; sleep 3; }
    panel_on || { press_power; sleep 4; }
    if ! panel_on; then echo "panel will not wake" >&2; exit 1; fi
    # The swipe is harmless when nothing was locked (the PIN is disabled here),
    # so just do it rather than trusting a heuristic to decide whether to.
    for i in 1 2 3; do
        is_locked || { echo "session ready (after $((i-1)) swipes)"; exit 0; }
        swipe_up; sleep 3
    done
    # The heuristic can false-positive on a bright screen; say so instead of
    # failing the arm outright.
    echo "warning: clock band still bright after 3 swipes -- either still locked, or a bright app is up" >&2
    exit 0
    ;;
blank)
    panel_on || { echo "panel already off"; exit 0; }
    press_power; sleep 2
    panel_on && { echo "panel still on" >&2; exit 1; }
    echo "blanked"
    ;;
*) usage ;;
esac
