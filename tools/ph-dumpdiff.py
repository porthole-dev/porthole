#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Diff a stock-Android TKDUMP capture against our camss port's register state.

Consumes:
  stock : `strings logs/ramoops.bin | grep TKDUMP` output (RUNBOOK-stock-dump.md)
          lines like: TKDUMP csiphy1 +0a0: 00000000 00000004 ...
  ours  : tk-regdump.py / tk-phystat.py style dumps, lines like
          "0a0: 00000000 00000004 ..." or "csiphy1 +0a0: ..." -- anything with
          an offset followed by 32-bit words.

Usage: tk-dumpdiff.py STOCK.txt OURS.txt [--block csiphy1]

Prints one line per differing word: offset, stock value, our value. The
whole point of the stock dump is this diff, so keep it dumb and lossless.
"""
import argparse
import re
import sys

WORD = re.compile(r'([0-9a-fA-F]{8})')
OFF = re.compile(r'\+?([0-9a-fA-F]{2,4}):')
BLOCK = re.compile(r'(csiphy\d|csid\d)')


def parse(path, want_block):
    regs = {}
    for line in open(path):
        if 'clk' in line or 'params' in line:
            continue
        b = BLOCK.search(line)
        if want_block and (not b or b.group(1) != want_block):
            continue
        m = OFF.search(line)
        if not m:
            continue
        off = int(m.group(1), 16)
        words = WORD.findall(line[m.end():])
        for i, w in enumerate(words):
            regs[off + 4 * i] = int(w, 16)
    return regs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('stock')
    ap.add_argument('ours')
    ap.add_argument('--block', default='csiphy1',
                    help='csiphy0/csiphy1/csid0/csid1... (default csiphy1)')
    args = ap.parse_args()

    stock = parse(args.stock, args.block)
    ours = parse(args.ours, args.block)
    if not stock:
        sys.exit(f'no {args.block} registers parsed from {args.stock}')
    if not ours:
        sys.exit(f'no {args.block} registers parsed from {args.ours}')

    diffs = same = 0
    for off in sorted(set(stock) | set(ours)):
        s, o = stock.get(off), ours.get(off)
        if s == o:
            same += 1
            continue
        diffs += 1
        print(f'{args.block} +{off:03x}: stock '
              f'{"--------" if s is None else f"{s:08x}"}  ours '
              f'{"----------" if o is None else f"{o:08x}"}')
    print(f'# {args.block}: {same} identical, {diffs} different')


def selftest():
    import io, tempfile, os
    a = 'TKDUMP csiphy1 +000: 00000091 0000000c\nTKDUMP csiphy1 clk foo = 1\n'
    b = 'csiphy1 +000: 00000091 000000ff\n'
    fa = tempfile.NamedTemporaryFile('w', delete=False); fa.write(a); fa.close()
    fb = tempfile.NamedTemporaryFile('w', delete=False); fb.write(b); fb.close()
    sa, sb = parse(fa.name, 'csiphy1'), parse(fb.name, 'csiphy1')
    os.unlink(fa.name); os.unlink(fb.name)
    assert sa == {0: 0x91, 4: 0xc}, sa
    assert sb == {0: 0x91, 4: 0xff}, sb
    print('selftest ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--selftest']:
        selftest()
    else:
        main()
