#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: BOOTED (edits greetd's config on the device, over ssh)
# env: PORTHOLE_USER (the account to auto-login), TK_DEVICE_* as usual
# exits: 0 ok · 64 usage · 69 no greetd config found (the tool could not run)
#
# Enable or disable greetd auto-login, which is the ONLY reliable way to get an
# UNLOCKED graphical session on phosh without a human.
#
# Why this exists, written down so nobody rebuilds the flaky thing again:
# phosh's lockscreen is a layer-shell surface. On 2026-09-03 all of these were
# checked and ALL of them report "unlocked" while it is plainly on screen:
#
#   - logind            loginctl show-session <id> -p LockedHint  -> no
#   - GNOME screensaver org.gnome.ScreenSaver.GetActive           -> false
#   - wayland toplevels lswt / wlrctl toplevel list               -> lists the app
#
# So there is no state to poll, and "swipe up then type the PIN" is a guess
# that silently types the PIN into whatever has focus when it guesses wrong.
# greetd's initial_session skips the greeter entirely: the session is created
# at boot, already authenticated, with no lockscreen to dismiss.
#
# The trade: an auto-login device is unlocked at boot. Only for a test device,
# and `disable` puts it back. The config is backed up on first enable.
#
#   tools/tk-autologin.sh status
#   tools/tk-autologin.sh enable    # then reboot
#   tools/tk-autologin.sh disable   # then reboot
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=/dev/null
source tools/tk-lib.sh

CFG=/etc/phrog/greetd-config.toml
BAK=$CFG.pre-autologin
USER_NAME=$PORTHOLE_USER  # tk-lib.sh already defaults this to "user"

usage() { echo "usage: tools/tk-autologin.sh status|enable|disable" >&2; exit 64; }
[ $# -eq 1 ] || usage

tk_run "test -f $CFG" || { echo "no greetd config at $CFG" >&2; exit 69; }

case "$1" in
status)
    if tk_run "grep -q '^\[initial_session\]' $CFG"; then
        echo "auto-login: ENABLED"
        tk_run "sed -n '/^\[initial_session\]/,/^\$/p' $CFG"
    else
        echo "auto-login: disabled (greeter + lockscreen on every boot)"
    fi
    ;;
enable)
    tk_run "sudo -n test -f $BAK || sudo -n cp $CFG $BAK"
    # Drop any previous block, then append a fresh one. Idempotent.
    tk_run "sudo -n sed -i '/^\[initial_session\]/,/^\$/d' $CFG"
    tk_run "sudo -n sh -c 'printf \"\n[initial_session]\ncommand = \\\"systemd-cat phosh-session\\\"\nuser = \\\"$USER_NAME\\\"\n\" >> $CFG'"
    echo "auto-login ENABLED for $USER_NAME (backup: $BAK)"
    echo "reboot for it to take effect: the session comes up already unlocked."
    ;;
disable)
    if tk_run "sudo -n test -f $BAK"; then
        tk_run "sudo -n cp $BAK $CFG"
        echo "restored $CFG from $BAK"
    else
        tk_run "sudo -n sed -i '/^\[initial_session\]/,/^\$/d' $CFG"
        echo "removed the initial_session block (no backup was present)"
    fi
    echo "reboot for it to take effect."
    ;;
*) usage ;;
esac
