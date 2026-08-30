---
id: pmbootstrap-never-runs-the-shell-in-an-apkbuild
title: pmbootstrap parses an APKBUILD line by line and never runs the shell
scope: generic
subsystem: build
severity: trap
confidence: proven
evidence: 2026-08-30. pmb/parse/_apkbuild.py calls _parse_attributes(path, lines, ...) over the file's LINES; nothing execs bash. temp/webkit2gtk-6.0 (forked from Alpine community) adds libjxl-dev to makedepends inside a `case "$CARCH" in ... esac`; libjxl-dev was never installed into the buildroot and the build died ~90s into cmake with "libjxl is required for USE_JPEGXL", while `apk search libjxl-dev` showed the package present for aarch64.
first-learned: 2026-08-30
---

**An APKBUILD is a shell script, and pmbootstrap does not run it.**
`pmb/parse/_apkbuild.py` reads the file's lines and pulls assignments out of
them textually. So anything a real shell would have computed -- a conditional,
a loop, a substitution -- is invisible.

This is how Alpine itself writes a per-architecture dependency:

    case "$CARCH" in
    s390x) ;;
    *)
    	makedepends="$makedepends libjxl-dev"
    	;;
    esac

abuild, running inside the chroot, executes that and wants `libjxl-dev`.
pmbootstrap, deciding what to *install into the buildroot first*, never sees
it. The dependency is therefore absent at configure time and present in the
build's own expectations, which is the worst of both.

**Why it costs an evening rather than a minute.** The failure surfaces ninety
seconds into cmake as

    libjxl is required for USE_JPEGXL

which reads as a missing distro package. The obvious check makes it *more*
confusing, not less:

    apk search libjxl-dev      -> it exists, for this arch

So the search says the package is there, the build says it is not, and neither
statement is wrong -- they are about different chroots. Nothing anywhere
mentions the parser.

**The rule:** any aport forked from Alpine that appends to `depends`,
`makedepends` or `checkdepends` conditionally must ALSO list those packages
statically, or pmbootstrap will not install them. `porthole pkg build` warns
about this before it starts, because the whole cost of the trap is that it
surfaces late and wearing someone else's clothes.

**The generic lesson:** a parser that reads a Turing-complete file with regexes
is not reading that file, it is reading a subset of it that happens to look the
same. Before believing a build tool about a dependency, check whether the tool
can see the line that declares it.

Related: [[a-444-test-clip-makes-working-hardware-decode-look-broken]] -- the
same shape, where the input rather than the tool was the thing nobody checked.
