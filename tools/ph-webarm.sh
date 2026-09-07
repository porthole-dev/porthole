#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device as the session user (ph-webeval.py, ph-webvq.py, ph-touch.py,
#        ph-gesture-bench.py in /tmp; /tmp/sess.sh exporting the session env;
#        Epiphany; WAYLAND_DEBUG readable). Network reach to YouTube.
# env: -
# exits: 0 measured · 1 the video never played (arm void -- do not read its numbers)
# ph-webarm.sh LABEL [ENV...] -- one YouTube arm with the page state PROVEN
# before anything is measured.
#
# Every browser number before 2026-09-02 that came from a page whose state
# was assumed (consent wall, unstarted player, a <video> that never loaded)
# was wrong. This arm launches Epiphany with the env under test, waits for
# the player's <video> to exist, plays it muted, proves currentTime advances
# (one tap retry), and only then records 10 s of the browser's OWN
# wl_surface.commit intervals and presentation-feedback vsync deltas, idle
# and during a drag. The DPU counter is never used: it is phoc's rate.
#
#   ph-webarm.sh base
#   ph-webarm.sh sysmem "FD_MESA_DEBUG=sysmem"
L=$1; X=${2:-}
for u in $(systemctl --user list-units "app-*Epiphany-*.scope" --no-legend | awk '{print $1}'); do systemctl --user stop $u; done; sleep 2
rm -f ~/.local/share/epiphany/session_state.xml
F0=$(sudo -n dmesg | grep -c "gpu fault")
setsid systemd-run --user --scope --quiet --slice=app.slice -u app-gnome-org.gnome.Epiphany-$$.scope env WEBKIT_SKIA_ENABLE_CPU_RENDERING=1 WEBKIT_SKIA_CPU_PAINTING_THREADS=2 WEBKIT_GST_VIDEO_DECODING_LIMIT=2560x1440@60 WEBKIT_LAYERS_TILE_SIZE=1440x1024 WEBKIT_INSPECTOR_HTTP_SERVER=127.0.0.1:9222 WAYLAND_DEBUG=1 $X epiphany "https://www.youtube.com/watch?v=aqz-KE-bpKQ" >/tmp/eph.log 2>/tmp/wl.log </dev/null &
# wait for the player's <video> to exist with data (YouTube creates it late), then start it and prove it advances
for _ in $(seq 1 40); do sleep 2; st=$(python3 /tmp/ph-webeval.py 'var v=document.querySelector("video"); v?v.readyState:-1' 2>/dev/null); [ "${st:--1}" -ge 1 ] 2>/dev/null && break; done
echo "[$L] title: $(python3 /tmp/ph-webeval.py 'document.title' 2>/dev/null | cut -c1-50)  video readyState=$st"
python3 /tmp/ph-webeval.py 'var v=document.querySelector("video"); v.muted=true; v.play(); "play"' >/dev/null 2>&1
sleep 8
t1=$(python3 /tmp/ph-webeval.py 'document.querySelector("video").currentTime' 2>/dev/null); sleep 3
t2=$(python3 /tmp/ph-webeval.py 'document.querySelector("video").currentTime' 2>/dev/null)
adv=$(python3 -c "import sys; print('advancing' if float('${t2:-0}')-float('${t1:-0}')>2 else 'stuck')")
if [ "$adv" != "advancing" ]; then sudo -n python3 /tmp/ph-touch.py tap 720 600; sleep 6; t1=$(python3 /tmp/ph-webeval.py 'document.querySelector("video").currentTime'); sleep 3; t2=$(python3 /tmp/ph-webeval.py 'document.querySelector("video").currentTime'); adv=$(python3 -c "print('advancing' if float('${t2:-0}')-float('${t1:-0}')>2 else 'stuck')"); fi
echo "[$L] $(python3 /tmp/ph-webeval.py 'var v=document.querySelector("video"); JSON.stringify({w:v.videoWidth,h:v.videoHeight,paused:v.paused,t:Math.round(v.currentTime),rs:v.readyState})') $adv"
[ "$adv" = "advancing" ] || { echo "[$L] VIDEO NOT PLAYING -- arm void"; exit 1; }
S0=$(wc -l < /tmp/wl.log); sleep 10; S1=$(wc -l < /tmp/wl.log)
python3 /tmp/ph-webvq.py 2 1 | tail -1; python3 /tmp/threadcpu.py | head -1
sed -n "${S0},${S1}p" /tmp/wl.log > /tmp/wl-$L-idle.log
S0=$(wc -l < /tmp/wl.log); sudo -n python3 /tmp/ph-gesture-bench.py drag 720 2400 720 900 500 4 >/dev/null 2>&1; S1=$(wc -l < /tmp/wl.log)
sed -n "${S0},${S1}p" /tmp/wl.log > /tmp/wl-$L-drag.log
python3 - "$L" <<'PY'
import re,sys
L=sys.argv[1]
def sec(s):
    h,m,r=s.split(":"); return int(h)*3600+int(m)*60+float(r)
for ph in ("idle","drag"):
    ts={}; pres=[]
    for l in open(f"/tmp/wl-{L}-{ph}.log"):
        m=re.match(r'\[(\d+:\d+:[\d.]+)\]\s+ -> (wl_surface#\d+)\.commit\(\)',l)
        if m: ts.setdefault(m.group(2),[]).append(sec(m.group(1)))
        m=re.search(r'wp_presentation_feedback#\d+\.presented\(([^)]*)\)',l)
        if m:
            a=[x.strip() for x in m.group(1).split(",")]
            if len(a)>=7: pres.append(int(a[5]))
    top=max(ts.items(), key=lambda x:len(x[1])) if ts else (None,[])
    d=sorted((b-a)*1000 for a,b in zip(top[1],top[1][1:])); n=len(d)
    pd=[b-a for a,b in zip(pres,pres[1:])]
    import collections
    print("[%s %s] commits=%d over %.1fs p50=%.1f p90=%.1f max=%.1f >33ms:%d | presented every N vsyncs: %s"%(L,ph,n+1,(top[1][-1]-top[1][0]) if n else 0,d[n//2] if n else 0,d[int(n*.9)] if n else 0,d[-1] if n else 0,sum(x>33 for x in d),dict(collections.Counter(pd).most_common(4))))
PY
echo "faults delta $(( $(sudo -n dmesg | grep -c "gpu fault") - F0 ))"
timeout 10 grim -s 0.25 /tmp/shot.png
