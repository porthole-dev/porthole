#!/usr/bin/env python3
# scope: soc:qcom
# needs: - (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Read samples from a buffer-only IIO device (no in_*_raw), e.g. qcom-smgr-*.

The SMGR driver only pushes into a kfifo buffer, so `cat in_accel_x_raw` does
not exist -- you have to enable the scan elements and read /dev/iio:deviceN.

    tk-iio-read.py qcom-smgr-accel [count]

Prints scaled values in IIO units (m/s^2, rad/s, gauss, kPa, ...).
"""
import os
import struct
import sys
import time

IIO = '/sys/bus/iio/devices'


def find(name):
    for d in sorted(os.listdir(IIO)):
        p = f'{IIO}/{d}'
        try:
            if open(f'{p}/name').read().strip() == name:
                return p, d
        except OSError:
            pass
    sys.exit(f'no IIO device named {name}')


def write(path, val):
    with open(path, 'w') as f:
        f.write(str(val))


def parse_type(s):
    # e.g. "le:s32/32>>0"
    endian, rest = s.split(':')
    sign = rest[0]
    bits, rest = rest[1:].split('/')
    storage = int(rest.split('>>')[0])
    return ('<' if endian == 'le' else '>'), sign, int(bits), storage


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else 'qcom-smgr-accel'
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    path, dev = find(name)

    els = sorted(f[:-3] for f in os.listdir(f'{path}/scan_elements')
                 if f.endswith('_en'))
    # index order is what the buffer actually uses
    els.sort(key=lambda e: int(open(f'{path}/scan_elements/{e}_index').read()))

    fmt = '<'
    offset = 0
    names = []
    for e in els:
        endian, sign, bits, storage = parse_type(
            open(f'{path}/scan_elements/{e}_type').read().strip())
        size = storage // 8
        pad = -offset % size              # IIO aligns each element to its size
        fmt += 'x' * pad                  # struct '<' does not pad on its own
        offset += pad
        code = {1: 'b', 2: 'h', 4: 'i', 8: 'q'}[size]
        fmt += code if sign == 's' else code.upper()
        offset += size
        names.append(e.replace('in_', ''))
    record = offset + (-offset % 8)

    # scale is per channel type (in_accel_scale covers x/y/z), and a device can
    # mix them -- prox-light has a Q16.16 proximity next to whole-lux light
    scales = []
    for e in els:
        base = e[:-2] if e[-2:] in ('_x', '_y', '_z') else e
        try:
            scales.append(float(open(f'{path}/{base}_scale').read()))
        except OSError:
            scales.append(1.0)

    try:
        write(f'{path}/buffer/enable', 0)
    except OSError:
        pass
    for e in els:
        write(f'{path}/scan_elements/{e}_en', 1)
    write(f'{path}/buffer/length', 64)
    write(f'{path}/buffer/enable', 1)

    try:
        with open(f'/dev/{dev}', 'rb', buffering=0) as f:
            for _ in range(count):
                buf = f.read(record)
                if not buf or len(buf) < struct.calcsize(fmt):
                    print('short read'); break
                vals = struct.unpack_from(fmt, buf)
                out = [f'ts={v}' if 'timestamp' in n else f'{n}={v * s:+.4f}'
                       for n, v, s in zip(names, vals, scales)]
                print('  '.join(out))
                sys.stdout.flush()
    finally:
        write(f'{path}/buffer/enable', 0)


if __name__ == '__main__':
    main()
