#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device as root (ptrace); elfutils (eu-stack)
# env: -
# exits: 0 measured · 1 no such process or thread
# tk-stallcatch.py SECONDS COMM THREAD_SUBSTR [IDLE_MS] -- when THREAD of the
# busiest process named COMM makes no CPU progress for IDLE_MS, dump the WHOLE
# process (eu-stack, every thread of interest) plus the kernel stacks.
#
# WHY THIS SHAPE, because both points produced clean nulls on 2026-09-06:
#   - Epiphany runs several WebKitWebProcess (five with one YouTube tab); the
#     page with the media pipeline is the one with the most threads, so that
#     is the one picked, never the first /proc match.
#   - thread names are truncated from the FRONT ("eadedCompositor"), so the
#     thread is matched by substring, e.g. "Compositor", "v4l2", "dec".
# Offsets are printed module-relative: symbolize on the host with the build's
# .debug file (llvm-symbolizer, vaddr = file offset + the RX segment delta,
# 0x10000 on webkitgtk 2.52.6).
#
#   tk-stallcatch.py 60 WebKitWebProces Compositor 300   # the compositor idles
#   tk-stallcatch.py 60 WebKitWebProces dec 400          # the decoder starves
# See brain/traps/a-stall-catcher-must-pick-the-busiest-webkitwebprocess.md.
import os, sys, time, glob, subprocess
secs = float(sys.argv[1]); comm = sys.argv[2]; tsub = sys.argv[3]
IDLE_MS = int(sys.argv[4]) if len(sys.argv) > 4 else 300
MAXCAP = 4
pid = None; best = -1
for d in glob.glob("/proc/[0-9]*"):
    try:
        if open(d + "/comm").read().strip() == comm:
            n = len(os.listdir(d + "/task"))   # several WebKitWebProcess exist; the page with the media pipeline has the most threads
            if n > best: best, pid = n, os.path.basename(d)
    except OSError: pass
if not pid: sys.exit(f"no process with comm {comm!r}")
def threads():
    out = {}
    for t in glob.glob(f"/proc/{pid}/task/*"):
        try: out[os.path.basename(t)] = open(t + "/comm").read().strip()
        except OSError: pass
    return out
th = threads()
tids = [t for t, n in th.items() if tsub in n]
if not tids: sys.exit(f"no thread matching {tsub!r} in {sorted(set(th.values()))}")
tid = tids[0]
print(f"stallcatch2 pid={pid} tid={tid} ({th[tid]}) idle>={IDLE_MS}ms; threads={len(th)}"); sys.stdout.flush()
maps = open(f"/proc/{pid}/maps").read()
mods = {}
for l in maps.splitlines():
    p = l.split()
    if len(p) >= 6 and p[5].startswith("/"):
        mods.setdefault(p[5], []).append((int(p[0].split("-")[0], 16), int(p[2], 16)))
def modoff(addr):
    best = None
    for name, segs in mods.items():
        for lo, off in segs:
            if lo <= addr and (best is None or lo > best[0]): best = (lo, off, name)
    return (os.path.basename(best[2]), addr - best[0] + best[1]) if best else ("?", addr)
INTERESTING = ("Compositor", "v4l2", "dec", "Skia", "WebKitWebProces", "gst", "queue", "sink", "FenceMonitor", "Scrolling")
def dump(dur):
    t = time.strftime("%H:%M:%S")
    th = threads()
    print(f"--- {t} {th.get(tid)} idle {dur:.0f} ms")
    for x, n in sorted(th.items(), key=lambda kv: int(kv[0])):
        if any(s in n for s in INTERESTING) or x == pid:
            try:
                st = open(f"/proc/{pid}/task/{x}/stat").read(); st = st[st.rindex(") ") + 2:].split()[0]
                wc = open(f"/proc/{pid}/task/{x}/wchan").read().strip()
                ks = " < ".join(l.split()[1] for l in open(f"/proc/{pid}/task/{x}/stack").read().splitlines()[:4])
            except OSError: st = wc = ks = "?"
            print(f"  tid {x:>6} {n:<16} {st} wchan={wc} k={ks}")
    try:
        out = subprocess.run(["eu-stack", "-p", pid], capture_output=True, text=True, timeout=15).stdout
    except Exception as e:
        out = f"eu-stack failed: {e}"
    cur = None; n = 0
    for l in out.splitlines():
        if l.startswith("TID "):
            cur = l.split()[1].rstrip(":"); name = th.get(cur, "?")
            keep = any(s in name for s in INTERESTING) or cur == pid
            if keep: print(f"  == TID {cur} {name}")
            n = 0
            continue
        p = l.split()
        if cur and keep and len(p) >= 2 and p[0].startswith("#") and p[1].startswith("0x") and n < 22:
            a = int(p[1], 16); m, o = modoff(a)
            print(f"     {p[0]:>4} {m}+0x{o:x} {' '.join(p[2:])[:70]}"); n += 1
    sys.stdout.flush()
statf = f"/proc/{pid}/task/{tid}/stat"
end = time.time() + secs
prev_ticks, idle_since, caps, last_cap = None, None, 0, 0
while time.time() < end:
    try:
        s = open(statf).read(); f = s[s.rindex(") ") + 2:].split(); ticks = int(f[11]) + int(f[12])
    except (OSError, ValueError, IndexError): break
    now = time.time()
    if prev_ticks is None or ticks > prev_ticks: idle_since = now
    elif now - idle_since >= IDLE_MS / 1000 and caps < MAXCAP and now - last_cap > 3:
        caps += 1; last_cap = now; dump((now - idle_since) * 1000)
    prev_ticks = ticks
    time.sleep(0.01)
print(f"stallcatch2 done: {caps} captures")
