#!/usr/bin/env python3
"""Last value written to every register in a cffdump text; diff two files.
usage: finalstate.py A.txt [B.txt]   (prints regs whose final value differs)"""
import re, sys
line_re = re.compile(r'^\s+([A-Z][A-Z0-9_\[\]\.x]+): (.*)$')
def final(path):
    d = {}
    for ln in open(path, errors='replace'):
        if re.match(r'^\S+:\s*[!+ ]', ln):   # annotated register-state echo lines, skip
            continue
        m = line_re.match(ln)
        if m: d[m.group(1)] = m.group(2).strip()
    return d
a = final(sys.argv[1])
if len(sys.argv) == 2:
    for k in sorted(a): print(k, '=', a[k])
else:
    b = final(sys.argv[2])
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k): print(f'{k}: A={a.get(k)}  B={b.get(k)}')
