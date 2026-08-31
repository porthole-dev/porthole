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

# --- ccache reachable from outside envkernel ------------------------------

test_arm_ccache_is_callable_standalone() {
	# Package builds pay emulated hashing of every preprocessed source,
	# because ccache in the aarch64 buildroot IS an aarch64 binary running
	# under qemu. The kernel path already fixed this by putting ccache in
	# chroot_native; packages never inherited it. Calling the same function
	# is the whole fix, so it has to be reachable from outside envkernel.
	grep -q '^_ph_arm_ccache()' "$ROOT/tools/ph-build.sh" || return 1
	grep -q 'PORTHOLE_CCACHE_STANDALONE' "$ROOT/tools/ph-build.sh" || return 1
}
if test_arm_ccache_is_callable_standalone; then ok; else
	bad "_ph_arm_ccache is callable standalone" "marker or function missing"
fi

# --- the boot rung's base image ------------------------------------------
#
# The old default was /tmp/tk-base-boot.img with "seed it once" printed when it
# was missing -- an instruction that cannot be followed from where builds run,
# because the workspace container does not mount the host's /tmp. And the check
# fired AFTER the compile, so the cost was paid before the news.

partlabel() { # partlabel EXTRA_ENV [DEVICE]
    env -i PATH="$PATH" HOME="$HOME" PORTHOLE_ROOT="$ROOT" \
        PORTHOLE_DEVICE="${2:-google-taimen}" PORTHOLE_WORKDIR="$TMP/repo" \
        PORTHOLE_KERNEL_PKG=k PORTHOLE_DEVICE_PKG=d PORTHOLE_FW_PKG=f \
        PORTHOLE_DTB_FILE=x.dtb $1 \
        bash -c 'source "$PORTHOLE_ROOT/tools/ph-build.sh" >/dev/null 2>&1
                 _ph_boot_partlabel'
}

# Derived per device, because this file is scope: generic.
is "a/b slots give a suffixed partlabel" \
   "$(partlabel 'PORTHOLE_HAS_AB_SLOTS=1 PORTHOLE_ACTIVE_SLOT=b')" "boot_b"
is "no slots gives a plain partlabel" \
   "$(partlabel 'PORTHOLE_HAS_AB_SLOTS=0')" "boot"
# A/B slots with no ACTIVE slot recorded is the real state of an unprobed
# port -- google-cheetah in this repo is exactly that. Guessing a suffix there
# names a partition that may not exist; plain `boot` at least fails by saying
# which partlabel it looked for.
is "slots without a probed active slot does not guess a suffix" \
   "$(partlabel '' google-cheetah)" "boot"
is "the override wins over both" \
   "$(partlabel 'TK_BOOT_PARTLABEL=weird PORTHOLE_HAS_AB_SLOTS=1 PORTHOLE_ACTIVE_SLOT=b')" \
   "weird"

# The seed must refuse a truncated or garbage read rather than caching it: a
# repack from a bad base produces an image the bootloader rejects, with nothing
# in this file having complained.
seedcheck=$(env -i PATH="$PATH" HOME="$HOME" PORTHOLE_ROOT="$ROOT" \
    PORTHOLE_DEVICE=google-taimen PORTHOLE_WORKDIR="$TMP/repo" \
    PORTHOLE_KERNEL_PKG=k PORTHOLE_DEVICE_PKG=d PORTHOLE_FW_PKG=f \
    PORTHOLE_DTB_FILE=x.dtb TMP="$TMP" \
    bash -c 'source "$PORTHOLE_ROOT/tools/ph-build.sh" >/dev/null 2>&1
             _PH_BASEIMG="$TMP/base.img"
             tk_run() { case "$*" in *test\ -e*) return 0 ;;
                                     *) printf "not a boot image" ;; esac; }
             _ph_seed_baseimg >/dev/null 2>&1; echo "$?:$([ -e "$TMP/base.img" ] && echo cached || echo absent)"')
is "a non-boot-image read is refused, not cached" "$seedcheck" "1:absent"

# No /tmp anywhere in the base-image path: that was the whole defect.
if grep -q 'TK_BASEIMG:-/tmp' "$ROOT/tools/ph-build.sh"; then
    bad "the base image no longer defaults into /tmp" \
        "the container does not mount the host's /tmp"
else ok; fi

# --- the module stack: what `mod` has to take off before it can reload ------
#
# `porthole build mod ath10k_core` compiled, pushed, and gave up: "loaded and
# still held". tkmod looked for /sys/bus/{i2c,platform}/drivers/<the module it
# is replacing>, which is the camera sensor it was written for and nothing
# stacked -- ath10k_core has no devices of its own, ath10k_snoc holds it, and
# unbinding a directory that does not exist unbinds nothing. Measured on
# taimen 2026-08-31: 78 of 286 loaded modules have holders.
#
# tools/tk-modstack.sh is a FILE so that this can run it. The alternative is
# finding out whether the script that unloads a wifi driver is correct by
# unloading a wifi driver.

# A fixture sysfs shaped like the real one, two levels deep and with the
# diamond that matters: cfg80211 reaches ath10k_core directly AND through
# mac80211.
mkdir -p "$TMP/sys/module"/{ath,ath10k_snoc,ath10k_core,mac80211,cfg80211,imx179}/holders
: > "$TMP/sys/module/ath10k_core/holders/ath10k_snoc"
: > "$TMP/sys/module/mac80211/holders/ath10k_core"
for h in ath ath10k_core mac80211; do : > "$TMP/sys/module/cfg80211/holders/$h"; done
rmdir "$TMP/sys/module/imx179/holders"   # a module with no holders at all

ms() { SYS="$TMP/sys" bash -c '. tools/tk-modstack.sh; "$@"' _ "$@"; }

is "a module nothing stacks on has an empty stack" "$(ms ms_stack imx179)" ""
is "the one-level case is the module that holds it" \
   "$(ms ms_stack ath10k_core)" "ath10k_snoc"

# Removal order, not merely membership: rmmod fails on a module that is still
# held, so ath10k_snoc must precede ath10k_core and ath10k_core must precede
# mac80211. Listing cfg80211's holders in the order sysfs reports them --
# ath, ath10k_core, mac80211 -- fails on the second one.
stack=$(ms ms_stack cfg80211)
is "every holder appears once, however many ways it is reached" \
   "$(echo "$stack" | tr ' ' '\n' | sort | tr '\n' ' ')" \
   "ath ath10k_core ath10k_snoc mac80211 "
deepest_first() { # deepest_first LIST A B -> "ok" when A comes before B
    local i=0 a=0 b=0
    for m in $1; do i=$((i+1)); [ "$m" = "$2" ] && a=$i; [ "$m" = "$3" ] && b=$i; done
    [ "$a" -gt 0 ] && [ "$b" -gt 0 ] && [ "$a" -lt "$b" ] && echo ok
}
is "the holder of a holder comes off first" \
   "$(deepest_first "$stack" ath10k_snoc ath10k_core)" "ok"
is "a module comes off before the one it holds" \
   "$(deepest_first "$stack" ath10k_core mac80211)" "ok"
is "the stack goes back together in the other order" \
   "$(ms ms_reverse 'a b c')" "c b a"

# --- the guardrail --------------------------------------------------------
#
# Unloading the driver you are reaching the device over does not fail, it
# strands: the rmmod succeeds, the link drops, and nothing is left to run the
# modprobe that would bring it back. Over usb0 an ath10k reload is safe, which
# is why this must not simply refuse everything stacked.
mkdir -p "$TMP/sys/class/net/wlan0/device/driver" \
         "$TMP/sys/class/net/usb0/device/driver" "$TMP/bin"
ln -sf "$TMP/sys/module/ath10k_snoc" "$TMP/sys/class/net/wlan0/device/driver/module"
ln -sf "$TMP/sys/module/libcomposite" "$TMP/sys/class/net/usb0/device/driver/module"
mkdir -p "$TMP/sys/module/libcomposite/holders"
printf '#!/bin/sh\necho "$3 dev $IFACE src 172.16.42.1"\n' > "$TMP/bin/ip"
chmod +x "$TMP/bin/ip"

conflict() { # conflict IFACE MODULE -> what it refuses to unload, if anything
    IFACE=$1 SYS="$TMP/sys" PATH="$TMP/bin:$PATH" SSH_CONNECTION="172.16.42.2 1 172.16.42.1 22" \
        bash -c '. tools/tk-modstack.sh; ms_conflict "$1"' _ "$2"
}
is "reloading over wifi refuses, and names what carries the session" \
   "$(conflict wlan0 ath10k_core)" "ath10k_snoc"
is "the transport module itself is refused too" \
   "$(conflict wlan0 ath10k_snoc)" "ath10k_snoc"
is "the same reload over usb0 is allowed" "$(conflict usb0 ath10k_core)" ""
is "an unrelated module over wifi is allowed" "$(conflict wlan0 imx179)" ""
# Refusing because the transport could not be identified would block the rung
# far more often than the hazard it guards.
is "no ssh session to reason about fails open" \
   "$(SYS="$TMP/sys" bash -c '. tools/tk-modstack.sh; ms_conflict ath10k_core')" ""

# --- unbind, on whatever bus the module actually sits on -------------------
d="$TMP/sys/bus/platform/drivers/ath10k_snoc"
mkdir -p "$d/18800000.wifi" "$TMP/sys/module/ath10k_snoc"
ln -sf "$TMP/sys/module/ath10k_snoc" "$d/module"   # a real link, not a device
ln -sf "$d" "$d/18800000.wifi/driver"              # what a bound device has
: > "$d/unbind"; : > "$d/bind"; : > "$d/uevent"
printf '#!/bin/sh\nexec "$@"\n' > "$TMP/bin/sudo"; chmod +x "$TMP/bin/sudo"
SYS="$TMP/sys" PATH="$TMP/bin:$PATH" bash -c '. tools/tk-modstack.sh; ms_unbind ath10k_snoc'
is "the bound device is unbound" "$(cat "$d/unbind")" "18800000.wifi"

# --- and tkmod actually uses all of it -------------------------------------
reload=$(sed -n "$(grep -Fn 'cat "$modstack"' tools/ph-build.sh | cut -d: -f1 | head -1),\
$(grep -Fn "loaded \$name'\"" tools/ph-build.sh | cut -d: -f1 | head -1)p" tools/ph-build.sh)
before() { # before A B -> "ok" when A appears before B in the reload script
    local a b
    a=$(printf '%s\n' "$reload" | grep -n -- "$1" | head -1 | cut -d: -f1)
    b=$(printf '%s\n' "$reload" | grep -n -- "$2" | head -1 | cut -d: -f1)
    [ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ] && echo ok
}
is "the reload asks what holds the module"  "$(before 'ms_stack' 'rmmod')" "ok"
# Decided before anything comes off, or the refusal arrives after the damage.
is "the transport is checked before the first rmmod" \
   "$(before 'ms_conflict' 'rmmod')" "ok"
# A failed insmod that leaves a wifi driver unloaded is worse than a failed
# insmod, so the stack goes back before the exit-3 branch reports one.
is "the stack is restored before the failure is reported" \
   "$(before 'ms_reverse' 'exit 3')" "ok"

# The escaping is the part that cannot be eyeballed. Render the script tkmod
# would actually send -- variables expanded, quoting resolved -- and check
# that it is sh at all. It unloads a wifi driver; finding out on the phone is
# not a plan.
{ printf 'ssh() { printf "%%s\\n" "${@: -1}"; }\nTK_SSH_OPTS=(-q)\nphone=fake\n'
  printf 'name=ath10k_core\n_PH_REPO_ROOT=%s\nmodstack=$_PH_REPO_ROOT/tools/tk-modstack.sh\n' "$ROOT"
  printf '%s\n' "$reload" | tr -d '\t'; } > "$TMP/emit.sh"
bash "$TMP/emit.sh" > "$TMP/payload.sh" 2>/dev/null
if sh -n "$TMP/payload.sh" 2>/dev/null; then ok; else
    bad "the script tkmod sends is valid sh" "$(sh -n "$TMP/payload.sh" 2>&1 | head -3)"; fi
is "the module name reached the payload" \
   "$(grep -c 'ms_stack ath10k_core' "$TMP/payload.sh")" "1"


# --- tkbuild purges the envkernel _p apk before install --------------------
#
# _ph_make ends with `pmbootstrap build --envkernel`, which packages the tree
# under a dev version (<ver>_p<timestamp>-r0) and apk sorts _p<timestamp>
# ABOVE -rNN. _ph_assert_no_devpkgs at the top of tkbuild only proves the repo
# was clean BEFORE _ph_make ran; it says nothing about after. Reproduced on a
# real build 2026-08-31: `pmbootstrap install` refused with "unable to select
# packages" against a _p apk the same build had produced three minutes
# earlier. tkbuild must call tkpurge-devpkgs (which re-indexes) strictly
# between _ph_make and `pmbootstrap install`, or the rung poisons its own repo
# on every run. Source-order check, same technique as `before()` above.
tkbuild_body=$(sed -n "$(grep -n '^tkbuild() {' tools/ph-build.sh | cut -d: -f1),\
$(awk '/^tkbuild\(\) \{/{f=1;next} f&&/^}/{print NR;exit}' tools/ph-build.sh)p" \
    tools/ph-build.sh)
tb_before() { # tb_before A B -> "ok" when A appears before B in tkbuild()
    local a b
    a=$(printf '%s\n' "$tkbuild_body" | grep -n -- "$1" | head -1 | cut -d: -f1)
    b=$(printf '%s\n' "$tkbuild_body" | grep -n -- "$2" | head -1 | cut -d: -f1)
    [ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ] && echo ok
}
is "tkbuild purges after _ph_make" \
   "$(tb_before '_ph_make || return 1' 'tkpurge-devpkgs || return 1')" "ok"
is "tkbuild purges before pmbootstrap install" \
   "$(tb_before 'tkpurge-devpkgs || return 1' 'pmbootstrap install --password')" "ok"

echo "test_ph_build.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
