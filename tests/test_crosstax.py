#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""ph-crosstax.py's classifier, with no container and no build."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))
import _runner  # noqa: E402


def _mod():
    """By path, because a hyphenated filename is not an importable name."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ph_crosstax", ROOT / "tools" / "ph-crosstax.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_an_emulated_compiler_is_named_as_emulated():
    """The shape the existing finding recorded, verbatim: crossdirect execs
    the target-arch binary by absolute path and binfmt hands it to qemu."""
    c = _mod().classify
    assert c("qemu-aarch64-static /usr/bin/cc -o libfoo.so foo.o") == "qemu"
    assert c("qemu-aarch64-static /usr/bin/clang++ -o Test a.o") == "qemu"


def test_the_native_cross_compiler_is_named_as_native():
    c = _mod().classify
    assert c("/native/usr/lib/ccache/bin/aarch64-alpine-linux-musl-gcc "
             "-c foo.c") == "native"
    assert c("/usr/lib/ccache/bin/clang++ -c foo.cpp") == "native"


def test_everything_else_is_neither():
    """ninja, sh, abuild and the sampler's own ps must not be counted as
    either side, or the split is a ratio of noise."""
    c = _mod().classify
    for line in ("ninja -C build", "/bin/sh /usr/bin/abuild",
                 "ps -eo pid,ppid,stat,args=", "[cc]"):
        assert c(line) == "", line

    # ANCHORED, both sides, and these are the cases that say so.
    #
    # A qemu name that is not the executable is somebody talking ABOUT qemu --
    # a grep, a ps, the sampler's own pipeline. Unanchored, this tool counts
    # itself.
    for line in ("grep -r qemu-aarch64-static /pmb",
                 "/bin/sh -c 'pgrep qemu-aarch64-static'"):
        assert c(line) == "", line
    # ...and the NATIVE side is the ccache path, not the compiler name. The
    # same `gcc`/`clang++` names appear on both sides of this split and on
    # neither: a configure test compiling conftest.c with the chroot's own
    # /usr/bin/gcc is real work and is not the cross compiler, so counting it
    # would inflate the native share with something crossdirect never touched.
    for line in ("/usr/bin/gcc -c conftest.c",
                 "/usr/libexec/gcc/x86_64-alpine-linux-musl/cc1plus -quiet a.c"):
        assert c(line) == "", line


def test_a_zombie_is_not_running_work():
    """The original finding caught 40 `[cc]` zombies in one snapshot. Counting
    them as running work invents a compile phase that had already exited."""
    tally = _mod().tally_sample(
        "  1  0 Ss   /bin/sh /usr/bin/abuild\n"
        "  2  1 R    qemu-aarch64-static /usr/bin/cc -o libfoo.so foo.o\n"
        "  3  1 Z    qemu-aarch64-static /usr/bin/cc -o dead.so x.o\n"
        "  4  1 R    /native/usr/lib/ccache/bin/aarch64-alpine-linux-musl-gcc -c a.c\n")
    assert tally == {"qemu": 1, "native": 1}, tally


def test_a_side_with_nothing_running_is_absent_not_zero():
    """`tally_sample` counts what it saw. A side that ran nothing contributes
    no key, so the caller can tell "no compiles in this window" from "some,
    all native" -- and cannot divide by a total it invented."""
    assert _mod().tally_sample("  1  0 Ss   ninja -C build\n") == {}
    assert _mod().tally_sample("") == {}


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
