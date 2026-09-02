#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device with passwordless sudo; perf; CONFIG_UPROBE_EVENTS;
#        a running WebKitWebProcess. The offsets below are for ONE build of
#        libwebkitgtk-6.0 (see ADDR) and change with every relink: recompute
#        them as `nm` vaddr minus the text LOAD delta (readelf -l) and edit ADDR.
# env: -
# exits: 0 ran
# tk-webframe.sh [SECONDS] -- where each compositor frame's period goes.
#
# Uprobes on ThreadedCompositor::renderLayerTree (entry + return),
# AcceleratedSurface::didRenderFrame and ::frameDone, plus the msm GPU
# submit/retire tracepoints system-wide, then one table: period, CPU paint,
# GPU submit->retire span, GPU tail after the paint, paint end -> FrameDone.
# This is what separated "the compositor paints 20 ms and then WAITS 10 ms"
# from "the compositor is slow", and killed the a5xx batch-bound theory
# (two kernel submits per frame). Evidence for the finding
# epiphanys-frame-is-20ms-of-compositor-cpu-plus-a-10ms-gpu-tail-not-a5xx-batches.
# ADDR: webkit2gtk-6.0 2.52.6-r52, build-id 473546012ccb99255f7949aa90e5ad3d131a4c92
L=/usr/lib/libwebkitgtk-6.0.so.4.16.10; T=/sys/kernel/tracing
sudo -n sh -c "echo > $T/uprobe_events"
sudo -n sh -c "printf 'p:wk/frame $L:0x226f588\nr:wk/frame_ret $L:0x226f588\np:wk/didrender $L:0x226cc7c\np:wk/framedone $L:0x226d59c\n' >> $T/uprobe_events"
P=$(pgrep WebKitWebProc | tail -1)
sudo -n perf record -q -o /tmp/tl.data -a -e wk:frame -e wk:frame_ret -e wk:didrender -e wk:framedone -e drm_msm_gpu:msm_gpu_submit -e drm_msm_gpu:msm_gpu_submit_retired -- sleep ${1:-5} 2>&1 | grep -v '^\['
sudo -n perf script -i /tmp/tl.data -F tid,time,event,trace 2>/dev/null > /tmp/tl.txt
sudo -n sh -c "echo > $T/uprobe_events"
python3 - <<'PY'
import re,statistics as st
ev=[]
for l in open('/tmp/tl.txt'):
    m=re.match(r'\s*(\d+)\s+([\d.]+):\s+(\S+):\s*(.*)',l)
    if m: ev.append((float(m.group(2)),m.group(3),m.group(1),m.group(4)))
ev.sort()
frames=[]; cur=None
gpu={}  # id -> (flush_t, retired_t, elapsed_ns)
for t,e,tid,rest in ev:
    if e=='wk:frame':
        cur={'start':t,'gpu':[]}; frames.append(cur)
    elif cur is None: continue
    elif e=='wk:frame_ret': cur['end']=t
    elif e=='wk:didrender': cur['didrender']=t
    elif e=='wk:framedone': cur['framedone']=t
    elif e.endswith('msm_gpu_submit'):
        i=re.search(r'id=(\d+)',rest).group(1); gpu[i]=[t,None,0]; cur['gpu'].append(i)
    elif 'submit_retired' in e:
        i=re.search(r'id=(\d+)',rest).group(1); el=re.search(r'elapsed=(\d+)',rest)
        if i in gpu: gpu[i][1]=t; gpu[i][2]=int(el.group(1)) if el else 0
full=[f for f in frames[1:-1] if 'end' in f]
per=[(b['start']-a['start'])*1000 for a,b in zip(frames,frames[1:])]
cpu=[(f['end']-f['start'])*1000 for f in full]
gp=[]; gl=[]
for f in full:
    ids=[i for i in f['gpu'] if gpu.get(i) and gpu[i][1]]
    if ids:
        gp.append((max(gpu[i][1] for i in ids)-min(gpu[i][0] for i in ids))*1000)   # first submit -> last retire
        gl.append((max(gpu[i][1] for i in ids)-f['end'])*1000)  # last retire after paint end
fd=[(f['framedone']-f['end'])*1000 for f in full if 'framedone' in f and f['framedone']>f['end']]
nxt=[(b['start']-a['framedone'])*1000 for a,b in zip(full,full[1:]) if 'framedone' in a and b['start']>a['framedone']]
p=lambda v:"p50=%.1f p90=%.1f n=%d"%(st.median(v),sorted(v)[int(len(v)*.9)],len(v)) if v else "n=0"
print("frames=%d"%len(frames))
print("period            ",p(per))
print("cpu paint (renderLayerTree)",p(cpu))
print("gpu submit->retire span",p(gp))
print("gpu retire after paint end",p(gl))
print("paint end -> frameDone",p(fd))
print("frameDone -> next frame start",p(nxt))
PY
