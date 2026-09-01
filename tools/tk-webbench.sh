#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device as the session user (tk-gesture-bench.py + tk-touch.py +
#        tk-ui.py in /tmp; a running phosh session; Epiphany installed)
# env: -
# exits: 0 ran · 1 no session
# tk-webbench.sh -- browser smoothness A/B on a deterministic heavy page.
#
# Generates a raster-expensive local feed (gradients, shadows, sticky
# blurred header -- the workload that reproduces real-world browser jank
# where a plain text page measures a false 0), launches Epiphany with the
# WebKit env under test, flings and drags it, and reports frame stats, the
# a5xx fault delta and the hottest thermal zone. One line per variant makes
# an evening of rendering experiments comparable:
#
#   tk-webbench.sh "WEBKIT_SKIA_ENABLE_CPU_RENDERING=1 WEBKIT_SKIA_CPU_PAINTING_THREADS=4" cpu4
#   tk-webbench.sh "WEBKIT_SKIA_ENABLE_CPU_RENDERING=1 WEBKIT_SKIA_CPU_PAINTING_THREADS=2" cpu2
#   tk-webbench.sh "GDK_DEBUG=" gpu     # Skia-GPU: WATCH THE FAULT DELTA
#
# Two instrument warnings paid for on taimen 2026-09-01: synthetic touches
# ride a new uinput node, so an input-boost daemon watching the real
# touchscreen does NOT fire -- absolute numbers are a floor, compare only
# within a run set. And a nonzero fault delta means a GPU reset happened:
# clients' GL textures are now dead (phosh loses its background), the
# cmd-mode panel may hold visible wreckage no screenshot shows, and the
# session needs a restart before the next variant is comparable.
ENVS=$1; LABEL=${2:-variant}
PAGE=/tmp/webbench-feed.html
[ -f "$PAGE" ] || python3 - "$PAGE" <<'PYEOF'
import sys
cards = []
for i in range(200):
    h = (i * 37) % 360
    cards.append(
        f'<div class="card" style="background:linear-gradient(135deg,'
        f'hsl({h},70%,45%),hsl({(h+60)%360},60%,30%))">'
        f'<div class="ava" style="background:radial-gradient(circle at 30% 30%,'
        f'hsl({(h+180)%360},80%,70%),hsl({h},60%,20%))"></div>'
        f'<div class="txt"><b>Channel {i}</b> · {i*7%60} minutes ago<br>'
        f'Video title number {i}: the quick brown fox does something '
        f'surprising in scene {i%9}<br><span class="pill">HD</span>'
        f'<span class="pill">{i*13%500}K views</span>'
        f'<span class="pill">{i%20} comments</span></div></div>')
open(sys.argv[1], "w").write(
    '<!doctype html><html><head><meta charset="utf-8"><title>feed</title>'
    '<style>body{margin:0;font:16px sans-serif;background:#111;color:#eee}'
    'header{position:sticky;top:0;background:#000d;backdrop-filter:blur(6px);'
    'padding:14px;font-size:20px;box-shadow:0 2px 12px #000;z-index:2}'
    '.card{display:flex;gap:12px;margin:10px;padding:14px;border-radius:16px;'
    'box-shadow:0 4px 14px #0008}'
    '.ava{width:64px;height:64px;border-radius:50%;flex:none;'
    'box-shadow:inset 0 0 8px #0006}.txt{line-height:1.45}'
    '.pill{display:inline-block;background:#fff2;border-radius:999px;'
    'padding:2px 10px;margin:4px 6px 0 0;font-size:13px}</style></head>'
    f'<body><header>Heavy Feed Benchmark</header>{"".join(cards)}</body></html>')
PYEOF
# The three instruments this reads its numbers from. Unchecked, a missing one
# made every `grep` below match nothing and the run printed a fault delta and a
# temperature with no frame stats at all -- a null from a path that never
# executed, which brain/laws/a-null-from-an-unexecuted-path-is-not-a-refutation
# exists to stop being read as "this variant janks less".
for _i in tk-gesture-bench.py tk-touch.py tk-ui.py; do
	[ -f "/tmp/$_i" ] || { echo "missing /tmp/$_i -- push it first"; exit 1; }
done

P=$(pgrep -x phosh | head -1)
[ -n "$P" ] || { echo "no phosh session"; exit 1; }
eval "$(sudo -n tr '\0' '\n' < /proc/"$P"/environ |
	grep -E '^(WAYLAND_DISPLAY|XDG_RUNTIME_DIR|DBUS_SESSION_BUS_ADDRESS)=' |
	sed 's/^/export /')"
# Unquoted on purpose: pgrep answers with one PID per line and a second
# Epiphany left running is a second set of WebProcesses competing for the GPU,
# which is the one thing an A/B must not have.
# shellcheck disable=SC2046
pgrep -x epiphany >/dev/null && { kill $(pgrep -x epiphany); sleep 3; }
F0=$(sudo -n dmesg | grep -c "a5xx.*fault")
env $ENVS setsid epiphany --new-window "file://$PAGE" >/dev/null 2>&1 &
sleep 10
sudo -n python3 /tmp/tk-gesture-bench.py grid-fling 4 2>/dev/null |
	grep -E "frame ms|dropped|jank>|NOT" | sed "s/^/[$LABEL fling] /"
sudo -n python3 /tmp/tk-gesture-bench.py grid-drag 4 2>/dev/null |
	grep -E "frame ms|dropped|jank>|NOT" | sed "s/^/[$LABEL drag]  /"
F1=$(sudo -n dmesg | grep -c "a5xx.*fault")
T=$(for z in /sys/class/thermal/thermal_zone*/temp; do cat "$z" 2>/dev/null; done | sort -rn | head -1)
echo "[$LABEL] gpu-faults-delta=$((F1-F0)) hottest=$((T/1000))C"
