#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: device:google-taimen
# needs: -  (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Poll Easel RX PHY_STOPSTATE / PHY_RX fast, to catch HS data bursts.

A data lane sits in LP-11 (stop state) between packets, so a single read
cannot distinguish "never bursts" from "sampled during blanking". This polls
flat-out and reports how often each lane left stop state.

  ph-easel-poll.py [rxdev] [samples]
"""
import mmap, os, struct, sys

DEV = "/sys/bus/pci/devices/0000:01:00.0/resource2"
PERIPH = 0x04000000
RX = {0: 0x04012000, 1: 0x04013000, 2: 0x04014000}

dev = int(sys.argv[1]) if len(sys.argv) > 1 else 1
n = int(sys.argv[2]) if len(sys.argv) > 2 else 200000

fd = os.open(DEV, os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, os.path.getsize(DEV), mmap.MAP_SHARED, mmap.PROT_READ)
ss_off = RX[dev] + 0x4c - PERIPH
rx_off = RX[dev] + 0x48 - PERIPH

lane_active = [0, 0, 0, 0]
clk_hs = 0
seen = {}
for _ in range(n):
    v = struct.unpack_from("<I", m, ss_off)[0]
    r = struct.unpack_from("<I", m, rx_off)[0]
    seen[v] = seen.get(v, 0) + 1
    for i in range(4):
        if not (v >> i) & 1:
            lane_active[i] += 1
    if (r >> 17) & 1:
        clk_hs += 1

print("samples=%d  clock in HS: %d (%.1f%%)" % (n, clk_hs, 100.0*clk_hs/n))
for i in range(4):
    print("  data lane %d left stop state: %d (%.2f%%)" %
          (i, lane_active[i], 100.0*lane_active[i]/n))
print("  distinct STOPSTATE values: %s" %
      " ".join("0x%x:%d" % (k, v) for k, v in sorted(seen.items())[:8]))
