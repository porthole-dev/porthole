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

echo "test_ph_build.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
