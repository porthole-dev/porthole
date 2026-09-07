#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device with passwordless sudo; perf; CONFIG_UPROBE_EVENTS; a running
#        WebKitWebProcess. Offsets are per build (see ADDR and ph-webframe.sh).
# env: -
# exits: 0 ran
# ph-webdraws.sh [SECONDS] -- the compositor's draw census per frame.
#
# Fetches the FloatRect argument of every TextureMapper::drawTexture (and the
# NV12 video draw, and BitmapTexturePool::acquireTexture) through uprobes and
# prints draws per frame, summed quad area per frame (CSS px of the layer
# space -- multiply by DPR^2 for device pixels) and the commonest quad sizes.
# On m.youtube.com this showed 73 quads / 61 Mpx device per 3.7 Mpx viewport,
# 33 of them full-width tiles from three page-sized layers: the GPU half of
# the frame is overdraw, not the video.
# ADDR: webkit2gtk-6.0 2.52.6-r52, build-id 473546012ccb99255f7949aa90e5ad3d131a4c92
L=/usr/lib/libwebkitgtk-6.0.so.4.16.10; T=/sys/kernel/tracing
sudo -n sh -c "echo > $T/uprobe_events"
sudo -n sh -c "printf 'p:wk/frame $L:0x226f588\np:wk/dt $L:0x22dd91c w=+8(%%x2):u32 h=+12(%%x2):u32\np:wk/dtid $L:0x22ddd4c w=+8(%%x3):u32 h=+12(%%x3):u32\np:wk/nv12 $L:0x22df044 w=+8(%%x4):u32 h=+12(%%x4):u32\np:wk/acq $L:0x22d85b0 w=+0(%%x1):u32 h=+4(%%x1):u32\n' >> $T/uprobe_events"
P=$(pgrep WebKitWebProc | tail -1)
sudo -n perf record -q -o /tmp/fill.data -p $P -e wk:frame -e wk:dt -e wk:dtid -e wk:nv12 -e wk:acq -- sleep ${1:-3} 2>&1 | grep -v '^\['
# shellcheck disable=SC2024  # sudo is for perf; the redirect target is /tmp and needs none
sudo -n perf script -i /tmp/fill.data -F event,trace 2>/dev/null > /tmp/fill.txt
sudo -n sh -c "echo > $T/uprobe_events"
python3 - <<'PY'
import re,struct,collections
f=lambda u:struct.unpack('f',struct.pack('I',u))[0]
frames=[]; cur=None
for l in open('/tmp/fill.txt'):
    m=re.match(r'\s*(\S+):\s*(.*)',l)
    if not m: continue
    e,rest=m.groups()
    if e=='wk:frame': cur=collections.defaultdict(list); frames.append(cur); continue
    if cur is None: continue
    w=re.search(r'w=(\d+)',rest); h=re.search(r'h=(\d+)',rest)
    if not (w and h): continue
    if e=='wk:acq': cur['acq'].append((int(w.group(1)),int(h.group(1))))
    else: cur[e].append((f(int(w.group(1))),f(int(h.group(1)))))
fr=frames[1:-1]
print("frames=%d"%len(fr))
for k in ('wk:dt','wk:dtid','wk:nv12'):
    n=[len(x[k]) for x in fr]; a=[sum(w*h for w,h in x[k])/1e6 for x in fr]
    print("%-8s draws/frame=%.1f  area/frame=%.1f Mpx"%(k,sum(n)/len(fr),sum(a)/len(fr)))
sz=collections.Counter()
for x in fr:
    for w,h in x['wk:dt']: sz[(round(w),round(h))]+=1
print("top quad sizes:",[(k,round(v/len(fr),1)) for k,v in sz.most_common(8)])
print("intermediate surfaces/frame:",[x['acq'] for x in fr[:3]])
PY
