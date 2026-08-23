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
_PH_TREE="$_PH_REPO/linux"
_PH_PMB=${PORTHOLE_PMB_DIR:-$_PH_PMB}
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
tkclean() {
	local mk="$_ph_mnt/.output/Makefile"
	local d prev
	d=$(_ph_depth)
	if [ "$d" -eq 0 ]; then
		echo ">> /mnt/linux not mounted -- nothing to unstack"
		return 0
	fi
	echo ">> /mnt/linux is stacked ${d} deep; peeling"
	sudo -v || { echo ">> need sudo to unmount"; return 1; }

	for _ in $(seq 1 80); do   # bounded: never spin forever on a stuck mount
		prev=$(_ph_depth)
		[ "$prev" -eq 0 ] && break
		# Try the shadowed Makefile every round; it only succeeds once we reach
		# the bottom layer it is attached to.
		sudo umount "$mk" 2>/dev/null
		# Do NOT hide this error -- suppressing it is what made the first version
		# of this function report "no progress" with no explanation.
		if ! sudo umount "$_ph_mnt"; then
			echo ">> plain umount failed; retrying lazily (safe for bind mounts)"
			sudo umount -l "$_ph_mnt" || { echo ">> lazy umount failed too"; break; }
		fi
		if [ "$(_ph_depth)" -eq "$prev" ]; then
			echo ">> depth stuck at ${prev} -- not making progress"
			break
		fi
	done

	sudo umount "$mk" 2>/dev/null
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

_ph_make() {
	local out="$_PH_TREE/.output"
	local img="$out/arch/${PORTHOLE_ARCH_DIR}/boot/Image.gz"
	local dtb="$out/arch/${PORTHOLE_ARCH_DIR}/boot/dts/${PORTHOLE_DTB%/*}/$_PH_DTB"
	local dtsdir="$_PH_TREE/arch/${PORTHOLE_ARCH_DIR}/boot/dts/${PORTHOLE_DTB%/*}"

	# Re-sync the in-tree defconfig from pmaports so both can't drift.
	# MUST be the -6.18 aport config: the tree is v6.18 now. Syncing from the
	# 6.0 aport config (as this line did until 2026-08-09) clobbers 6.18-only
	# symbols with their pre-rename 6.0 names, which olddefconfig then drops
	# SILENTLY: CONFIG_QCOM_QFPROM=y instead of CONFIG_NVMEM_QCOM_QFPROM=y
	# killed the qusb2 fuse cell and with it ALL USB. Cost the night of 2026-08-08.
	cp "$_PH_REPO/pmaports/device/testing/$_PH_KPKG/${PORTHOLE_KCONFIG_FILE:-config-postmarketos-${PORTHOLE_SOC}.${PORTHOLE_ARCH}}" \
	   "$_PH_TREE/arch/${PORTHOLE_ARCH_DIR}/configs/${PORTHOLE_DEFCONFIG}" || return 1

	# Clear any stacked /mnt/linux binds BEFORE adding another one, or pmbootstrap
	# will abort on the shadowed .output/Makefile overmount. See tkclean.
	tkclean || { echo ">> could not unstack /mnt/linux -- run 'pmbootstrap shutdown' and retry"; return 1; }

	# Activate envkernel FRESH. A stale "active" flag with /mnt/linux not mounted
	# makes a guarded re-source skip the remount, and make then finds no Makefile.
	type deactivate >/dev/null 2>&1 && deactivate
	pushd "$_PH_TREE" >/dev/null || return 1
	set --   # `source` would pass our args to envkernel, which rejects them
	source "$HOME/src/pmbootstrap/helpers/envkernel.sh" || { popd >/dev/null || return 1; return 1; }

	# envkernel provides `make` as an ALIAS carrying ARCH=arm64 and the chroot
	# invocation. Bash expands aliases at PARSE time, and this function was parsed
	# when ph-build.sh was sourced -- before envkernel ran -- so a bare `make`
	# here resolves to the host binary instead, builds for x86 and dies with
	# `Can't find default configuration "arch/x86/configs/$PORTHOLE_DEFCONFIG"`.
	# eval re-parses at runtime, once the alias exists.
	shopt -s expand_aliases
	eval make "$PORTHOLE_DEFCONFIG" || { popd >/dev/null || return 1
		echo ">> defconfig FAILED"; return 1; }
	# make's exit code can lie once the tree is already built (BTF prep re-runs and
	# returns non-zero with nothing wrong), so verify artifacts instead of trusting it.
	eval make -j"$(nproc)"
	# This file is SOURCED, so a failed popd would strand the user's own
	# interactive shell in the kernel tree.
	popd >/dev/null || return 1

	if [ ! -s "$img" ]; then
		echo ">> no Image.gz at $img -- real build failure"; return 1
	fi
	if [ "$out/.config" -nt "$img" ]; then
		echo ">> Image.gz older than .config -- a config change did not compile"; return 1
	fi
	if [ "$dtsdir/pmi8998.dtsi" -nt "$dtb" ] || \
	   [ "$dtsdir/msm8998-google-wahoo.dtsi" -nt "$dtb" ] || \
	   [ "$dtsdir/${_PH_DTB%.dtb}.dts" -nt "$dtb" ]; then
		echo ">> dtb older than its DTS -- it did not rebuild (check dtc output)"; return 1
	fi
	echo ">> kernel + dtb current -- packaging"

	# Exit codes below lie (benign umount-32). Keep them chained; see header.
	# They lie in one direction only, so verify the artifact instead: a build
	# that produced no apk newer than the Image.gz it was meant to package is
	# a stale build, and everything downstream would carry the old kernel.
	local kapk_before kapk_after
	kapk_before=$(ls -t "$_PH_PMB"/packages/edge/${PORTHOLE_ARCH}/"$_PH_KPKG"-*.apk 2>/dev/null | head -1)
	pmbootstrap build --envkernel "$_PH_KPKG"
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
	pmbootstrap build "$_PH_FWPKG"
	pmbootstrap build "$_PH_DEVPKG"
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
	stale=$(ls "$repo"/linux-postmarketos-qcom-msm8998*_p*.apk 2>/dev/null | wc -l)
	[ "$stale" -eq 0 ] && return 0
	echo ">> purging $stale envkernel (_p) kernel apks that would outrank the release build"
	sudo mkdir -p "$repo/.stale-devpkgs" || return 1
	sudo sh -c "mv '$repo'/linux-postmarketos-qcom-msm8998*_p*.apk '$repo/.stale-devpkgs'/" || return 1
	pmbootstrap index || return 1
}

# Refuse to proceed if an envkernel package could outrank the release build.
_ph_assert_no_devpkgs() {
	local repo="$_PH_PMB/packages/edge/${PORTHOLE_ARCH}"
	local stale
	stale=$(ls "$repo"/linux-postmarketos-qcom-msm8998*_p*.apk 2>/dev/null | wc -l)
	if [ "$stale" -ne 0 ]; then
		echo "REFUSING: $stale envkernel (_p) kernel apks are in the local repo." >&2
		echo "apk sorts _p<timestamp> ABOVE -rNN, so one of those would be installed" >&2
		echo "instead of what you just built. Run tkpurge-devpkgs first." >&2
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
_ph_install_kernel_release() {
	local ver
	# shellcheck disable=SC2154  # pkgver/pkgrel are set by the sourced APKBUILD
	ver=$(. "$_PH_REPO/pmaports/device/testing/$_PH_KPKG/APKBUILD" 2>/dev/null
	      echo "$pkgver-r$pkgrel")
	[ -n "$ver" ] && [ "$ver" != "-r" ] || { echo ">> could not read $_PH_KPKG pkgver/pkgrel" >&2; return 1; }
	echo ">> installing $_PH_KPKG=$ver into the rootfs chroot"
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
	# Password comes from the environment so it is not committed. Set it once:
	#   export TK_PMOS_PASSWORD=...
	: "${TK_PMOS_PASSWORD:?set TK_PMOS_PASSWORD (the rootfs user password) before tkbuild}"
	pmbootstrap install --password "$TK_PMOS_PASSWORD" || return 1
	# install just reminted the filesystem UUIDs, so any recorded set is now a
	# lie -- and tkflash-boot would patch the fresh export back to the old ones.
	rm -f "$_PH_REPO/.device-uuids"
	pmbootstrap export || return 1

	echo
	echo ">> verifying the exported image is actually what we built"
	"$_PH_REPO/tools/bootimg-verify.py" \
		"$(readlink -f /tmp/postmarketOS-export/boot.img)" \
		--dtb "$_PH_DTB_BUILT" \
		--config "$_PH_PKGCONFIG" \
		--ref-config "$_PH_OUT/.config"
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
tkflash() {
	pmbootstrap flasher flash_rootfs || return 1
	tkflash-boot
}

tkflash-boot() {
	# Get there ourselves. tkpush-modules has to run while the phone is UP,
	# so the caller cannot have parked it in the bootloader beforehand.
	"$FASTBOOT" devices 2>/dev/null | grep -q fastboot || \
		"$_PH_REPO/tools/tk-to-fastboot.sh" || return 1

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
	local dtb="${TK_REF_DTB:-$_PH_DTB_BUILT}"

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

	fastboot flash boot_a "$img" || return 1
	fastboot flash boot_b "$img" || return 1
	# mainline needs Caleb's stub on the active slot; TWRP needs the stock one.
	fastboot flash dtbo_a "$_PH_REPO/dtbo/dtbo_idx12.img" || return 1
	fastboot flash dtbo_b "$_PH_REPO/dtbo/dtbo_idx12.img" || return 1
	fastboot set_active b    # also resets that slot's retry counter
	fastboot reboot
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
tkbuild-kernel() {
	_ph_make || return 1

	# -r: the rootfs chroot, not the build chroot. -U -u: refresh the index and
	# upgrade, so it picks up the apk just built rather than a cached older one.
	_ph_install_kernel_release || return 1
	pmbootstrap export || return 1

	local dtb="$_PH_DTB_BUILT"
	"$_PH_REPO/tools/bootimg-verify.py" \
		"$(readlink -f /tmp/postmarketOS-export/boot.img)" --dtb "$dtb" || {
		echo ">> refusing to flash a stale image"; return 1; }

	tkpush-modules || return 1
	tkflash-boot
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
		sudo mv /lib/modules/$kver /lib/modules/$kver.old
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
	ssh "$phone" 'cat /proc/cmdline' 2>/dev/null | tr ' ' '\n' |
		grep -E '^pmos_(boot|root)_uuid=' > "$_PH_REPO/.device-uuids"
	[ -s "$_PH_REPO/.device-uuids" ] &&
		echo ">> recorded device UUIDs: $(tr '\n' ' ' < "$_PH_REPO/.device-uuids")"
}

# FAST loop for driver-only changes: build the module, push it, reload it.
#
# A .ko change needs `make` and an insmod -- not a package, not a boot.img, not
# a reboot. tkbuild-kernel is for CONFIG and DTS changes; using it to iterate on
# one driver costs ~6 minutes a cycle instead of ~40 seconds, which is most of a
# session if you are chasing a sensor bring-up.
#
#   tkmod drivers/media/i2c/imx179.ko imx179
tkmod() {
	local rel=$1 name=$2 phone=${PHONE:-$PORTHOLE_USER@$HOST}
	[ -n "$rel" ] && [ -n "$name" ] || { echo ">> usage: tkmod <path/to/mod.ko> <modname>"; return 1; }

	tkclean || return 1
	type deactivate >/dev/null 2>&1 && deactivate
	pushd "$_PH_TREE" >/dev/null || return 1
	set --
	source "$HOME/src/pmbootstrap/helpers/envkernel.sh" || { popd >/dev/null || return 1; return 1; }
	shopt -s expand_aliases
	eval make -j"$(nproc)" modules || { popd >/dev/null || return 1; echo ">> build failed"; return 1; }
	popd >/dev/null || return 1

	local ko="$_PH_OUT/$rel"
	[ -f "$ko" ] || { echo ">> no module at $ko"; return 1; }

	scp -q "$ko" "$phone:/tmp/$name.ko" || return 1

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
	scp -q "/tmp/$name.ko.xz" "$phone:/tmp/$name.ko.xz" || return 1

	# rmmod alone is not enough once something holds the driver -- camss keeps a
	# reference to a sensor subdev, so the module refcount never reaches zero and
	# insmod then fails with "File exists", which reads like a stale file rather
	# than a busy module. Unbind every device first, then reload and let it
	# re-probe (camss re-registers its media device when the subdev comes back).
	ssh "$phone" "set -e
		d=/sys/bus/i2c/drivers/$name
		[ -d \$d ] || d=/sys/bus/platform/drivers/$name
		if [ -d \$d ]; then
			for dev in \$(ls \$d | grep -E '^[0-9]'); do
				echo \$dev | sudo tee \$d/unbind >/dev/null 2>&1 || true
			done
		fi
		sudo rmmod $name 2>/dev/null || true

		# Overwrite every installed copy so modprobe agrees with insmod.
		# Only the RUNNING kernel's tree -- /lib/modules also holds stale
		# .old/.broken copies from earlier flashes, and depmod would be
		# run against a version that is not booted.
		inst=\$(find /lib/modules/\$(uname -r) -name '$name.ko.xz' -o -name '${name//_/-}.ko.xz' 2>/dev/null)
		if [ -n \"\$inst\" ]; then
			for f in \$inst; do sudo cp /tmp/$name.ko.xz \$f; done
			sudo depmod -a
			echo \">> installed: \$inst\"
		else
			echo '>> WARNING: no installed .ko.xz found; modprobe will not see this build'
		fi

		sudo insmod /tmp/$name.ko
		echo '>> loaded $name'" || return 1
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
_PH_BASEIMG=${TK_BASEIMG:-/tmp/tk-base-boot.img}

tkboot() {
	local with_kernel=""
	[ "$1" = "--kernel" ] && with_kernel=1

	[ -f "$_PH_BASEIMG" ] || {
		echo ">> no base image at $_PH_BASEIMG"
		echo "   seed it once from a known-good UUID-patched boot.img:"
		echo "     cp <good>.img $_PH_BASEIMG"
		return 1
	}

	tkclean || return 1
	type deactivate >/dev/null 2>&1 && deactivate
	pushd "$_PH_TREE" >/dev/null || return 1
	set --
	source "$HOME/src/pmbootstrap/helpers/envkernel.sh" || { popd >/dev/null || return 1; return 1; }
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

	"$_PH_REPO/tools/tk-to-fastboot.sh" || return 1
	"$FASTBOOT" boot "$out"
}
