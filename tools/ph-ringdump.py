#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: - (host only, no device)
# env: HOST
# exits: 0 ok · non-zero on failure
"""Decode the ringbuffer out of an adreno devcoredump into CP packets.

Run on the HOST against a dump pulled from /sys/class/devcoredump/devcdN/data.

The dump carries the ring as ascii85 (uncompressed on 6.0 -- adreno_show_object()
only ascii85-encodes, and it truncates at the last non-zero dword). What makes
this worth decoding is rptr/wptr: the CP stops where it choked, so the dwords
between rptr and wptr are exactly the packets it never got through, and the ones
just before rptr are what it was chewing on when it died.

  ph-ringdump.py DUMP [--from N] [--to N]

Opcode names are parsed straight out of the kernel's adreno_pm4.xml.h so this
cannot drift from the tree it is being used against.
"""
import argparse
import base64
import os
import re
import struct
import sys

PM4_HEADER = "drivers/gpu/drm/msm/adreno/adreno_pm4.xml.h"


def load_opcodes(tree):
    """Pull `CP_FOO = 0xNN` out of the type3 packet enum."""
    path = os.path.join(tree, PM4_HEADER)
    names = {}
    try:
        text = open(path).read()
    except OSError:
        return names
    m = re.search(r"enum adreno_pm4_type3_packets\s*\{(.*?)\}", text, re.S)
    if not m:
        return names
    for name, val in re.findall(r"(CP_\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)",
                                m.group(1)):
        names.setdefault(int(val, 0), name)
    return names


def extract_ring(path):
    """Return (dwords, rptr, wptr) for ringbuffer id 0."""
    rptr = wptr = None
    chunks = []
    in_ring = False
    in_data = False

    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if stripped == "ringbuffer:":
                in_ring = True
                continue
            if in_ring and stripped == "bos:":
                break
            if not in_ring:
                continue

            if stripped.startswith("rptr:") and rptr is None:
                rptr = int(stripped.split(":")[1])
            elif stripped.startswith("wptr:") and wptr is None:
                wptr = int(stripped.split(":")[1])
            elif stripped.startswith("data:"):
                in_data = True
            elif in_data:
                # Payload lines are deeply indented; anything shallower ends it.
                if line.startswith(" " * 5) and not stripped.startswith("- "):
                    chunks.append(stripped)
                else:
                    in_data = False
                    if rptr is not None and wptr is not None:
                        break

    if not chunks:
        raise SystemExit("no ringbuffer payload found in %s" % path)

    raw = base64.a85decode("".join(chunks))
    # ascii85_encode() in lib/ascii85.c encodes the u32 VALUE in base 85, most
    # significant digit first, so each decoded group comes back big-endian --
    # unpacking these little-endian yields garbage that never parses as a packet.
    dwords = list(struct.unpack(">%dI" % (len(raw) // 4), raw[:len(raw) // 4 * 4]))
    return dwords, rptr, wptr


def decode(dwords, start, end, names, rptr, wptr):
    i = start
    while i < min(end, len(dwords)):
        dw = dwords[i]
        typ = dw >> 28
        marks = []
        if i == rptr:
            marks.append("<== rptr (CP STOPPED HERE)")
        if i == wptr:
            marks.append("<== wptr (end of submit)")
        mark = "  " + " ".join(marks) if marks else ""

        if typ == 0x7:
            op = (dw >> 16) & 0x7F
            cnt = dw & 0x3FFF
            nm = names.get(op, "CP_UNK_0x%02x" % op)
            print("[%4d] %08x  PKT7 %-28s count=%d%s" % (i, dw, nm, cnt, mark))
            for j in range(1, cnt + 1):
                if i + j < len(dwords):
                    print("       %08x      payload[%d]" % (dwords[i + j], j - 1))
            i += cnt + 1
        elif typ == 0x4:
            off = (dw >> 8) & 0x7FFFF
            cnt = dw & 0x7F
            print("[%4d] %08x  PKT4 reg=0x%05x               count=%d%s"
                  % (i, dw, off, cnt, mark))
            for j in range(1, cnt + 1):
                if i + j < len(dwords):
                    print("       %08x      payload[%d]" % (dwords[i + j], j - 1))
            i += cnt + 1
        else:
            print("[%4d] %08x  (not a packet header)%s" % (i, dw, mark))
            i += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--tree", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "linux"))
    ap.add_argument("--from", dest="start", type=int, default=None)
    ap.add_argument("--to", dest="end", type=int, default=None)
    args = ap.parse_args()

    dwords, rptr, wptr = extract_ring(args.dump)
    names = load_opcodes(args.tree)
    print("ring: %d dwords decoded, rptr=%s wptr=%s, %d opcode names"
          % (len(dwords), rptr, wptr, len(names)))
    print("the CP left %s dwords unexecuted\n"
          % (wptr - rptr if None not in (rptr, wptr) else "?"))

    start = args.start if args.start is not None else max(0, (rptr or 0) - 40)
    end = args.end if args.end is not None else (wptr or len(dwords)) + 4
    decode(dwords, start, end, names, rptr, wptr)


if __name__ == "__main__":
    main()
