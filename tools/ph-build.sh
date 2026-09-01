# SPDX-License-Identifier: MIT
# shellcheck shell=bash
# ph-build.sh -- envkernel build/flash loop for the active device. SOURCE this.
#
# scope:  generic
# needs: - (host; the device only for tkflash, which takes the mutex itself)
# env:    PORTHOLE_WORKDIR (required), PORTHOLE_KERNEL_PKG, PORTHOLE_DEVICE_PKG
#         PORTHOLE_FW_PKG, PORTHOLE_DTB, PORTHOLE_DEFCONFIG, TK_KPKG
# gives:  tkbuild tkflash tkclean tkpurge-devpkgs
# exits:  0 built and verified - 1 anything else
#
# The function names are kept from the taimen toolbox: they are what everyone
# types and what every runbook prints.
#
# NOTE: any angler/60-postmarket.sh helpers on your PATH build a different
# device entirely. Do not mix them with this.
# (was: The angler helpers in 60-postmarket.sh build linux-616 / msm8994 -- the retired
# device. Do not use them here.
#
#   tkbuild   build kernel -> package -> reindex -> install -> export -> VERIFY
#   tkflash   flash boot.img to both slots, set active, reboot
#
# Two traps are encoded here because both have cost real sessions:
#
# 1. `pmbootstrap build --envkernel` aborts on a stale `umount .output/Makefile`
#    (exit 32) AFTER writing the .apk but BEFORE refreshing APKINDEX. `install`
#    then resolves the kernel from a stale index and packs an OLD vmlinuz behind
#    a FRESH boot.img mtime. So: `index` runs chained, and the exit codes of
#    build/index are deliberately ignored -- they lie.
#
# 2. `index` must run while the chroot from `build` is STILL MOUNTED. pmbootstrap
#    bind-mounts the abuild signing key only while the chroot is active, so a
#    standalone `pmbootstrap index` after a shutdown fails with "can't cd
#    /mnt/pmbootstrap" / "No private key found". Never split them.
#
# Because both failures are invisible in timestamps, tkbuild ends by comparing the
# DTB inside the exported boot.img against the one make just produced, and refuses
# to report success if they differ.

# Sourcing lib/porthole.sh is what makes this file device-neutral: every value
# below comes from the active profile plus your config.env, and nothing here
# knows which phone you own.
# shellcheck source=../lib/porthole.sh
. "$(dirname "${BASH_SOURCE[0]}")/tk-lib.sh"

_PH_REPO=${PORTHOLE_WORKDIR:?set PORTHOLE_WORKDIR to the device working repo (kernel, pmaports, blobs) in ~/.config/porthole/config.env}
# Honour PORTHOLE_KERNEL_TREE the way dts, kconfig and verify already do. The
# series being built is not always on the branch the main checkout happens to
# be sitting on -- it is often a worktree -- and switching the main checkout to
# reach it is how a half-finished branch gets built and flashed.
_PH_TREE=${PORTHOLE_KERNEL_TREE:-$_PH_REPO/linux}
# A RELATIVE value resolves against the device working repo, never against
# whatever pwd this happens to be sourced with. `PORTHOLE_KERNEL_TREE=linux-ws`
# built fine in the workspace and died on --host with `pushd: linux-ws: No such
# file or directory` -- one variable, two meanings, decided by the caller's cwd.
# lib/porthole_cmd_build.py:_tree applies the same rule, and must.
case $_PH_TREE in /*) ;; *) _PH_TREE=$_PH_REPO/$_PH_TREE ;; esac
_PH_PMB=${PORTHOLE_PMB_DIR:-$_PH_PMB}
# pmaports. The device repo normally symlinks it, but that symlink names an
# ABSOLUTE host path, and inside the workspace container the pmbootstrap work
# dir is mounted at /pmb instead -- so the symlink dangles and every workspace
# build died on the config cp below. Fall back to the work dir's own checkout,
# which is the same tree on both sides of the boundary.
# This checkout, for .run -- distinct from _PH_REPO, which is the DEVICE repo.
_PH_REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
_PH_APORTS=$_PH_REPO/pmaports
[ -d "$_PH_APORTS/device" ] || _PH_APORTS=$_PH_PMB/cache_git/pmaports
# The kernel APORT to build. It MUST name the same kernel series the tree is on:
# pmaports carries both linux-postmarketos-qcom-msm8998 (6.0) and -6.18, and
# this said the 6.0 one while syncing its defconfig FROM the 6.18 aport and
# building the 6.18 tree. `pmbootstrap build` then rebuilt a package nothing
# here had touched, left the 6.18 apk at whatever the last real build produced,
# and every later step -- apk add, export, flash -- quietly carried that stale
# kernel. Override with TK_KPKG when working a different series.
_PH_KPKG=${TK_KPKG:-${PORTHOLE_KERNEL_PKG:?profile does not set PORTHOLE_KERNEL_PKG}}
_PH_DEVPKG=${PORTHOLE_DEVICE_PKG:?profile does not set PORTHOLE_DEVICE_PKG}
_PH_FWPKG=${PORTHOLE_FW_PKG:?profile does not set PORTHOLE_FW_PKG}
_PH_DTB=${PORTHOLE_DTB_FILE:?profile does not set PORTHOLE_DTB_FILE}
_PH_OUT="$_PH_TREE/.output"
_PH_DTB_BUILT="$_PH_OUT/arch/${PORTHOLE_ARCH_DIR}/boot/dts/${PORTHOLE_DTB%/*}/$_PH_DTB"
_PH_PKGCONFIG="$_PH_PMB/chroot_rootfs_${PORTHOLE_CODENAME}/boot/config"

# Peel stacked /mnt/linux bind mounts.
#
# Every `source envkernel.sh` bind-mounts the kernel tree onto
# chroot_native/mnt/linux WITHOUT removing the previous one -- `deactivate` does
# not unmount. After a day of build cycles it was stacked 16 deep here.
#
# That is what breaks pmbootstrap, and not in the way the error suggests. The
# `.output/Makefile` overmount sits on whichever /mnt/linux layer was on top when
# it was created; later binds stack ON TOP of it, so the *path*
# /mnt/linux/.output/Makefile now resolves to the newest layer, where nothing is
# mounted. It is still listed in /proc/mounts but is unreachable by name, so
# `umount` returns 32 "not mounted" and pmb.helpers.mount.umount_all aborts the
# whole command -- during build (skipping the reindex) or during install.
#
# So: peel one layer at a time, retrying the Makefile at each level as it
# becomes visible again.
_ph_mnt="$_PH_PMB/chroot_native/mnt/linux"

_ph_depth() { awk -v p="$_ph_mnt" '$2==p' /proc/mounts | wc -l; }

# Confirmed topology (via /proc/self/mountinfo, 2026-07-25): the .output/Makefile
# overmount is a child of the BOTTOM /mnt/linux layer, buried under 15 more. So it
# only becomes reachable after everything above it is peeled -- which is also why
# `pmbootstrap shutdown` cannot recover on its own.
# Where pmbootstrap keeps envkernel.sh. It is not on PATH, its location depends
# on how pmbootstrap was installed, and it was hardcoded to one developer's
# checkout -- which made `scope: generic` in this file's header false for
# everybody else.
_ph_find_envkernel() {
	local c pmb_root
	for c in \
		"${PORTHOLE_ENVKERNEL:-}" \
		"${PORTHOLE_PMBOOTSTRAP_SRC:-}/helpers/envkernel.sh" \
		"$HOME/.local/share/pmbootstrap/helpers/envkernel.sh" \
		"/usr/share/pmbootstrap/helpers/envkernel.sh"
	do
		[ -n "$c" ] && [ -r "$c" ] && { printf '%s\n' "$c"; return 0; }
	done
	# pipx and pip installs put it beside the pmb package.
	pmb_root=$(python3 -c 'import importlib.util as u,pathlib;s=u.find_spec("pmb");print(pathlib.Path(s.origin).parent.parent if s and s.origin else "")' 2>/dev/null)
	if [ -n "$pmb_root" ]; then
		for c in "$pmb_root/helpers/envkernel.sh" "$pmb_root/pmb/helpers/envkernel.sh"; do
			[ -r "$c" ] && { printf '%s\n' "$c"; return 0; }
		done
	fi
	echo ">> cannot find envkernel.sh." >&2
	echo ">> set PORTHOLE_ENVKERNEL to its path, or PORTHOLE_PMBOOTSTRAP_SRC" >&2
	echo ">> to a pmbootstrap checkout, and try again." >&2
	return 1
}

# sudo, unless we already are root.
#
# In the workspace container uid 0 is the user's own unprivileged uid outside,
# and the image ships no sudo at all -- so `sudo -v` there is not a permission
# question, it is `command not found`, and it took out unstacking on the second
# build in a container. On the host nothing changes: a normal user still goes
# through sudo exactly as before.
#
# Only for commands run HERE. The blocks that ssh into the phone keep their own
# sudo: that is the phone's root, not this machine's.
_ph_sudo() {
	if [ "$(id -u)" = 0 ]; then
		"$@"
	else
		sudo "$@"
	fi
}

tkclean() {
	local mk="$_ph_mnt/.output/Makefile"
	local d prev
	d=$(_ph_depth)
	if [ "$d" -eq 0 ]; then
		echo ">> /mnt/linux not mounted -- nothing to unstack"
		return 0
	fi
	echo ">> /mnt/linux is stacked ${d} deep; peeling"
	[ "$(id -u)" = 0 ] || sudo -v || { echo ">> need sudo to unmount"; return 1; }

	for _ in $(seq 1 80); do   # bounded: never spin forever on a stuck mount
		prev=$(_ph_depth)
		[ "$prev" -eq 0 ] && break
		# Try the shadowed Makefile every round; it only succeeds once we reach
		# the bottom layer it is attached to.
		_ph_sudo umount "$mk" 2>/dev/null
		# Do NOT hide this error -- suppressing it is what made the first version
		# of this function report "no progress" with no explanation.
		if ! _ph_sudo umount "$_ph_mnt"; then
			echo ">> plain umount failed; retrying lazily (safe for bind mounts)"
			_ph_sudo umount -l "$_ph_mnt" || { echo ">> lazy umount failed too"; break; }
		fi
		if [ "$(_ph_depth)" -eq "$prev" ]; then
			echo ">> depth stuck at ${prev} -- not making progress"
			break
		fi
	done

	_ph_sudo umount "$mk" 2>/dev/null
	d=$(_ph_depth)
	echo ">> /mnt/linux now mounted ${d}x"
	if [ "$d" -ne 0 ]; then
		echo ">> still stacked. Inspect with:"
		echo "     awk -v p=$_ph_mnt '\$5==p' /proc/self/mountinfo"
		return 1
	fi
	# The Makefile overmount hangs off the bottom layer, so it must be gone too.
	if awk -v p="$mk" '$5==p' /proc/self/mountinfo | grep -q .; then
		echo ">> WARNING: .output/Makefile overmount still present"
		return 1
	fi
	return 0
}

# Say which tree is about to be built, before building it.
#
# Every verb honours PORTHOLE_KERNEL_TREE and defaults to $repo/linux, and on
# a mature port the main checkout is very often sitting on some unrelated
# branch while the work lives in a worktree. Forgetting the variable does not
# fail -- it quietly builds the wrong source and pushes it to the phone. That
# happened on 2026-08-27: a `porthole build mod` without it built msm.ko from
# the main checkout's branch, installed it to /lib/modules, and reported
# success. envkernel does print the path, but buried in its own banner among
# twenty other lines.
#
# One line, at the top, naming the branch as well as the path -- the branch is
# what makes "that is not the tree I meant" obvious at a glance.
# `pmbootstrap build` zaps before every package and --lax skips that. This
# block used to claim the zap was most of the wall clock in the flashing rungs.
# MEASURED 2026-08-29 and it is not, on a warm buildroot: the device package
# took 1.66-1.72 s with --lax and without, alternating.
#
# The kernel row of that measurement (14.96 / 15.34 / 15.25 / 14.68 s) proved
# nothing and has been dropped: `pmbootstrap build --envkernel` returns from
# pmb/commands/build.py BEFORE the strict-mode zap block, so --lax cannot
# reach that path at all. On line 450 below the flag is inert by construction,
# not merely unhelpful. The three package builds are where it is live.
#
# Also worth knowing, because the name misleads: zap_buildroots() deletes
# chroot_native as well as chroot_buildroot* (pmb/chroot/zap.py). So a
# non-lax package build takes the toolchain the next envkernel compile would
# have used with it -- which is why a "chroot re-init" shows up as a full
# kernel rebuild, and why _ph_arm_ccache re-arms on every activate.
#
# So the knob buys nothing measurable while it accepts something real. This
# repo has been bitten repeatedly by stale build state -- the _p apk that
# outranks a release, the stale APKINDEX that makes install pick an older
# package -- and each one presented as a mysterious wrong-kernel bug rather
# than as a caching problem.
#
# Left in place because it is one line and may still pay on a genuinely cold
# buildroot, which is the one case not measured. Do not reach for it while
# iterating; reach for the right rung, which is worth minutes rather than
# seconds. See brain/findings/lax-build-buys-nothing-measurable.md.
# An array, not a command substitution: unquoted $(...) is a word-splitting
# bug waiting to happen, and quoting it would pass an empty argument through
# when the variable is unset.
_PH_LAX=()
[ -n "${PORTHOLE_LAX_BUILD:-}" ] && _PH_LAX=(--lax)

# ...and in the WORKSPACE it is not a choice. A non-lax `pmbootstrap build`
# cannot run there at all: zap_buildroots() umounts the chroot, and the
# recursive /dev bind the rootless workspace needs (the mknod shim; --rbind is
# not optional, a plain --bind gives 0:0 and pmbootstrap rejects it) leaves
# PROPAGATED sub-mounts that a rootless userns cannot umount by path --
#
#   umount: /pmb/chroot_native/dev/shm: not mounted.   (exit 32)
#
# -- and pmbootstrap raises on that before building anything. Verified by
# running each once and slicing pmbootstrap's log: nolax dies at "Zapping
# buildroots", --lax gets past it and the whole queue completes.
#
# So this is not the speed-for-staleness trade the block above weighs. Without
# it the packaging rungs simply do not exist in the workspace. It is still a
# real trade -- upstream made strict the default FOR correctness (e14f4169, MR
# 2939) -- and what covers us is that porthole guards the specific staleness
# that has actually bitten this repo: _ph_assert_no_devpkgs refuses an
# envkernel _p apk that would outrank a release, tkpurge-devpkgs clears them,
# and _ph_make verifies the apk is newer than the Image.gz it packaged.
#
# Detected by a file only this image installs, rather than by a path
# convention that would silently stop matching. `--host` is unaffected.
[ -x /usr/local/bin/porthole-devnodes ] && _PH_LAX=(--lax)

_ph_announce_tree() {
	local branch="" head=""
	if [ -d "$_PH_TREE/.git" ] || [ -f "$_PH_TREE/.git" ]; then
		head=$(git -C "$_PH_TREE" rev-parse --abbrev-ref HEAD 2>/dev/null)
		branch=" [${head:-detached} $(git -C "$_PH_TREE" rev-parse --short HEAD 2>/dev/null)]"
	fi
	echo ">> tree: $_PH_TREE$branch"

	[ -n "${PORTHOLE_KERNEL_TREE:-}" ] ||
		echo ">>       (default; set PORTHOLE_KERNEL_TREE to build a worktree)"

	# The one thing that reads PORTHOLE_KERNEL_BRANCH, and the reason it now
	# has a reader at all. The key is documentation -- "the branch that is the
	# product" -- and on 2026-08-29 the 6.18 -> 7.2 move updated every key a
	# build consumes and left this one naming the old branch for a day. An
	# unread key has no build to fail it, so it rots silently; one line that
	# reads it is a smaller fix than a checker that watches it rot.
	#
	# Only when a branch NAME resolved. A worktree seen from inside the
	# workspace container is detached as far as git there is concerned, and a
	# warning that fires on every containerised build is a warning nobody
	# reads. `porthole doctor` catches the version-token case from either side.
	if [ -n "${PORTHOLE_KERNEL_BRANCH:-}" ] && [ -n "$head" ] &&
	   [ "$head" != HEAD ] && [ "$head" != "$PORTHOLE_KERNEL_BRANCH" ]; then
		echo ">> NOTE: profile says the product branch is $PORTHOLE_KERNEL_BRANCH"
		echo ">>       this tree is on $head -- one of the two is stale"
	fi

	# Say it out loud when the tree and the phone are different kernels.
	# Both facts were already printed by other commands and nobody put them
	# together: on 2026-08-29 the tree sat on v6.18 while the device ran
	# 7.2.2, and the only thing that would have caught a `mod` push from it
	# is MODVERSIONS refusing the result with "disagrees about version of
	# symbol module_layout" -- a message about a symbol, not about the tree
	# being two releases behind the phone.
	local tv dv
	tv=$(awk -F' = ' '/^VERSION/{v=$2} /^PATCHLEVEL/{p=$2}
	                  END{if (v != "") print v "." p}' "$_PH_TREE/Makefile" 2>/dev/null)
	dv=$(TK_RUN_TIMEOUT=8 tk_run 'uname -r' 2>/dev/null | tr -d '\r\n')
	if [ -n "$tv" ] && [ -n "$dv" ] && [ "${dv%.*}" != "$tv" ]; then
		echo ">> WARNING: this tree is Linux $tv, the device runs $dv"
		echo ">>          a module built here will not load there"
	fi
}

# Does the tree defconfig already match the aport, with a build newer than it?
#
# The resync in _ph_measure costs 4.92 s of chroot round trip on EVERY build
# (measured 2026-08-29) and most builds have not touched the config at all.
#
# Skipping is only safe because the test is byte equality: if the file the `cp`
# would write is ALREADY identical to its destination, not copying it cannot
# change what gets built. That is what keeps the 2026-08-08 failure out of
# reach -- there the config came from the WRONG aport, which `cmp` catches.
#
# Every uncertain case returns false and resyncs. A missing file, a tree that
# has never been built, no `.config` at all: all resync. Failing open costs
# 4.92 s; failing closed costs a silently mis-configured kernel.
_ph_defconfig_current() {
	local aport dc
	aport="$_PH_APORTS/device/testing/$_PH_KPKG/${PORTHOLE_KCONFIG_FILE:-config-postmarketos-${PORTHOLE_SOC}.${PORTHOLE_ARCH}}"
	dc="$_PH_TREE/arch/${PORTHOLE_ARCH_DIR}/configs/${PORTHOLE_DEFCONFIG}"
	[ -f "$aport" ] && [ -f "$dc" ] && cmp -s "$aport" "$dc" &&
		[ -f "$_PH_OUT/.config" ] && [ "$_PH_OUT/.config" -nt "$dc" ]
}

# Put ccache in the path of the compile. Three things have to be true at once
# and NONE of them were, which is why a cache directory existed for months and
# was never written to.
#
#  1. The compiler must live where the cache does. The compile runs as pmos
#     inside chroot_native -- x86_64, cross-compiling with clang -- not in the
#     workspace image and not in an aarch64 chroot. So the ccache that matters
#     is the one installed INSIDE chroot_native; the image's own package sits
#     on a different rootfs and could never be reached. It also explains the
#     `cache_ccache_aarch64` that "never appeared after an aarch64 build": it
#     never could, the chroot doing the work is the native one.
#  2. CCACHE_DISABLE=1 must be gone. envkernel bakes it into its make alias;
#     sandbox/Containerfile rewrites that line to CCACHE_DIR. On the HOST path
#     (`--host`) the upstream helper is untouched, so this function arms a
#     cache host builds still will not use. Not worth forking a checkout that
#     is not ours; the workspace is the supported path.
#  3. clang must be reachable THROUGH ccache. /usr/lib/ccache/bin is first on
#     the chroot PATH (pmb/config/__init__.py) but Alpine's ccache ships
#     masquerade symlinks for gcc/cc/g++/c++ only -- no clang. An LLVM=1 build
#     walks straight past it without the two symlinks below.
#
# Re-armed on every activate rather than once, because `pmbootstrap build`
# without --lax calls zap_buildroots(), and that deletes chroot_native itself,
# not just the buildroots the name suggests. Every flashing rung would
# otherwise disarm the cache. The guard is a plain path test rather than a
# chroot round trip, so the normal case -- already armed -- costs nothing.
#
# Never fails the build. A cache is an optimisation; a build that refuses
# because one could not be set up is strictly worse than an uncached build.
# PORTHOLE_NO_CCACHE=1 skips it entirely.
_ph_arm_ccache() {
	[ -n "${PORTHOLE_NO_CCACHE:-}" ] && return 0
	# Point 2, TESTED rather than assumed by asking which envkernel is about
	# to be sourced. One that still carries CCACHE_DISABLE=1 compiles uncached
	# whatever is installed, so arming a cache for it is work that buys
	# nothing -- and on the host path it would also be installing a package
	# into somebody's own chroot for no gain. If a host checkout is ever
	# patched the same way, this starts arming it with no further change.
	local ek
	ek=$(_ph_find_envkernel 2>/dev/null) || return 0
	grep -q CCACHE_DISABLE "$ek" 2>/dev/null && return 0
	[ -e "$_PH_PMB/chroot_native/usr/lib/ccache/bin/clang" ] && return 0
	echo ">> arming ccache in chroot_native (once per chroot)"
	pmbootstrap -q chroot -- apk add ccache >/dev/null 2>&1 || {
		echo ">>   could not install ccache in the chroot -- compiling uncached"
		return 0; }
	# RELATIVE, like the symlinks apk puts next to them, and not by taste: the
	# guard above stats this path from OUTSIDE the chroot, where an absolute
	# /usr/bin/ccache resolves to the container's own filesystem and is not
	# there. An absolute link is dangling to `[ -e ]`, so the guard never
	# fired and every single build paid another chroot round trip to re-arm
	# something already armed.
	pmbootstrap -q chroot -- sh -c \
		'for c in clang clang++; do ln -sf ../../../bin/ccache /usr/lib/ccache/bin/$c; done' \
		>/dev/null 2>&1 ||
		echo ">>   could not link clang into the ccache dir -- compiling uncached"
	return 0
}

# Callable without envkernel, so the PACKAGE path can arm the same cache. The
# kernel rungs have had native ccache since the chroot_native fix; package
# builds run ccache as the emulated aarch64 binary in front of the compiler
# and pay qemu for hashing every preprocessed source.
# PORTHOLE_CCACHE_STANDALONE is the marker tests/test_ph_build.sh looks for.
if [ -n "${PORTHOLE_CCACHE_STANDALONE:-}" ]; then
	_ph_arm_ccache
	return 0 2>/dev/null || exit 0
fi

# Bring envkernel up and LEAVE THE CALLER IN $_PH_TREE. The caller must popd.
#
# The asymmetry is deliberate: `make` is an alias envkernel defines, aliases
# expand at parse time, and the caller has to `eval make` in the tree. One
# function that both pushed and popped could not host the build.
#
# This block was copy-pasted in three places and every line of it is
# load-bearing, which is the worst combination. The `deactivate` before the
# re-source is there because a stale POSTMARKETOS_ENVKERNEL_ENABLED makes a
# guarded re-source skip the remount, and `make` then finds no Makefile -- a
# harness written while measuring this file reproduced exactly that by leaving
# it out, and read it as a pmbootstrap bug for three runs.
_ph_activate() {
	# Clear any stacked /mnt/linux binds BEFORE adding another one, or
	# pmbootstrap will abort on the shadowed .output/Makefile overmount.
	tkclean || { echo ">> could not unstack /mnt/linux -- run 'pmbootstrap shutdown' and retry"; return 1; }
	_ph_arm_ccache
	type deactivate >/dev/null 2>&1 && deactivate
	pushd "$_PH_TREE" >/dev/null || return 1
	set --   # `source` would pass our args to envkernel, which rejects them
	_ph_envkernel="$(_ph_find_envkernel)" || { popd >/dev/null || return 1; return 1; }
	source "$_ph_envkernel" || { popd >/dev/null || return 1; return 1; }
	_ph_claim_output || { popd >/dev/null || return 1; return 1; }
}

# Make .output writable by the chroot user that is about to build in it.
#
# `make` runs INSIDE the chroot as pmos, while .output lives in the kernel tree
# on the host. envkernel chowns it to pmos -- but only in create_output_folder,
# which returns early when .output already exists. So a tree that has been
# built in one environment cannot be built in another: the uids do not line up.
#
# That bites the moment the workspace container is used on a tree the host
# built, because a rootless userns maps your uid and nothing else -- the host's
# pmos (12345) is not the container's pmos. It shows up as pages of
# `mkdir: can't create directory '.tmp_174': Permission denied` from kbuild,
# which names neither .output nor ownership.
#
# One chown per (tree, environment), remembered in .run so the cost is paid
# once rather than per build. Deliberately NOT stored under .output itself:
# that is the directory we may not be able to write yet.
_ph_claim_output() {
	[ -d "$_PH_OUT" ] || return 0      # envkernel creates AND chowns it itself
	local rundir stamp
	rundir=${PORTHOLE_RUNDIR:-$_PH_REPO_ROOT/.run}
	stamp="$rundir/output-owner-$(printf '%s|%s' "$_PH_TREE" "$_PH_PMB" | cksum | cut -d' ' -f1)"
	[ -f "$stamp" ] && return 0
	echo ">> claiming $_PH_OUT for this build environment (one time)"
	if command pmbootstrap -q chroot -- chown -R pmos:pmos /mnt/linux/.output; then
		mkdir -p "$rundir" && : > "$stamp"
		return 0
	fi
	# Not a fixable permission problem. A rootless container maps YOUR uid and
	# nothing else, so a .output built on the host is full of files owned by a
	# uid that does not exist in here -- they read as `nobody`, and chown needs
	# a uid it can see. The same is true in reverse.
	#
	# .output is a build cache and nothing else: no source, no config you
	# cannot regenerate. Deleting it costs one full build and is the only
	# thing that actually works. Say so; do not do it silently, because that
	# full build is ten minutes nobody asked for.
	echo ">> REFUSING: $_PH_OUT was built in a different environment" >&2
	echo ">>   and cannot be re-owned here -- the uids inside a rootless" >&2
	echo ">>   container do not line up with the host's." >&2
	echo ">>   It is a build cache -- no sources, no config you cannot" >&2
	echo ">>   regenerate. Three ways out, in order of least surprise:" >&2
	echo ">>     porthole build --host        build where it came from" >&2
	echo ">>     PORTHOLE_KERNEL_TREE=<a fresh worktree> porthole build" >&2
	echo ">>     sudo rm -rf $_PH_OUT   then rebuild here (one full build)" >&2
	echo ">>   The last one needs YOUR root: the files belong to a uid this" >&2
	echo ">>   container cannot see, so nothing in here can remove them." >&2
	return 1
}

# Build the TREE and verify what came out. No packaging -- see _ph_make.
#
# `porthole build auto` calls this to answer one question: which files did make
# touch. The router reads .ko, .dtb and Image.gz out of .output and never opens
# an apk, so packaging on that path was 14.66 s of pure cost -- and it wrote a
# `_p` apk every time, which is precisely what _ph_assert_no_devpkgs exists to
# refuse. The preview was manufacturing the hazard the rungs guard against.
_ph_measure() {
	_ph_announce_tree
	local out="$_PH_TREE/.output"
	local img="$out/arch/${PORTHOLE_ARCH_DIR}/boot/Image.gz"
	local dtb="$out/arch/${PORTHOLE_ARCH_DIR}/boot/dts/${PORTHOLE_DTB%/*}/$_PH_DTB"
	local dtsdir="$_PH_TREE/arch/${PORTHOLE_ARCH_DIR}/boot/dts/${PORTHOLE_DTB%/*}"

	# Every rung compiles through here, and the cp below overwrites the tree's
	# defconfig with the APORT's. Different kernel series means that is a silent
	# clobber which dies later somewhere unrelated: on 2026-08-29 the 7.2 config
	# went into the 6.18 tree and the build failed in drivers/gpu/drm with
	# "unable to open output file". tkbuild-kernel already checked this; every
	# other rung did not, and a fresh session with no PORTHOLE_KERNEL_PKG in its
	# environment is exactly the one that gets the profile's mismatched default.
	if ! _ph_tree_matches_aport; then
		echo ">> REFUSING: tree is Linux $_PH_TREE_V but $_PH_KPKG is $_PH_APORT_V" >&2
		echo ">>   set PORTHOLE_KERNEL_PKG to the aport for this tree, or rebase the tree." >&2
		return 1
	fi

	# Re-sync the in-tree defconfig from pmaports so both can't drift.
	# MUST be the aport config for the series the tree is on. Syncing from the
	# 6.0 aport config (as this line did until 2026-08-09) clobbers 6.18-only
	# symbols with their pre-rename 6.0 names, which olddefconfig then drops
	# SILENTLY: CONFIG_QCOM_QFPROM=y instead of CONFIG_NVMEM_QCOM_QFPROM=y
	# killed the qusb2 fuse cell and with it ALL USB. Cost the night of 2026-08-08.
	#
	# The two halves are not adjacent -- the copy happens here, the
	# `make <defconfig>` after envkernel is up -- so the decision is made once
	# and consulted twice. NOT copying is also what keeps the skip stable: the
	# defconfig's mtime stays put, so .config stays newer than it.
	local skip_defconfig=
	if _ph_defconfig_current; then
		skip_defconfig=1
		echo ">> defconfig unchanged -- skipping the resync"
	else
		cp "$_PH_APORTS/device/testing/$_PH_KPKG/${PORTHOLE_KCONFIG_FILE:-config-postmarketos-${PORTHOLE_SOC}.${PORTHOLE_ARCH}}" \
		   "$_PH_TREE/arch/${PORTHOLE_ARCH_DIR}/configs/${PORTHOLE_DEFCONFIG}" || return 1
	fi

	_ph_activate || return 1

	# envkernel provides `make` as an ALIAS carrying ARCH=arm64 and the chroot
	# invocation. Bash expands aliases at PARSE time, and this function was parsed
	# when ph-build.sh was sourced -- before envkernel ran -- so a bare `make`
	# here resolves to the host binary instead, builds for x86 and dies with
	# `Can't find default configuration "arch/x86/configs/$PORTHOLE_DEFCONFIG"`.
	# eval re-parses at runtime, once the alias exists.
	shopt -s expand_aliases
	if [ -z "$skip_defconfig" ]; then
		eval make "$PORTHOLE_DEFCONFIG" || { popd >/dev/null || return 1
			echo ">> defconfig FAILED"; return 1; }
	fi
	eval make -j"$(nproc)"; local mk=$?
	# This file is SOURCED, so a failed popd would strand the user's own
	# interactive shell in the kernel tree.
	popd >/dev/null || return 1

	# make's verdict, FIRST. This used to be discarded on a claim that BTF prep
	# re-runs return non-zero with nothing wrong, leaving the artifact checks
	# below as the only judge -- and they cannot see a failed compile. An object
	# that does not build writes no file, so Image.gz stays put, .config is not
	# newer than it, no dts is newer than its dtb: every check passes, `auto`
	# finds nothing changed since the last push and prints "make rebuilt nothing"
	# and exits 0. A broken tree read as an up-to-date one. Cost a DRM port an
	# hour of looking in the wrong module (#22).
	#
	# The checks below stay: they answer the opposite question -- make exiting 0
	# having produced nothing usable -- and neither one covers the other.
	if [ "$mk" -ne 0 ]; then
		echo ">> make exited $mk -- the build FAILED, the errors are above" >&2
		return 1
	fi

	if [ ! -s "$img" ]; then
		echo ">> no Image.gz at $img -- real build failure"; return 1
	fi
	if [ "$out/.config" -nt "$img" ]; then
		echo ">> Image.gz older than .config -- a config change did not compile"; return 1
	fi
	# Which .dtsi files this board pulls in is a per-device fact, so it comes
	# from the profile. Default: the board .dts itself. A device whose SoC dtsi
	# is edited without this set gets a dtb that silently did not rebuild --
	# which is what PORTHOLE_DTS_DEPS exists to prevent.
	local _stale=""
	for _dep in ${PORTHOLE_DTS_DEPS:-} "${_PH_DTB%.dtb}.dts"; do
		[ -e "$dtsdir/$_dep" ] || continue
		[ "$dtsdir/$_dep" -nt "$dtb" ] && _stale="$_dep"
	done
	if [ -n "$_stale" ]; then
		echo ">> $_stale is newer than the dtb"
		echo ">> dtb older than its DTS -- it did not rebuild (check dtc output)"; return 1
	fi
	_ph_stamp_write
}

# _ph_measure, then package what it built. Everything downstream of a flashing
# rung needs the apk; `auto` does not, and calls _ph_measure directly.
# One `pmbootstrap build`, with the upstream bug that stops every FRESH
# workspace on its first packaging rung worked around.
#
# THE BUG. A dependency whose APKBUILD arch resolves to the NATIVE arch --
# `arch="noarch"` or `arch="all"` -- inside a build for a FOREIGN device arch
# gets its cross mode computed against the device (aarch64: emulation needed,
# so CROSS_NATIVE2 or CROSSDIRECT, cross ENABLED) while its own pkg_arch
# resolves to native. pmb/build/_package.py then calls
# `init_compiler(cross, pkg_arch)` and pmb/build/init.py asks for
# `gcc-<native>` + `g++-<native>`: a cross compiler TO the native arch. That
# package cannot exist -- nobody cross-compiles x86_64 from x86_64 -- so apk
# answers "unable to select packages" and the rung dies having built nothing.
# pmb/build/autodetect.py:52 is where the two archs stop agreeing.
#
# WHY IT LOOKS LIKE BAD LUCK. It only fires when such a dependency actually
# needs BUILDING. A machine that has built it once has it in its local repo
# forever after, which is why the reference host never saw this and every new
# workspace does. On taimen it is `pinephone-callaudiod` (noarch) and then
# `rmtfs` (all) -- two of the five packages behind `device-google-taimen`.
#
# THE FIX IS UPSTREAM'S OWN LOGIC. Naming the device arch explicitly makes
# pkg_arch and cross agree again: it then asks for `gcc-aarch64`, which does
# exist in the binary repo, and the package builds. Verified 2026-08-30.
#
# Bounded, and it re-runs the ORIGINAL command each pass rather than assuming
# one offender: the queue can hold several, and a loop that trusts a failing
# command to change its mind on its own is how a build spins for an hour.
_ph_pmb_build() {
	local pkg=$1 out plain rc dep native tries=0
	native=$(uname -m)
	while :; do
		out=$(pmbootstrap build "${_PH_LAX[@]}" "$pkg" 2>&1); rc=$?
		printf '%s\n' "$out"
		[ "$rc" -eq 0 ] && return 0
		# Colour codes are in the real output and would break both greps.
		plain=$(printf '%s\n' "$out" | sed 's/\x1b\[[0-9;]*m//g')
		printf '%s\n' "$plain" | grep -q "gcc-$native" || return "$rc"
		if [ "$tries" -ge 8 ]; then
			echo ">> still asking for gcc-$native after $tries rebuilds -- giving up" >&2
			return "$rc"
		fi
		dep=$(printf '%s\n' "$plain" |
			grep -oE '\([0-9]+/[0-9]+\) [^ ]+/[^ :]+' | tail -1 | sed 's|.*/||')
		[ -n "$dep" ] || return "$rc"
		tries=$((tries + 1))
		echo ">> $dep is arch-independent, and pmbootstrap asked for gcc-$native to"
		echo ">>   build it -- a cross compiler to the native arch, which does not"
		echo ">>   exist. Upstream bug; naming the device arch is what it should"
		echo ">>   have done. Building it that way, then retrying:"
		echo ">>   pmbootstrap build --arch ${PORTHOLE_ARCH} $dep"
		pmbootstrap build "${_PH_LAX[@]}" --arch "${PORTHOLE_ARCH}" "$dep" || return "$rc"
	done
}

_ph_make() {
	_ph_measure || return 1
	local out="$_PH_TREE/.output"
	local img="$out/arch/${PORTHOLE_ARCH_DIR}/boot/Image.gz"
	echo ">> kernel + dtb current -- packaging"

	# Exit codes below lie (benign umount-32). Keep them chained; see header.
	# They lie in one direction only, so verify the artifact instead: a build
	# that produced no apk newer than the Image.gz it was meant to package is
	# a stale build, and everything downstream would carry the old kernel.
	local kapk_before kapk_after
	kapk_before=$(ls -t "$_PH_PMB"/packages/edge/${PORTHOLE_ARCH}/"$_PH_KPKG"-*.apk 2>/dev/null | head -1)
	pmbootstrap build "${_PH_LAX[@]}" --envkernel "$_PH_KPKG"
	kapk_after=$(ls -t "$_PH_PMB"/packages/edge/${PORTHOLE_ARCH}/"$_PH_KPKG"-*.apk 2>/dev/null | head -1)
	if [ -z "$kapk_after" ]; then
		echo ">> no $_PH_KPKG apk was produced -- the kernel package did not build"
		return 1
	fi
	if [ "$kapk_after" = "$kapk_before" ] && [ "$img" -nt "$kapk_after" ]; then
		echo ">> $_PH_KPKG apk is older than the kernel just built -- stale package"
		echo ">>   apk:   $kapk_after"
		return 1
	fi
	# Firmware BEFORE the device package: device-google-taimen-nonfree-firmware
	# depends on it, and install fails with "no such package" if it is not built
	# and indexed first.
	_ph_pmb_build "$_PH_FWPKG"
	_ph_pmb_build "$_PH_DEVPKG"
	pmbootstrap index
}

# Envkernel builds version themselves `<ver>_p<timestamp>-r0`, and apk sorts that
# ABOVE a release `<ver>-rNN`. So a single stale envkernel apk left in the local
# repo silently wins every dependency resolution -- `pmbootstrap install`, an
# `apk add` in the rootfs chroot, everything -- and you flash a kernel from days
# ago while every log line says you built a fresh one. On 2026-08-19 there were
# 183 of them and `install` picked `6.18_p20260818144641-r0` over `6.18-r26`.
#
# There is no apk-level fix: the version comparison is doing exactly what it is
# documented to do. The only defence is to not leave them in the repo.
tkpurge-devpkgs() {
	local repo="$_PH_PMB/packages/edge/${PORTHOLE_ARCH}"
	local stale
	stale=$(ls "$repo"/${_PH_KPKG}*_p*.apk 2>/dev/null | wc -l)
	[ "$stale" -eq 0 ] && return 0
	echo ">> purging $stale envkernel (_p) kernel apks that would outrank the release build"
	_ph_sudo mkdir -p "$repo/.stale-devpkgs" || return 1
	_ph_sudo sh -c "mv '$repo'/${_PH_KPKG}*_p*.apk '$repo/.stale-devpkgs'/" || return 1
	pmbootstrap index || return 1
}

# Refuse to proceed if an envkernel package could outrank the release build.
_ph_assert_no_devpkgs() {
	local repo="$_PH_PMB/packages/edge/${PORTHOLE_ARCH}"
	local stale
	stale=$(ls "$repo"/${_PH_KPKG}*_p*.apk 2>/dev/null | wc -l)
	if [ "$stale" -ne 0 ]; then
		echo "REFUSING: $stale envkernel (_p) kernel apks are in the local repo." >&2
		echo "apk sorts _p<timestamp> ABOVE -rNN, so one of those would be installed" >&2
		echo "instead of what you just built. Run 'porthole build purge' first." >&2
		return 1
	fi

	# Purging the repo is NOT enough. An envkernel _p already INSTALLED in the
	# rootfs chroot outranks every release, so `apk add -U -u` will not replace
	# it -- it is an upgrade request and -r28 is a downgrade from
	# _p20260820013151-r0. Measured on 2026-08-20: the repo was clean, tkpurge
	# reported zero, and `pmbootstrap export` still shipped a kernel from a
	# 01:31 envkernel build, missing every config symbol added that morning.
	# apk info -W /boot/vmlinuz is what catches it; timestamps do not.
	local croot="$_PH_PMB/chroot_rootfs_${PORTHOLE_CODENAME}"
	local owner
	[ -e "$croot/boot/vmlinuz" ] || return 0
	owner=$(pmbootstrap chroot -r -- apk info -W /boot/vmlinuz 2>/dev/null |
		sed -n 's/.*owned by //p')
	case "$owner" in
	*_p[0-9]*)
		echo "REFUSING: the rootfs chroot has an envkernel kernel installed:" >&2
		echo "  $owner" >&2
		echo "apk will not downgrade it to a release, so export/flash would carry" >&2
		echo "that kernel. Pin the release explicitly:" >&2
		echo "  pmbootstrap chroot -r -- apk add --allow-untrusted \\" >&2
		echo "      '$_PH_KPKG=<pkgver>-r<pkgrel>'" >&2
		return 1
		;;
	esac
	return 0
}

# Install the kernel aport into the rootfs chroot PINNED to its release version.
# `apk add -U -u <pkg>` is not safe here: it is an upgrade, and an installed
# envkernel _p outranks every release, so the upgrade silently does nothing.
# Install the RELEASE kernel -- the aport's <pkgver>-r<pkgrel> -- into the
# rootfs chroot.
#
# It must be the aport build, not the tree. This device's /boot/vmlinuz is owned
# by linux-...-6.18-r89, an aport package, and the tree is allowed to diverge
# from the series: tools/tk-reconcile.sh reported 43 differing files on
# 2026-08-27. Installing a tree build here would flash a materially different
# kernel -- see brain/traps/never-flash-a-tree-built-kernel-when-the-device-
# ships-from-an-aport.md, which cost a session on 2026-08-25.
#
# The aport has to have been BUILT, though, and nothing upstream of here builds
# it: _ph_make only runs `pmbootstrap build --envkernel`, which packages the
# tree under a dev version (6.18_p<timestamp>-r0) and never the release. So a
# pkgrel bump with no build -- exactly the state on 2026-08-27, aport at r91,
# local repo stopping at r89 -- reached apk as:
#
#   ERROR: unable to select packages:
#     linux-postmarketos-qcom-msm8998-6.18-6.18-r89:
#       breaks: world[linux-postmarketos-qcom-msm8998-6.18=6.18-r91]
#
# which names neither the missing version nor what to do about it. Build it if
# it is not there, and say so plainly if it still is not.
_ph_install_kernel_release() {
	local ver
	# shellcheck disable=SC2154  # pkgver/pkgrel are set by the sourced APKBUILD
	ver=$(. "$_PH_APORTS/device/testing/$_PH_KPKG/APKBUILD" 2>/dev/null
	      echo "$pkgver-r$pkgrel")
	[ -n "$ver" ] && [ "$ver" != "-r" ] || { echo ">> could not read $_PH_KPKG pkgver/pkgrel" >&2; return 1; }

	local repo="$_PH_PMB/packages/edge/${PORTHOLE_ARCH}"
	if [ ! -f "$repo/$_PH_KPKG-$ver.apk" ]; then
		echo ">> $_PH_KPKG-$ver.apk is not in the local repo -- building the aport"
		echo ">>   (the newest there is: $(ls -t "$repo/$_PH_KPKG"-*.apk 2>/dev/null | head -1 | xargs -r basename))"
		# _ph_make has just run `pmbootstrap build --envkernel`, so the repo now
		# holds a 6.18_p<timestamp>-r0 apk -- and apk sorts _p<timestamp> ABOVE
		# -rNN. pmbootstrap compares against that, decides the package is "up to
		# date" and builds nothing, so the release never appears and this check
		# fires twice. The fast cycle poisons its own repo on every run; purge
		# before building, which is exactly what tkpurge-devpkgs is for.
		tkpurge-devpkgs || return 1
		_ph_pmb_build "$_PH_KPKG" || return 1
	fi
	[ -f "$repo/$_PH_KPKG-$ver.apk" ] || {
		echo ">> still no $_PH_KPKG-$ver.apk after building." >&2
		echo ">> The APKBUILD says pkgrel=${ver##*-r}; check the series applies" >&2
		echo ">> (tools/tk-reconcile.sh) and that the build actually succeeded." >&2
		return 1; }

	echo ">> installing $_PH_KPKG=$ver into the rootfs chroot"
	_PH_INSTALLED_APK="$repo/$_PH_KPKG-$ver.apk"
	pmbootstrap chroot -r -- apk add -U --allow-untrusted "$_PH_KPKG=$ver" || return 1
	pmbootstrap chroot -r -- apk info -W /boot/vmlinuz 2>/dev/null | sed -n 's/.*owned by //p' |
		grep -q -- "-r${ver##*-r}$" || {
		echo ">> /boot/vmlinuz is STILL not $ver after install -- refusing" >&2; return 1; }
}

# Refuse to build a kernel that is missing a fix which is already written.
#
# On 2026-08-20 the phone took a kernel panic on camera close. The fix for that
# exact panic -- 943ebe467ccf, carrying the identical oops in its own commit
# message -- had been committed the day before, on tk618/suspend-gsi-stop, and
# was not an ancestor of the branch that got built. The aport series had it
# (patch 0119); the dev branch did not. Nothing warned, and the crash was
# re-diagnosed from scratch.
#
# Add a SHA here whenever a fix must not be lost again. Cheap, and it is
# precisely the check that would have caught that one.
_PH_MUST_SHIP="
943ebe467ccf
"

_ph_assert_must_ship() {
	local sha missing=0
	[ -d "$_PH_TREE/.git" ] || return 0
	for sha in $_PH_MUST_SHIP; do
		git -C "$_PH_TREE" rev-parse --verify --quiet "$sha^{commit}" >/dev/null || continue
		# Ancestry by SHA is the fast path, but a cherry-pick onto another
		# branch rewrites the SHA -- so fall back to comparing patch-ids,
		# which is what actually answers "is this change present".
		git -C "$_PH_TREE" merge-base --is-ancestor "$sha" HEAD 2>/dev/null && continue
		local want have
		want=$(git -C "$_PH_TREE" show "$sha" | git patch-id --stable | cut -d" " -f1)
		have=$(git -C "$_PH_TREE" log --format=%H "$sha..HEAD" 2>/dev/null |
			while read -r c; do
				git -C "$_PH_TREE" show "$c" | git patch-id --stable | cut -d" " -f1
			done | grep -Fx "$want")
		[ -n "$have" ] && continue
		echo "REFUSING: $sha is not present on HEAD ($(git -C "$_PH_TREE" rev-parse --abbrev-ref HEAD))" >&2
		echo "  $(git -C "$_PH_TREE" log -1 --format=%s "$sha" 2>/dev/null)" >&2
		missing=1
	done
	[ "$missing" -eq 0 ] && return 0
	echo "" >&2
	echo "Cherry-pick it, or build from the aport instead:" >&2
	echo "  pmbootstrap build linux-postmarketos-qcom-msm8998-6.18" >&2
	return 1
}

# FULL cycle. `install` runs mkfs and REMINTS the filesystem UUIDs, so it must be
# paired with tkflash (rootfs + boot). Use when the rootfs itself changed, or when
# the device's rootfs and boot have desynced.
tkbuild() {
	_ph_assert_no_devpkgs || return 1
	_ph_assert_must_ship || return 1
	_ph_make || return 1
	# _ph_make just ran `pmbootstrap build --envkernel`, which packages the tree
	# under a dev version (<ver>_p<timestamp>-r0) and indexes it -- so the repo
	# that was clean when _ph_assert_no_devpkgs ran above is not clean any more.
	# apk sorts _p<timestamp> ABOVE -rNN, so the `pmbootstrap install` below
	# would resolve the world file's release request against that apk instead.
	# Reproduced on a real build 2026-08-31: `install` refused with
	#   ERROR: unable to select packages:
	#     linux-postmarketos-qcom-msm8998-7.2-7.2.2-r22:
	#       breaks: .pmbootstrap-...[linux-postmarketos-qcom-msm8998-7.2><...]
	# against packages/edge/aarch64/linux-...-7.2-7.2.2_p20260831223057-r0.apk --
	# an apk the SAME build had produced three minutes earlier. Same hazard
	# _ph_install_kernel_release documents and purges before its own build
	# (~line 746); tkbuild has to purge here for the same reason.
	tkpurge-devpkgs || return 1
	# Password comes from the environment so it is not committed. Set it once:
	#   export TK_PMOS_PASSWORD=...
	: "${TK_PMOS_PASSWORD:?set TK_PMOS_PASSWORD (the rootfs user password) before tkbuild}"
	echo ">> installing the kernel into the rootfs chroot (pmbootstrap install) --"
	echo ">>   mkfs + package installs, normally minutes, no progress signal"
	pmbootstrap install --password "$TK_PMOS_PASSWORD" || return 1
	# install just reminted the filesystem UUIDs, so any recorded set is now a
	# lie -- and tkflash-boot would patch the fresh export back to the old ones.
	rm -f "$_PH_REPO/.device-uuids"
	echo ">> exporting the built image (pmbootstrap export)"
	pmbootstrap export || return 1

	echo
	echo ">> verifying the exported image is actually what we built"
	"$_PH_REPO/tools/bootimg-verify.py" \
		"$(readlink -f /tmp/postmarketOS-export/boot.img)" \
		--dtb "$_PH_DTB_BUILT" \
		--config "$_PH_PKGCONFIG" \
		--ref-config "$_PH_OUT/.config"
}

# Wait for the phone to come back, and say which kernel answered.
#
# Why this is in the build path at all: every function below used to hand back
# the instant the device was ASKED to move -- `fastboot boot`, `fastboot reboot`
# -- which leaves the caller with nothing to do but guess. An agent then writes
# `sleep 60`, and that is wrong in both directions (see
# brain/laws/poll-never-sleep.md): it wastes 40 s when the phone came back in 18
# and calls a failure when it needed 65. The wait belongs in the tool, where the
# boot id baseline actually exists.
#
# TK_BOOT_DEADLINE overrides the deadline; it is the worst case you are willing
# to call a failure, NOT a poll interval.
_ph_wait_up() {
	local old_id=${1:-} secs=${TK_BOOT_DEADLINE:-300} new_id
	echo ">> waiting for the phone (deadline ${secs}s, polling -- not sleeping)"
	if ! new_id=$(tk_wait_ssh "$old_id" "$(tk_deadline_ms "$secs")"); then
		echo ">> phone did not come back within ${secs}s" >&2
		echo ">>   tools/tk-recover.sh, or porthole serial console, to see why" >&2
		return 1
	fi
	# Which kernel answered is the one question a boot test must not assume.
	# brain/traps/prove-which-kernel-answered.md
	echo ">> up: boot_id $new_id"
	ssh "${TK_SSH_OPTS[@]}" "${PHONE:-$PORTHOLE_USER@$HOST}" \
		'cat /proc/version' 2>/dev/null | sed 's/^/>> /'
}

# Flash rootfs AND boot, as a pair.
#
# `pmbootstrap install` runs mkfs, which mints NEW filesystem UUIDs, and boot.img
# hard-codes them in its cmdline as pmos_root_uuid=/pmos_boot_uuid=. Flash only
# boot and the initramfs hunts for a filesystem that is not on disk, then falls
# back to a telnet shell on port 23 -- which looks exactly like the debug-shell
# hook even when that hook is gone. Observed 2026-07-25: cmdline wanted
# 6805a9af-... while the device still held 39056921-....
#
# Both slots, because a failed boot decrements slot-retry-count and can flip the
# active slot, and the bootloader reads boot AND dtbo from whichever slot is live.
#
# tkflash-boot skips the rootfs. ONLY safe when install has not re-run since the
# last flash_rootfs -- verify with tools/rootfs-uuid.py first.
# What the last build actually used, recorded rather than re-derived.
#
# Why this file exists, and it is the worst failure of 2026-08-26: flash
# resolved its DTB reference from the CURRENT environment, while the build had
# run against a different tree (PORTHOLE_KERNEL_TREE was exported for `build`
# and not for `flash`). So flash compared a correct export against the stale
# .output of the main checkout, printed `DTB MISMATCH -- DO NOT FLASH`, and had
# ALREADY WRITTEN THE ROOTFS. A scary, wrong error on a half-flashed device.
#
# Two separate mistakes were involved and both are fixed here: flash must use
# what build used (this stamp), and it must refuse BEFORE it writes anything
# (see tkflash).
_PH_STAMP="$_PH_REPO/.last-build"

# Record that the DEVICE now has what this tree built.
#
# `porthole build auto` routes on what make has produced SINCE this moment, so
# it is written after a push or flash SUCCEEDS and never before. That is what
# makes the preview idempotent: a preview compiles but pushes nothing, so it
# cannot move this stamp, so the run that follows still sees the same work.
#
# Before this, `auto` routed on what one `make` invocation happened to touch --
# and the preview runs a real make, so it consumed the very evidence it was
# about to route on. `porthole build` followed by `porthole build auto --yes`
# reported "make rebuilt nothing -- there is nothing to push" about a tree with
# a real change in it, and the documented preview-then-run flow was therefore
# self-defeating.
#
# cksum of the tree path, matching lib/porthole_cmd_build.py:_pushed_stamp.
# A test compares the two implementations, because a stamp written under one
# name and read under another fails SILENTLY: a missing stamp is a legal state
# meaning "never pushed".
_ph_pushed_write() {
	local rundir stamp
	rundir=${PORTHOLE_RUNDIR:-$_PH_REPO_ROOT/.run}
	mkdir -p "$rundir" 2>/dev/null || return 0
	stamp="$rundir/pushed-$(printf '%s' "$_PH_TREE" | cksum | cut -d' ' -f1)"
	: > "$stamp" 2>/dev/null || return 0
	return 0
}

_ph_stamp_write() {
	{
		printf 'tree=%s\n' "$_PH_TREE"
		printf 'dtb=%s\n'  "$_PH_DTB_BUILT"
		printf 'kpkg=%s\n' "$_PH_KPKG"
		printf 'when=%s\n' "$(date -Is)"
	} >"$_PH_STAMP" 2>/dev/null || {
		echo ">> WARNING: could not record the build stamp at $_PH_STAMP" >&2
		return 0
	}
	echo ">> recorded build stamp: tree=$_PH_TREE"
}

# Resolve the .dtb that boot.img is verified AGAINST, and say where it came
# from. Silence here is what made the original failure unreadable.
# The .dtb inside the kernel apk that was just installed.
#
# This is the reference the fast rung needs and did not have. It verified
# boot.img against $_PH_DTB_BUILT -- the ENVKERNEL TREE build -- while the
# image is packed from the APORT release, and the two are allowed to diverge
# (43 files on 2026-08-27). So a correct image was refused:
#
#   FAIL  DTB MISMATCH -- the boot.img does not contain the DTB you built
#           in boot.img : 93401 bytes  c33924f43fe53cc8
#           just built  : 89970 bytes  43950108572f173a
#
# where 93401/c339 was the right answer and 89970/4395 was the tree's. The
# comment in tkflash-boot already prescribed this fix; nothing implemented it.
#
# The apk, not the rootfs chroot: boot.img is packed FROM that chroot, so
# comparing the two would always agree and would catch nothing. The apk is the
# artifact the build produced, which is what the guard is actually asking about
# -- did install pick the package we just built, or an older indexed one.
_ph_dtb_from_apk() {
	local apk=${1:?} out
	[ -f "$apk" ] || { echo ">> no apk at $apk" >&2; return 1; }
	out=$(mktemp -d)/dtb
	mkdir -p "$out"
	tar -xzf "$apk" -C "$out" "boot/dtbs/${PORTHOLE_DTB%/*}/$_PH_DTB" 2>/dev/null || {
		echo ">> $apk has no boot/dtbs/${PORTHOLE_DTB%/*}/$_PH_DTB" >&2; return 1; }
	printf "%s\n" "$out/boot/dtbs/${PORTHOLE_DTB%/*}/$_PH_DTB"
}

_ph_ref_dtb() {
	if [ -n "${TK_REF_DTB:-}" ]; then
		echo ">> reference dtb: TK_REF_DTB (explicit)" >&2
		printf '%s\n' "$TK_REF_DTB"; return 0
	fi
	# An aport build answers for itself. When a kernel package has just been
	# installed, the .dtb it carries is the reference -- the build stamp below
	# points at the envkernel TREE build, which is a different kernel entirely
	# on any port where the series and the tree have diverged.
	if [ -n "${_PH_INSTALLED_APK:-}" ] && [ -f "${_PH_INSTALLED_APK:-}" ]; then
		local apk_dtb
		if apk_dtb=$(_ph_dtb_from_apk "$_PH_INSTALLED_APK"); then
			echo ">> reference dtb: the $(basename "$_PH_INSTALLED_APK") just installed" >&2
			printf '%s\n' "$apk_dtb"; return 0
		fi
	fi
	if [ -s "$_PH_STAMP" ]; then
		local s_tree s_dtb
		s_tree=$(sed -n 's/^tree=//p' "$_PH_STAMP")
		s_dtb=$(sed -n 's/^dtb=//p' "$_PH_STAMP")
		if [ -n "$s_dtb" ] && [ -e "$s_dtb" ]; then
			# The whole point: if the tree configured NOW is not the tree that
			# built this image, say so instead of silently comparing against
			# the wrong .output.
			if [ "$s_tree" != "$_PH_TREE" ]; then
				echo ">> NOTE: this image was built from $s_tree" >&2
				echo ">>       the environment now names $_PH_TREE" >&2
				echo ">>       verifying against the tree that BUILT it, which is the" >&2
				echo ">>       one that can answer. Unset PORTHOLE_KERNEL_TREE or rebuild" >&2
				echo ">>       if that is not what you meant." >&2
			fi
			printf '%s\n' "$s_dtb"; return 0
		fi
	fi
	echo ">> no build stamp -- deriving the reference dtb from the current tree." >&2
	echo ">>   If this image was built elsewhere the comparison is meaningless;" >&2
	echo ">>   rebuild, or set TK_REF_DTB to the .dtb inside the kernel apk." >&2
	printf '%s\n' "$_PH_DTB_BUILT"
}

# Verify the export before ANYTHING is written to the device.
_ph_verify_export() {
	local img dtb
	img=$(readlink -f /tmp/postmarketOS-export/boot.img 2>/dev/null)
	[ -s "$img" ] || { echo ">> no exported boot.img -- run a build first" >&2; return 1; }
	dtb=$(_ph_ref_dtb) || return 1
	echo ">> pre-flight: verifying the export before writing anything"
	"$_PH_REPO/tools/bootimg-verify.py" "$img" --dtb "$dtb" || {
		echo ">> refusing to flash a stale image -- NOTHING has been written" >&2
		return 1; }
}

tkflash() {
	# Verify FIRST. flash_rootfs writes ~640 MB and is not undoable, so a
	# refusal after it has run leaves a half-flashed device and an error that
	# reads like a build problem. The UUID patch below only rewrites the
	# cmdline, never the dtb, so checking the unpatched export here is the same
	# check tkflash-boot repeats on the final image.
	_ph_verify_export || return 1
	pmbootstrap flasher flash_rootfs || return 1
	tkflash-boot
}

tkflash-boot() {
	# Baseline the boot id BEFORE the device moves, so the wait at the end can
	# tell "came back" from "never left". Read it while the phone is still up:
	# once it is in the bootloader there is no ssh to ask, and an empty baseline
	# compares unequal to every real id -- see
	# brain/laws/empty-must-mean-unknown-never-changed.md.
	local old_id; old_id=$(tk_boot_id 2>/dev/null || true)

	# Get there ourselves. tkpush-modules has to run while the phone is UP,
	# so the caller cannot have parked it in the bootloader beforehand.
	# tk_in_fastboot, not a hand-rolled `"$FASTBOOT" devices | grep`: the
	# hand-rolled copy cannot tell "no device" from "no fastboot binary" -- both
	# are empty stdout -- and it has no timeout either. See ph_need_fastboot.
	tk_in_fastboot || "$_PH_REPO/tools/tk-to-fastboot.sh" || return 1

	local img; img=$(readlink -f /tmp/postmarketOS-export/boot.img)
	# The DTB to verify boot.img AGAINST. Defaults to the envkernel tree build,
	# which is right when the kernel came from `_ph_make`.
	#
	# It is WRONG when the kernel was built from the aport series, because the
	# tree and the aport are allowed to diverge (tools/tk-reconcile.sh exists to
	# report exactly that). On 2026-08-21 this refused to flash a CORRECT image:
	# the aport had dropped the capacity-dmips-mhz=<388> override, but the
	# reference was a tree build from two days earlier that still had it, so the
	# guard reported "stale image" about the one image that was not stale.
	#
	#   in boot.img : 63a6f3e0205e116f   gold capacity 0x600 (1536, correct)
	#   just built  : 1d9e2f63638a28b5   gold capacity 0x184 (388, the bug)
	#
	# For an aport build, point this at the .dtb inside the kernel apk you
	# installed -- that is a non-circular reference, unlike the chroot the image
	# was packed from, which would always agree with itself:
	#
	#   TK_REF_DTB=/path/to/unpacked-apk/boot/dtbs/qcom/msm8998-google-taimen.dtb
	local dtb; dtb=$(_ph_ref_dtb) || return 1

	# The exported boot.img carries the UUIDs of whatever rootfs the CHROOT was
	# last installed with. If `pmbootstrap install` has re-run since the device's
	# rootfs was flashed, those no longer name any filesystem on the phone: the
	# initramfs comes up (USB gadget enumerates, ssh refused), hunts forever, and
	# the phone sits at a black screen looking bricked. Every failed boot also
	# burns a slot-retry-count, so after three of them the bootloader gives up on
	# the slot and drops to fastboot -- which reads as a second, unrelated fault.
	# Cost an afternoon on 2026-08-03. tkpush-modules records the phone's real
	# UUIDs while it is still up; patch them in rather than trusting the export.
	local uf="$_PH_REPO/.device-uuids" tok args=()
	if [ -s "$uf" ]; then
		for tok in $(cat "$uf"); do args+=(--remove "${tok%%=*}=" --add "$tok"); done
		"$_PH_REPO/tools/bootimg-cmdline.py" patch "$img" \
			-o /tmp/tk-boot-uuid.img "${args[@]}" || return 1
		img=/tmp/tk-boot-uuid.img
	else
		echo ">> WARNING: no $uf -- flashing the export's UUIDs unchecked"
	fi

	"$_PH_REPO/tools/bootimg-verify.py" "$img" --dtb "$dtb" || {
		echo ">> refusing to flash a stale image"; return 1; }

	# Which slots exist, which one may be armed, and which dtbo to write are
	# all profile facts. They were taimen's values written into a file whose
	# header claims `scope: generic`, and PORTHOLE_SLOT_FORBIDDEN /
	# PORTHOLE_ACTIVE_SLOT / PORTHOLE_DTBO_IMG existed in the schema while
	# nothing read them.
	local slots="${PORTHOLE_SLOTS:-a b}" target="${PORTHOLE_ACTIVE_SLOT:-}"
	local forbidden="${PORTHOLE_SLOT_FORBIDDEN:-}"

	if [ "${PORTHOLE_HAS_AB_SLOTS:-0}" != "1" ]; then
		fastboot flash boot "$img" || return 1
	else
		local s
		for s in $slots; do
			fastboot flash "boot_$s" "$img" || return 1
		done
	fi

	if [ -n "${PORTHOLE_DTBO_IMG:-}" ]; then
		local dtbo_img="$_PH_REPO/${PORTHOLE_DTBO_IMG}"
		if [ ! -s "$dtbo_img" ]; then
			echo ">> PORTHOLE_DTBO_IMG is set but $dtbo_img is missing" >&2
			return 1
		fi
		# The bootloader reads dtbo from the ACTIVE slot, so both get it or the
		# next slot flip silently reverts you. See brain/traps/dtbo-must-match.
		if [ "${PORTHOLE_HAS_AB_SLOTS:-0}" != "1" ]; then
			fastboot flash dtbo "$dtbo_img" || return 1
		else
			for s in $slots; do
				fastboot flash "dtbo_$s" "$dtbo_img" || return 1
			done
		fi
	fi

	if [ "${PORTHOLE_HAS_AB_SLOTS:-0}" = "1" ] && [ -n "$target" ]; then
		# REFUSE rather than arm a slot the profile says has no known-good
		# image. Recovery from the bootloader cannot re-arm a slot, so this is
		# the last point at which the mistake is cheap.
		case " $forbidden " in
			*" $target "*)
				echo ">> REFUSING: PORTHOLE_ACTIVE_SLOT=$target is listed in" >&2
				echo ">> PORTHOLE_SLOT_FORBIDDEN=$forbidden" >&2
				return 1 ;;
		esac
		fastboot set_active "$target"   # also resets that slot's retry counter
	elif [ "${PORTHOLE_HAS_AB_SLOTS:-0}" = "1" ]; then
		echo ">> PORTHOLE_ACTIVE_SLOT is unset -- leaving the active slot alone"
	fi
	fastboot reboot
	_ph_wait_up "$old_id"
}

# FAST cycle: kernel only, UUIDs untouched.
#
# The slow part of the loop is not the compile, it is that `pmbootstrap install`
# runs mkfs and remints pmos_boot_uuid/pmos_root_uuid, which forces a ~640 MB
# flash_rootfs every time or the initramfs cannot find root.
#
# Upgrading the kernel apk INSIDE the existing rootfs chroot avoids all of that:
# postmarketos-installkernel + mkinitfs regenerate /boot/boot.img against the
# fstab UUIDs already there, so boot.img stays consistent with the rootfs already
# on the device and only the boot partition needs flashing.
#
# Use this for kernel/DTS iteration. Switch back to tkbuild+tkflash when the
# rootfs contents change (new packages, device-package files such as the sudoers
# drop-in) or if boot and rootfs have desynced.
# Is the local tree the same kernel as the aport we are about to ship?
#
# Compares MAJOR.MINOR only: the tree Makefile carries VERSION/PATCHLEVEL and
# nothing finer, while the aport pins a point release (7.2.2). Taking the first
# two components of pkgver makes "7.2.2" -> "7.2" and leaves a two-component
# "6.18" alone, which the naive ${pkgver%.*} would have turned into "6".
_ph_tree_matches_aport() {
	_PH_TREE_V=$(awk -F' = ' '/^VERSION/{v=$2} /^PATCHLEVEL/{p=$2}
	                          END{if (v != "") print v "." p}' "$_PH_TREE/Makefile" 2>/dev/null)
	# The `[ -n ]` below is the "unknown: build it" escape, and until 2026-08-29
	# it could not fire: awk printed "." for an empty pkgver, "." is not empty,
	# and the comparison then refused the build with
	#   REFUSING: tree is Linux 7.2 but linux-...-7.2 is .
	# -- a message about a version mismatch when the real problem was that the
	# APKBUILD could not be read at all. Reproduced by running `pmbootstrap zap`
	# inside the workspace: that unmounts porthole's own bind mounts, pmaports
	# goes empty, and this is the face it wears. Say which of the two it is.
	local apkbuild="$_PH_APORTS/device/testing/$_PH_KPKG/APKBUILD"
	if [ ! -r "$apkbuild" ]; then
		echo ">> cannot read the aport at $apkbuild" >&2
		echo ">>   if it is an empty directory the workspace lost its mounts;" >&2
		echo ">>   \`porthole sandbox up\` re-arms them." >&2
		_PH_APORT_V=""
		return 0
	fi
	# shellcheck disable=SC2154  # pkgver is set by the sourced APKBUILD
	_PH_APORT_V=$(. "$apkbuild" 2>/dev/null
	              [ -n "$pkgver" ] && echo "$pkgver" | awk -F. '{print $1 "." $2}')
	[ -n "$_PH_TREE_V" ] && [ -n "$_PH_APORT_V" ] || return 0   # unknown: build it
	[ "$_PH_TREE_V" = "$_PH_APORT_V" ]
}

tkbuild-kernel() {
	# This rung ships the APORT, not the tree: _ph_install_kernel_release adds
	# the apk, tkpush-modules takes its modules out of the rootfs chroot, and
	# the dtb is read back out of the apk below -- deliberately. So a tree build
	# cannot change what lands on the phone. Building it anyway costs minutes at
	# best; at worst it is a hard failure that looks like a kernel bug. On
	# 2026-08-29 the tree sat on v6.18 while the aport had moved to 7.2.2, and
	# _ph_make copied the 7.2 config into the 6.18 tree and died in
	# drivers/gpu/drm with "unable to open output file". Nothing was wrong with
	# either kernel; they were simply not the same one.
	if _ph_tree_matches_aport; then
		_ph_make || return 1
	else
		echo ">> skipping the tree build: tree is Linux $_PH_TREE_V, aport is $_PH_APORT_V"
		echo ">>   this rung flashes the aport apk, so the tree cannot affect it."
		echo ">>   use \`porthole build\` if you meant to build and verify the tree."
	fi

	# -r: the rootfs chroot, not the build chroot. -U -u: refresh the index and
	# upgrade, so it picks up the apk just built rather than a cached older one.
	_ph_install_kernel_release || return 1
	echo ">> exporting the built image (pmbootstrap export)"
	pmbootstrap export || return 1

	# Against the apk that was installed, NOT the tree: this rung flashes the
	# aport release, and the tree is a different kernel.
	local dtb; dtb=$(_ph_dtb_from_apk "$_PH_INSTALLED_APK") || return 1
	"$_PH_REPO/tools/bootimg-verify.py" \
		"$(readlink -f /tmp/postmarketOS-export/boot.img)" --dtb "$dtb" || {
		echo ">> refusing to flash a stale image"; return 1; }

	tkpush-modules || return 1
	tkflash-boot || return 1
	_ph_pushed_write
}

# Move the device to a DIFFERENT kernel flavor -- a major version bump.
#
# Why `fast` (tkbuild-kernel) cannot do this. _ph_install_kernel_release does a
# plain `apk add`, which silently assumes the flavor never changes. Two kernel
# aports own the same paths -- /boot/vmlinuz and every .dtb under /boot/dtbs --
# so adding the new flavor while the old one is installed produces a wall of
#
#   ERROR: ...-7.2-7.2.2-r0: trying to overwrite boot/vmlinuz
#          owned by ...-6.18-6.18-r101
#
# and then the failure mode that actually costs a session: apk registers the new
# package ANYWAY. `apk info` afterwards lists BOTH flavors while /boot/vmlinuz
# still belongs to the old one, so `pmbootstrap export` packs the OLD kernel
# into boot.img, the flash "succeeds", and the device comes back on the kernel
# you were trying to leave. Nothing in that sequence prints a warning.
#
# The swap therefore has to be ONE apk transaction. apk reads a leading `!` as
# "remove this in the same operation", so the old flavor leaves exactly as the
# new one arrives and no moment exists where both own the same file. `-u` comes
# along because the device kernel subpackage pins a flavor in its depends=: with
# the old kernel going away it must move in the same transaction or apk refuses
# on a broken dependency.
#
# Modules are the other half, and the reason this rung exists at all rather than
# being a note in the runbook. kernel.release changes across a major bump, so
# /usr/lib/modules/<new release> does not exist on the phone in any form -- this
# is not the stale-module case that tkpush-modules usually guards, it is a total
# absence. A boot.img-only flash then lands a kernel that finds no modules: it
# boots, ssh answers, and there is no display, no wifi and no audio. So the push
# is not optional here, and it runs BEFORE the flash while the phone is still up
# on the old kernel -- there is no way to push modules to a device that cannot
# bring up its network because the modules are missing.
#
# The old module tree is left where it is. tkpush-modules moves the previous set
# aside as <release>.old rather than deleting it, and the outgoing kernel's tree
# lives under its own release directory anyway, so rolling back is re-flashing
# the previous boot.img and nothing else.
tkupgrade-kernel() {
	local ver incumbent repo
	repo="$_PH_PMB/packages/edge/${PORTHOLE_ARCH}"

	# shellcheck disable=SC2154  # pkgver/pkgrel are set by the sourced APKBUILD
	ver=$(. "$_PH_APORTS/device/testing/$_PH_KPKG/APKBUILD" 2>/dev/null
	      echo "$pkgver-r$pkgrel")
	[ -n "$ver" ] && [ "$ver" != "-r" ] || {
		echo ">> could not read $_PH_KPKG pkgver/pkgrel" >&2; return 1; }

	[ -f "$repo/$_PH_KPKG-$ver.apk" ] || {
		echo ">> $_PH_KPKG-$ver.apk is not in the local repo -- build it first:" >&2
		echo ">>   pmbootstrap build $_PH_KPKG" >&2
		return 1; }

	# Ask apk who owns /boot/vmlinuz rather than guessing from the package
	# name. A flavor is an arbitrary string and two of them share no prefix in
	# general, so deriving "the other kernel" by pattern would be a guess about
	# somebody else's naming; the file ownership is the fact.
	incumbent=$(pmbootstrap chroot -r -- apk info -W /boot/vmlinuz 2>/dev/null |
		sed -n 's/.*owned by //p' | sed 's/-[^-]*-r[0-9]*$//')
	[ -n "$incumbent" ] || {
		echo ">> nothing owns /boot/vmlinuz in the rootfs chroot -- is it installed?" >&2
		return 1; }

	# What the DEVICE runs is what decides whether this is an upgrade -- not
	# what the rootfs chroot holds. The chroot is host-side staging, and it can
	# legitimately be on the target already: a previous run got that far before
	# failing, or a repair put it there. Refusing on the chroot's state refuses
	# the exact case this rung exists for, with a message telling you to use a
	# rung that cannot do the job. Measured 2026-08-29.
	local dev_release target_release
	# shellcheck source=tk-lib.sh
	. "$_PH_REPO/tools/tk-lib.sh"
	target_release=$(tar -xzOf "$repo/$_PH_KPKG-$ver.apk" \
		"usr/share/kernel/${_PH_KPKG#linux-}/kernel.release" 2>/dev/null | tr -d '\r\n')
	[ -n "$target_release" ] || {
		echo ">> $_PH_KPKG-$ver.apk carries no kernel.release -- refusing" >&2; return 1; }
	dev_release=$(tk_run 'uname -r' 2>/dev/null | tr -d '\r\n')

	if [ -n "$dev_release" ] && [ "$dev_release" = "$target_release" ]; then
		echo ">> the device already runs $target_release -- not a flavor change."
		echo ">> Use \`porthole build fast\` for a same-flavor rebuild."
		return 1
	fi
	echo ">> device runs ${dev_release:-<unknown>}, target is $target_release"

	if [ "$incumbent" = "$_PH_KPKG" ]; then
		echo ">> the rootfs chroot is already staged on $_PH_KPKG -- no swap needed"
	else

		# A failed `apk add` of the target leaves it REGISTERED with none of its
		# files unpacked -- that is how the conflict wall ends, and it is silent.
		# The swap below would then purge the incumbent while apk, believing the
		# target is already present, installs nothing: /boot/vmlinuz simply ceases
		# to exist and the export packs an image with no kernel in it. Repair that
		# state before touching anything, rather than after. Measured 2026-08-29.
		if pmbootstrap chroot -r -- apk info 2>/dev/null | grep -qx "$_PH_KPKG"; then
			echo ">> $_PH_KPKG is registered but $incumbent owns /boot/vmlinuz --"
			echo ">> a previous add half-applied; reinstalling its files first"
			pmbootstrap chroot -r -- apk fix --allow-untrusted "$_PH_KPKG" || return 1
	fi

	echo ">> swapping $incumbent -> $_PH_KPKG=$ver in one transaction"
	pmbootstrap chroot -r -- apk add -U -u --allow-untrusted \
		"$_PH_KPKG=$ver" "!$incumbent" || return 1

	# Verify the swap by ownership, not by apk's exit code: the whole reason
	# this function exists is that apk can exit non-zero having half-applied a
	# kernel change, and can exit zero having applied it to the wrong package.
	local owner
	owner=$(pmbootstrap chroot -r -- apk info -W /boot/vmlinuz 2>/dev/null |
		sed -n 's/.*owned by //p')
	case "$owner" in
		"$_PH_KPKG-$ver") : ;;
		*) echo ">> /boot/vmlinuz is owned by '$owner', not $_PH_KPKG-$ver -- refusing" >&2
		   return 1 ;;
	esac
	pmbootstrap chroot -r -- apk info 2>/dev/null | grep -qx "$incumbent" && {
		echo ">> $incumbent is STILL installed alongside $_PH_KPKG -- refusing" >&2
		return 1; }
	fi
	_PH_INSTALLED_APK="$repo/$_PH_KPKG-$ver.apk"

	echo ">> exporting the built image (pmbootstrap export)"
	pmbootstrap export || return 1

	# Against the apk that was installed, NOT the tree: this rung ships the
	# aport release, and on a version bump the tree is a different kernel
	# entirely rather than merely a diverged one.
	local dtb; dtb=$(_ph_dtb_from_apk "$_PH_INSTALLED_APK") || return 1
	"$_PH_REPO/tools/bootimg-verify.py" \
		"$(readlink -f /tmp/postmarketOS-export/boot.img)" --dtb "$dtb" || {
		echo ">> refusing to flash a stale image"; return 1; }

	# Modules first, while the phone is still up on the outgoing kernel.
	tkpush-modules || return 1
	tkflash-boot || return 1
	_ph_pushed_write
}

# Put the freshly built modules on the phone.
#
# tkbuild-kernel only flashes boot.img, so /lib/modules on the phone keeps
# whatever the last full install put there. That is fine for a source-only
# change, and silently catastrophic for a CONFIG change: any config edit moves
# the module_layout CRC, so EVERY module then fails to insert with "disagrees
# about version of symbol module_layout". The phone still boots and still
# answers ssh -- it just comes up with no display, no wifi and no audio, which
# reads as "the kernel change broke the device" rather than "the modules are
# stale". That cost a session once; do not remove this step.
#
# 2026-08-02: it happened AGAIN, from the opposite direction, and the old version
# of this function could not see it. The extract produced 274 files of ZERO
# BYTES, tar exited 0, and the echo below dutifully reported "pushed 274
# modules". The phone then booted with `lsmod | wc -l` = 5: no display, no wifi,
# no audio, no zram, seven failed units -- white Google logo then a black screen,
# which reads as a dead phone. It is not. ssh over USB answered the whole time,
# and `modinfo` on any pushed .ko printed nothing but its filename.
#
# A count is not an integrity check. So now: stage into a scratch dir, verify the
# tarball's md5 survived the wire, refuse to swap if ANY .ko is empty, and only
# then move the live directory aside. On any failure the running set is never
# touched. The previous set stays at $kver.old as before.
#
# PHONE is honoured so this works over WiFi too; the old hardcoded USB address
# is only reachable when the gadget is up.
tkpush-modules() {
	local phone=${PHONE:-$PORTHOLE_USER@$HOST}
	# The phone mints a new host key on essentially every boot, so bare ssh
	# fails "Connection closed" or stops to ask about the key and this returns
	# a stale-module success. tk-lib.sh already carries the options every other
	# tk-* script uses; source it rather than growing a second set.
	# shellcheck source=tk-lib.sh
	. "$_PH_REPO/tools/tk-lib.sh"
	# The phone has to be UP: this is an scp. A previous failed run may well
	# have parked it in the bootloader -- tkflash-boot puts it there -- and the
	# bare failure is `scp: Connection closed`, which reads as a network fault
	# and says nothing about the state the device is actually in.
	local st; st=$(tk_device_state)
	[ "$st" = BOOTED ] || {
		echo ">> the device is $st, and pushing modules needs it BOOTED." >&2
		echo ">> tools/tk-reboot.sh will bring it back, then re-run." >&2
		return 76; }

	local src="$_PH_PMB/chroot_rootfs_${PORTHOLE_CODENAME}/lib/modules"
	local kver; kver=$(basename "$(ls -d "$src"/* | head -1)")
	[ -d "$src/$kver" ] || { echo ">> no modules at $src"; return 1; }

	# Catch a broken build before it ever reaches the phone.
	local empty; empty=$(find "$src/$kver" -name '*.ko*' -size 0 | wc -l)
	[ "$empty" -eq 0 ] || { echo ">> $empty EMPTY .ko in $src -- bad build, refusing"; return 1; }

	local tar=/tmp/tk-modules-$kver.tar.gz base sum
	tar -C "$src" -czf "$tar" "$kver" || return 1
	base=$(basename "$tar")
	sum=$(md5sum "$tar" | cut -d' ' -f1)

	scp -q "${TK_SSH_OPTS[@]}" "$tar" "$phone:/tmp/" || return 1
	ssh "${TK_SSH_OPTS[@]}" "$phone" "set -e
		[ \"\$(md5sum /tmp/$base | cut -d' ' -f1)\" = '$sum' ] || {
			echo '>> md5 MISMATCH after transfer -- not touching /lib/modules'; exit 1; }

		sudo rm -rf /lib/modules/.stage
		sudo mkdir -p /lib/modules/.stage
		sudo tar -C /lib/modules/.stage -xzf /tmp/$base

		n=\$(sudo find /lib/modules/.stage/$kver -name '*.ko*' | wc -l)
		z=\$(sudo find /lib/modules/.stage/$kver -name '*.ko*' -size 0 | wc -l)
		[ \"\$z\" -eq 0 ] && [ \"\$n\" -gt 100 ] || {
			sudo rm -rf /lib/modules/.stage
			echo \">> staged set is bad (\$n modules, \$z empty) -- live set untouched\"; exit 1; }

		sudo rm -rf /lib/modules/$kver.old
		# Only rotate a set that is actually there. On a major kernel bump
		# kernel.release changes, so /lib/modules/\$kver does not exist on the
		# phone at all and the unconditional mv failed with \"can't rename
		# '/lib/modules/$kver': No such file or directory\" -- which reads as a
		# push failure when in fact there was simply nothing to displace.
		# Measured 2026-08-29 moving taimen from 6.18.0 to 7.2.2.
		[ -d /lib/modules/$kver ] && sudo mv /lib/modules/$kver /lib/modules/$kver.old
		sudo mv /lib/modules/.stage/$kver /lib/modules/$kver
		sudo rmdir /lib/modules/.stage
		sudo depmod -a $kver
		sudo sync

		# Prove one module is actually loadable, not merely present.
		sudo modinfo \$(sudo find /lib/modules/$kver -name 'msm.ko*' | head -1) \
			| grep -q '^vermagic' || {
			echo '>> pushed set has no readable vermagic -- roll back with:'
			echo \"     sudo rm -rf /lib/modules/$kver && sudo mv /lib/modules/$kver.old /lib/modules/$kver && sudo depmod -a $kver\"
			exit 1; }

		echo \">> pushed \$n modules, vermagic OK\"" || return 1

	# While the phone is still up, record the UUIDs its initramfs actually needs.
	# tkflash-boot patches them into the export; see the comment there.
	ssh "${TK_SSH_OPTS[@]}" "$phone" 'cat /proc/cmdline' 2>/dev/null | tr ' ' '\n' |
		grep -E '^pmos_(boot|root)_uuid=' > "$_PH_REPO/.device-uuids"
	[ -s "$_PH_REPO/.device-uuids" ] &&
		echo ">> recorded device UUIDs: $(tr '\n' ' ' < "$_PH_REPO/.device-uuids")"
}

# FAST loop for driver-only changes: build the module, push it, reload it.
#
# Stage one built module somewhere writable, drop its .BTF, echo the new path.
#
# A module built from the TREE carries a .BTF that references the aport
# kernel's BTF by type id. The ids do not line up, the module notifier fails
# the load with -40, and modprobe reports "Symbolic link loop" -- see
# brain/traps/a-tree-built-module-carries-btf-the-running-kernel-rejects.md.
# That trap says the fix is handled, and it was, in tools/tk-push-module.sh.
# `mod` -- the rung the ladder tells you to try FIRST -- never went through
# that script and pushed the module raw, so on 2026-08-31 a `porthole build
# mod` on ath10k_core left taimen with no wifi driver at all: the old module
# unloaded, the new one would not load, and the fix the repo had already
# written sat one code path away.
#
# Staged rather than rewritten in place: build output under .output belongs to
# the workspace container's uid and is not ours to edit -- the same reason
# tk-push-module.sh copies first. `chmod u+w` because `cp -p` brings a
# read-only mode with it and the strip opens the file "r+b".
#
# Split out of tkmod because everything else in that function needs a
# container, a kernel build and a phone; this step needs none of the three,
# and it is the one whose absence has already cost a wifi driver.
#
# It also STRIPS DEBUG INFO, and that was issue #20. `fast` installs modules
# extracted from the built apk, which abuild stripped; `mod` built from the
# tree and pushed the .ko whole, so the same source produced a venus-core.ko
# of 3431808 bytes against the packaged 315448 -- ~11x -- while its siblings
# stayed byte-identical. Rebooting onto that mix bootlooped taimen twice, with
# no console, no pstore and no remote window: the USB gadget re-enumerated
# every ~21-28 s and port 22 answered "Connection refused" before sshd was up.
# Recovery was a physical Power+VolDown, both times.
#
# `--strip-debug` specifically, because that is what the kernel's own
# `modules_install INSTALL_MOD_STRIP=1` runs and what abuild does -- not an
# invention, the same operation the packaged copy already went through.
#
# BEST EFFORT, and the order matters. llvm-strip lives only inside
# chroot_native (the host has no aarch64 strip; that is why the BTF fix below
# is Python ELF surgery), and it cannot be run from outside the chroot -- its
# musl libs are not there. So it goes THROUGH pmbootstrap, and when there is
# no chroot to reach -- a `--host` build, a fresh workspace -- the module is
# pushed unstripped and said so. The device-side size guard in tkmod is what
# makes that safe: it refuses the push rather than trusting this to have run.
_ph_strip_module() {
	local staged=$1 name=$2
	local native="$_PH_PMB/chroot_native" tmp="ph-strip-$name.ko"
	if [ ! -d "$native/tmp" ] || ! command -v pmbootstrap >/dev/null 2>&1; then
		echo ">> no chroot_native to reach llvm-strip in -- pushing unstripped" >&2
		return 0
	fi
	cp -p "$staged" "$native/tmp/$tmp" 2>/dev/null || return 0
	if pmbootstrap -q chroot -- llvm-strip --strip-debug "/tmp/$tmp" >&2; then
		cp -p "$native/tmp/$tmp" "$staged" && chmod u+w "$staged"
	else
		echo ">> llvm-strip failed -- pushing unstripped; the size guard decides" >&2
	fi
	rm -f "$native/tmp/$tmp"
}

_ph_stage_module() {
	local ko=$1 name=$2
	local staged="/tmp/$name.ko"   # $name is not set until the local above ends
	cp -p "$ko" "$staged" || return 1
	chmod u+w "$staged" || return 1
	# Strip BEFORE neutering .BTF: llvm-strip rewrites the section table, and
	# doing it second could undo a rename that has to survive to the loader.
	_ph_strip_module "$staged" "$name"
	# stdout is the staged path; the tool's own report goes to stderr.
	"$_PH_REPO_ROOT/tools/tk-strip-btf.py" "$staged" >&2 || return 1
	echo "$staged"
}

# A .ko change needs `make` and an insmod -- not a package, not a boot.img, not
# a reboot. tkbuild-kernel is for CONFIG and DTS changes; using it to iterate on
# one driver costs ~6 minutes a cycle instead of ~40 seconds, which is most of a
# session if you are chasing a sensor bring-up.
#
#   tkmod drivers/media/i2c/imx179.ko imx179
# Say what an ssh/scp failure to the device actually was.
#
# `scp: Connection closed` names nothing, and the first guess is a stale ssh
# control master -- a real hazard after a reboot, and the wrong one here. It
# cost a session to learn the answer was that the phone does not have this key.
#
# One probe, on the failure path only, so a working push pays nothing.
# ControlMaster=no because a dead master is one of the answers this has to be
# able to distinguish, and reusing it would hide the very thing being asked.
_ph_ssh_diagnose() {
	local phone=$1 err
	err=$(ssh "${TK_SSH_OPTS[@]}" -o ControlMaster=no -o ControlPath=none \
		"$phone" true 2>&1)
	case $err in
	*"Permission denied"*)
		echo ">> the device refuses this key: ${PORTHOLE_SSH_KEY:-(your default ssh keys)}" >&2
		echo ">> in the workspace that is the ONLY key the container has, so" >&2
		echo ">> every push fails until the phone is told about it. Once, by hand:" >&2
		echo ">>   ssh-keygen -y -f ${PORTHOLE_SSH_KEY:-~/.ssh/id_ed25519}" >&2
		echo ">>   # add that one line to ~/.ssh/authorized_keys ON THE PHONE" >&2
		echo ">> \`porthole doctor\` reports this as workspace: device key." >&2 ;;
	"")
		echo ">> ssh answers now, so the failure was transient or the mux was stale" >&2
		echo ">>   ph_ssh_mux_reset   # if it recurs" >&2 ;;
	*)
		echo ">> ssh to $phone failed: $err" >&2 ;;
	esac
}

tkmod() {
	local rel=$1 name=$2 phone=${PHONE:-$PORTHOLE_USER@$HOST}
	[ -n "$rel" ] && [ -n "$name" ] || { echo ">> usage: tkmod <path/to/mod.ko> <modname>"; return 1; }

	_ph_announce_tree

	# `mod` builds incrementally and needs a tree that has already had one
	# full envkernel build: .output/.config and the symbol tables live there.
	# Without them kbuild fails with its own advice -- "Configuration file
	# .config not found! Please run some configurator (e.g. make menuconfig)"
	# -- which is useless here, names the wrong fix, and cost a cycle on
	# 2026-08-29 against a freshly created worktree.
	[ -f "$_PH_OUT/.config" ] || {
		echo ">> $_PH_TREE has never had a full build ($_PH_OUT/.config is missing)." >&2
		echo ">> \`mod\` is an incremental rung and cannot prepare a tree." >&2
		echo ">> Run a full rung once in this tree first:" >&2
		echo ">>   porthole build fast --yes        # or: kernel" >&2
		echo ">> Ignore any kbuild advice about menuconfig; the tree is the issue." >&2
		return 1; }

	_ph_activate || return 1
	shopt -s expand_aliases
	eval make -j"$(nproc)" modules || { popd >/dev/null || return 1; echo ">> build failed"; return 1; }
	popd >/dev/null || return 1

	local ko="$_PH_OUT/$rel"
	[ -f "$ko" ] || { echo ">> no module at $ko"; return 1; }

	# Every copy that leaves here is staged and stripped -- the hot insmod one
	# AND the .ko.xz written over the installed module below.
	ko=$(_ph_stage_module "$ko" "$name") || return 1

	# TK_SSH_OPTS, like every other device call in this file. It carries
	# -i "$PORTHOLE_SSH_KEY" -o IdentitiesOnly=yes (lib/porthole.sh:188), and
	# in the workspace that key is /run/porthole/device_key -- the ONLY key the
	# container has. Without it these three calls offered no key at all, so
	# every `porthole build mod --yes` ended `scp: Connection closed`, which
	# reads like a network fault and sent a session looking at the ssh mux.
	# They also lost ConnectTimeout, BatchMode (a prompt instead of a fast
	# failure) and the multiplexing that makes every other device call ~15ms.
	scp -q "${TK_SSH_OPTS[@]}" "$ko" "$phone:/tmp/$name.ko" || {
		_ph_ssh_diagnose "$phone"; return 1; }

	# ALSO replace the installed module, not just the hot-loaded one.
	#
	# insmod alone leaves /lib/modules/<ver>/**/<mod>.ko.xz untouched, so the
	# next `modprobe` -- which every test script does after an `rmmod` --
	# silently loads the OLD build. That is not a theoretical hazard: it
	# invalidated two whole test rounds in the camera bring-up (a register
	# fix "did not work" because the driver containing it was never running).
	# Modules on the device are xz-compressed; a plain .ko next to the .ko.xz
	# is ignored (TODO section 3), so compress and overwrite in place.
	xz -cf "$ko" > "/tmp/$name.ko.xz" || return 1
	scp -q "${TK_SSH_OPTS[@]}" "/tmp/$name.ko.xz" "$phone:/tmp/$name.ko.xz" || {
		_ph_ssh_diagnose "$phone"; return 1; }

	# rmmod alone is not enough once something holds the driver -- camss keeps a
	# reference to a sensor subdev, so the module refcount never reaches zero and
	# insmod then fails with "File exists", which reads like a stale file rather
	# than a busy module. Unbind every device first, then reload and let it
	# re-probe (camss re-registers its media device when the subdev comes back).
	# The reader half of the reload lives in a file so the tests can run it
	# against a fixture sysfs; it is pasted in here rather than pushed because
	# it must be in place before anything is torn down.
	local modstack="$_PH_REPO_ROOT/tools/tk-modstack.sh"
	[ -f "$modstack" ] || { echo ">> missing $modstack"; return 1; }
	local stack_down=""
	ssh "${TK_SSH_OPTS[@]}" "$phone" "$(cat "$modstack")
		set -e

		# Everything holding $name, deepest first. A module with devices of
		# its own has an empty stack and this is the old behaviour exactly.
		stack=\$(ms_stack $name)
		[ -z \"\$stack\" ] || echo \">> stack above $name:\$stack\"

		# Decided BEFORE anything is unloaded, and the module is still
		# installed below either way -- so a refusal leaves the device exactly
		# as it was, with the new build on disk for the next boot.
		if carrier=\$(ms_conflict $name); then held=1; else held=; fi

		if [ -z \"\$held\" ]; then
			for h in \$stack; do
				ms_unbind \$h
				sudo rmmod \$h 2>/dev/null || true
			done
			ms_unbind $name
			sudo rmmod $name 2>/dev/null || true
		fi

		# Overwrite every installed copy so modprobe agrees with insmod.
		# Only the RUNNING kernel's tree -- /lib/modules also holds stale
		# .old/.broken copies from earlier flashes, and depmod would be
		# run against a version that is not booted.
		#
		# Match whatever form the rootfs actually ships. This looked only for
		# .ko.xz, so on a rootfs with UNCOMPRESSED modules it found nothing and
		# announced 'modprobe will not see this build' about a module it was
		# perfectly able to install -- a warning that reads like a broken build.
		# kmod decides by extension, so the replacement must keep the extension
		# the installed file already has.
		inst=\$(find /lib/modules/\$(uname -r) -type f \\
			\\( -name '$name.ko'    -o -name '${name//_/-}.ko' \\
			-o -name '$name.ko.xz' -o -name '${name//_/-}.ko.xz' \\
			-o -name '$name.ko.gz' -o -name '${name//_/-}.ko.gz' \\) 2>/dev/null)
		# ISSUE #20. What is about to be written must look like it came from
		# the same build path as the set it is joining. A module pushed
		# unstripped into a stripped set bootlooped taimen twice on
		# venus-core -- no console, no pstore, physical recovery both times.
		# Which property is fatal (stripping, section layout, a sibling ABI
		# skew) is NOT established, so this does not test for stripping: it
		# tests for the artefacts disagreeing, which is true whichever it is.
		#
		# Decided BEFORE the first write, so a refusal leaves the device
		# exactly as it was running.
		#
		# Only uncompressed siblings are comparable -- a raw .ko against an
		# installed .ko.xz says nothing about either. Skipped rather than
		# guessed at.
		ratio=\${TK_MOD_SIZE_RATIO:-4}
		new_sz=\$(stat -c %s /tmp/$name.ko 2>/dev/null || echo 0)
		for f in \$inst; do
			case \"\$f\" in *.ko) ;; *) continue ;; esac
			old_sz=\$(stat -c %s \"\$f\" 2>/dev/null || echo 0)
			[ \"\$old_sz\" -gt 0 ] && [ \"\$new_sz\" -gt 0 ] || continue
			if [ \$(( new_sz / old_sz )) -ge \"\$ratio\" ]; then
				echo \">> REFUSING: the module just built is \$(( new_sz / old_sz ))x the\"
				echo \">> size of the one it would replace, so the two came from\"
				echo \">> different build paths -- almost certainly a tree module\"
				echo \">> against a stripped, packaged set.\"
				echo \">>   about to write  \$new_sz bytes\"
				echo \">>   installed now   \$old_sz bytes  (\$f)\"
				echo \">> NOTHING was written and nothing was unloaded. On venus-core\"
				echo \">> this combination bootloops the device with no console, no\"
				echo \">> pstore and no remote way back (issue #20).\"
				echo \">> Use 'porthole build fast --yes', which installs the whole\"
				echo \">> set from one package. TK_MOD_SIZE_RATIO raises the bound.\"
				exit 6
			fi
		done

		if [ -n \"\$inst\" ]; then
			for f in \$inst; do
				case \"\$f\" in
				*.ko)    sudo cp /tmp/$name.ko \"\$f\" ;;
				*.ko.xz) sudo cp /tmp/$name.ko.xz \"\$f\" ;;
				*.ko.gz) gzip -c /tmp/$name.ko | sudo tee \"\$f\" >/dev/null ||
					{ echo \">> could not gzip for \$f\"; exit 1; } ;;
				esac
			done
			sudo depmod -a
			echo \">> installed: \$inst\"
		else
			echo '>> WARNING: $name is not installed under /lib/modules in any form'
			echo '>>   (.ko, .ko.xz, .ko.gz all absent) -- modprobe will not see this'
			echo '>>   build. insmod below still loads it for this boot.'
		fi

		# Refused above: the install has happened, nothing has been unloaded,
		# and the session that would have had to survive the reload is intact.
		if [ -n \"\$held\" ]; then
			echo \">> REFUSING: \$carrier drives the interface this ssh arrived on.\"
			echo \">> Unloading $name would take \$carrier down with it and there\"
			echo \">> would be nothing left to run the modprobe that brings it back.\"
			echo \">> The copy on disk IS updated -- reboot to run it, or re-run\"
			echo \">> over a transport this module does not carry.\"
			exit 4
		fi

		# insmod's failure modes are not interchangeable, and conflating them
		# is what makes a busy module read as a build error.
		rc=0
		if ! out=\$(sudo insmod /tmp/$name.ko 2>&1); then
			case \"\$out\" in
			*'File exists'*|*'Device or resource busy'*) rc=3 ;;
			*)
				echo \">> insmod failed: \$out\"; exit 1 ;;
			esac
		fi

		# Put the stack back whatever happened above, and put it back FIRST.
		# A failed insmod that leaves a wifi driver unloaded is worse than a
		# failed insmod, and the window with the chip powered off is where
		# taimen floods dmesg with the level-IRQ storm.
		down=
		for h in \$(ms_reverse \"\$stack\"); do
			sudo modprobe \$h 2>/dev/null || true
			grep -q \"^\$h \" /proc/modules || down=\"\$down \$h\"
		done

		if [ \$rc -eq 3 ]; then
			echo \">> $name is loaded and still held, so it could not be unloaded.\"
			echo \">> The copy on disk IS updated -- reboot to run it, or unbind\"
			echo \">> whatever holds it and re-run.\"
			exit 3
		fi
		if [ -n \"\$down\" ]; then
			echo \">> the new $name is loaded, but this did not come back:\$down\"
			echo \">> the stack is DOWN -- reboot to restore it.\"
			exit 5
		fi
		echo '>> loaded $name'"
	case $? in
		0) ;;
		3) echo ">> not reloaded this boot -- skipping the srcversion check,"
		   echo ">>   which would report the OLD module and read as a bad build."
		   return 3 ;;
		4) echo ">> not reloaded: this module carries the link you are on."
		   echo ">>   the build is fine and installed; nothing was torn down."
		   return 4 ;;
		# The module IS the new one, so the proof below still runs and still
		# means something. The stack being down is reported after it.
		5) stack_down=1 ;;
		*) return 1 ;;
	esac

	# Prove the module that is RUNNING is the one just built.
	#
	# `insmod` exiting 0 is not that proof: the unbind/rmmod above can leave the
	# old module in place when something still holds it, and insmod then fails
	# in ways that read as success at this level. srcversion is a hash of the
	# module source as built, so comparing the .ko on disk against
	# /sys/module/<name>/srcversion answers "which binary answered" -- the same
	# discipline brain/traps/prove-which-kernel-answered.md applies to kernels.
	#
	# This is also what removes the caller's reason to sleep-then-poll: when
	# this returns 0 the new code is loaded, now, and the next test can run.
	local want sysname
	want=$(modinfo -F srcversion "$ko" 2>/dev/null)
	sysname=${name//-/_}
	if [ -z "$want" ]; then
		echo ">> WARNING: built $name.ko has no srcversion -- cannot verify the load"
		_ph_pushed_write   # it did reach the device; only the proof is missing
		return 0
	fi
	ssh "${TK_SSH_OPTS[@]}" "$phone" "
		got=\$(cat /sys/module/$sysname/srcversion 2>/dev/null)
		if [ -z \"\$got\" ]; then
			echo '>> $name is NOT loaded (/sys/module/$sysname absent)'; exit 1; fi
		if [ \"\$got\" != '$want' ]; then
			echo \">> STALE: running $name is srcversion \$got, built is $want\"
			echo '>> the old module never unloaded -- something still holds it'
			exit 1; fi
		echo '>> verified: running $name is the build just pushed ($want)'" || return 1
	_ph_pushed_write
	[ -z "$stack_down" ] || {
		echo ">> ...but the stack above $name did not come back. Reboot."
		return 5; }
}

# FAST loop for DTS and built-in code: make, repack, RAM-boot. No pmbootstrap.
#
# `_ph_make` ends with three `pmbootstrap build` calls, an `index`, an
# `apk add` and an `export`. For a `fastboot boot` test NONE of that is needed:
# the Image.gz and the DTB exist the moment `make` returns, and
# bootimg-repack-dtb.py splices both into an existing boot.img (its --kernel
# flag exists for exactly this). Packaging is only required when the phone must
# boot the change from flash, or when modules must be installed.
#
# Measured on this tree: ~4 min -> ~40 s for a DTS or clock-driver edit.
#
#   tkboot                 # make dtbs only, repack, boot
#   tkboot --kernel        # built-in code changed too: make Image.gz as well
# The base image the dtb is spliced into.
#
# The old default was /tmp/tk-base-boot.img, with "seed it once from a
# known-good UUID-patched boot.img" printed when it was absent. That
# instruction cannot be followed from where the build runs: the workspace
# container does not mount the host's /tmp, so seeding the named path on the
# host changed nothing, and pointing TK_BASEIMG at a scratch path failed the
# same way for the same reason.
#
# Left EMPTY by default and resolved inside tkboot against the device's own
# kernel release. TK_BASEIMG still overrides, for a base image you have and the
# device cannot supply.
_PH_BASEIMG=${TK_BASEIMG:-}

# Which partition holds a known-good boot image.
#
# Derived, never hardcoded: this file is `scope: generic`, and a device without
# A/B slots has a plain `boot`. TK_BOOT_PARTLABEL is the escape hatch for a
# bootloader that names it something else.
_ph_boot_partlabel() {
	if [ -n "${TK_BOOT_PARTLABEL:-}" ]; then
		printf '%s\n' "$TK_BOOT_PARTLABEL"
	elif [ "${PORTHOLE_HAS_AB_SLOTS:-0}" = "1" ] && [ -n "${PORTHOLE_ACTIVE_SLOT:-}" ]; then
		printf 'boot_%s\n' "$PORTHOLE_ACTIVE_SLOT"
	else
		printf 'boot\n'
	fi
}

# Pull a known-good base image off the device.
#
# The active slot's boot partition IS a known-good, UUID-patched image by
# definition, so there is nothing for a human to find and nothing to seed by
# hand. Cached under .run, which the container DOES mount.
#
# Keyed on the device's kernel release by the caller, and that is what makes
# the cache safe rather than merely convenient: after a `fast` flash the key
# changes and the image re-seeds, so a DTS-only RAM boot can never carry a
# stale kernel behind a fresh dtb -- a failure that would present as a bad
# devicetree and cost a boot to diagnose.
#
# Verify the artifact, not the exit code: a truncated read is still a file, and
# a repack from a truncated base produces an image the bootloader rejects with
# nothing here having complained. bootimg-cmdline.py already answers exactly
# "is this an Android boot image" and refuses with a sentence that says so.
_ph_seed_baseimg() {
	local part tmp
	[ -s "$_PH_BASEIMG" ] && return 0
	part=$(_ph_boot_partlabel)
	echo ">> no base image yet -- seeding from the device's $part"
	mkdir -p "$(dirname "$_PH_BASEIMG")" || return 1
	tmp="$_PH_BASEIMG.partial"
	if ! TK_RUN_TIMEOUT=15 tk_run "test -e /dev/disk/by-partlabel/$part" >/dev/null 2>&1; then
		echo ">> the device has no /dev/disk/by-partlabel/$part" >&2
		echo ">> set TK_BOOT_PARTLABEL to the partition holding a good boot image," >&2
		echo ">> or TK_BASEIMG to a known-good UUID-patched boot.img you already have" >&2
		return 1
	fi
	# `cat`, not `dd`: dd's count/bs would have to be guessed per device, and
	# the partition is exactly the image. sudo because the block device is not
	# world-readable.
	if ! TK_RUN_TIMEOUT=180 tk_run "sudo cat /dev/disk/by-partlabel/$part" > "$tmp" 2>/dev/null; then
		rm -f "$tmp"
		echo ">> could not read $part from the device" >&2
		return 1
	fi
	if ! "$_PH_REPO_ROOT/tools/bootimg-cmdline.py" show "$tmp" >/dev/null 2>&1; then
		rm -f "$tmp"
		echo ">> what came off $part is not a boot image -- refusing to cache it" >&2
		echo ">> (a truncated read is still a file; the repack would not say so)" >&2
		return 1
	fi
	mv "$tmp" "$_PH_BASEIMG" || return 1
	echo ">> base image cached at $_PH_BASEIMG"
}

tkboot() {
	local with_kernel=""
	[ "${1:-}" = "--kernel" ] && with_kernel=1

	_ph_announce_tree

	# --kernel RAM-boots a freshly built Image against the initramfs and the
	# /lib/modules ALREADY on the device. Those modules will not load: a rebuild
	# moves the build id and the BTF, and modprobe refuses every .ko with
	# "failed to validate module BTF: -22". A DTS-only boot is unaffected, which
	# is why the plain rung is safe and this one is not.
	#
	# On a device whose initramfs needs a module to mount root, that is fatal
	# rather than merely degraded: taimen loop-mounts its pmOS subpartition, so
	# without loop.ko the initramfs never finds root and drops to the debug
	# shell -- which reads exactly like a bad kernel and cost a boot on
	# 2026-08-27 before anyone looked at /proc/version and saw a different build.
	if [ -n "$with_kernel" ]; then
		if [ -n "${PORTHOLE_RAMBOOT_NEEDS_MODULES:-}" ]; then
			echo ">> REFUSING: this device needs ${PORTHOLE_RAMBOOT_NEEDS_MODULES} to mount root," >&2
			echo ">> and a freshly built kernel cannot load the modules on the device." >&2
			echo ">> The RAM boot would land in the initramfs debug shell." >&2
			echo ">> Use \`porthole build fast --yes\` -- it rebuilds the initramfs and" >&2
			echo ">> pushes matching modules. Set PORTHOLE_RAMBOOT_NEEDS_MODULES= to override." >&2
			return 1
		fi
		echo ">> note: --kernel rebuilds Image.gz, so the modules on the device"
		echo ">>       will NOT load against it. Fine if this device reaches"
		echo ">>       userspace without them; use \`fast\` if it does not."
	fi

	# BEFORE the compile, not after. `auto` routes to `boot` without checking
	# the rung can run, so a missing base image used to surface minutes into a
	# make -- the cost paid before the news.
	if [ -z "$_PH_BASEIMG" ]; then
		local _rel _rundir
		_rundir=${PORTHOLE_RUNDIR:-$_PH_REPO_ROOT/.run}
		_rel=$(TK_RUN_TIMEOUT=8 tk_run 'uname -r' 2>/dev/null | tr -d '\r\n')
		[ -n "$_rel" ] || {
			echo ">> cannot reach the device, so no base image can be seeded" >&2
			echo ">> boot the phone, or set TK_BASEIMG to a known-good" >&2
			echo ">> UUID-patched boot.img" >&2
			return 1; }
		_PH_BASEIMG="$_rundir/base-boot-$_rel.img"
	fi
	_ph_seed_baseimg || return 1

	_ph_activate || return 1
	shopt -s expand_aliases
	if [ -n "$with_kernel" ]; then
		eval make -j"$(nproc)" Image.gz dtbs || { popd >/dev/null || return 1; return 1; }
	else
		eval make -j"$(nproc)" dtbs || { popd >/dev/null || return 1; return 1; }
	fi
	popd >/dev/null || return 1

	local out=/tmp/tk-fast-boot.img
	if [ -n "$with_kernel" ]; then
		"$_PH_REPO/tools/bootimg-repack-dtb.py" "$_PH_BASEIMG" \
			"$_PH_DTB_BUILT" "$out" \
			--kernel "$_PH_OUT/arch/arm64/boot/Image.gz" || return 1
	else
		"$_PH_REPO/tools/bootimg-repack-dtb.py" "$_PH_BASEIMG" \
			"$_PH_DTB_BUILT" "$out" || return 1
	fi

	# Baseline before the device moves; see the comment in tkflash-boot.
	local old_id; old_id=$(tk_boot_id 2>/dev/null || true)
	"$_PH_REPO/tools/tk-to-fastboot.sh" || return 1
	"$FASTBOOT" boot "$out" || return 1
	_ph_wait_up "$old_id" || return 1
	_ph_pushed_write
}
