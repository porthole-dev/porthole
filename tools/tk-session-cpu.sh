#!/bin/sh
# scope: generic
# needs: on-device (scp it over, or pipe with `ssh ... sh -s`)
# env: PHONE, TK_AGENT
# exits: 0 ok · 1 failed
# tk-session-cpu.sh -- rank a graphical session's user units by cumulative CPU.
# Run ON THE DEVICE as root:
#
#   TK_AGENT=<you> tools/tk-device.sh --need-booted bash -c \
#     'source tools/tk-lib.sh
#      scp "${TK_SSH_OPTS[@]}" tools/tk-session-cpu.sh "$PHONE":/tmp/ >/dev/null
#      ssh "${TK_SSH_OPTS[@]}" "$PHONE" "sh /tmp/tk-session-cpu.sh"'
#
# WHY THIS EXISTS
#   "The session is slow" needs a culprit, and on this device the candidates
#   (gnome-software's apk query storm, the localsearch-3 indexer, a greeter
#   running a full phosh session) are all *units*, not processes -- they fork,
#   respawn and change pid, so per-process sampling misses them. systemd
#   already accounts CPU per unit; this just reads CPUUsageNSec and sorts.
#
#   It found the ws-08 open-item-3 number on the first run: the greeter burns
#   ~42.7 CPU-seconds in mobi.phosh.Phrog.service before the PIN is typed.
#
# READ IT TWICE. A large number that has STOPPED growing is startup cost; one
# that is still growing is a live storm. Run it, wait 30 s, run it again and
# diff -- that distinction is the whole point, and phrog looked alarming until
# the second read showed 3 ms of growth in 20 s.
#
# WHICH SESSION -- resolved at run time, never hardcoded, and deliberately the
# same rule tools/tk-applaunch-bench.sh uses: prefer the first seated
# Class=user session, fall back to a seated Class=greeter one. A bare ssh login
# is Class=user with NO seat and must not be picked. Numbers from a greeter
# session and a user session are not comparable and the header says which.
set -u

find_session() {
	for sid in $(loginctl list-sessions --no-legend 2>/dev/null | awk '{print $1}'); do
		[ "$(loginctl show-session "$sid" -p Class --value 2>/dev/null)" = "$1" ] || continue
		[ -n "$(loginctl show-session "$sid" -p Seat --value 2>/dev/null)" ] || continue
		loginctl show-session "$sid" -p User --value 2>/dev/null
		return 0
	done
	return 1
}

CLASS=user
SESSION_UID=$(find_session user) || { CLASS=greeter; SESSION_UID=$(find_session greeter); } || {
	echo "no seated session (user or greeter) found -- is the screen off?" >&2
	exit 1
}

as() {
	sudo -n -u "#$SESSION_UID" \
		env XDG_RUNTIME_DIR="/run/user/$SESSION_UID" \
		DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$SESSION_UID/bus" "$@"
}

echo "### session uid=$SESSION_UID class=$CLASS  uptime=$(cut -d' ' -f1 /proc/uptime)s"
echo "### kernel $(uname -r)  $(grep -o '#[0-9]*-postmarketos[^ ]*' /proc/version)"
[ "$CLASS" = user ] || echo "### NOTE: greeter session -- this is the lock screen, not a logged-in user"
echo "### cumulative CPU per user unit (ms), highest first"

as systemctl --user list-units --type=service --all --no-legend 2>/dev/null |
	awk '{print $1}' |
	while read -r unit; do
		ns=$(as systemctl --user show "$unit" -p CPUUsageNSec --value 2>/dev/null)
		case "$ns" in '' | '[not set]' | *[!0-9]*) continue ;; esac
		echo "$((ns / 1000000)) $unit"
	done | sort -rn | head -"${1:-25}"
