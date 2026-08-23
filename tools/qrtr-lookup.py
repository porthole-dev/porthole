#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: soc:qcom
# needs: - (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Minimal qrtr-lookup: list QMI services on the QRTR bus. No deps."""
import socket, struct
CTRL_PORT, NEW_SERVER, NEW_LOOKUP = 0xfffffffe, 4, 10
s = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
try:
    s.bind((s.getsockname()[0], 0))
except OSError:
    pass
node, _ = s.getsockname()
s.sendto(struct.pack('<5I', NEW_LOOKUP, 0, 0, 0, 0), (node, CTRL_PORT))
s.settimeout(3)
print(f"{'service':>8} {'ver':>4} {'inst':>5} {'node':>5} {'port':>6}")
while True:
    try:
        data, _ = s.recvfrom(4096)
    except socket.timeout:
        break
    cmd, svc, inst, n, p = struct.unpack('<5I', data[:20])
    if cmd != NEW_SERVER:
        continue
    if not (svc or inst or n or p):
        break
    print(f"{svc:>8} {inst & 0xff:>4} {inst >> 8:>5} {n:>5} {p:>6}")
