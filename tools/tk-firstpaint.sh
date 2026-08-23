#!/bin/sh
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · 1 failed
# tk-firstpaint.sh -- what the user actually waits for: launch -> first frame.
#
# WHY THIS EXISTS ALONGSIDE tk-applaunch-bench.sh
#   That harness times the app's D-Bus name appearing, and says so honestly:
#   "Not first-paint". Measured 2026-08-20 it reports 170-190 ms cold while the
#   screen does not light up for ~0.5 s. Both numbers are true; only this one is
#   the complaint the user is making.
#
# THE PANEL IS THE INSTRUMENT
#   DSI command mode: encoder-0/status's vsync counter advances only when a
#   frame is really transferred, and is static at rest. So the first tick after
#   a launch IS first paint. Needs debugfs readable by the session user:
#     sudo mount -o remount,mode=755 /sys/kernel/debug
#     sudo chmod a+rx /sys/kernel/debug/dri /sys/kernel/debug/dri/0 \
#                     /sys/kernel/debug/dri/0/encoder-0
#     sudo chmod a+r  /sys/kernel/debug/dri/0/encoder-0/status
#
# TWO FALSE ZEROS THIS SCRIPT REFUSES TO REPORT, both hit on 2026-08-20
#   1. Display off. A blank panel transfers no frames, so "wait for the screen
#      to go quiet" succeeds instantly and nothing ever paints. The
#      bl_power/enabled precondition below is not optional. Wake the panel with
#      tools/tk-key.py, and set idle-delay 0 for the duration of the run.
#   2. App never appeared. lswt confirms a window exists before the number is
#      allowed to count (AGENTS.md 3b: no number without a witness).
#
# TWO NUMBERS, AND ONLY ONE OF THEM IS THE COMPLAINT
#   "first frame" is the first vsync tick after the launch. Measured 2026-08-20
#   that is 0.5-0.7 s for everything, Firefox included -- because it is phosh
#   painting its own launch feedback, NOT the app. Quoting it as app latency is
#   wrong, and this script did exactly that until the lswt witness caught it.
#   "WINDOW on screen" is when the app's own surface exists. That is the number
#   the user is describing. Measured: TextEditor 2.1 s warm; Firefox 5.4-5.7 s
#   warm, 6.8-7.0 s cold.
#
#   tk-firstpaint.sh [ROUNDS] [APP_ID:PROCNAME ...]     default 3 rounds
#
# ponytail: polls /proc/uptime and one debugfs counter. No new deps beyond grim
# and lswt, which tk-ui.py already assumes.
set -u
export XDG_RUNTIME_DIR=/run/user/10000 WAYLAND_DISPLAY=wayland-0
ENC=/sys/kernel/debug/dri/0/encoder-0/status
ROUNDS=${1:-3}; shift 2>/dev/null || true
APPS=${*:-"org.gnome.clocks:gnome-clocks org.gnome.TextEditor:gnome-text-editor"}
vs(){ sed -n 's/.*vsync: *\([0-9]*\).*/\1/p' "$ENC"; }
now(){ cut -d' ' -f1 /proc/uptime; }
# Make the instrument readable ourselves. Doing this in a previous shell does
# not survive: systemd's sys-kernel-debug.mount re-applies mode=700 out from
# under you, which then reads as "instrument unavailable" mid-run.
if [ ! -r "$ENC" ]; then
	sudo -n mount -o remount,mode=755 /sys/kernel/debug 2>/dev/null
	sudo -n chmod a+rx /sys/kernel/debug/dri /sys/kernel/debug/dri/0 \
	                   /sys/kernel/debug/dri/0/encoder-0 2>/dev/null
	sudo -n chmod a+r "$ENC" 2>/dev/null
fi
[ -r "$ENC" ] || { echo "FATAL: cannot read $ENC -- instrument unavailable"; exit 1; }

for spec in $APPS; do
  app=${spec%%:*}; proc=${spec#*:}
  echo "== $app =="
  i=0
  while [ $i -lt "$ROUNDS" ]; do
    i=$((i+1))
    pkill -x "$proc" 2>/dev/null; sleep 2
    # PRECONDITION: the panel must be awake. A blank screen "quiesces" instantly
    # and then no frame ever arrives -- a false zero, not a fast launch.
    if [ "$(cat /sys/class/backlight/*/bl_power)" != "0" ] || \
       [ "$(cat /sys/class/drm/card0-DSI-1/enabled)" != "enabled" ]; then
      echo "  round $i: DISPLAY IS OFF -- skipping (instrument invalid)"; continue
    fi
    sudo -n sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null
    # quiesce: vsync must not move across a 400ms window
    q=0
    while [ $q -lt 30 ]; do
      a=$(vs); sleep 0.4; b=$(vs)
      [ "$a" = "$b" ] && break
      q=$((q+1))
    done
    [ "$a" = "$b" ] || { echo "  round $i: screen never quiesced (something is animating) - SKIP"; continue; }
    base=$(vs); t0=$(now); paint=""; win=""
    gtk-launch "$app" >/dev/null 2>&1 &
    n=0
    while [ $n -lt 400 ]; do
      [ -z "$paint" ] && [ "$(vs)" != "$base" ] && paint=$(now)
      lswt 2>/dev/null | grep -q "$app" && { win=$(now); break; }
      n=$((n+1)); sleep 0.1
    done
    awk -v a="$t0" -v f="$paint" -v w="$win" -v r="$i" 'BEGIN{
      printf "  round %d: ", r
      if (f!="") printf "first frame %5.0f ms   ", (f-a)*1000; else printf "first frame   n/a   "
      if (w!="") printf "WINDOW on screen %6.0f ms\n", (w-a)*1000
      else        printf "window never appeared (40s) -- number is invalid\n" }' 
  done
  pkill -x "$proc" 2>/dev/null
done
