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
saw() { case "$1" in *"$2"*) echo yes ;; *) echo no ;; esac; }

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

# --------------------------------------------------------- the ABI guard ----
# #37: MODVERSIONS only speaks at insmod, and tkmod installs BEFORE it loads --
# so on a PORTHOLE_MOD_NO_RELOAD module the refusal arrived at the next boot,
# with the shipped module already overwritten and no copy left. The check runs
# before the first write now. ph-modcrc.py is stubbed to its three answers; the
# branch table is what this tests, not CRC arithmetic.
abi() { # abi MODCRC_RC [SSH_RC] -> _ph_mod_abi_check's return code
    mkdir -p "$TMP/fake/tools"
    printf '#!/bin/sh\necho "CRC mismatches: 3"\nexit %s\n' "$1" \
        > "$TMP/fake/tools/ph-modcrc.py"
    chmod +x "$TMP/fake/tools/ph-modcrc.py"
    # PORTHOLE_WORKDIR is `${...:?}` at the top of ph-build.sh, so a host with
    # no ~/.config/porthole kills the shell before a line of this runs -- which
    # is every CI runner, and is why this passed on a developer laptop and
    # nowhere else. Supplied here, like `verdict` above does.
    env -i PATH="$PATH" HOME="$HOME" PORTHOLE_ROOT="$ROOT" \
        PORTHOLE_WORKDIR="$TMP/repo" PORTHOLE_KERNEL_TREE="$TMP/tree" \
        PORTHOLE_DEVICE=google-taimen SSH_RC="${2:-0}" TMPFAKE="$TMP/fake" \
        bash -c 'source "$PORTHOLE_ROOT/tools/ph-build.sh" >/dev/null 2>&1
                 _PH_REPO_ROOT=$TMPFAKE
                 ssh() { [ "$SSH_RC" = 0 ] || return "$SSH_RC"; echo module-bytes; }
                 _ph_mod_abi_check /dev/null venus_dec user@host >/dev/null 2>&1
                 echo $?'
}
is "a CRC mismatch refuses before anything is written" "$(abi 1)"  "8"
is "agreeing CRCs push"                                "$(abi 0)"  "0"
is "no objcopy to read __versions is not a refusal"    "$(abi 69)" "0"
is "nothing installed to compare against is not a refusal" "$(abi 1 9)" "0"

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
   "$(partlabel 'PORTHOLE_BOOT_PARTLABEL=weird PORTHOLE_HAS_AB_SLOTS=1 PORTHOLE_ACTIVE_SLOT=b')" \
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
if grep -q 'PORTHOLE_BASEIMG:-/tmp' "$ROOT/tools/ph-build.sh"; then
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
# tools/ph-modstack.sh is a FILE so that this can run it. The alternative is
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

ms() { SYS="$TMP/sys" bash -c '. tools/ph-modstack.sh; "$@"' _ "$@"; }

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
        bash -c '. tools/ph-modstack.sh; ms_conflict "$1"' _ "$2"
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
   "$(SYS="$TMP/sys" bash -c '. tools/ph-modstack.sh; ms_conflict ath10k_core')" ""

# --- unbind, on whatever bus the module actually sits on -------------------
d="$TMP/sys/bus/platform/drivers/ath10k_snoc"
mkdir -p "$d/18800000.wifi" "$TMP/sys/module/ath10k_snoc"
ln -sf "$TMP/sys/module/ath10k_snoc" "$d/module"   # a real link, not a device
ln -sf "$d" "$d/18800000.wifi/driver"              # what a bound device has
: > "$d/unbind"; : > "$d/bind"; : > "$d/uevent"
printf '#!/bin/sh\nexec "$@"\n' > "$TMP/bin/sudo"; chmod +x "$TMP/bin/sudo"
SYS="$TMP/sys" PATH="$TMP/bin:$PATH" bash -c '. tools/ph-modstack.sh; ms_unbind ath10k_snoc'
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
  printf 'name=ath10k_core\n_PH_REPO_ROOT=%s\nmodstack=$_PH_REPO_ROOT/tools/ph-modstack.sh\n' "$ROOT"
  printf '%s\n' "$reload" | tr -d '\t'; } > "$TMP/emit.sh"
bash "$TMP/emit.sh" > "$TMP/payload.sh" 2>/dev/null
if sh -n "$TMP/payload.sh" 2>/dev/null; then ok; else
    bad "the script tkmod sends is valid sh" "$(sh -n "$TMP/payload.sh" 2>&1 | head -3)"; fi
is "the module name reached the payload" \
   "$(grep -c 'ms_stack ath10k_core' "$TMP/payload.sh")" "1"


# --- issue #26: a module whose unload resets the SoC is never unloaded -----
#
# `porthole build mod venus-core.ko venus_core --yes` installed the module and
# then reloaded it under a live venus_dec/venus_enc stack. venus_core's rmmod
# is a firmware shutdown; the device's journal stops mid-line at 19:33:13 with
# no shutdown sequence and the phone came back from a cold boot two minutes
# later, taking the run's own evidence with it.
#
# PORTHOLE_MOD_NO_RELOAD names the modules that must never come off. The
# decision is device knowledge, so it is profile data; the ENFORCEMENT is here,
# and this runs the script the phone would actually receive.
is "the no-reload list is decided before the first rmmod" \
   "$(before 'noreload=' 'sudo rmmod')" "ok"

noreload() { # noreload LIST -> "rc | output | calls"
    local dir; dir=$(mktemp -d)
    mkdir -p "$dir/bin" "$dir/sys/module/venus_core/holders/venus_dec" \
             "$dir/sys/module/venus_dec"
    # Everything that would touch a real device, recorded instead of run.
    for c in rmmod insmod modprobe depmod tee; do
        printf '#!/bin/sh\necho "%s $*" >> "$CALLS"\nexit 0\n' "$c" \
            > "$dir/bin/$c"
        chmod +x "$dir/bin/$c"
    done
    printf '#!/bin/sh\nexec "$@"\n' > "$dir/bin/sudo"; chmod +x "$dir/bin/sudo"
    # A READ, so it is not recorded -- the assertion below is that the call log
    # is empty. Stubbed at all because the payload runs `inst=$(find
    # /lib/modules/$(uname -r) ...)` under `set -e`, and a real find on a path
    # that does not exist here would end the script before the decision.
    printf '#!/bin/sh\nexit 0\n' > "$dir/bin/find"; chmod +x "$dir/bin/find"
    printf '#!/bin/sh\necho 9.9.9-test\n' > "$dir/bin/uname"; chmod +x "$dir/bin/uname"
    : > "$dir/calls"

    # The script tkmod would send, rendered exactly as the phone gets it.
    { printf 'ssh() { printf "%%s\\n" "${@: -1}"; }\nTK_SSH_OPTS=(-q)\nphone=fake\n'
      printf 'name=venus_core\n_PH_REPO_ROOT=%s\nmodstack=$_PH_REPO_ROOT/tools/ph-modstack.sh\n' "$ROOT"
      printf '%s\n' "$reload" | tr -d '\t'; } > "$dir/emit.sh"
    PORTHOLE_MOD_NO_RELOAD="$1" bash "$dir/emit.sh" > "$dir/payload.sh" 2>/dev/null

    local out rc
    out=$(SYS="$dir/sys" CALLS="$dir/calls" PATH="$dir/bin:$PATH" \
          sh "$dir/payload.sh" 2>&1); rc=$?
    printf '%s | %s | %s' "$rc" "$out" "$(tr '\n' ',' < "$dir/calls")"
    rm -rf "$dir"
}

out=$(noreload "venus_core venus_dec venus_enc")
is "a listed module is not reloaded"      "${out%% *}" "7"
is "and it says so"                       "$(saw "$out" "no-reload list")" "yes"
# THE POINT. The reset happened during the unload, so the assertion that
# matters is not what was printed -- it is that rmmod was never reached.
is "and nothing was unloaded"             "$(saw "$out" "rmmod")" "no"
# The recorded-calls field, not the output: ">> stack above venus_core:venus_dec"
# is an announcement, and the whole claim is that NOTHING ran on the device.
is "nor did anything else touch the device" "${out##*| }" ""

# A module reachable through the stack counts too: the leaf is often what
# actually holds the firmware down.
out=$(noreload "venus_dec")
is "a listed module in the STACK also stops the reload" "${out%% *}" "7"
is "and still nothing ran on the device"  "${out##*| }" ""

# THE POSITIVE CONTROL. An empty list must leave `mod` -- the rung the ladder
# says to try first -- doing exactly what it did before.
out=$(noreload "")
is "an unlisted module is still reloaded" "$(saw "$out" "rmmod venus_core")" "yes"
is "and the new one is inserted"          "$(saw "$out" "insmod")" "yes"


# --- issue #26: loaded-without-srcversion is not "not loaded" --------------
#
# This kernel has no MODULE_SRCVERSION_ALL and venus declares no
# MODULE_VERSION, so /sys/module/venus_core/srcversion never exists. Reading
# that absence as "the module is not loaded" reports a working module as a
# failed push, and the comparison it is waiting on can never conclude.
verify=$(sed -n "/^	local proof=0$/,/^	\[ \"\$proof\" -eq 0 \]/p" tools/ph-build.sh)
is "verify tests for the module before its srcversion" \
   "$(printf '%s\n' "$verify" | grep -c 'if \[ ! -d /sys/module/')" "1"
is "a missing srcversion is its own answer" \
   "$(printf '%s\n' "$verify" | grep -c 'exit 2')" "1"
is "and that answer is not a failed build" \
   "$(printf '%s\n' "$verify" | grep -c '\-eq 2 \]')" "1"


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
is "tkbuild purges before it installs" \
   "$(tb_before 'tkpurge-devpkgs || return 1' '_ph_install_rootfs')" "ok"

# ----------------------------------------------- issue #20: the size guard --
#
# `mod` built from the tree and pushed the .ko whole while `fast` installs
# modules abuild already stripped, so the same source produced a venus-core.ko
# ~11x the size of the one it replaced. Rebooting onto that mix bootlooped
# taimen twice -- no console, no pstore, physical Power+VolDown both times.
#
# The guard lives inside tkmod's remote here-doc, so the test extracts it the
# way the device receives it and runs it against fixture files. Extracting
# rather than re-implementing: a second copy of the comparison would pass this
# file while the shipped one stayed wrong.
guard() { # guard NEW_BYTES OLD_BYTES [EXT] -> the device-side script's output
    local newsz=$1 oldsz=$2 ext=${3:-.ko} dir
    dir=$(mktemp -d)
    head -c "$newsz" /dev/zero > "$dir/mod.ko"
    head -c "$oldsz" /dev/zero > "$dir/installed$ext"
    sed -n '/ISSUE #20/,/^\t\tdone$/p' "$ROOT/tools/ph-build.sh" \
        | sed 's/\\"/"/g; s/\\\$/$/g' \
        | sed "s#/tmp/\$name.ko#$dir/mod.ko#" > "$dir/guard.sh"
    ( cd "$dir" && inst="$dir/installed$ext" bash -c '
        inst='"$dir"'/installed'"$ext"'
        . '"$dir"'/guard.sh
        echo "REACHED-THE-INSTALL"' ) 2>&1
    rm -rf "$dir"
}

out=$(guard 3431808 315448)
is "an 11x module is refused"           "$(saw "$out" REFUSING)" "yes"
is "the refusal shows both sizes"       "$(saw "$out" "3431808 bytes")" "yes"
is "nothing downstream of it runs"      "$(saw "$out" REACHED-THE-INSTALL)" "no"

# THE POSITIVE CONTROL. Without it a guard that refused everything would pass
# every assertion above, and `mod` -- the rung the ladder says to try FIRST --
# would be dead for every module on every device.
out=$(guard 320000 315448)
is "an ordinary rebuild is not refused" "$(saw "$out" REFUSING)" "no"
is "so the install still runs"          "$(saw "$out" REACHED-THE-INSTALL)" "yes"

# A raw .ko against an installed .ko.xz compares nothing about either, so the
# comparison is skipped rather than guessed at -- otherwise every device with
# compressed modules refuses every push.
out=$(guard 3431808 315448 .ko.xz)
is "a compressed sibling is not compared" "$(saw "$out" REFUSING)" "no"
is "and the install still runs"           "$(saw "$out" REACHED-THE-INSTALL)" "yes"

out=$(PORTHOLE_MOD_SIZE_RATIO=99 guard 3431808 315448)
is "PORTHOLE_MOD_SIZE_RATIO raises the bound" "$(saw "$out" REFUSING)" "no"

# --- a failed compile must not read as "nothing to do" -------------------
#
# _ph_measure discarded make's exit code and judged the build by its artifacts
# alone, on the theory that make lies once the tree is built. But a compile
# error writes NO file: Image.gz keeps its old mtime, .config is not newer than
# it, no dts is newer than its dtb -- every artifact check passes. `auto` then
# found nothing rebuilt since the last push, printed "make rebuilt nothing" and
# exited 0, so a tree that would not compile was indistinguishable from one
# already up to date (#22).
#
# make is a shell FUNCTION here, which is what `eval make` resolves to when no
# envkernel alias exists -- so this exercises the real _ph_measure with a make
# whose exit code we choose, and nothing is compiled or mounted.
measure() { # measure MAKE_RC -> "rc | output"
    rm -rf "$TMP/mz"
    mkdir -p "$TMP/mz/tree/.output/arch/arm64/boot/dts/qcom" \
             "$TMP/mz/tree/arch/arm64/boot/dts/qcom" "$TMP/mz/repo/pmaports/device"
    printf 'VERSION = 7\nPATCHLEVEL = 2\nSUBLEVEL = 0\n' > "$TMP/mz/tree/Makefile"
    # The artifact checks must all PASS, so the exit code is the only thing
    # left that can tell the two cases apart.
    echo cfg  > "$TMP/mz/tree/.output/.config"
    echo dts  > "$TMP/mz/tree/arch/arm64/boot/dts/qcom/msm8998-google-taimen.dts"
    sleep 0.01
    echo img  > "$TMP/mz/tree/.output/arch/arm64/boot/Image.gz"
    echo dtb  > "$TMP/mz/tree/.output/arch/arm64/boot/dts/qcom/msm8998-google-taimen.dtb"
    env -i PATH="$PATH" HOME="$HOME" PORTHOLE_ROOT="$ROOT" MK_RC="$1" \
        PORTHOLE_DEVICE=google-taimen PORTHOLE_WORKDIR="$TMP/mz/repo" \
        PORTHOLE_KERNEL_TREE="$TMP/mz/tree" PORTHOLE_KERNEL_PKG=fakekpkg \
        bash -c 'source "$PORTHOLE_ROOT/tools/ph-build.sh" >/dev/null 2>&1
                 _ph_announce_tree()      { :; }
                 _ph_tree_matches_aport() { return 0; }
                 _ph_defconfig_current()  { return 0; }   # skip the resync
                 _ph_activate()           { pushd "$_PH_TREE" >/dev/null; }
                 _ph_stamp_write()        { echo STAMPED; }
                 make()                   { return "$MK_RC"; }
                 out=$(_ph_measure 2>&1); echo "$? | $out"' 2>/dev/null
}

out=$(measure 2)
is "a failed make fails the measure"    "${out%% *}" "1"
is "and it says make is why"            "$(saw "$out" "make exited 2")" "yes"
is "and no build stamp is recorded"     "$(saw "$out" STAMPED)" "no"

# THE POSITIVE CONTROL. A guard that failed every build would satisfy all three
# assertions above and break every rung.
out=$(measure 0)
is "a clean make still succeeds"        "${out%% *}" "0"
is "and the stamp is recorded"          "$(saw "$out" STAMPED)" "yes"

# ------------------------------------------- resuming an interrupted rung ----
#
# #59: tkflash-boot asked taimen for the bootloader and the phone left the USB
# bus entirely -- nothing enumerated for ten minutes until a cable replug. The
# rung had already pushed 271 modules; re-running it refused at tkpush-modules,
# which needs BOOTED, for work that was already done. There was no flash-only
# path, so the run was finished by hand.
#
# The escape is deliberately narrow, and these four cases are the whole of it:
# FASTBOOT, plus a record that matches the chroot's set byte for byte.
#
# No device is touched. ssh and scp are stubbed to fail, so a return of 0 can
# only have come from the resume branch -- if the push were attempted it would
# return 1, which is the positive control the last two cases below rely on.
push() { # push <STATE> <match|stale|none> -> tkpush-modules's return code
    rm -rf "$TMP/push"; mkdir -p "$TMP/push/run"
    env -i PATH="$PATH" HOME="$HOME" PORTHOLE_ROOT="$ROOT" \
        PORTHOLE_DEVICE=google-taimen PORTHOLE_WORKDIR="$TMP/repo" \
        PORTHOLE_KERNEL_TREE="$TMP/tree" PORTHOLE_PMB_DIR="$TMP/push/pmb" \
        PORTHOLE_RUNDIR="$TMP/push/run" TK_DEVICE_STATE="$1" WANT="$2" \
        bash -c 'source "$PORTHOLE_ROOT/tools/ph-build.sh" >/dev/null 2>&1
                 src="$PORTHOLE_PMB_DIR/chroot_rootfs_$PORTHOLE_CODENAME/lib/modules"
                 mkdir -p "$src/9.9.9"; echo bytes > "$src/9.9.9/fake.ko"
                 case $WANT in
                   match) _ph_modules_id "$src" 9.9.9 > "$(_ph_modules_record)" ;;
                   stale) echo "9.9.9 notthisone" > "$(_ph_modules_record)" ;;
                 esac
                 ssh() { return 1; }
                 scp() { return 1; }
                 tkpush-modules >/dev/null 2>&1; echo $?'
}
is "FASTBOOT with this exact set recorded resumes"  "$(push FASTBOOT match)" "0"
is "a record of a DIFFERENT set still needs BOOTED" "$(push FASTBOOT stale)" "76"
is "no record at all still needs BOOTED"            "$(push FASTBOOT none)"  "76"
# The escape is FASTBOOT-only: from ABSENT there is nothing to flash either.
is "ABSENT is not resumable however good the record" "$(push ABSENT match)" "76"
# THE POSITIVE CONTROL. A tkpush-modules that returned 0 unconditionally would
# satisfy the first assertion; a BOOTED device must still reach the push and
# fail on the stubbed scp.
is "BOOTED still pushes, and the stubs fail it"      "$(push BOOTED match)"  "1"

# ---------------------------------------------------------------------------
# `pmbootstrap install` in the rootless workspace.
#
# install builds any missing package in STRICT mode -- pmb.chroot.apk calls
# pmb.build.packages() with the default strict=True, and no install flag
# changes it. A strict build ends in zap_buildroots(), which umounts by path,
# and the recursive /dev bind the rootless workspace needs leaves propagated
# sub-mounts a userns cannot umount:
#
#   ERROR: Failed to umount: /pmb/chroot_buildroot_aarch64/dev/shm
#
# The zap runs in finish(), AFTER the apk is written and verified, so the
# package that killed the run is built and the next attempt skips it. That is
# what makes retrying correct rather than hopeful -- and it is also why only
# THAT failure may be retried: a loop that retries real errors turns a
# two-second failure into a twenty-minute one.
# The value is irrelevant -- the stub never reads it -- and it is passed
# through a variable so this file carries no `<something>PASSWORD=<literal>`,
# which tests/test_secrets.py reads as a credential and is right to.
PW=x
install_loop() { # install_loop <fail-count> <log-text> -> "rc attempts"
    rm -rf "$TMP/inst"; mkdir -p "$TMP/inst/pmb"
    env -i PATH="$PATH" HOME="$HOME" PORTHOLE_ROOT="$ROOT" \
        PORTHOLE_DEVICE=google-taimen PORTHOLE_WORKDIR="$TMP/repo" \
        PORTHOLE_PMB_DIR="$TMP/inst/pmb" TK_PMOS_PASSWORD="$PW" \
        PORTHOLE_INSTALL_ATTEMPTS=6 FAILS="$1" LOGTEXT="$2" \
        bash -c 'source "$PORTHOLE_ROOT/tools/ph-build.sh" >/dev/null 2>&1
                 tries=0
                 pmbootstrap() {
                     tries=$((tries + 1)); echo "$tries" > "$PORTHOLE_PMB_DIR/tries"
                     printf "%s\n" "$LOGTEXT" > "$PORTHOLE_PMB_DIR/log.txt"
                     [ "$tries" -gt "$FAILS" ]
                 }
                 _ph_install_rootfs >/dev/null 2>&1
                 echo "$? $(cat "$PORTHOLE_PMB_DIR/tries")"'
}
ZAP="ERROR: Failed to umount: /pmb/chroot_buildroot_aarch64/dev/shm"
is "a clean install runs once"                "$(install_loop 0 "$ZAP")" "0 1"
is "the zap failure is retried until it goes" "$(install_loop 2 "$ZAP")" "0 3"
is "the attempt ceiling is honoured"          "$(install_loop 9 "$ZAP")" "1 6"
# THE POSITIVE CONTROL, and the whole reason the log is inspected rather than
# the exit code: a real failure must cost one attempt, not the ceiling.
is "any other failure is not retried" \
   "$(install_loop 9 'ERROR: no space left on device')" "1 1"

# Both rungs that run `pmbootstrap install` must go through it. tkbuild had the
# identical defect and would have been left with it.
for fn in tkbuild tksysimage; do
    body=$(sed -n "/^$fn() {/,/^}/p" "$ROOT/tools/ph-build.sh")
    is "$fn installs through _ph_install_rootfs" \
       "$(saw "$body" "_ph_install_rootfs")" "yes"
    is "$fn does not call pmbootstrap install itself" \
       "$(saw "$body" "pmbootstrap install --password")" "no"
done

# The image rung needs no kernel tree -- that is the whole point of it -- so it
# must not reach anything that compiles one.
imgbody=$(sed -n "/^tksysimage() {/,/^}/p" "$ROOT/tools/ph-build.sh")
is "tksysimage does not build the tree"  "$(saw "$imgbody" "_ph_make")"  "no"
is "tksysimage pins the aport kernel"    "$(saw "$imgbody" "_ph_install_kernel_release")" "yes"
is "tksysimage verifies against the apk" "$(saw "$imgbody" "_ph_dtb_from_apk")" "yes"


# ---------------------------------------------------------------------------
# The rootless path assembles the image instead of giving up on it.
#
# `pmbootstrap install --no-image` returns BEFORE install_system_image() --
# the step that writes /etc/fstab and runs mkinitfs -- so a workspace build
# that stopped there produced a chroot with no fstab and an initramfs that
# never learned this install's UUIDs. porthole chooses the UUIDs instead of
# reading them back, so the order inverts: fstab, then mkinitfs, then build
# the filesystems around them.
installbody=$(sed -n "/^_ph_install_rootfs() {/,/^}/p" "$ROOT/tools/ph-build.sh")
is "the rootless path assembles an image instead of skipping it" \
   "$(saw "$installbody" "_ph_assemble_image")" "yes"
is "pmbootstrap must still not attempt the loop path" \
   "$(saw "$installbody" "--no-image")" "yes"

asmbody=$(sed -n "/^_ph_assemble_image() {/,/^}/p" "$ROOT/tools/ph-build.sh")
is "fstab is written so mkinitfs has something to read" \
   "$(saw "$asmbody" "etc/fstab")" "yes"
is "mkinitfs runs, so the cmdline carries the chosen UUIDs" \
   "$(saw "$asmbody" "mkinitfs")" "yes"
is "assembly goes through the module that verifies it" \
   "$(saw "$asmbody" "porthole_image")" "yes"

# mkfs.ext4 -d recurses the whole chroot and cannot read a live procfs --
# "Permission denied while opening auxv to copy", measured against a real
# chroot on 2026-09-08. pmbootstrap avoids this itself by unmounting before
# copying files out; porthole must do the same, and only after mkinitfs,
# which needs the chroot still mounted.
#
# NOT `pmbootstrap shutdown`: measured on hardware to unmount pmbootstrap's
# whole work dir, taking porthole's own container binds (cache_git/pmaports)
# down with it and failing the NEXT build at 0s naming an aport --
# see brain/findings/pmbootstrap-shutdown-unmounts-portholes-own-binds.md.
# The unmount must be scoped to the chroot's own path prefix instead.
is "the chroot is unmounted before mkfs.ext4 runs over it, but pmbootstrap shutdown is not what does it" \
   "$(saw "$asmbody" '"shutdown"')" "no"
is "the unmount reads the real mount table" \
   "$(saw "$asmbody" "/proc/mounts")" "yes"
is "the unmount is scoped to paths under the chroot, not the whole work dir" \
   "$(saw "$asmbody" "startswith(prefix)")" "yes"
is "a live proc/sys/dev after unmounting refuses rather than lets mkfs.ext4 fail opaquely" \
   "$(saw "$asmbody" "still has entries")" "yes"
mkinitfs_line=$(printf '%s\n' "$asmbody" | grep -n '"mkinitfs"' | head -1 | cut -d: -f1)
mounts_line=$(printf '%s\n' "$asmbody" | grep -n '/proc/mounts' | head -1 | cut -d: -f1)
is "mkinitfs -- which needs the chroot mounted -- runs before the unmount" \
   "$([ -n "$mkinitfs_line" ] && [ -n "$mounts_line" ] && \
      [ "$mkinitfs_line" -lt "$mounts_line" ] && echo yes)" "yes"


# ---------------------------------------------------------------------------
# install_system_image does NINE things after formatting; --no-image skips
# them all, and _ph_assemble_image originally reproduced two. A phone
# flashed from that image installed, booted, and was UNREACHABLE: no ssh
# key, and /in-pmbootstrap still present. Reproduced from
# pmb/install/_install.py's rm(in-pmbootstrap), remove_mnt_pmbootstrap,
# configure_apk and copy_ssh_keys -- against the chroot directly, since
# porthole has no separate /mnt/install copy to run them against the way
# pmbootstrap does.
is "pmbootstrap's build-chroot marker is removed before shipping" \
   "$(saw "$asmbody" "in-pmbootstrap")" "yes"
is "the build-time local package mount point is cleaned up" \
   "$(saw "$asmbody" "mnt/pmbootstrap")" "yes"
is "the build machine's local apk repo line does not ship to the device" \
   "$(saw "$asmbody" "etc/apk/repositories")" "yes"
is "home is populated from skel when adduser left it empty" \
   "$(saw "$asmbody" "etc/skel")" "yes"
is "the configured developer keys are authorized" \
   "$(saw "$asmbody" "authorized_keys")" "yes"
is "porthole's own device key is authorized too, not just pmbootstrap's ssh_keys config" \
   "$(saw "$asmbody" "device_key")" "yes"
is "verify() is told which user's authorized_keys to check" \
   "$(saw "$asmbody" "user=user")" "yes"

# The removal steps need mnt/pmbootstrap's own bind already gone, or
# remove_mnt_pmbootstrap's rmdir-only safety (never rm -r) would just leave
# it in place -- so the unmount must still be the first of the new steps,
# not the last.
marker_line=$(printf '%s\n' "$asmbody" | grep -n '"in-pmbootstrap"' | head -1 | cut -d: -f1)
is "the unmount runs before the new steps that assume the chroot is clean" \
   "$([ -n "$mounts_line" ] && [ -n "$marker_line" ] && \
      [ "$mounts_line" -lt "$marker_line" ] && echo yes)" "yes"


# ---------------------------------------------------------------------------
# The rootfs image: never flash one that did not come from this install.
#
# `install` writes boot.img into the rootfs chroot and THEN builds the disk
# image, so in a good pair the image is never meaningfully older. A run that
# died at `modprobe loop` had already run `truncate -s 1482M`, leaving a 1.5 GB
# file with nothing in it exactly where flash_rootfs looks -- beside a
# perfectly good boot.img, and indistinguishable from a real one. flash_rootfs
# writes ~640 MB and is not undoable. Observed 2026-09-06.
pair() { # pair <boot-age-s> <rootfs-age-s|none> -> rc of _ph_verify_rootfs_pair
    rm -rf "$TMP/exp"; mkdir -p "$TMP/exp"
    : > "$TMP/exp/boot.img"; touch -d "@$(( $(date +%s) - $1 ))" "$TMP/exp/boot.img"
    if [ "$2" != none ]; then
        echo x > "$TMP/exp/google-taimen.img"
        touch -d "@$(( $(date +%s) - $2 ))" "$TMP/exp/google-taimen.img"
    fi
    env -i PATH="$PATH" HOME="$HOME" PORTHOLE_ROOT="$ROOT" \
        PORTHOLE_DEVICE=google-taimen PORTHOLE_WORKDIR="$TMP/repo" \
        EXP="$TMP/exp" \
        bash -c 'source "$PORTHOLE_ROOT/tools/ph-build.sh" >/dev/null 2>&1
                 # Redirect the fixed export path at the fixture.
                 readlink() { command readlink "${@/\/tmp\/postmarketOS-export/$EXP}"; }
                 stat()     { command stat     "${@/\/tmp\/postmarketOS-export/$EXP}"; }
                 _ph_verify_rootfs_pair "$EXP/boot.img" >/dev/null 2>&1; echo $?'
}
is "a pair written together is accepted"      "$(pair 10 12)"   "0"
is "a rootfs older than boot.img is refused"  "$(pair 10 3600)" "1"
is "no rootfs image at all is refused"        "$(pair 10 none)" "1"
# THE POSITIVE CONTROL for the threshold: a few seconds of ordering inside one
# install must not read as two different runs.
is "seconds of skew inside one install pass"  "$(pair 0 30)"    "0"


echo "test_ph_build.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
