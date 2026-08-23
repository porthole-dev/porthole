#!/usr/bin/env python3
# scope: soc:msm8998
# needs: - (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Reassemble a split Qualcomm PIL image (foo.mdt + foo.b00..bNN) into one foo.mbn.

Android ships these firmwares split: the .mdt holds the ELF header plus program
headers (and the signed hash segment), and each .bNN holds the contents of program
header NN. Mainline's remoteproc asks for a single .mbn -- our DT requests e.g.
"qcom/msm8998/wahoo/adsp.mbn" -- so the pieces have to be welded back together.

Same job as Bjorn Andersson's pil-squasher, reimplemented here so the build does
not depend on having that tool around.

Layout of the output: the .mdt verbatim (header + phdrs + hash), then every
segment written at its p_offset from the matching .bNN.

Usage:
  pil-squash.py OUT.mbn IN.mdt
  pil-squash.py --auto OUTDIR IN1.mdt [IN2.mdt ...]
"""
import argparse
import os
import struct
import sys

ELFCLASS32, ELFCLASS64 = 1, 2


def parse_elf(mdt):
    if mdt[:4] != b'\x7fELF':
        raise ValueError("not an ELF file (bad magic)")
    cls = mdt[4]
    if cls == ELFCLASS64:
        e_phoff = struct.unpack_from('<Q', mdt, 0x20)[0]
        e_phentsize, e_phnum = struct.unpack_from('<HH', mdt, 0x36)
        fmt, off_off, filesz_off = '<IIQQQQQQ', 2, 5   # p_offset idx2, p_filesz idx5
    elif cls == ELFCLASS32:
        e_phoff = struct.unpack_from('<I', mdt, 0x1C)[0]
        e_phentsize, e_phnum = struct.unpack_from('<HH', mdt, 0x2A)
        fmt, off_off, filesz_off = '<IIIIIIII', 1, 4   # p_offset idx1, p_filesz idx4
    else:
        raise ValueError("unknown ELF class %d" % cls)

    segs = []
    for i in range(e_phnum):
        base = e_phoff + i * e_phentsize
        vals = struct.unpack_from(fmt, mdt, base)
        segs.append((i, vals[off_off], vals[filesz_off]))
    return cls, segs


def squash(mdt_path, out_path):
    mdt = open(mdt_path, 'rb').read()
    cls, segs = parse_elf(mdt)
    base = mdt_path[:-4] if mdt_path.endswith('.mdt') else mdt_path

    with open(out_path, 'wb') as out:
        out.write(mdt)
        used = 0
        for idx, off, filesz in segs:
            if not filesz:
                continue
            bin_path = "%s.b%02d" % (base, idx)
            if not os.path.exists(bin_path):
                # Segment 0 is usually the hash already present in the .mdt.
                continue
            data = open(bin_path, 'rb').read()
            if len(data) != filesz:
                print("  warn: %s is %d bytes, phdr says %d"
                      % (os.path.basename(bin_path), len(data), filesz),
                      file=sys.stderr)
            out.seek(off)
            out.write(data)
            used += 1
    size = os.path.getsize(out_path)
    print("%-24s <- %-20s ELF%d, %d/%d segments, %d bytes"
          % (os.path.basename(out_path), os.path.basename(mdt_path),
             32 if cls == ELFCLASS32 else 64, used, len(segs), size))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--auto', action='store_true',
                    help="first arg is an output DIRECTORY; name each .mbn after its .mdt")
    ap.add_argument('out')
    ap.add_argument('mdt', nargs='+')
    a = ap.parse_args()

    if a.auto:
        os.makedirs(a.out, exist_ok=True)
        for m in a.mdt:
            name = os.path.basename(m)[:-4] + '.mbn'
            squash(m, os.path.join(a.out, name))
    else:
        if len(a.mdt) != 1:
            sys.exit("without --auto, pass exactly one .mdt")
        squash(a.mdt[0], a.out)


if __name__ == '__main__':
    main()
