#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok (changed or already clean) · 1 not an ELF we understand
"""Make a kernel module's .BTF section invisible to the module loader.

Why this exists, and why it is not optional:

A device that ships from an aport runs a kernel built in the aport chroot,
while `porthole build mod` builds the module from the tree. MODVERSIONS is
happy with that -- it compares exported symbol CRCs, and those match. **BTF
does not.** A module's .BTF encodes references into the *base kernel's* BTF by
type id, so a module built against a different vmlinux than the one running
resolves those ids to the wrong types, or to a cycle:

    BPF: Max chain length or cycle detected
    failed to validate module [mac80211] BTF: -40

and, because btf_module_notify() is a module notifier and
CONFIG_MODULE_ALLOW_BTF_MISMATCH is off, that -40 (ELOOP) *fails the load*.
modprobe renders it as the wonderfully unhelpful:

    modprobe: ERROR: could not insert 'mac80211': Symbolic link loop

Cost on taimen 2026-08-31: pushed a patched mac80211.ko, rebooted, and the
phone came up with no wlan0 at all. Nothing in the message names BTF, the
module, or the tree it came from.

The fix is to drop .BTF. `llvm-strip --strip-debug` would do it, but this runs
on the HOST, which has no aarch64-capable strip -- and requiring one would make
the cheapest rung in the ladder depend on a cross toolchain. So instead of
rewriting the ELF, we set the .BTF section header's sh_name to 0. The loader
finds sections by name (any_section_objs(info, ".BTF", ...)), so an unnamed
section is simply not there; nothing moves, no offset changes, and the file
stays byte-identical apart from four bytes. It is reversible and idempotent.

Usage: tk-strip-btf.py FOO.ko [BAR.ko ...]
"""
import struct
import sys

EI_CLASS, EI_DATA = 4, 5
ELFCLASS64, ELFDATA2LSB = 2, 1


def neuter_btf(path):
    """Return True if a .BTF section was renamed away, False if already clean."""
    with open(path, "r+b") as f:
        e = bytearray(f.read())

        if e[:4] != b"\x7fELF":
            sys.exit("%s: not an ELF file" % path)
        if e[EI_CLASS] != ELFCLASS64 or e[EI_DATA] != ELFDATA2LSB:
            # Every arm64 kernel module is ELF64 LE. Refuse rather than
            # guess at a layout we have never seen.
            sys.exit("%s: not 64-bit little-endian ELF" % path)

        e_shoff = struct.unpack_from("<Q", e, 0x28)[0]
        e_shentsize = struct.unpack_from("<H", e, 0x3A)[0]
        e_shnum = struct.unpack_from("<H", e, 0x3C)[0]
        e_shstrndx = struct.unpack_from("<H", e, 0x3E)[0]

        if not e_shoff or not e_shnum:
            sys.exit("%s: no section headers" % path)

        def hdr(i):
            return e_shoff + i * e_shentsize

        # The section-header string table, so we can read section names.
        str_off, str_size = struct.unpack_from("<QQ", e, hdr(e_shstrndx) + 0x18)
        shstrtab = bytes(e[str_off:str_off + str_size])

        for i in range(e_shnum):
            off = hdr(i)
            sh_name = struct.unpack_from("<I", e, off)[0]
            name = shstrtab[sh_name:shstrtab.index(b"\0", sh_name)]
            if name == b".BTF":
                # sh_name 0 is the leading NUL of .shstrtab: the empty name.
                struct.pack_into("<I", e, off, 0)
                f.seek(0)
                f.write(e)
                f.truncate()
                return True
    return False


def main(argv):
    if len(argv) < 2:
        sys.exit("usage: tk-strip-btf.py FOO.ko [BAR.ko ...]")
    for path in argv[1:]:
        if neuter_btf(path):
            print("### %s: .BTF neutered (module BTF would not match this kernel)" % path)
        else:
            print("### %s: no .BTF section, nothing to do" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
