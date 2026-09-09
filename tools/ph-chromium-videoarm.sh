#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device as the session user; /tmp/sess.sh; chromium; grim; v4l2-ctl;
#        sudo -n for clk_summary; a local clip
# env: PORTHOLE_VIDEO_FILE, PORTHOLE_VIDEO_SECONDS, PORTHOLE_DECODE_CLK
# exits: 0 measured · 1 the arm is void -- nothing was playing · 64 usage
# ph-chromium-videoarm.sh LABEL [CHROMIUM_FLAGS...] -- one PLAYBACK arm.
#
# Answers one question: is this Chromium decoding in HARDWARE, on the V4L2
# decoder, or in software while looking identical?
#
# THE INSTRUMENTS ARE OUTSIDE THE BROWSER, ON PURPOSE. chrome://media-internals
# names a decoder, but reading it needs CDP and it is the browser's own claim
# about itself. These two are neither:
#
#   1. an open fd on the decoder's /dev/video* node, held by a chromium
#      process. Binary, and impossible to fake from userspace.
#   2. the decode clock. Idle it sits at the XO rate (19.2 MHz on msm8998);
#      decoding it runs at hundreds of MHz. A software decoder cannot move it.
#
# The node is found by DRIVER, never by number: on this SoC six camss nodes
# come up before venus and the numbering is not stable across boots.
#
# THE CLIP IS SCRATCH, NOT AN ARTEFACT. Regenerate it rather than fetching one;
# venus does not care what the pictures are, only how many and how big:
#
#   ffmpeg -f lavfi -i testsrc2=size=1920x1080:rate=60 -t 20 -c:v libvpx-vp9 \
#          -b:v 8M -deadline realtime -cpu-used 8 -row-mt 1 -pix_fmt yuv420p \
#          -y v4l2test-1080p60-vp9.webm
#
# PLAYBACK PROOF. A clip that never started and a hardware path that never
# engaged both read as "clock idle, no fd", so the arm proves playback
# independently: two screenshots a second apart must DIFFER. Without that a
# void arm is indistinguishable from a negative result, which is the single
# most common way a session reports a confident wrong answer.
set -u
# The graphical session env. Without it chromium's --ozone-platform-hint=auto
# finds no WAYLAND_DISPLAY, falls back to X11, and exits with "Missing X server
# or $DISPLAY" before it ever opens a window -- which reads downstream as "the
# video did not play" rather than "the browser never started".
. /tmp/sess.sh
L=${1:?usage: ph-chromium-videoarm.sh LABEL [CHROMIUM_FLAGS...]}; shift || true
XFLAGS=$*
CLIP=${PORTHOLE_VIDEO_FILE:-$HOME/v4l2test-1080p60-vp9.webm}
SECS=${PORTHOLE_VIDEO_SECONDS:-12}
[ -f "$CLIP" ] || { echo "[$L] no clip at $CLIP -- arm void"; exit 1; }

# --- find the decoder node by driver, and its clock ---------------------------
DEC=""
for d in /dev/video*; do
	info=$(v4l2-ctl -d "$d" --info 2>/dev/null) || continue
	case "$info" in *"video decoder"*|*"Video Decoder"*) DEC=$d; break ;; esac
done
[ -n "$DEC" ] && echo "[$L] decoder node: $DEC ($(v4l2-ctl -d "$DEC" --info 2>/dev/null | sed -n 's/.*Card type *: *//p'))" \
	|| echo "[$L] WARNING: no V4L2 decoder node found -- the fd check cannot fire"

CLK=${PORTHOLE_DECODE_CLK:-}
clk_rate() {
	[ -n "$CLK" ] || { echo ""; return; }
	sudo -n awk -v c="$CLK" '$1==c {print $5; exit}' /sys/kernel/debug/clk/clk_summary 2>/dev/null
}
if [ -z "$CLK" ]; then
	CLK=$(sudo -n awk '$1 ~ /^video_subcore0_clk$/ {print $1; exit}' \
		/sys/kernel/debug/clk/clk_summary 2>/dev/null)
fi
[ -n "$CLK" ] && echo "[$L] decode clock: $CLK, idle $(clk_rate) Hz"

# --- page beside the clip -----------------------------------------------------
mkdir -p /tmp/tk-vid
ext=${CLIP##*.}
[ -e "/tmp/tk-vid/clip.$ext" ] || cp "$CLIP" "/tmp/tk-vid/clip.$ext"
cat > "/tmp/tk-vid/cr-$L.html" <<EOF
<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<style>html,body{margin:0;background:#000}video{width:100vw;display:block}</style>
<video src="clip.$ext" autoplay muted loop playsinline></video>
EOF

# --- one browser, cleanly ------------------------------------------------------
# The session vars are passed EXPLICITLY rather than inherited: systemd-run
# starts the scope from the user MANAGER's environment, not this shell's, and
# that manager may never have had WAYLAND_DISPLAY imported. Chromium then picks
# the X11 ozone backend, exits with "Missing X server or $DISPLAY", and the arm
# reads as "nothing was playing".  --ozone-platform=wayland rather than the
# conf's hint=auto, so a missing variable fails loudly instead of silently
# selecting X11.
for u in $(systemctl --user list-units "app-*hromium*.scope" --no-legend 2>/dev/null | awk '{print $1}'); do
	systemctl --user stop "$u"
done
pkill -u "$(id -u)" -f 'chromium.*--type=' 2>/dev/null
sleep 2  # contract: sleep-ok phoc's client teardown exposes no state to poll over ssh

setsid systemd-run --user --scope --quiet --slice=app.slice \
	-u "app-chromium-v$$.scope" \
	env WAYLAND_DISPLAY="$WAYLAND_DISPLAY" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
	DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" \
	CHROMIUM_USER_FLAGS="$XFLAGS --enable-logging=stderr --vmodule=*/media/gpu/*=2" \
	chromium --ozone-platform=wayland "file:///tmp/tk-vid/cr-$L.html" \
	>"/tmp/cr-$L.log" 2>&1 </dev/null &

# --- prove it is playing, then sample -----------------------------------------
shot() { grim -t png "/tmp/cr-$L-$1.png" 2>/dev/null && sha256sum "/tmp/cr-$L-$1.png" | cut -c1-16; }
fd_held() {
	[ -n "$DEC" ] || return 1
	for p in $(pgrep -u "$(id -u)" chromium 2>/dev/null); do
		# An exact readlink match, not a substring of `ls -l` output: the
		# decoder path can appear in that listing as part of a different
		# target, and the fd number column can contain it outright.
		for fd in "/proc/$p/fd/"*; do
			[ "$(readlink "$fd" 2>/dev/null)" = "$DEC" ] && return 0
		done
	done
	return 1
}

maxclk=0; sawfd=no
for _ in $(seq 1 "$SECS"); do
	sleep 1  # contract: sleep-ok this IS the sampling interval, not a wait for an event
	fd_held && sawfd=yes
	r=$(clk_rate); case "$r" in ''|*[!0-9]*) ;; *) [ "$r" -gt "$maxclk" ] && maxclk=$r ;; esac
done

a=$(shot a); sleep 1; b=$(shot b)  # contract: sleep-ok the gap IS the measurement
[ -n "$a" ] && [ "$a" = "$b" ] && {
	echo "[$L] [arm void] the window did not change in 1 s -- nothing was playing"
	exit 1
}

echo "[$L] decoder fd held: $sawfd   peak decode clock: $maxclk Hz"
echo "[$L] chromium's own probe:"
grep -aiE 'v4l2|VideoDecoder|vaapi' "/tmp/cr-$L.log" 2>/dev/null | tail -6 | sed 's/^/    /'
echo "[$L] gpu $(( $(cat /sys/class/devfreq/*.gpu/cur_freq 2>/dev/null | head -1) / 1000000 )) MHz   die $(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1) mC"
