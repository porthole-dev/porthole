#!/bin/bash
# SPDX-License-Identifier: MIT
# tools/ph-build.sh: does the `fast` rung know when the tree is not the aport?
#
# `fast` (tkbuild-kernel) ships the APORT -- it installs the apk, pushes that
# package's modules out of the rootfs chroot, and reads the dtb back out of the
# apk. Nothing the tree build produces reaches the phone. It still ran the tree
# build first, and on 2026-08-29 that turned a working configuration into a
# hard failure: the tree sat on v6.18 while the aport had moved to 7.2.2, so
# _ph_make copied the 7.2 config into the 6.18 tree and died in drivers/gpu/drm
# with "unable to open output file". Neither kernel was broken; they were not
# the same kernel.
#
# Needs no device and builds nothing. Run: bash tests/test_ph_build.sh
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); }
bad() { FAIL=$((FAIL+1)); echo "FAIL $1"; echo "     $2"; }
is()  { if [ "$2" = "$3" ]; then ok; else bad "$1" "got '$2', want '$3'"; fi; }

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

# Call the REAL function against fixture files rather than re-implementing its
# version arithmetic here -- a test that repeats the expression under test
# passes just as happily when the expression is wrong.
#
# `env -i` because a developer with PORTHOLE_KERNEL_PKG exported (which is how
# this whole class of bug reached the phone in the first place) would otherwise
# have it silently outrank the fixture.
verdict() { # verdict TREE_VERSION TREE_PATCHLEVEL APORT_PKGVER -> "rc tree aport"
    mkdir -p "$TMP/repo/pmaports/device/testing/fakekpkg" "$TMP/tree"
    printf 'VERSION = %s\nPATCHLEVEL = %s\nSUBLEVEL = 0\n' "$1" "$2" > "$TMP/tree/Makefile"
    printf 'pkgver=%s\npkgrel=1\n' "$3" > "$TMP/repo/pmaports/device/testing/fakekpkg/APKBUILD"
    env -i PATH="$PATH" HOME="$HOME" PORTHOLE_ROOT="$ROOT" \
        PORTHOLE_DEVICE=google-taimen PORTHOLE_WORKDIR="$TMP/repo" \
        PORTHOLE_KERNEL_TREE="$TMP/tree" PORTHOLE_KERNEL_PKG=fakekpkg \
        bash -c 'source "$PORTHOLE_ROOT/tools/ph-build.sh" >/dev/null 2>&1
                 _ph_tree_matches_aport
                 echo "$? $_PH_TREE_V $_PH_APORT_V"'
}

# The case that cost a build: a major version apart.
is "6.18 tree vs 7.2.2 aport is a mismatch" "$(verdict 6 18 7.2.2)" "1 6.18 7.2"

# The ordinary case: the tree corresponds to the aport it ships.
is "7.2 tree vs 7.2.2 aport matches"        "$(verdict 7 2 7.2.2)"  "0 7.2 7.2"

# A two-component pkgver must not lose its minor. `${pkgver%.*}` -- the obvious
# way to write this -- turns 6.18 into 6 and would call every 6.x tree a match.
is "two-component pkgver keeps its minor"   "$(verdict 6 18 6.18)"  "0 6.18 6.18"
is "6.18 tree vs 6.0 aport is a mismatch"   "$(verdict 6 18 6.0)"   "1 6.18 6.0"

# Unknown on either side must build, not skip: refusing to build because a file
# could not be read would be a worse failure than the one this guards.
mkdir -p "$TMP/empty"
rc=$(env -i PATH="$PATH" HOME="$HOME" PORTHOLE_ROOT="$ROOT" \
        PORTHOLE_DEVICE=google-taimen PORTHOLE_WORKDIR="$TMP/repo" \
        PORTHOLE_KERNEL_TREE="$TMP/empty" PORTHOLE_KERNEL_PKG=fakekpkg \
        bash -c 'source "$PORTHOLE_ROOT/tools/ph-build.sh" >/dev/null 2>&1
                 _ph_tree_matches_aport; echo $?')
is "an unreadable tree Makefile still builds" "$rc" "0"

# --- the defconfig guard -------------------------------------------------
#
# _ph_make re-copied the aport config over the tree defconfig and re-ran
# `make <defconfig>` on EVERY build, measured at 4.92 s, including the runs
# where nothing had changed. The guard skips both when the file it would copy
# is already byte-identical to the destination AND the built .config is newer
# than that destination.
#
# It must fail OPEN. The hazard here is the 2026-08-08 one -- a config from the
# wrong aport going into the tree and olddefconfig silently dropping renamed
# symbols, which killed USB -- and a guard that skips when it should not have
# is how that comes back. So every uncertain case must resync.
dc() { # dc <same|differ|no-aport|no-defconfig> <newer|older|absent> -> rc
    rm -rf "$TMP/dc"
    mkdir -p "$TMP/dc/repo/pmaports/device/testing/fakekpkg" \
             "$TMP/dc/tree/arch/arm64/configs" "$TMP/dc/tree/.output"
    local aport="$TMP/dc/repo/pmaports/device/testing/fakekpkg/fakeconfig"
    local dc="$TMP/dc/tree/arch/arm64/configs/taimen_defconfig"
    [ "$1" = no-aport ]    || printf 'CONFIG_A=y\n' > "$aport"
    case $1 in
        same)   printf 'CONFIG_A=y\n' > "$dc" ;;
        differ) printf 'CONFIG_B=y\n' > "$dc" ;;
        no-aport) printf 'CONFIG_A=y\n' > "$dc" ;;
        no-defconfig) ;;
    esac
    # Pin both mtimes. Writing the two files back to back lands them in the
    # SAME clock tick often enough to matter, and equal is not newer -- which
    # made this fixture pass or fail depending on where the tick fell.
    touch -d '2020-01-02' "$dc" 2>/dev/null
    case $2 in
        newer)  touch -d '2020-01-03' "$TMP/dc/tree/.output/.config" ;;
        older)  touch -d '2020-01-01' "$TMP/dc/tree/.output/.config" ;;
        absent) ;;
    esac
    env -i PATH="$PATH" HOME="$HOME" PORTHOLE_ROOT="$ROOT" \
        PORTHOLE_DEVICE=google-taimen PORTHOLE_WORKDIR="$TMP/dc/repo" \
        PORTHOLE_KERNEL_TREE="$TMP/dc/tree" PORTHOLE_KERNEL_PKG=fakekpkg \
        PORTHOLE_KCONFIG_FILE=fakeconfig \
        bash -c 'source "$PORTHOLE_ROOT/tools/ph-build.sh" >/dev/null 2>&1
                 _ph_defconfig_current; echo $?'
}

# The only case that may skip.
is "identical config and a newer .config skips" "$(dc same newer)"   "0"

# Everything else resyncs.
is "a differing aport config resyncs"           "$(dc differ newer)" "1"
is "a stale .config resyncs"                    "$(dc same older)"   "1"
is "a missing .config resyncs"                  "$(dc same absent)"  "1"
is "a missing aport config resyncs"             "$(dc no-aport newer)" "1"
is "a missing tree defconfig resyncs"           "$(dc no-defconfig newer)" "1"

# --- the measure/build split ---------------------------------------------
#
# `porthole build auto` calls into ph-build.sh purely to see what make
# rebuilt, and the router only reads .ko/.dtb/Image.gz out of .output -- it
# never opens the apk. The packaging step cost 14.66 s on every preview AND
# wrote a `_p` apk, which is the exact thing _ph_assert_no_devpkgs exists to
# refuse. So the measure path must not package, and the build path must.
#
# Stubs `pmbootstrap` on PATH and asks which functions reach it.
packages() { # packages <_ph_measure|_ph_make> -> "yes"|"no"
    rm -rf "$TMP/pk"; mkdir -p "$TMP/pk/bin"
    cat > "$TMP/pk/bin/pmbootstrap" <<'STUB'
#!/bin/sh
for a in "$@"; do [ "$a" = build ] && { echo hit >> "$PH_TEST_HITS"; exit 0; }; done
exit 0
STUB
    chmod +x "$TMP/pk/bin/pmbootstrap"
    : > "$TMP/pk/hits"
    # Neutralise everything with side effects; we are asking about one call.
    env -i PATH="$TMP/pk/bin:$PATH" HOME="$HOME" PORTHOLE_ROOT="$ROOT" \
        PORTHOLE_DEVICE=google-taimen PORTHOLE_WORKDIR="$TMP/dc/repo" \
        PORTHOLE_KERNEL_TREE="$TMP/dc/tree" PORTHOLE_KERNEL_PKG=fakekpkg \
        PH_TEST_HITS="$TMP/pk/hits" PH_FN="$1" \
        bash -c 'source "$PORTHOLE_ROOT/tools/ph-build.sh" >/dev/null 2>&1
                 declare -F "$PH_FN" >/dev/null || { echo missing; exit; }
                 grep -q "pmbootstrap build" \
                     <(declare -f "$PH_FN") && echo yes || echo no' 2>/dev/null
}

is "_ph_measure does not package"  "$(packages _ph_measure)" "no"
is "_ph_make does package"         "$(packages _ph_make)"    "yes"

# --- the arch-independent-dependency workaround --------------------------
#
# A `noarch`/`all` dependency of a foreign-arch device build makes pmbootstrap
# ask for `gcc-<native>` -- a cross compiler to the native arch, which cannot
# exist -- and the rung dies having built nothing. Every fresh workspace hits
# it; a machine that has built the package once never does, which is why it
# went unseen. _ph_pmb_build rebuilds the offender with the device arch named
# and retries.
#
# pmbootstrap is STUBBED here: this asserts the recovery logic, not pmbootstrap.
# The canned failure carries the real ANSI colour codes, because the first
# version of the extraction matched happily against clean text and not at all
# against what the command actually prints.
pmb_stub() { # pmb_stub NATIVE -> "rc | recovery command | attempts"
    env -i PATH="$PATH" HOME="$HOME" PORTHOLE_ROOT="$ROOT" NATIVE="$1" \
        PORTHOLE_DEVICE=google-taimen PORTHOLE_WORKDIR="$TMP/repo" \
        PORTHOLE_ARCH=aarch64 TMP="$TMP" \
        bash -c '
        source "$PORTHOLE_ROOT/tools/ph-build.sh" >/dev/null 2>&1
        : > "$TMP/calls"
        uname() { echo "$NATIVE"; }
        pmbootstrap() {
            echo "$*" >> "$TMP/calls"
            case "$*" in
              *--arch*) return 0 ;;                       # the recovery build
              *) [ -s "$TMP/fixed" ] && return 0
                 printf "fixed\n" > "$TMP/fixed"
                 printf "\033[93m=> (1/4)\033[0m \033[94medge/rmtfs\033[0m: Installing dependencies\033[0m\n"
                 printf "\033[91mERROR:\033[0m apk add crossdirect g++-%s gcc-%s abuild\033[0m\n" "$NATIVE" "$NATIVE"
                 return 1 ;;
            esac
        }
        _ph_pmb_build device-google-taimen >/dev/null 2>&1
        printf "%s | %s | %s\n" "$?" "$(grep -c -- --arch "$TMP/calls")" "$(grep -o "rmtfs" "$TMP/calls" | head -1)"'
}

is "a native-arch cross request is recovered and retried" "$(pmb_stub x86_64)" "0 | 1 | rmtfs"

# A failure that is NOT this bug must propagate untouched, or a real build
# error would be retried forever behind a misleading explanation.
other=$(env -i PATH="$PATH" HOME="$HOME" PORTHOLE_ROOT="$ROOT" \
        PORTHOLE_DEVICE=google-taimen PORTHOLE_WORKDIR="$TMP/repo" PORTHOLE_ARCH=aarch64 \
        bash -c '
        source "$PORTHOLE_ROOT/tools/ph-build.sh" >/dev/null 2>&1
        pmbootstrap() { echo "ERROR: something else entirely"; return 3; }
        _ph_pmb_build device-google-taimen >/dev/null 2>&1; echo $?')
is "an unrelated build failure is not retried" "$other" "3"

echo "test_ph_build.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
