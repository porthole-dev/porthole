#!/usr/bin/env python3
# scope: soc:qcom
# needs: - (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Extract the SSC sensor-registry group table from Android's sensors.qcom.

The SNS_REG QMI service hands SSC raw byte-slices of /persist/sensors/sns.reg,
one per "group". The (group id, offset, size) table lives as plain data inside
/vendor/bin/sensors.qcom. sns-reg upstream reassembles groups from a
hand-derived per-key map instead, which is missing 15 of the 53 groups taimen's
SMGR reads -- and a short answer makes SLPI assert in sns_smgr_reg.c and reboot
in a loop, so SMGR never publishes.

    tk-sns-groups.py sensors.qcom > groups.conf

Self-check: the table is only accepted if the slices tile sns.reg exactly, i.e.
max(offset + size) equals the registry length (0x6e0e on taimen).
"""
import struct
import sys

REG_LEN = 0x6e0e  # taimen /persist/sensors/sns.reg


def find_table(blob):
    """Locate the table by its one unmistakable run: groups 3300..3329."""
    for base in range(0, len(blob) - 6, 2):
        if struct.unpack_from('<H', blob, base)[0] != 3300:
            continue
        # records are (u16 size, u16 addr, u16 id); the anchor is an id field
        start = base - 4
        while start - 6 >= 0 and any(struct.unpack_from('<3H', blob, start - 6)):
            start -= 6
        table = []
        off = start
        while off + 6 <= len(blob):
            size, addr, gid = struct.unpack_from('<3H', blob, off)
            if not (size or addr or gid):
                break
            table.append((gid, addr, size))
            off += 6
        if table and max(a + s for _, a, s in table) == REG_LEN:
            return table
    return None


def demo():
    """Round-trip the record layout without needing the vendor blob."""
    want = [(0, 0x0000, 0x18), (1040, 0x0100, 0x80), (2000, 0x0200, 0x10)]
    blob = b'\x00\x00' + b''.join(
        struct.pack('<3H', s, a, g) for g, a, s in want) + b'\x00' * 6
    got = []
    off = 2
    while True:
        s, a, g = struct.unpack_from('<3H', blob, off)
        if not (s or a or g):
            break
        got.append((g, a, s))
        off += 6
    assert got == want, got
    print("ok", file=sys.stderr)


if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == '--demo':
        demo()
        raise SystemExit

    table = find_table(open(sys.argv[1], 'rb').read())
    if not table:
        sys.exit("no group table found (does this sensors.qcom match the device?)")

    print("# group_id offset size -- extracted from /vendor/bin/sensors.qcom")
    print("# by tools/tk-sns-groups.py; consumed by sns-reg as /etc/sns-reg.d/groups.conf")
    for gid, addr, size in sorted(table):
        print(gid, addr, size)
