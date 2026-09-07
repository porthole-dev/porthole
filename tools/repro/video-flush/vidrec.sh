#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: runs ON THE DEVICE. /tmp/sess.sh, /tmp/ph-wkphase.sh, /tmp/ph-webeval.py.
# env: TK_WKPHASE_OFFSETS (required), PORTHOLE_VID_URL, PORTHOLE_VID_LIMIT, PORTHOLE_VID_QUALITY
# exits: 0 measured · 1 the arm is void
# vidrec.sh -- one steady-state YouTube playback arm, probed, with a control.
#
# Three things here are not decoration, and each of them cost an arm:
#
# 1. NO DRAG. ph-webarm.sh drags mid-arm; that scrolls the player out of the
#    viewport, and MediaPlayerPrivateGStreamer::setVisibleInViewport() puts the
#    whole pipeline into GST_STATE_PAUSED for a MUTED video. Every frame
#    statistic after that describes a stopped decoder.
# 2. A POSITIVE CONTROL. A seek forces a real pipeline flush, so probes that
#    fire in the control window were attached -- which is what makes a null in
#    the playback window mean "this does not happen during steady playback"
#    rather than "the probes never attached".
# 3. FRAMES ARE SAMPLED, NOT TOTALLED. getVideoPlaybackQuality() counters reset
#    when YouTube switches representation, so a single before/after subtraction
#    reads as a stall that never happened. Sample it and print the series.
set -u
[ -f /tmp/wkoff.sh ] && . /tmp/wkoff.sh
: "${TK_WKPHASE_OFFSETS:?run tools/ph-wkoffsets.sh on the host}"
export TK_WKPHASE_OFFSETS
. /tmp/sess.sh
EV=/tmp/ph-webeval.py
URL=${PORTHOLE_VID_URL:-https://www.youtube.com/watch?v=aqz-KE-bpKQ}
LIMIT=${PORTHOLE_VID_LIMIT:-2560x1440@60}
QUAL=${PORTHOLE_VID_QUALITY:-hd1440}
WIN=${PORTHOLE_VID_WINDOW:-30}

for u in $(systemctl --user list-units "app-*Epiphany-*.scope" --no-legend | awk '{print $1}'); do
	systemctl --user stop "$u" 2>/dev/null
done
pkill -x epiphany 2>/dev/null; sleep 3; pkill -f WebKitWebProc""ess 2>/dev/null; sleep 3
N=$(pgrep -f WebKitWebProc""ess | wc -l)
echo "webprocs before arming: $N"
[ "$N" -eq 0 ] || { echo "REFUSE: a browser is still running, probes would not attach"; echo VIDRECDONE; exit 1; }

sh /tmp/ph-wkphase.sh arm
rm -f /tmp/wl-vid.log
setsid systemd-run --user --scope --quiet --slice=app.slice \
	-u "app-gnome-org.gnome.Epiphany-$$.scope" \
	env WEBKIT_SKIA_ENABLE_CPU_RENDERING=1 WEBKIT_SKIA_CPU_PAINTING_THREADS=2 \
	WEBKIT_LAYERS_TILE_SIZE=1440x1024 WEBKIT_GST_VIDEO_DECODING_LIMIT="$LIMIT" \
	WEBKIT_INSPECTOR_HTTP_SERVER=127.0.0.1:9222 WAYLAND_DEBUG=1 \
	${PORTHOLE_VID_ENV:-} epiphany "$URL" >/tmp/eph-vid.log 2>/tmp/wl-vid.log </dev/null &

st=-1
for _ in $(seq 1 40); do
	sleep 2
	st=$(python3 $EV 'var v=document.querySelector("video"); v?v.readyState:-1' 2>/dev/null)
	[ "${st:--1}" -ge 1 ] 2>/dev/null && break
done
python3 $EV 'var v=document.querySelector("video"); v.muted=true; v.play(); "play"' >/dev/null 2>&1
sleep 6
if [ "$QUAL" != auto ]; then
	python3 $EV "var p=document.getElementById('movie_player'); p&&p.setPlaybackQualityRange?(p.setPlaybackQualityRange('$QUAL'),'asked'):'no api'" >/dev/null 2>&1
fi
# Wait for the representation to actually settle, then prove it is advancing.
for _ in $(seq 1 20); do
	sleep 2
	H=$(python3 $EV 'document.querySelector("video").videoHeight' 2>/dev/null)
	case "$H" in ''|*[!0-9]*) H=0 ;; esac
	[ "$H" -ge 720 ] && break
done
# YouTube will not autoplay without a user gesture, and an injected tap against
# a BLANKED panel reaches the client but never wakes the output, so the tap
# lands on a page nobody is showing and the arm reports "stuck" with no reason.
# Unblank first, then tap, then re-prove -- twice, because the first tap often
# only dismisses a consent overlay.
advancing() {
	a=$(python3 $EV 'document.querySelector("video").currentTime' 2>/dev/null)
	sleep 3
	b=$(python3 $EV 'document.querySelector("video").currentTime' 2>/dev/null)
	python3 -c "print('advancing' if float('${b:-0}')-float('${a:-0}')>2 else 'stuck')"
}
ADV=$(advancing)
for _ in 1 2; do
	[ "$ADV" = advancing ] && break
	python3 /tmp/ph-ui.py unblank >/dev/null 2>&1
	sudo -n python3 /tmp/ph-touch.py tap 720 600 >/dev/null 2>&1
	sleep 5
	python3 $EV 'var v=document.querySelector("video"); v.muted=true; v.play(); "play"' >/dev/null 2>&1
	sleep 4
	ADV=$(advancing)
done
echo "[vid] $(python3 $EV 'var v=document.querySelector("video"),p=document.getElementById("movie_player");JSON.stringify({w:v.videoWidth,h:v.videoHeight,q:p&&p.getPlaybackQuality?p.getPlaybackQuality():"?"})' 2>/dev/null) $ADV"
[ "$ADV" = advancing ] || { echo "[vid] VIDEO NOT PLAYING -- arm void"; sh /tmp/ph-wkphase.sh off; echo VIDRECDONE; exit 1; }

S0=$(wc -l < /tmp/wl-vid.log)
rm -f /tmp/vid-win.out
setsid sh -c "sh /tmp/ph-wkphase.sh measure $WIN > /tmp/vid-win.out 2>&1; echo VIDWINDOWDONE >> /tmp/vid-win.out" </dev/null >/dev/null 2>&1 &
(sleep 2; python3 /tmp/threadcpu.py 10 > /tmp/tc-vid.txt 2>&1) &
echo "=== frame series (5 s apart, steady playback, no gesture) ==="
i=0
while [ "$i" -lt $((WIN / 5)) ]; do
	sleep 5
	python3 $EV 'var v=document.querySelector("video"),q=v.getVideoPlaybackQuality();JSON.stringify({t:Math.round(v.currentTime*10)/10,h:v.videoHeight,tot:q.totalVideoFrames,drop:q.droppedVideoFrames})' 2>/dev/null | head -1
	i=$((i + 1))
done
i=0; while [ "$i" -lt 30 ]; do grep -q VIDWINDOWDONE /tmp/vid-win.out 2>/dev/null && break; sleep 2; i=$((i+1)); done
S1=$(wc -l < /tmp/wl-vid.log)
echo "=== WebProcess threads during playback ==="
cat /tmp/tc-vid.txt 2>/dev/null | head -12
echo "=== PLAYBACK WINDOW (probes) ==="
cat /tmp/vid-win.out 2>/dev/null

echo "=== CONTROL: a seek, which must flush the pipeline ==="
rm -f /tmp/vid-ctl.out
setsid sh -c "sh /tmp/ph-wkphase.sh measure 12 > /tmp/vid-ctl.out 2>&1; echo CTLDONE >> /tmp/vid-ctl.out" </dev/null >/dev/null 2>&1 &
sleep 2
python3 $EV 'var v=document.querySelector("video"); v.currentTime=v.currentTime+45; "seek"' >/dev/null 2>&1
sleep 4
python3 $EV 'var v=document.querySelector("video"); v.currentTime=v.currentTime+45; "seek"' >/dev/null 2>&1
i=0; while [ "$i" -lt 20 ]; do grep -q CTLDONE /tmp/vid-ctl.out 2>/dev/null && break; sleep 2; i=$((i+1)); done
cat /tmp/vid-ctl.out 2>/dev/null

sed -n "${S0},${S1}p" /tmp/wl-vid.log > /tmp/wl-vid-window.log
python3 - <<'PY'
import re, collections
def sec(s):
    h, m, r = s.split(":"); return int(h)*3600 + int(m)*60 + float(r)
ts, pres = {}, []
for l in open("/tmp/wl-vid-window.log"):
    m = re.match(r'\[(\d+:\d+:[\d.]+)\]\s+ -> (wl_surface#\d+)\.commit\(\)', l)
    if m: ts.setdefault(m.group(2), []).append(sec(m.group(1)))
    m = re.search(r'wp_presentation_feedback#\d+\.presented\(([^)]*)\)', l)
    if m:
        a = [x.strip() for x in m.group(1).split(",")]
        if len(a) >= 7: pres.append(int(a[5]))
top = max(ts.items(), key=lambda x: len(x[1])) if ts else (None, [])
d = sorted((b - a) * 1000 for a, b in zip(top[1], top[1][1:])); n = len(d)
pd = [b - a for a, b in zip(pres, pres[1:])]
print("client %s: commits=%d p50=%.1f p90=%.1f p99=%.1f max=%.1f jank>33ms=%d" % (
    top[0], n + 1, d[n//2] if n else 0, d[int(n*.9)] if n else 0,
    d[int(n*.99)] if n else 0, d[-1] if n else 0, sum(x > 33 for x in d)))
print("presented every N vsyncs:", dict(collections.Counter(pd).most_common(5)))
PY
grep . /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -t: -k2 -rn | head -1
sh /tmp/ph-wkphase.sh off
for u in $(systemctl --user list-units "app-*Epiphany-*.scope" --no-legend | awk '{print $1}'); do
	systemctl --user stop "$u" 2>/dev/null
done
pkill -x epiphany 2>/dev/null
echo VIDRECDONE
