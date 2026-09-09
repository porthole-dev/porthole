#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: staged by ../scroll-blank/arm.sh; runs ON THE DEVICE as the session user
# env: TK_BLANK_URL, TK_BLANK_ENV, TK_BLANK_LABEL
# exits: 0 measured (prints BLANKARMDONE) · 1 arm void
# blankarm.sh -- how much of the screen is UNPAINTED during a fast fling.
#
# The symptom this exists for: "I scroll fast and half the screen is white,
# then the content slowly fills back in." Every earlier instrument here
# measured frame TIMING, and a blank frame delivered on time is a perfect
# frame by all of them.
#
# Three windows, in this order and for these reasons:
#   idle   -- the control. A page has flat regions of its own (margins, the
#             video letterbox); without this number the fling number means
#             nothing.
#   fling  -- the symptom, sampled with grim. grim costs one composite per
#             sample, so this window's TIMING is not usable.
#   timing -- the same fling with nothing sampling pixels, for the frame
#             record. Deliberately a separate window for that reason.
set -u
. /tmp/sess.sh 2>/dev/null || true
. /tmp/blankenv.sh 2>/dev/null || true

# Uprobes outlive the arm that armed them. An arm that exits early -- and the
# "page too short" guard below is an early exit that fires often -- otherwise
# leaves 14 probes on libwebkitgtk for every browser the device starts
# afterwards, and the next session inherits a browser that is quietly slower
# for no reason it can see. Disarm from a trap, not from the happy path.
cleanup() {
	sh /tmp/ph-wkphase.sh off >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
L=${TK_BLANK_LABEL:-blank}
URL=${TK_BLANK_URL:-'https://en.wikipedia.org/wiki/Linux_kernel?useformat=desktop'}
EV=/tmp/ph-webeval.py

for u in $(systemctl --user list-units "app-*Epiphany-*.scope" --no-legend | awk '{print $1}'); do
	systemctl --user stop "$u"
done
pkill -x epiphany 2>/dev/null
sleep 3
pkill -f WebKitWebProcess 2>/dev/null
sleep 2

# Optional: uprobes on the WebKit main thread's rendering phases. They must be
# armed BEFORE the browser maps the library, which is why this is here and not
# next to the measurement -- see brain/traps/uprobes-do-not-attach-to-an-already-
# mapped-library.
PHASE=0
if [ -n "${TK_BLANK_PHASE:-}" ] && [ -f /tmp/wkoff.sh ]; then
	. /tmp/wkoff.sh
	export TK_WKPHASE_OFFSETS
	N=$(pgrep -fc WebKitWebProcess 2>/dev/null || echo 0)
	if [ "$N" -eq 0 ]; then
		sh /tmp/ph-wkphase.sh arm && PHASE=1
	else
		echo "[$L] $N web processes still up -- phase probes would not attach, skipping"
	fi
fi
rm -f ~/.local/share/epiphany/session_state.xml "/tmp/wl-$L.log"
F0=$(sudo -n dmesg | grep -c "gpu fault")
T0=$(($(cat /sys/class/thermal/thermal_zone0/temp)/1000))

# shellcheck disable=SC2086
setsid systemd-run --user --scope --quiet --slice=app.slice \
	-u "app-gnome-org.gnome.Epiphany-$$.scope" \
	env WEBKIT_SKIA_ENABLE_CPU_RENDERING=1 WEBKIT_SKIA_CPU_PAINTING_THREADS=2 \
	WEBKIT_GST_VIDEO_DECODING_LIMIT=2560x1440@60 WEBKIT_LAYERS_TILE_SIZE=1440x1024 \
	WEBKIT_INSPECTOR_HTTP_SERVER=127.0.0.1:9222 WAYLAND_DEBUG=1 \
	${TK_BLANK_ENV:-} epiphany ${PORTHOLE_EPHY_ARGS:-} "$URL" \
	>"/tmp/eph-$L.log" 2>"/tmp/wl-$L.log" </dev/null &

# Room to fling, measured in VIEWPORTS rather than pixels. An absolute 6000 px
# is a desktop-Wikipedia number: m.youtube.com's results page came back at
# 1284 px and was refused as "too short" when it is simply a page that lays out
# in CSS pixels at DPR 3 and fills itself lazily. What the arm needs is a
# document several screens tall, whatever the units.
#
# NUDGE is why the loop is not just a longer poll: an infinite-scroll page
# stays one viewport tall until something scrolls it, so waiting alone never
# reaches the threshold. One scroll to the bottom, then back to the top.
NEED=${TK_BLANK_VIEWPORTS:-3}
H=0; VH=0; nudged=0
for i in $(seq 1 40); do
	sleep 2
	H=$(python3 $EV 'document.documentElement.scrollHeight' 2>/dev/null)
	VH=$(python3 $EV 'window.innerHeight' 2>/dev/null)
	case "$H" in ''|*[!0-9]*) H=0 ;; esac
	case "$VH" in ''|*[!0-9]*) VH=0 ;; esac
	[ "$VH" -gt 0 ] && [ "$H" -gt $((VH * NEED)) ] && break
	if [ "$i" -ge 8 ] && [ "$nudged" -eq 0 ] && [ "$VH" -gt 0 ]; then
		nudged=1
		echo "[$L] ${H}px / ${VH}px viewport -- nudging a lazy page to load more"
		python3 $EV 'window.scrollTo(0, document.body.scrollHeight); "down"' >/dev/null 2>&1
		sleep 4
		python3 $EV 'window.scrollTo(0, 0); "up"' >/dev/null 2>&1
	fi
done
echo "[$L] $(python3 $EV 'document.title' 2>/dev/null | cut -c1-46) scrollHeight=$H viewport=$VH"
# "pages take a while to render" needs a number, and the browser already has
# one. Splitting it matters: ttfb is the network's, dom-load is ours.
echo "[$L] nav ms: $(python3 $EV 'var t=performance.timing,n=t.navigationStart; JSON.stringify({dns:t.domainLookupEnd-t.domainLookupStart,conn:t.connectEnd-t.connectStart,ttfb:t.responseStart-n,resp:t.responseEnd-t.responseStart,domInteractive:t.domInteractive-n,domContentLoaded:t.domContentLoadedEventEnd-n,load:t.loadEventEnd-n})' 2>/dev/null)"
[ "$VH" -gt 0 ] && [ "$H" -gt $((VH * NEED)) ] || {
	echo "[$L] ${H}px is under $NEED viewports of ${VH}px -- nothing to fling, arm void"
	echo BLANKARMDONE; exit 1; }

# The screen has to be ON or every window below measures a dark output and the
# drag moves nothing. tk-ui says whether the screensaver refused.
# TK_BLANK_VIDEO=1: start the page's <video> and PROVE it is advancing before
# anything is measured. "Does it lag while I scroll" is a different workload
# from a pure scroll, and a paused player looks identical to a playing one in
# every frame statistic. Nothing here reads its numbers if the video is stuck.
VIDEO=0
if [ -n "${TK_BLANK_VIDEO:-}" ]; then
	for _ in $(seq 1 20); do
		rs=$(python3 $EV 'var v=document.querySelector("video"); v?v.readyState:-1' 2>/dev/null)
		case "$rs" in ''|*[!0-9-]*) rs=-1 ;; esac
		[ "$rs" -ge 1 ] && break
		sleep 2
	done
	python3 $EV 'var v=document.querySelector("video"); v.muted=true; v.play(); "play"' >/dev/null 2>&1
	sleep 6
	t1=$(python3 $EV 'document.querySelector("video").currentTime' 2>/dev/null)
	sleep 3
	t2=$(python3 $EV 'document.querySelector("video").currentTime' 2>/dev/null)
	if python3 -c "import sys; sys.exit(0 if float('${t2:-0}')-float('${t1:-0}') > 2 else 1)"; then
		VIDEO=1
	else
		sudo -n python3 /tmp/ph-touch.py tap 720 600 >/dev/null 2>&1
		sleep 8
		t1=$(python3 $EV 'document.querySelector("video").currentTime' 2>/dev/null); sleep 3
		t2=$(python3 $EV 'document.querySelector("video").currentTime' 2>/dev/null)
		python3 -c "import sys; sys.exit(0 if float('${t2:-0}')-float('${t1:-0}') > 2 else 1)" && VIDEO=1
	fi
	echo "[$L] video: $(python3 $EV 'var v=document.querySelector("video"),q=v.getVideoPlaybackQuality?v.getVideoPlaybackQuality():{}; JSON.stringify({w:v.videoWidth,h:v.videoHeight,paused:v.paused,t:Math.round(v.currentTime),total:q.totalVideoFrames,dropped:q.droppedVideoFrames})' 2>/dev/null) advancing=$VIDEO"
	[ "$VIDEO" = 1 ] || { echo "[$L] the video never advanced -- arm void, do not read its numbers"; echo BLANKARMDONE; exit 1; }
	Q0=$(python3 $EV 'var q=document.querySelector("video").getVideoPlaybackQuality(); q.totalVideoFrames+":"+q.droppedVideoFrames' 2>/dev/null)
fi

python3 /tmp/ph-ui.py unblank >/dev/null 2>&1 || echo "[$L] WARNING: unblank refused"
# ...and raise the browser. An arm before this one left the session in phosh's
# app grid, which sits over every window: the page loaded, the probes armed,
# and six flings went into the launcher. The scrollY guard caught it, but only
# after three minutes -- and /tmp/pre-$L.png is the thing to look at when it does.
python3 /tmp/ph-ui.py focus org.gnome.Epiphany >/dev/null 2>&1 || echo "[$L] WARNING: could not raise the browser"
python3 $EV 'window.scrollTo(0,0); "top"' >/dev/null 2>&1
sleep 2
Y0=$(python3 $EV 'window.scrollY' 2>/dev/null)

# The environment actually in force, from the process rather than the launcher.
WP=$(pgrep -n WebKitWebProc || true)
[ -n "$WP" ] && echo "[$L] environ: $(sudo -n tr '\0' '\n' < /proc/$WP/environ | grep -c WEBKIT_) WEBKIT_ vars"

echo "[$L] --- idle (control) ---"
python3 /tmp/ph-blankwatch.py 4 | tail -1

grim -t png "/tmp/pre-$L.png" 2>/dev/null && echo "[$L] pre-drag shot: /tmp/pre-$L.png"
echo "[$L] --- fling, pixels sampled ---"
python3 /tmp/ph-blankwatch.py 12 --save-worst "/tmp/worst-$L.png" > "/tmp/blank-$L.txt" 2>&1 &
BW=$!
sleep 1
sudo -n python3 /tmp/ph-gesture-bench.py drag ${TK_BLANK_DRAG:-720 2400 720 700 140 6} --fling --pause 900 >/dev/null 2>&1
wait $BW
tail -1 "/tmp/blank-$L.txt"
echo "[$L] worst samples:"; grep "^t=" "/tmp/blank-$L.txt" 2>/dev/null | sort -t= -k3 -rn | head -4

echo "[$L] --- fling, frame record (no pixel sampling) ---"
S0=$(wc -l < "/tmp/wl-$L.log")
if [ "$PHASE" = 1 ]; then
	rm -f /tmp/wk-$L.out
	setsid sh -c "sh /tmp/ph-wkphase.sh measure 12 > /tmp/wk-$L.out 2>&1; echo PHASEDONE >> /tmp/wk-$L.out" </dev/null >/dev/null 2>&1 &
	sleep 1
fi
sudo -n python3 /tmp/ph-gesture-bench.py drag ${TK_BLANK_DRAG:-720 2400 720 700 140 6} --fling --pause 900 --client "/tmp/wl-$L.log" 2>&1 | tail -6
S1=$(wc -l < "/tmp/wl-$L.log")
echo "[$L] wl lines during timing window: $((S1-S0))"

Y1=$(python3 $EV 'window.scrollY' 2>/dev/null)
echo "[$L] scrollY $Y0 -> $Y1"

if [ "$PHASE" = 1 ]; then
	# The drag is shorter than the window measuring it; poll for the marker
	# rather than reading a file that is still being written.
	i=0
	while [ "$i" -lt 40 ]; do
		grep -q PHASEDONE "/tmp/wk-$L.out" 2>/dev/null && break
		sleep 1; i=$((i + 1))
	done
	echo "[$L] --- main-thread phases during that drag ---"
	sed '/PHASEDONE/d' "/tmp/wk-$L.out" 2>/dev/null
fi

U=$(id -u)
CG=/sys/fs/cgroup/user.slice/user-$U.slice/user@$U.service/app.slice
SC=$(cat "$CG"/app-gnome-org.gnome.Epiphany-*.scope/memory.current 2>/dev/null | head -1)
SH=$(cat "$CG"/app-gnome-org.gnome.Epiphany-*.scope/memory.high 2>/dev/null | head -1)
echo "[$L] scope memory.current=$((${SC:-0}/1048576))M high=$((${SH:-0}/1048576))M"
[ -n "$WP" ] && echo "[$L] webproc drm=$(sudo -n awk '/drm-resident-memory/{s+=$2} END{print int(s/1024)}' /proc/$WP/fdinfo/* 2>/dev/null)M rss=$(awk '/VmRSS/{print $2/1024}' /proc/$WP/status 2>/dev/null | cut -d. -f1)M"
if [ "$VIDEO" = 1 ]; then
	Q1=$(python3 $EV 'var q=document.querySelector("video").getVideoPlaybackQuality(); q.totalVideoFrames+":"+q.droppedVideoFrames' 2>/dev/null)
	echo "[$L] video frames total:dropped $Q0 -> $Q1  (dropped over the arm is the number that matters)"
fi
echo "[$L] psi_full_mem=$(awk '/^full/{print $2}' /proc/pressure/memory) die=$(($(cat /sys/class/thermal/thermal_zone0/temp)/1000))C (was ${T0}C) gpu=$(($(cat /sys/class/devfreq/*gpu*/cur_freq | head -1)/1000000))MHz"
echo "[$L] gpu faults this arm: $(( $(sudo -n dmesg | grep -c 'gpu fault') - F0 ))"
echo BLANKARMDONE
