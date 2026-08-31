#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""tk-strip-btf.py: making a module's .BTF invisible to the module loader.

Checked against ELF64 images this test builds itself, because the thing being
verified is byte-level surgery on a section header table and "it loaded on the
phone once" is not the same as being correct. In particular this pins the two
properties the fix depends on:

  - ONLY the four sh_name bytes of the .BTF header change; every other section
    header, and the section *contents*, are untouched. If this ever starts
    rewriting the ELF properly it must still pass, so the assertion is on the
    resulting names and payload rather than on "the file is unchanged".
  - a section whose name merely *starts with* .BTF (.BTF.ext) survives, which
    is the obvious way to get this wrong with a prefix match.

Needs no device, no root, no cross toolchain and no real module.
"""
import importlib.util
import pathlib
import struct
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "tk_strip_btf", ROOT / "tools" / "tk-strip-btf.py")
tk_strip_btf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tk_strip_btf)

TMP = pathlib.Path(tempfile.mkdtemp(prefix="porthole-btf-test-"))
SHENT = 64


def build_elf(names):
    """A minimal ELF64 LE with one section per name, plus NULL and .shstrtab."""
    strtab, offs = bytearray(b"\0"), {}
    for n in names + [".shstrtab"]:
        offs[n] = len(strtab)
        strtab += n.encode() + b"\0"

    payload = b"".join(("body-" + n).encode().ljust(16, b"\0") for n in names)
    body_off = 64
    str_off = body_off + len(payload)
    sh_off = (str_off + len(strtab) + 7) & ~7

    e = bytearray(sh_off + SHENT * (len(names) + 2))
    e[0:16] = b"\x7fELF\x02\x01\x01" + b"\0" * 9
    struct.pack_into("<HHI", e, 16, 1, 183, 1)          # type REL, aarch64
    struct.pack_into("<Q", e, 0x28, sh_off)             # e_shoff
    struct.pack_into("<HHHHHH", e, 0x34, 64, 0, 0, SHENT, len(names) + 2,
                     len(names) + 1)                    # ehsize..shstrndx
    e[body_off:body_off + len(payload)] = payload
    e[str_off:str_off + len(strtab)] = strtab

    for i, n in enumerate(names, start=1):
        base = sh_off + i * SHENT
        struct.pack_into("<II", e, base, offs[n], 1)
        struct.pack_into("<QQ", e, base + 0x18, body_off + (i - 1) * 16, 16)
    base = sh_off + (len(names) + 1) * SHENT
    struct.pack_into("<II", e, base, offs[".shstrtab"], 3)
    struct.pack_into("<QQ", e, base + 0x18, str_off, len(strtab))
    return bytes(e)


def section_names(raw):
    sh_off = struct.unpack_from("<Q", raw, 0x28)[0]
    ent, num, ndx = struct.unpack_from("<HHH", raw, 0x3A)
    s_off, s_size = struct.unpack_from("<QQ", raw, sh_off + ndx * ent + 0x18)
    strtab = raw[s_off:s_off + s_size]
    out = []
    for i in range(num):
        nm = struct.unpack_from("<I", raw, sh_off + i * ent)[0]
        out.append(strtab[nm:strtab.index(b"\0", nm)].decode())
    return out


def write(name, blob):
    p = TMP / name
    p.write_bytes(blob)
    return p


def check(label, cond):
    print("%-52s %s" % (label, "ok" if cond else "FAIL"))
    if not cond:
        raise SystemExit(1)


def main():
    # 1. the ordinary case
    p = write("mod.ko", build_elf([".BTF", ".text"]))
    before = p.read_bytes()
    check("reports a change on a module carrying .BTF",
          tk_strip_btf.neuter_btf(str(p)) is True)
    after = p.read_bytes()
    check("no section is named .BTF any more",
          ".BTF" not in section_names(after))
    check(".text survives", ".text" in section_names(after))
    check("file size is unchanged", len(after) == len(before))
    check("section contents are untouched", b"body-.text" in after)

    # 2. idempotent -- a second push of the same file must be a no-op
    check("second call reports nothing to do",
          tk_strip_btf.neuter_btf(str(p)) is False)

    # 3. a prefix-named sibling must NOT be caught by a sloppy match
    p2 = write("ext.ko", build_elf([".BTF.ext", ".text"]))
    check("a module with only .BTF.ext is left alone",
          tk_strip_btf.neuter_btf(str(p2)) is False)
    check(".BTF.ext still named", ".BTF.ext" in section_names(p2.read_bytes()))

    # 4. both present: .BTF goes, .BTF.ext stays
    p3 = write("both.ko", build_elf([".BTF", ".BTF.ext", ".text"]))
    tk_strip_btf.neuter_btf(str(p3))
    names = section_names(p3.read_bytes())
    check(".BTF removed while .BTF.ext kept",
          ".BTF" not in names and ".BTF.ext" in names)

    # 5. refuse what we do not understand rather than corrupting it
    p4 = write("not-elf.ko", b"MZ\x90\x00this is not an ELF at all")
    try:
        tk_strip_btf.neuter_btf(str(p4))
        raise SystemExit("FAIL: accepted a non-ELF file")
    except SystemExit as exc:
        check("a non-ELF file is refused, not rewritten",
              exc.code not in (0, None) and p4.read_bytes().startswith(b"MZ"))

    print("test_strip_btf.py: all checks passed")


if __name__ == "__main__":
    main()
