#!/bin/sh
# scope: generic
# tk-applaunch-bench.sh -- cold/warm application launch latency under phosh.
# Run ON THE DEVICE as root:
#
#   TK_AGENT=<you> tools/tk-device.sh bash -c \
#     'source tools/tk-lib.sh; ssh "${TK_SSH_OPTS[@]}" "$PHONE" "sudo -n sh -s" \
#      < tools/tk-applaunch-bench.sh' [ROUNDS] [APP:PROCNAME ...]
#
# WHICH SESSION -- RESOLVED AT RUN TIME, NOT HARDCODED
#   Do not assume a uid. This device's seated session has been uid 113
#   "greetd" (a `Class=greeter` compositor -- the phone sitting at the lock
#   screen, nobody unlocked in) at some points and will be uid 10000, the login user
#   (a real `Class=user` session) once someone logs in. Those are different
#   buses, different environments, different resident-process sets, and a
#   number from one is not comparable to a number from the other. So: walk
#   `loginctl list-sessions`, prefer the first seated session with
#   Class=user, fall back to a seated Class=greeter session if no user
#   session exists yet. Whichever it picks, the run header records it --
#   an unlabelled number is not reusable by anyone else in this campaign.
#
# WHAT IT MEASURES
#   Wall time from `gtk-launch` to the app's well-known D-Bus name appearing
#   on that session's bus -- the earliest externally observable signal that
#   the GApplication primary instance is up. Not first-paint: this rootfs
#   has no swaymsg-equivalent, no grim, no xdotool, so there is no
#   compositor IPC to time the first frame directly. It is a proxy, but it
#   is exact and it is the same proxy every run, which is what a regression
#   number needs.
#
# COLD = drop_caches immediately before launch (nothing of the app's
# binary/libs is resident) -- the first-launch-after-boot case the user
# actually complained about. WARM = relaunch right after killing it, page
# cache untouched.
#
# ponytail: on-device polling against /proc/uptime (10ms resolution), not
# host-side ssh-round-trip polling -- busybox date has no %N (AGENTS.md),
# and one ssh round trip costs ~0.2-0.5s, which would dominate a sub-second
# launch. Everything stays inside one ssh session. Iteration-count timeout
# instead of a float deadline -- one less awk expression to get wrong on a
# busybox awk.
set -u

POLL=0.1
MAXITERS=150   # 150 * 0.1s = 15s timeout
ROUNDS=${1:-3}
shift 2>/dev/null || true
# org.gnome.Calculator deliberately NOT a default: measured 2026-08-10, it
# never puts a name on the bus within the timeout (100% of rounds, cold AND
# warm), while org.gnome.TextEditor launched right next to it in the same
# run registers in well under a second every time. The process itself does
# spawn (confirmed via ps) -- this looks like a real per-app difference in
# whether the binary calls g_application_register() at all, not a bug in
# this tool's mechanism, but it was not root-caused this session. Pass it
# explicitly (with a suitable process name) if you want to chase that; do
# not add it back as a default until it is understood, or every run using
# the default set silently reports a false "app is unusably slow".
APPS=${*:-"org.gnome.TextEditor:gnome-text-editor org.gnome.clocks:gnome-clocks"}

# find_session -> echoes "UID CLASS SEAT" of the session to measure, or
# nothing (and a non-zero exit) if the phone has no seated session at all
# (e.g. screen off with logind having dropped the seat -- rare, but do not
# guess a uid in that case).
find_session() {
	want_class=$1
	for sid in $(loginctl list-sessions --no-legend 2>/dev/null | awk '{print $1}'); do
		class=$(loginctl show-session "$sid" -p Class --value 2>/dev/null)
		seat=$(loginctl show-session "$sid" -p Seat --value 2>/dev/null)
		[ "$class" = "$want_class" ] && [ -n "$seat" ] || continue
		uid=$(loginctl show-session "$sid" -p User --value 2>/dev/null)
		[ -n "$uid" ] && { echo "$uid $class $seat"; return 0; }
	done
	return 1
}

SESSION_INFO=$(find_session user) || SESSION_INFO=$(find_session greeter) || {
	echo "no seated session (user or greeter) found -- is the screen off?" >&2
	exit 1
}
SESSION_UID=$(echo "$SESSION_INFO" | cut -d' ' -f1)
SESSION_CLASS=$(echo "$SESSION_INFO" | cut -d' ' -f2)
SESSION_SEAT=$(echo "$SESSION_INFO" | cut -d' ' -f3)
RUNTIME_DIR=/run/user/$SESSION_UID
BUS=unix:path=$RUNTIME_DIR/bus

as_session() {
	sudo -n -u "#$SESSION_UID" env XDG_RUNTIME_DIR="$RUNTIME_DIR" \
		WAYLAND_DISPLAY=wayland-0 DBUS_SESSION_BUS_ADDRESS="$BUS" "$@"
}

# --acquired is load-bearing: a plain `busctl list` also reports ACTIVATABLE
# names, which exist from session start for anything with a DBusActivatable
# desktop entry. Without it this returned true immediately and the harness
# timed GNOME Software -- a DBusActivatable app -- at ~40 ms when it actually
# takes ~10 s. Every number this script produced for such an app before
# 2026-08-21 is void.
bus_owned() {
	as_session busctl --user list --acquired --no-legend 2>/dev/null | awk '{print $1}' | grep -qx "$1"
}

now() { awk '{print $1}' /proc/uptime; }
elapsed_ms() { awk -v a="$1" -v b="$2" 'BEGIN{printf "%.0f", (b-a)*1000}'; }

wait_for_bus() {
	name=$1
	i=0
	while [ "$i" -lt "$MAXITERS" ]; do
		bus_owned "$name" && { now; return 0; }
		i=$((i + 1))
		sleep "$POLL"
	done
	echo TIMEOUT
	return 1
}

quit_app() {
	pkill -x "$1" 2>/dev/null
	i=0
	while pgrep -x "$1" >/dev/null 2>&1 && [ "$i" -lt 30 ]; do
		sleep 0.1
		i=$((i + 1))
	done
}

# bench_one APP MODE -> prints "  MODE: N ms" (or TIMEOUT) and echoes N on a
# second line prefixed "SAMPLE:" for the caller to accumulate -- no arrays in
# ash, so the caller collects a space-separated list itself.
bench_one() {
	appname=$1 mode=$2
	t0=$(now)
	as_session gtk-launch "$appname" >/dev/null 2>&1 &
	t1=$(wait_for_bus "$appname")
	if [ "$t1" = TIMEOUT ]; then
		echo "  $mode: TIMEOUT (>$(awk -v m=$MAXITERS -v p=$POLL 'BEGIN{print m*p}')s)"
	else
		ms=$(elapsed_ms "$t0" "$t1")
		echo "  $mode: ${ms} ms"
		echo "SAMPLE:${ms}"
	fi
}

# summarize LABEL SAMPLES... -> min/median/max/mean, and the honest verdict
# tk-perf-ab.sh already established: if the spread inside one arm is as wide
# as the gap between two arms, don't trust the gap.
summarize() {
	label=$1; shift
	[ $# -eq 0 ] && { echo "  $label: no samples (all timed out)"; return; }
	printf '%s\n' "$@" | sort -n | awk -v label="$label" '
		{ a[NR] = $1; sum += $1 }
		END {
			n = NR
			min = a[1]; max = a[n]; mean = sum / n
			mid = int((n + 1) / 2)
			median = (n % 2) ? a[mid] : (a[mid] + a[mid + 1]) / 2
			printf "  %s: n=%d min=%d median=%.0f mean=%.0f max=%d spread=%d ms\n", \
				label, n, min, median, mean, max, max - min
		}'
}

echo "### kernel $(uname -r)  uptime $(cat /proc/uptime)  rounds=$ROUNDS"
echo "### SESSION: uid=$SESSION_UID class=$SESSION_CLASS seat=$SESSION_SEAT"
if [ "$SESSION_CLASS" != "user" ]; then
	echo "### WARNING: no logged-in user session found -- this run measured the"
	echo "### $SESSION_CLASS session (lock screen), NOT the user's own session the"
	echo "### complaint is about. Retake once the phone is unlocked and logged in."
fi
echo "### NOTE: no cpufreq on this kernel -- CPUs sit at boot rate, this is a"
echo "### no-DVFS baseline and WILL move once the cpufreq agent lands scaling."
echo "### PSI before"; head -2 /proc/pressure/memory /proc/pressure/io
echo "### zram before"; cat /sys/block/zram0/mm_stat 2>/dev/null

for pair in $APPS; do
	appname=${pair%%:*}
	procname=${pair##*:}
	echo "== $appname ($procname) =="
	cold_samples=""
	warm_samples=""
	r=1
	while [ "$r" -le "$ROUNDS" ]; do
		echo " round $r"
		quit_app "$procname"
		sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null
		out=$(bench_one "$appname" cold)
		echo "$out" | grep -v '^SAMPLE:'
		s=$(echo "$out" | sed -n 's/^SAMPLE://p')
		[ -n "$s" ] && cold_samples="$cold_samples $s"
		quit_app "$procname"
		out=$(bench_one "$appname" warm)
		echo "$out" | grep -v '^SAMPLE:'
		s=$(echo "$out" | sed -n 's/^SAMPLE://p')
		[ -n "$s" ] && warm_samples="$warm_samples $s"
		r=$((r + 1))
	done
	quit_app "$procname"
	echo " -- summary --"
	summarize cold $cold_samples
	summarize warm $warm_samples
done

echo "### PSI after"; head -2 /proc/pressure/memory /proc/pressure/io
echo "### zram after"; cat /sys/block/zram0/mm_stat 2>/dev/null
echo "### DONE"
