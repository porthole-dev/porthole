#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device as the session user (ph-webeval.py, ph-touch.py, ph-ui.py,
#        ph-gesture-bench.py, threadcpu.py in /tmp; /tmp/sess.sh); Epiphany;
#        grim and lswt. Network reach to the page.
# env: TK_SCROLL_URL (default: a long Wikipedia article)
# exits: 0 measured · 1 the arm is void -- the page never loaded, or the drag
#        moved nothing and the numbers describe a still screen
# ph-scrollarm.sh LABEL [ENV...] -- one PURE-SCROLL arm, with no video playing.
#
# WHY THIS EXISTS, separately from ph-webarm.sh: that tool's "drag" phase runs
# while a 1440p YouTube video is decoding and compositing, so every scroll
# number this project has recorded is a scroll *plus playback* number. The
# 19 ms paint that the 2026-09-04 handoff calls the ceiling was measured under
# playback and then reasoned about as if it described scrolling. Those are
# different workloads: measured 2026-09-05, a pure Wikipedia scroll costs the
# WebProcess 45% of ONE core, with the two SkiaCPUWorker threads at 3%
# combined. Scrolling is not raster-bound on this device, and no instrument
# here could show that until this one.
#
# THE TRAP THIS TOOL EXISTS TO CLOSE: a drag against a page that is already at
# the end of its scroll range animates nothing, and every frame statistic then
# describes a still screen. ph-gesture-bench.py catches it ("the gesture hit
# nothing that animates") but only after the fact. This arm proves the page can
# scroll BEFORE it measures -- scrollTo(0,0), then read scrollY back after the
# drag and refuse to report if it did not move.
#
#   ph-scrollarm.sh base
#   ph-scrollarm.sh uclamp "WEBKIT_SKIA_CPU_PAINTING_THREADS=6"
#
# TK_EPHY_ARGS passes flags to epiphany itself. `--kiosk-mode` is the one that
# matters: it removes the chrome that otherwise auto-hides mid-drag, and a
# chrome hide RESIZES the web view, which re-evaluates the page's dynamic media
# queries and can reconstruct the whole style resolver.
set -u
L=${1:?usage: ph-scrollarm.sh LABEL [ENV...]}; X=${2:-}
# useformat=desktop matters: mobile Wikipedia collapses every section, so the
# same article is 3363px tall there and 74085px here. A page with no room to
# scroll is the void arm this tool exists to refuse.
URL=${TK_SCROLL_URL:-'https://en.wikipedia.org/wiki/Linux_kernel?useformat=desktop'}
EV=/tmp/ph-webeval.py

for u in $(systemctl --user list-units "app-*Epiphany-*.scope" --no-legend | awk '{print $1}'); do
	systemctl --user stop "$u"
done
sleep 2  # contract: sleep-ok systemctl stop already blocks on the cgroup, but phoc's own client teardown (releasing the surface/registration) has no state exposed over ssh to poll for
rm -f ~/.local/share/epiphany/session_state.xml "/tmp/wl-$L.log"

# WAYLAND_DEBUG gives the app's OWN commit intervals; the DPU counter is phoc's
# rate and says nothing about whether the browser kept up. Skia CPU rendering is
# set explicitly because a launch that skips the user manager loses
# environment.d -- see the trap of that name; $X is last so an arm can override.
setsid systemd-run --user --scope --quiet --slice=app.slice \
	-u "app-gnome-org.gnome.Epiphany-$$.scope" \
	env WEBKIT_SKIA_ENABLE_CPU_RENDERING=1 WEBKIT_SKIA_CPU_PAINTING_THREADS=2 \
	WEBKIT_LAYERS_TILE_SIZE=1440x1024 \
	WEBKIT_INSPECTOR_HTTP_SERVER=127.0.0.1:9222 WAYLAND_DEBUG=1 \
	$X epiphany ${TK_EPHY_ARGS:-} "$URL" >"/tmp/eph-$L.log" 2>"/tmp/wl-$L.log" </dev/null &

# Poll the condition that actually matters -- a document tall enough to scroll.
# readyState alone is not it: it reads "complete" on Epiphany's initial
# about:blank AND on a laid-out-but-still-growing page, and this arm reported a
# 827px "complete" document once for exactly that reason.
H=0
for _ in $(seq 1 40); do
	sleep 2
	H=$(python3 $EV 'document.documentElement.scrollHeight' 2>/dev/null)
	# Test $H, not ${H:-0}: the default made the empty case unreachable, so a
	# webeval that answered nothing left H empty and every later
	# `[ "$H" -gt 6000 ]` printed `sh: out of range` forty times over instead
	# of refusing the arm.
	case "$H" in ''|*[!0-9]*) H=0 ;; esac
	[ "$H" -gt 6000 ] && break
done
echo "[$L] $(python3 $EV 'document.title' 2>/dev/null | cut -c1-46) scrollHeight=$H"
[ "$H" -gt 6000 ] || { echo "[$L] page is only ${H}px tall, nothing to scroll -- arm void"; exit 1; }

# Anything that has to act on the browser's own threads has to run here: after
# the page is up (so the threads exist) and before the drag (so it is measured).
# Per-task uclamp is the reason this exists -- it cannot be set from the launch
# env, and this systemd has no cgroup cpu controller to set it through.
[ -n "${TK_SCROLL_HOOK:-}" ] && { echo "[$L] hook: $TK_SCROLL_HOOK"; sh -c "$TK_SCROLL_HOOK"; }

python3 $EV 'window.scrollTo(0,0); "top"' >/dev/null 2>&1
# Poll the readback instead of guessing how long the commit takes to reach
# the inspector -- same idea as the scrollHeight poll above.
Y0=
for _ in $(seq 1 5); do
	Y0=$(python3 $EV 'window.scrollY' 2>/dev/null)
	[ "$Y0" = 0 ] && break
	sleep 1
done

# Drag finger up = content scrolls down. From the top there is always room.
(sleep 1; python3 /tmp/threadcpu.py 8 > "/tmp/tc-$L.txt" 2>&1) &
TC=$!
# TK_SCROLL_DRAG overrides the gesture, because "is it smooth" and "is this
# knob better" want different ones: the default leaves 700 ms between drags,
# `--pause 0` scrolls continuously and is the one to use when a gap in the
# frame record is supposed to mean a stall.
# shellcheck disable=SC2086
sudo -n python3 /tmp/ph-gesture-bench.py drag ${TK_SCROLL_DRAG:-720 2400 720 900 500 8} \
	--client "/tmp/wl-$L.log" 2>&1 | tail -8
# ONLY the sampler. A bare `wait` also waits on the browser started above, which
# never exits, and the whole arm hangs with its output stuck in the pipeline.
wait $TC

Y1=$(python3 $EV 'window.scrollY' 2>/dev/null)
echo "[$L] scrollY $Y0 -> $Y1"
python3 - "$Y0" "$Y1" <<'PY' || exit 1
import sys
try:
    moved = float(sys.argv[2]) - float(sys.argv[1])
except ValueError:
    sys.exit("[arm void] scrollY unreadable -- the inspector answered nothing")
if moved < 500:
    sys.exit(f"[arm void] the page moved {moved:.0f}px; the numbers above "
             "describe a still screen, not a scroll")
PY
cat "/tmp/tc-$L.txt"
