#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: runs ON THE DEVICE as the session user. /tmp/sess.sh, /tmp/tk-webeval.py,
#        /tmp/tk-webvq.py, /tmp/tk-touch.py, /tmp/tk-ui.py; /tmp/stallcatch2.py
#        (a copy of tools/tk-stallcatch.py) and eu-stack when TK_STALLCATCH=1.
# env: /tmp/videnv.sh is sourced if present: TK_VID_URL, TK_VID_LIMIT,
#      TK_VID_QUALITY, TK_VID_WINDOW, TK_VID_LABEL, TK_VID_ENV, TK_VID_JS,
#      TK_STALLCATCH
# exits: 0 measured · 1 the video never played (arm void)
# vidbase.sh -- one steady-state YouTube playback arm, no probes: a 2 s frame
# series from getVideoPlaybackQuality(), the UI process's own wl_surface.commit
# gaps and presentation cadence, the GPU clock, the journal, and (optionally)
# whole-process stack dumps whenever the compositor thread or the decoder
# thread idles. This is the arm that found the ambient-mode blur on 2026-09-06.
# Stage with tools/repro/phoc-planes/run-arm.sh /tmp/vidbase.sh VIDBASEDONE.
set -u
. /tmp/sess.sh
[ -f /tmp/videnv.sh ] && . /tmp/videnv.sh
EV=/tmp/tk-webeval.py
URL=${TK_VID_URL:-https://www.youtube.com/watch?v=aqz-KE-bpKQ}
LIMIT=${TK_VID_LIMIT:-2560x1440@60}
QUAL=${TK_VID_QUALITY:-hd1440}
WIN=${TK_VID_WINDOW:-60}
LABEL=${TK_VID_LABEL:-base}
stop_browser() { for u in $(systemctl --user list-units "app-*Epiphany-*.scope" --no-legend | awk '{print $1}'); do systemctl --user stop "$u" 2>/dev/null; done; pkill -x epiphany 2>/dev/null; }
stop_browser; sleep 2; pkill -f WebKitWebProc""ess 2>/dev/null; sleep 2
python3 /tmp/tk-ui.py unblank >/dev/null 2>&1
J0=$(date "+%Y-%m-%d %H:%M:%S")
echo "[$LABEL] start $J0 limit=$LIMIT qual=$QUAL env='${TK_VID_ENV:-}' temp0=$(cat /sys/class/thermal/thermal_zone0/temp)"
rm -f /tmp/wl-$LABEL.log
setsid systemd-run --user --scope --quiet --slice=app.slice -u "app-gnome-org.gnome.Epiphany-$$.scope" \
	env WEBKIT_SKIA_ENABLE_CPU_RENDERING=1 WEBKIT_SKIA_CPU_PAINTING_THREADS=2 WEBKIT_LAYERS_TILE_SIZE=1440x1024 \
	WEBKIT_GST_VIDEO_DECODING_LIMIT="$LIMIT" WEBKIT_INSPECTOR_HTTP_SERVER=127.0.0.1:9222 WAYLAND_DEBUG=1 \
	${TK_VID_ENV:-} epiphany "$URL" >/tmp/eph-$LABEL.log 2>/tmp/wl-$LABEL.log </dev/null &
st=-1
for _ in $(seq 1 40); do sleep 2; st=$(python3 $EV 'var v=document.querySelector("video"); v?v.readyState:-1' 2>/dev/null); [ "${st:--1}" -ge 1 ] 2>/dev/null && break; done
python3 $EV 'var v=document.querySelector("video"); v.muted=true; v.play(); "play"' >/dev/null 2>&1
sleep 6
[ "$QUAL" != auto ] && python3 $EV "var p=document.getElementById('movie_player'); p&&p.setPlaybackQualityRange?(p.setPlaybackQualityRange('$QUAL'),'asked'):'no api'" >/dev/null 2>&1
for _ in $(seq 1 20); do sleep 2; H=$(python3 $EV 'document.querySelector("video").videoHeight' 2>/dev/null); case "$H" in ''|*[!0-9]*) H=0 ;; esac; [ "$H" -ge 720 ] && break; done
advancing() { a=$(python3 $EV 'document.querySelector("video").currentTime' 2>/dev/null); sleep 3; b=$(python3 $EV 'document.querySelector("video").currentTime' 2>/dev/null); python3 -c "print('advancing' if float('${b:-0}')-float('${a:-0}')>2 else 'stuck')"; }
ADV=$(advancing)
for _ in 1 2; do
	[ "$ADV" = advancing ] && break
	python3 /tmp/tk-ui.py unblank >/dev/null 2>&1
	sudo -n python3 /tmp/tk-touch.py tap 720 600 >/dev/null 2>&1; sleep 5
	python3 $EV 'var v=document.querySelector("video"); v.muted=true; v.play(); "play"' >/dev/null 2>&1; sleep 4
	ADV=$(advancing)
done
echo "[$LABEL] $(python3 $EV 'var v=document.querySelector("video"),p=document.getElementById("movie_player");JSON.stringify({w:v.videoWidth,h:v.videoHeight,q:p&&p.getPlaybackQuality?p.getPlaybackQuality():"?",vis:document.visibilityState})' 2>/dev/null) $ADV"
[ "$ADV" = advancing ] || { echo "[$LABEL] VIDEO NOT PLAYING -- arm void"; stop_browser; echo VIDBASEDONE; exit 1; }
if [ -n "${TK_VID_JS:-}" ]; then
	echo "[$LABEL] js: $(python3 $EV "$TK_VID_JS" 2>&1 | head -c 1500)"
	sleep 3
fi
S0=$(wc -l < /tmp/wl-$LABEL.log)
rm -f /tmp/gpu-$LABEL.txt
( i=0; while [ $i -lt $WIN ]; do echo "$(date +%s) $(cat /sys/class/devfreq/5000000.gpu/cur_freq) $(cat /sys/class/thermal/thermal_zone0/temp) $(cat /sys/class/thermal/thermal_zone4/temp)" >> /tmp/gpu-$LABEL.txt; sleep 1; i=$((i+1)); done ) &
[ "${TK_STALLCATCH:-0}" = 1 ] && setsid sh -c "sudo -n python3 /tmp/stallcatch2.py $WIN WebKitWebProces Compositor 300 > /tmp/stall-$LABEL.txt 2>&1" </dev/null >/dev/null 2>&1 &
[ "${TK_STALLCATCH:-0}" = 1 ] && setsid sh -c "sudo -n python3 /tmp/stallcatch2.py $WIN WebKitWebProces dec 400 > /tmp/stall-$LABEL-ui.txt 2>&1" </dev/null >/dev/null 2>&1 &
echo "=== frame series (2 s apart) t h tot drop ready ahead_s paused vis ==="
i=0
while [ "$i" -lt $((WIN / 2)) ]; do
	sleep 2
	python3 $EV 'var v=document.querySelector("video"),q=v.getVideoPlaybackQuality();[Math.round(v.currentTime*10)/10,v.videoHeight,q.totalVideoFrames,q.droppedVideoFrames,v.readyState,(v.buffered.length?Math.round((v.buffered.end(v.buffered.length-1)-v.currentTime)*10)/10:-1),v.paused,document.visibilityState].join(" ")' 2>/dev/null | head -1
	i=$((i + 1))
done
S1=$(wc -l < /tmp/wl-$LABEL.log)
sed -n "${S0},${S1}p" /tmp/wl-$LABEL.log > /tmp/wl-$LABEL-window.log
python3 - "$LABEL" <<'PY'
import re, collections, sys
L=sys.argv[1]
def sec(s):
    h, m, r = s.split(":"); return int(h)*3600 + int(m)*60 + float(r)
ts, pres = {}, []
for l in open(f"/tmp/wl-{L}-window.log"):
    m = re.match(r'\[(\d+:\d+:[\d.]+)\]\s+ -> (wl_surface#\d+)\.commit\(\)', l)
    if m: ts.setdefault(m.group(2), []).append(sec(m.group(1)))
    m = re.search(r'\[(\d+:\d+:[\d.]+)\].*wp_presentation_feedback#\d+\.presented\(([^)]*)\)', l)
    if m:
        a = [x.strip() for x in m.group(2).split(",")]
        if len(a) >= 7: pres.append((sec(m.group(1)), int(a[5])))
top = max(ts.items(), key=lambda x: len(x[1])) if ts else (None, [])
t = top[1]
d = sorted((b - a) * 1000 for a, b in zip(t, t[1:])); n = len(d)
gaps = [(round(a % 3600, 2), round((b - a) * 1000)) for a, b in zip(t, t[1:]) if (b - a) > 0.1]
pd = [b[1] - a[1] for a, b in zip(pres, pres[1:])]
span = (t[-1] - t[0]) if n else 0
print("client %s: commits=%d over %.1fs (%.1f/s) p50=%.1f p90=%.1f p99=%.1f max=%.1f  >33ms:%d  >100ms:%d" % (
    top[0], n + 1, span, (n / span) if span else 0, d[n//2] if n else 0, d[int(n*.9)] if n else 0,
    d[int(n*.99)] if n else 0, d[-1] if n else 0, sum(x > 33 for x in d), len(gaps)))
print("presented every N vsyncs:", dict(collections.Counter(pd).most_common(6)))
print("gaps >100ms (t_in_hour, ms):", gaps[:30])
PY
echo "=== gpu MHz / cpu0 mC / gpu mC (1 Hz) ==="
awk '{g[int($2/1e6)]++; if($3>mt)mt=$3; if($4>mg)mg=$4} END{for(k in g) printf "%s MHz x%d  ", k, g[k]; printf "\nmax cpu0 %d mC, max gpu %d mC\n", mt, mg}' /tmp/gpu-$LABEL.txt
[ "${TK_STALLCATCH:-0}" = 1 ] && { echo "=== stall captures ==="; sleep 3; cat /tmp/stall-$LABEL.txt; echo "=== UI process stall captures ==="; cat /tmp/stall-$LABEL-ui.txt; }
echo "=== journal since start ==="
sudo -n journalctl --since "$J0" --no-pager -o short-iso 2>/dev/null | grep -i -E "dumped core|abnormally|gpu fault|hangcheck|venus|liftoff|Resource busy|WebKitWebProc|oom|epiphany\[" | grep -v -E "GResources|libinput" | cut -c12-19,40-200 | head -30
stop_browser
sleep 2
echo "[$LABEL] end temp=$(cat /sys/class/thermal/thermal_zone0/temp) gpu=$(cat /sys/class/devfreq/5000000.gpu/cur_freq)"
echo VIDBASEDONE
