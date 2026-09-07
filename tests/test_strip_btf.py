#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""ph-strip-btf.py: making a module's .BTF invisible to the module loader.

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

It also pins the thing a correct tool cannot pin about itself: that the rung
which pushes a module actually CALLS it. ph-push-module.sh has since the trap
was written; `porthole build mod` did not, for the whole life of the verb.

Needs no device, no root, no cross toolchain and no real module.
"""
import importlib.util
import os
import pathlib
import struct
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "tk_strip_btf", ROOT / "tools" / "ph-strip-btf.py")
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


def stage_via_shell(ko, name="stagetest"):
    """ph-build.sh's own `_ph_stage_module`, called for real.

    A near-empty environment on purpose: PORTHOLE_* leaking in from the
    developer's shell is how this class of bug reaches a phone.
    """
    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
           "PORTHOLE_ROOT": str(ROOT), "PORTHOLE_DEVICE": "google-taimen",
           "PORTHOLE_WORKDIR": str(TMP / "repo"),
           "PORTHOLE_KERNEL_TREE": str(TMP / "tree")}
    (TMP / "repo").mkdir(exist_ok=True)
    (TMP / "tree").mkdir(exist_ok=True)
    done = subprocess.run(
        ["bash", "-c",
         'source "$PORTHOLE_ROOT/tools/ph-build.sh" >/dev/null 2>&1\n'
         '_ph_stage_module "$1" "$2" 2>/dev/null', "_", str(ko), name],
        env=env, capture_output=True, text=True)
    return done.returncode, done.stdout.strip()


def tkmod_body(text):
    """tkmod() as written, up to the closing brace at column 0."""
    start = text.index("\ntkmod() {")
    return text[start:text.index("\n}\n", start)]


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

    # 6. the rung that pushes a module must actually call the strip.
    #
    # Observed 2026-08-31 on taimen: `porthole build mod` on ath10k_core
    # unloaded the old driver and could not load the new one, leaving the
    # phone with no wifi at all. ph-push-module.sh had neutered .BTF since the
    # trap was written; tkmod pushed the raw .ko and nothing said so. A tool
    # that is correct and never called is not a fix.
    src = (ROOT / "tools" / "ph-build.sh").read_text()
    body = tkmod_body(src)
    staged = body.find("_ph_stage_module")
    check("tkmod stages the module before the first scp",
          0 <= staged < body.find("scp "))
    # Both copies: the hot insmod one and the .ko.xz written over the
    # installed module. Pushing a stripped .ko beside an unstripped .ko.xz
    # would survive the insmod and fail the next modprobe.
    check("nothing after the staging reaches for the raw build path",
          staged >= 0 and "$_PH_OUT/$rel" not in body[staged:])

    # The other path that puts a tree-built .ko on the device. It has always
    # stripped; nothing asserted it, which is how tkmod's copy went missing.
    push = (ROOT / "tools" / "ph-push-module.sh").read_text()
    check("ph-push-module.sh still strips before its scp",
          0 <= push.find("ph-strip-btf.py") < push.find("scp "))

    # 7. and it does what it says, called for real.
    mod = write("staged.ko", build_elf([".BTF", ".text"]))
    rc, out = stage_via_shell(mod)
    check("_ph_stage_module succeeds", rc == 0 and out)
    # Named after the module, or every module stages over the same /tmp/.ko --
    # which is what one `local` for both $name and $staged silently does.
    check("the staged copy is named after the module",
          out.endswith("/stagetest.ko"))
    check("what it hands back has no named .BTF",
          ".BTF" not in section_names(pathlib.Path(out).read_bytes()))
    check(".text survives the staging",
          ".text" in section_names(pathlib.Path(out).read_bytes()))
    # .output belongs to the workspace container's uid; rewriting it in place
    # is both a permission error and not ours to do.
    check("the built module itself is left alone",
          ".BTF" in section_names(mod.read_bytes()))

    print("test_strip_btf.py: all checks passed")


if __name__ == "__main__":
    main()
