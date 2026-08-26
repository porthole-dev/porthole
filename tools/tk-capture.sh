#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: BOOTED
# env: HOST, PHONE, TK_CAP_PORT, TK_CAP_WLAN
# exits: 0 ok · 1 failed
# tk-capture.sh -- arm every log channel this device has, from the HOST, and
# say at the end whether anything died.
#
#   tools/tk-capture.sh [outdir] [seconds]
#   tools/tk-capture.sh /tmp/cap 900
#
# Ctrl-C stops it early and still prints the verdict.
#
# WHY THIS EXISTS
#   On 2026-08-20 the phone took a real kernel panic and
#   `journalctl -b -1 -k | grep -c "Kernel panic"` returned ZERO. The journal
#   does not survive the reset that kills it, pstore comes up empty on this
#   device, and there is no serial cable and never will be. netconsole is the
#   only channel that survives, and until that day nobody had armed it -- on the
#   strength of a claim in docs/HANDOFF-SUSPEND.md:34 that turned out to be
#   false. Every hang investigated before that was investigated blind.
#
#   So: arm the survivor channel FIRST, then the convenient ones.
#
# THE FOUR CHANNELS, and how each one fails
#
#   netconsole/usb0   survives a panic, survives userspace death. THE witness.
#   netconsole/wlan0  same, but see the hardirq trap below. Needed for suspend
#                     tests, where USB goes down with the phone.
#   dmesg -w          dies with the phone; convenient while it lives.
#   journalctl -f     same, but carries userspace, which the kernel channels
#                     cannot see -- the phoc SIGSEGV of 2026-08-20 was found
#                     through coredumpctl, not through dmesg.
#
# TRAP 1 -- usb0 is the PRIMARY, wlan0 the secondary, and the order matters.
#   A printk from hardirq context (which is where IOMMU/GPU fault handlers live)
#   is flushed to the console synchronously. On the wlan0 path that reaches
#   mac80211's ieee80211_queue_skb(), which takes a _bh lock it must not:
#     WARNING: kernel/softirq.c:429 __local_bh_enable_ip  <- WARN_ON_ONCE(in_hardirq())
#       _raw_spin_unlock_bh / ieee80211_queue_skb [mac80211] / netpoll_send_udp
#       / write_msg [netconsole] / adreno_fault_handler / arm_smmu_context_fault
#   It is WARN_ON_ONCE so it fires once per boot and does not amplify anything,
#   but it taints the kernel and pollutes exactly the trace you came for. The
#   u_ether (usb0) target was armed in the same capture, is flushed by the same
#   printk, and does not warn. Hence TK_CAP_WLAN defaults to 0.
#
# TRAP 2 -- never `pkill -f <pattern>` over ssh with a pattern that appears in
#   your own command line. `pkill -f xdg-permission-store` killed the ssh
#   session that ran it on 2026-08-20. Everything here kills by recorded PID.
#
# TRAP 3 -- "ssh answers" is not "it did not reboot". Compare boot_id, which is
#   what tk-lib.sh's tk_boot_id does and what the verdict below uses.
#
# TRAP 4 -- host firewall. Fedora's FedoraWorkstation zone already allows
#   1025-65535/udp, so 6666/6667 work with no change. Port 514 would NOT.
#
# ponytail: no log rotation, no compression, no config file. A 15-minute
# capture is a few MB. Add rotation when a capture actually runs long enough
# to care.
set -u

_TK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tk-lib.sh
. "$_TK_DIR/tk-lib.sh"

OUT=${1:-/tmp/tk-capture-$(date +%H%M%S)}
DUR=${2:-900}
TK_CAP_WLAN=${TK_CAP_WLAN:-0}      # 1 = also arm wlan0 (suspend tests; see TRAP 1)
TK_CAP_PORT=${TK_CAP_PORT:-6666}

mkdir -p "$OUT" || exit 1

# The device needs to be up to be armed. Fail in a second rather than hang.
if ! ssh "${TK_SSH_OPTS[@]}" "$PHONE" true 2>/dev/null; then
	echo "tk-capture: $PHONE does not answer -- is the phone up?" >&2
	exit 1
fi

# Every arm step below is `sudo -n ... >/dev/null 2>&1`, because a channel this
# device does not have must not be an error. The cost of that: with no
# passwordless sudo, EVERY step fails silently, the script arms nothing, and the
# only symptom is an empty log noticed hours later -- after the run it was meant
# to capture is over and the device has moved on.
#
# So check the one precondition all of them share, once, loudly, before arming.
# brain/traps/no-passwordless-sudo-disables-the-whole-toolbox.md
if ! ssh "${TK_SSH_OPTS[@]}" "$PHONE" 'sudo -n true' 2>/dev/null; then
	echo "tk-capture: no passwordless sudo on $PHONE -- it would arm NOTHING" >&2
	echo "  and you would find out from an empty log, after the run." >&2
	echo "  Fix it on the DEVICE, then re-run:" >&2
	echo "    echo '<user> ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/99-porthole-dev" >&2
	echo "    sudo chmod 0440 /etc/sudoers.d/99-porthole-dev" >&2
	echo "  porthole doctor checks this too." >&2
	exit 1
fi

BOOT_BEFORE=$(tk_boot_id)
HOST_USB_MAC=$(cat /sys/class/net/enp5s0f3u2/address 2>/dev/null)
HOST_WLAN_MAC=$(cat /sys/class/net/wlp3s0/address 2>/dev/null)
HOST_WLAN_IP=$(ip -4 -br addr show wlp3s0 2>/dev/null | awk '{print $3}' | cut -d/ -f1)

# ---------------------------------------------------------------- arm the device
# netconsole is CONFIG_NETCONSOLE=y since kernel r28, so the modprobe is a no-op
# there; it is kept for older images where it was =m. Either way the target is
# configured through configfs at runtime, which is what NETCONSOLE_DYNAMIC is for
# -- do NOT put a target on the cmdline, it cannot follow a DHCP address.
arm_target() {  # name dev local_ip remote_ip remote_mac port
	local n=$1 d=$2 li=$3 ri=$4 rm=$5 rp=$6
	[ -n "$rm" ] || return 0
	ssh "${TK_SSH_OPTS[@]}" "$PHONE" "
		p=/sys/kernel/config/netconsole/$n
		[ -d \$p ] && { echo 0 | sudo -n tee \$p/enabled >/dev/null 2>&1; sudo -n rmdir \$p 2>/dev/null; }
		sudo -n mkdir -p \$p 2>/dev/null || exit 1
		echo $li | sudo -n tee \$p/local_ip   >/dev/null
		echo $ri | sudo -n tee \$p/remote_ip  >/dev/null
		echo $rm | sudo -n tee \$p/remote_mac >/dev/null
		echo $rp | sudo -n tee \$p/remote_port>/dev/null
		echo $d  | sudo -n tee \$p/dev_name   >/dev/null
		echo 1   | sudo -n tee \$p/enabled    >/dev/null
		cat \$p/enabled" 2>/dev/null
}

{
	ssh "${TK_SSH_OPTS[@]}" "$PHONE" 'sudo -n modprobe netconsole 2>/dev/null; true'
	echo "usb0  enabled=$(arm_target usb usb0 "$HOST" 172.16.42.2 "$HOST_USB_MAC" "$TK_CAP_PORT")"
	if [ "$TK_CAP_WLAN" = 1 ]; then
		WIP=$(ssh "${TK_SSH_OPTS[@]}" "$PHONE" "ip -4 -br addr show wlan0 2>/dev/null | awk '{print \$3}' | cut -d/ -f1")
		echo "wlan0 enabled=$(arm_target wifi wlan0 "$WIP" "$HOST_WLAN_IP" "$HOST_WLAN_MAC" $((TK_CAP_PORT+1)))"
	fi

	# A watchdog bark is silence unless the pretimeout governor turns it into a
	# panic, and the panic is what netconsole can carry. CONFIG_WATCHDOG_
	# PRETIMEOUT_GOV_PANIC has always been built in; only the runtime default
	# was noop (fixed in the shipped config from r28, set here too for older
	# images). sysrq is wanted so `echo c > /proc/sysrq-trigger` can prove the
	# channel end to end before trusting it.
	ssh "${TK_SSH_OPTS[@]}" "$PHONE" '
		echo panic | sudo -n tee /sys/class/watchdog/watchdog0/pretimeout_governor >/dev/null 2>&1
		echo 1     | sudo -n tee /proc/sys/kernel/sysrq >/dev/null 2>&1
		echo "watchdog gov=$(cat /sys/class/watchdog/watchdog0/pretimeout_governor 2>/dev/null)" \
		     "timeout=$(cat /sys/class/watchdog/watchdog0/timeout 2>/dev/null)" \
		     "panic=$(cat /proc/sys/kernel/panic 2>/dev/null)" \
		     "hung_task=$(cat /proc/sys/kernel/hung_task_timeout_secs 2>/dev/null)"'
} 2>&1 | tee "$OUT/arm.log"

# ------------------------------------------------------------------- baseline
# Counters, so the verdict can report a DELTA. "17 GPU faults" means nothing
# without knowing it started at 0.
ssh "${TK_SSH_OPTS[@]}" "$PHONE" '
	echo "boot_id=$(cat /proc/sys/kernel/random/boot_id)"
	echo "uptime=$(cut -d. -f1 /proc/uptime)"
	echo "gpu_faults=$(sudo -n dmesg 2>/dev/null | grep -c "gpu fault")"
	echo "hangchecks=$(sudo -n dmesg 2>/dev/null | grep -c "hangcheck detected gpu lockup")"
	echo "coredumps=$(sudo -n coredumpctl list --no-pager 2>/dev/null | grep -c SIGSEGV)"
	echo "underrun=$(sudo -n cat /sys/kernel/debug/dri/0/encoder-0/status 2>/dev/null | sed -n "s/.*underrun: *\([0-9]*\).*/\1/p")"
	' > "$OUT/baseline" 2>/dev/null
# shellcheck disable=SC1090
. "$OUT/baseline" 2>/dev/null || true

# ------------------------------------------------------------------ listeners
# A python UDP sink, not nc: OpenBSD nc rejects `-l -p` together, which silently
# produced an empty capture on 2026-08-20 -- an instrument that fails quietly is
# worse than no instrument. Both netconsole paths carry the same lines, so
# identical payloads are printed once, tagged with the port that won the race.
python3 -c '
import socket, sys, time, datetime
ports = [int(sys.argv[1])] + ([int(sys.argv[1]) + 1] if sys.argv[2] == "1" else [])
dur = float(sys.argv[3])
socks = []
for p in ports:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", p)); s.settimeout(0.2); socks.append((p, s))
end, seen = time.time() + dur, set()
while time.time() < end:
    for p, s in socks:
        try: d, _ = s.recvfrom(65535)
        except socket.timeout: continue
        t = d.decode("utf-8", "replace").rstrip("\n")
        if t in seen: continue          # same line on both paths
        seen.add(t)
        if len(seen) > 20000: seen.clear()
        print(f"{datetime.datetime.now():%H:%M:%S.%f} [nc:{p}] {t}", flush=True)
' "$TK_CAP_PORT" "$TK_CAP_WLAN" "$DUR" > "$OUT/netconsole.log" 2>&1 &
NC_PID=$!
sleep 0.5

ssh "${TK_SSH_OPTS[@]}" "$PHONE" 'sudo -n dmesg -w'                        > "$OUT/dmesg.log"   2>&1 & DM_PID=$!
ssh "${TK_SSH_OPTS[@]}" "$PHONE" 'sudo -n journalctl -f -o short-iso -n0'  > "$OUT/journal.log" 2>&1 & JR_PID=$!

echo "$NC_PID $DM_PID $JR_PID" > "$OUT/pids"

cleanup() {
	# By PID, never by pattern -- see TRAP 2.
	kill "$NC_PID" "$DM_PID" "$JR_PID" 2>/dev/null
	wait "$NC_PID" "$DM_PID" "$JR_PID" 2>/dev/null
}
trap 'echo; verdict; cleanup; exit 0' INT TERM

# -------------------------------------------------------------------- verdict
verdict() {
	echo
	echo "=============== tk-capture verdict: $OUT"
	local nowboot
	nowboot=$(tk_boot_id 2>/dev/null)
	if [ -z "$nowboot" ]; then
		echo "  DEVICE IS NOT ANSWERING -- it is hung or still rebooting."
		echo "  netconsole.log is the only witness; its tail is the last thing it said:"
		tail -20 "$OUT/netconsole.log" 2>/dev/null | sed 's/^/    /'
		return
	fi
	if [ "$nowboot" != "$BOOT_BEFORE" ]; then
		echo "  REBOOTED during the capture (boot_id changed)."
		ssh "${TK_SSH_OPTS[@]}" "$PHONE" 'grep -o "androidboot.bootreason=[a-z,]*" /proc/cmdline' 2>/dev/null | sed 's/^/    /'
		echo "    reboot != watchdog: a panic reboots as 'reboot' too, because"
		echo "    kernel.panic drives emergency_restart(). Read netconsole.log to tell them apart."
	else
		echo "  no reboot (boot_id unchanged)"
	fi

	# NOTE these counts are CUMULATIVE for the boot, not for the window:
	# `dmesg -w` replays the whole ring buffer before it follows. The
	# baseline->now deltas printed after them are the windowed numbers.
	echo "  --- seen this boot (cumulative) ---"
	for pat in 'Kernel panic' 'Unable to handle kernel' 'Internal error' \
	           'WARNING:' 'BUG:' 'hung_task' 'blocked for more than' \
	           'gpu fault' 'hangcheck detected gpu lockup' 'frame_done_timeout' \
	           'rcg didn.t update'; do
		local n; n=$(grep -ac "$pat" "$OUT/netconsole.log" "$OUT/dmesg.log" 2>/dev/null | awk -F: '{s+=$2} END{print s+0}')
		[ "${n:-0}" -gt 0 ] && printf '  %-34s %s\n' "$pat" "$n"
	done

	# Deltas against the baseline, which is the only way "17 faults" means anything.
	ssh "${TK_SSH_OPTS[@]}" "$PHONE" '
		echo "gpu_faults_now=$(sudo -n dmesg 2>/dev/null | grep -c "gpu fault")"
		echo "hangchecks_now=$(sudo -n dmesg 2>/dev/null | grep -c "hangcheck detected gpu lockup")"
		echo "coredumps_now=$(sudo -n coredumpctl list --no-pager 2>/dev/null | grep -c SIGSEGV)"' \
		> "$OUT/after" 2>/dev/null
	# shellcheck disable=SC1090
	. "$OUT/after" 2>/dev/null || true
	echo "  --- delta over this capture window ---"
	printf '  %-34s %s -> %s\n' 'gpu faults'  "${gpu_faults:-?}" "${gpu_faults_now:-?}"
	printf '  %-34s %s -> %s\n' 'hangchecks'  "${hangchecks:-?}" "${hangchecks_now:-?}"
	printf '  %-34s %s -> %s\n' 'SIGSEGV coredumps' "${coredumps:-?}" "${coredumps_now:-?}"

	# A userspace crash is invisible to both kernel channels. This is how the
	# phoc SIGSEGV was found; symbolize with `apk add <pkg>-dbg` then
	# `coredumpctl info <pid>` -- note coredumpctl caches the trace at dump
	# time, so install the -dbg package BEFORE the crash you care about.
	ssh "${TK_SSH_OPTS[@]}" "$PHONE" 'sudo -n coredumpctl list --no-pager 2>/dev/null | tail -4' 2>/dev/null |
		sed 's/^/    /'
	echo "==============="
}

echo ">> capturing to $OUT for ${DUR}s (Ctrl-C to stop early)"
echo ">> baseline: gpu_faults=${gpu_faults:-?} hangchecks=${hangchecks:-?} coredumps=${coredumps:-?}"
sleep "$DUR"
verdict
cleanup
