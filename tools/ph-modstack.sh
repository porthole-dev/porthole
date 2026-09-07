# SPDX-License-Identifier: MIT
# Reading the module stack, for tkmod's reload path. SOURCED, never run:
# tkmod pastes this into its remote script, and the tests source it against a
# fixture sysfs.
#
# shellcheck shell=sh   # sourced, and by whatever /bin/sh the phone has
# scope: generic
# needs: on-device (tkmod pastes it into its remote script; the tests source it
#        here against a fixture sysfs)
# env: SYS (sysfs root, /sys unless a test says otherwise)
# exits: 0 -- every answer comes back on stdout; an empty one is a real answer
#
# WHY A FILE, and not more of tkmod's here-doc:
#
# `porthole build mod ath10k_core` compiled, pushed, and then gave up --
# "loaded and still held, so it could not be unloaded" -- because tkmod looked
# for a driver directory named after the module it was replacing. That holds
# for the camera sensor it was written for and for nothing stacked: ath10k_core
# has no devices of its own, ath10k_snoc holds it, and unbinding a directory
# that does not exist unbinds nothing. Measured on taimen 2026-08-31: 78 of 286
# loaded modules have holders, so this is the ordinary case, not the exception.
#
# These three answers decide whether a wifi driver comes off a phone. A
# decision that can only be exercised by pushing a module to a device is a
# decision nobody tests, and the last defect in this file's neighbourhood was
# exactly that -- a fix that was real in one push path and missing in the other.
# The mutating half (rmmod, insmod, modprobe) stays in tkmod.
: "${SYS:=/sys}"

ms_holders() {
	# The modules that hold $1 -- one level, as sysfs reports it.
	ls "$SYS/module/$1/holders" 2>/dev/null
}

ms_stack() {
	# Every module that must come off before $1 can, in removal order.
	#
	# Depth first, deepest first: cfg80211 is held by mac80211 which is held by
	# ath10k_core which is held by ath10k_snoc, and removing them in the order
	# sysfs lists them would fail on the first one. Post-order emits a module
	# only after everything holding IT, which is exactly the removal order.
	#
	# The seen list is not paranoia about cycles: cfg80211 reaches ath10k_core
	# twice, directly and through mac80211, and without it the module would be
	# removed once and rmmod'd again on a name that is already gone.
	_ms_seen=" "
	_ms_out=""
	_ms_walk "$1"
	echo $_ms_out
}

_ms_walk() {
	# Positional parameters, because sh has no locals and this recurses: a
	# `for h in ...` loop would have every nested call overwrite its caller's
	# variable. $1 survives a nested call; a global would not.
	# shellcheck disable=SC2046  # word splitting is the point: one name per holder
	set -- $(ms_holders "$1")
	while [ $# -gt 0 ]; do
		case $_ms_seen in
		*" $1 "*) shift; continue ;;
		esac
		_ms_seen="$_ms_seen$1 "
		_ms_walk "$1"
		_ms_out="$_ms_out $1"
		shift
	done
}

ms_reverse() {
	# $1 as a list, back to front -- the order the stack goes back together.
	_ms_rev=""
	# shellcheck disable=SC2086  # ditto
	set -- $1
	while [ $# -gt 0 ]; do
		_ms_rev="$1 $_ms_rev"
		shift
	done
	echo $_ms_rev
}

ms_transport_mod() {
	# The module driving the interface THIS ssh session arrived on, if any.
	#
	# From SSH_CONNECTION rather than from a configured address: the question
	# is which link the session in progress is actually using, and a device
	# with both usb0 and wifi up has two right answers to anything else.
	# Silence is a real answer -- taimen's usb0 gadget resolves to
	# libcomposite, but a plain ethernet port may name no module at all, and
	# refusing to reload because we could not identify the transport would
	# block the rung far more often than the hazard it guards.
	[ -n "${SSH_CONNECTION:-}" ] || return 0
	_ms_if=$(ip route get "${SSH_CONNECTION%% *}" 2>/dev/null |
		sed -n 's/.* dev \([^ ][^ ]*\).*/\1/p' | head -1)
	[ -n "$_ms_if" ] || return 0
	_ms_link=$(readlink -f "$SYS/class/net/$_ms_if/device/driver/module" 2>/dev/null)
	# return 0 on the empty answer too: a non-zero exit here would abort the
	# caller's `set -e` script rather than mean "no module drives it".
	[ -n "$_ms_link" ] || return 0
	basename "$_ms_link"
}

ms_conflict() {
	# Echo the module carrying this session if taking $1's stack down would
	# take the session with it, and say nothing when it would not.
	#
	# THE guardrail. Unloading the driver you are reaching the device over
	# does not fail, it strands: the rmmod succeeds, the link drops, and
	# nothing is left to run the modprobe that would bring it back. Over usb0
	# an ath10k reload is safe and this stays out of the way; over wifi it is
	# the difference between a failed rung and a phone that needs a reboot.
	_ms_t=$(ms_transport_mod)
	[ -n "$_ms_t" ] || return 1
	[ "$_ms_t" = "$1" ] && { echo "$_ms_t"; return 0; }
	# shellcheck disable=SC2046  # ditto
	set -- $(ms_stack "$1")
	while [ $# -gt 0 ]; do
		[ "$1" = "$_ms_t" ] && { echo "$_ms_t"; return 0; }
		shift
	done
	return 1
}

ms_unbind() {
	# Unbind every device bound to $1, on whatever bus it sits on.
	#
	# The bus list used to be i2c then platform, named after the one sensor
	# this was written for. A module bound on any other bus was silently
	# skipped, and so was every module with no devices of its own.
	for _ms_d in "$SYS"/bus/*/drivers/"$1"; do
		[ -d "$_ms_d" ] || continue
		for _ms_dev in "$_ms_d"/*; do
			# A bound device has a driver symlink; bind, unbind, uevent and
			# module do not. Naming the files to skip would need updating
			# every time sysfs grows one.
			[ -e "$_ms_dev/driver" ] || continue
			basename "$_ms_dev" | sudo tee "$_ms_d/unbind" >/dev/null 2>&1 || true
		done
	done
}
