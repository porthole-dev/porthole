#!/usr/bin/env python3
# scope: soc:qcom
# needs: - (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Print nodes matching a name pattern out of a flattened device tree.

There is no dtc on this host and the factory DTBs are the only authority on how
taimen's cameras are actually wired -- lane counts, which CSIPHY each sensor sits
on, mclk, regulators. FDT is a simple enough format that parsing it is smaller
than installing a toolchain.

  dtb-grep.py FILE.dtb camera       # every node whose path matches "camera"
  dtb-grep.py FILE.dtb csiphy -v    # ... with all property values
"""
import struct
import sys

FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9


def parse(data):
    magic, totalsize, off_struct, off_strings = struct.unpack_from(">IIII", data, 0)
    if magic != 0xD00DFEED:
        raise SystemExit("not a dtb (magic %08x)" % magic)
    size_strings, size_struct = struct.unpack_from(">II", data, 32)
    strings = data[off_strings:off_strings + size_strings]
    out, path, pos = [], [], off_struct
    end = off_struct + size_struct
    while pos < end:
        (tag,) = struct.unpack_from(">I", data, pos)
        pos += 4
        if tag == FDT_BEGIN_NODE:
            z = data.index(b"\0", pos)
            path.append(data[pos:z].decode("utf-8", "replace"))
            pos = (z + 4) & ~3
            out.append(("node", "/".join(path), None))
        elif tag == FDT_END_NODE:
            path.pop()
            pos = (pos + 3) & ~3
        elif tag == FDT_PROP:
            plen, noff = struct.unpack_from(">II", data, pos)
            pos += 8
            val = data[pos:pos + plen]
            pos = (pos + plen + 3) & ~3
            z = strings.index(b"\0", noff)
            out.append(("prop", strings[noff:z].decode(), val))
        elif tag == FDT_END:
            break
    return out


def show(val):
    if not val:
        return "<empty>"
    if val[-1:] == b"\0" and all(32 <= c < 127 or c == 0 for c in val):
        return " ".join('"%s"' % s.decode() for s in val.split(b"\0") if s)
    if len(val) % 4 == 0 and len(val) <= 64:
        return "<" + " ".join("0x%x" % v for v in struct.unpack(">%dI" % (len(val) // 4), val)) + ">"
    return val.hex()


def selftest():
    """Build a tiny FDT by hand and assert the parser reads it back.

    A device-tree parser that silently mis-walks the struct block produces
    plausible-looking output, which is the worst failure mode for something
    being used as an authority on how the hardware is wired.
    """
    strings = b"compatible\0reg\0"
    body = b""
    body += struct.pack(">I", FDT_BEGIN_NODE) + b"\0\0\0\0"          # root ""
    body += struct.pack(">I", FDT_BEGIN_NODE) + b"cam@1\0\0\0"        # /cam@1
    val = b"qcom,camera\0"
    body += struct.pack(">III", FDT_PROP, len(val), 0) + val
    val = struct.pack(">I", 0x1234)
    body += struct.pack(">III", FDT_PROP, len(val), 11) + val
    body += struct.pack(">I", FDT_END_NODE)
    body += struct.pack(">I", FDT_END_NODE)
    body += struct.pack(">I", FDT_END)

    # FDT header: magic, totalsize, off_struct, off_strings, off_rsvmap,
    # version, last_comp_version, boot_cpuid, size_strings, size_struct.
    # size_strings/size_struct sit at byte 32 and 36 -- getting that wrong is
    # exactly the mistake this selftest exists to catch, and it caught it.
    off_struct = 40
    off_strings = off_struct + len(body)
    header = struct.pack(">IIIIIIII", 0xD00DFEED, off_strings + len(strings),
                         off_struct, off_strings, 0, 17, 16, 0)
    header += struct.pack(">II", len(strings), len(body))
    dtb = header + body + strings

    items = parse(dtb)
    names = [n for k, n, _ in items if k == "node"]
    props = {n: v for k, n, v in items if k == "prop"}
    assert names == ["", "/cam@1"], names
    assert props["compatible"] == b"qcom,camera\0", props
    assert show(props["reg"]) == "<0x1234>", show(props["reg"])
    assert show(props["compatible"]) == '"qcom,camera"'
    print("selftest ok")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        return selftest()
    data = open(sys.argv[1], "rb").read()
    pattern = sys.argv[2] if len(sys.argv) > 2 else ""
    verbose = "-v" in sys.argv

    items = parse(data)
    printing = False
    for kind, name, val in items:
        if kind == "node":
            printing = pattern in name
            if printing:
                print("\n%s" % name)
        elif printing:
            if verbose or len(val) <= 64:
                print("    %-28s %s" % (name, show(val)))


if __name__ == "__main__":
    main()
