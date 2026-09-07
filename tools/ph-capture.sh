#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: generic
# needs: BOOTED
# env: HOST, PHONE, PORTHOLE_CAP_PORT, PORTHOLE_CAP_WLAN
# exits: 0 ok · 1 failed
# ph-capture.sh -- arm every log channel this device has, from the HOST, and
# say at the end whether anything died.
#
#   tools/ph-capture.sh [outdir] [seconds]
#   tools/ph-capture.sh /tmp/cap 900
#   tools/ph-capture.sh logs/netconsole 0   # 0 = forever, re-arms across reboots
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
#   printk, and does not warn. Hence PORTHOLE_CAP_WLAN defaults to 0.
#
# TRAP 2 -- never `pkill -f <pattern>` over ssh with a pattern that appears in
#   your own command line. `pkill -f xdg-permission-store` killed the ssh
#   session that ran it on 2026-08-20. Everything here kills by recorded PID.
#
# TRAP 3 -- "ssh answers" is not "it did not reboot". Compare boot_id, which is
#   what ph-lib.sh's tk_boot_id does and what the verdict below uses.
#
# TRAP 4 -- host firewall. Fedora's FedoraWorkstation zone already allows
#   1025-65535/udp, so 6666/6667 work with no change. Port 514 would NOT.
#
# ponytail: no log rotation, no compression, no config file. A 15-minute
# capture is a few MB. Add rotation when a capture actually runs long enough
# to care.
set -u

_TK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ph-lib.sh
. "$_TK_DIR/ph-lib.sh"

OUT=${1:-/tmp/tk-capture-$(date +%H%M%S)}
DUR=${2:-900}                      # 0 = stay up forever, re-arming across reboots
# The UDP sink takes a deadline, not a mode. Ten years is forever enough.
SINK_DUR=$DUR; [ "$DUR" = 0 ] && SINK_DUR=315360000
# Two transports by default in forever mode, one otherwise. TRAP 1 is a real
# cost -- a hardirq printk over wlan0 taints the kernel once per boot -- but it
# is the cost of a polluted trace against the cost of no trace at all, and on
# 2026-08-26 a watchdog reset on display wake produced NOTHING over usb0: no
# pretimeout panic, no hard-lockup report, and an empty pstore afterwards. When
# the failure can take the USB path down with it, one transport is one point of
# failure. Set PORTHOLE_CAP_WLAN=0 to go back to usb0 alone.
[ "$DUR" = 0 ] && PORTHOLE_CAP_WLAN=${PORTHOLE_CAP_WLAN:-1}
PORTHOLE_CAP_WLAN=${PORTHOLE_CAP_WLAN:-0}
PORTHOLE_CAP_PORT=${PORTHOLE_CAP_PORT:-6666}
PORTHOLE_RESCUE_PORT=${PORTHOLE_RESCUE_PORT:-2323}

mkdir -p "$OUT" || exit 1

# Run ONE shell command on the device, over whichever channel is answering.
#
# ssh is not a dependable way to reach a phone you are trying to instrument:
# the boot after a watchdog reset on 2026-08-26 came up with sshd not yet
# accepting sessions, this loop read that as "device down, nothing to arm", and
# the whole boot went uninstrumented -- the one boot most likely to crash
# again. The PAM-free rescue channel answers in exactly that window, and in the
# PID-1 freeze that it was written for, so fall back to it rather than wait.
#
# One command per line is the rescue channel's whole protocol, so everything
# sent through here must be a single line -- no shell state survives between
# lines, because each one is its own /bin/sh -c. Its banner is line 1.
# Fall back on TRANSPORT failure only -- ssh's own 255, or 124 from the timeout
# that killed it -- never on the command's exit status and never on empty
# output. Treating "printed nothing" as failure re-runs the command over the
# rescue channel, which is how the verify probe wrote its token to /dev/kmsg
# twice and every silent arming step ran twice.
dev_run() {
	local out rc
	out=$(timeout 12 ssh "${TK_SSH_OPTS[@]}" "$PHONE" "$1" 2>/dev/null); rc=$?
	if [ "$rc" != 255 ] && [ "$rc" != 124 ]; then
		printf '%s\n' "$out"
		return "$rc"
	fi
	out=$(printf '%s\nexit\n' "$1" | timeout 25 nc "$HOST" "$PORTHOLE_RESCUE_PORT" 2>/dev/null | tail -n +2)
	[ -n "$out" ] || return 1
	printf '%s\n' "$out"
}

# tk_boot_id is ssh-only, and the boot that needs arming most is the one where
# ssh is not up yet.
cap_boot_id() { dev_run 'cat /proc/sys/kernel/random/boot_id' | tr -d '\r'; }


# The device needs to be up to be armed. Fail in a second rather than hang.
# Asked through dev_run rather than ssh: a phone whose sshd is not accepting
# sessions is still worth arming, and is in fact the one most worth arming.
if ! dev_run 'echo up' >/dev/null; then
	echo "tk-capture: $PHONE answers on neither ssh nor the rescue channel" >&2
	echo "  -- is the phone up, and is the cable in?" >&2
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
if [ "$(dev_run 'sudo -n true && echo yes')" != yes ]; then
	echo "tk-capture: no passwordless sudo on $PHONE -- it would arm NOTHING" >&2
	echo "  and you would find out from an empty log, after the run." >&2
	echo "  Fix it on the DEVICE, then re-run:" >&2
	echo "    echo '<user> ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/99-porthole-dev" >&2
	echo "    sudo chmod 0440 /etc/sudoers.d/99-porthole-dev" >&2
	echo "  porthole doctor checks this too." >&2
	exit 1
fi

BOOT_BEFORE=$(cap_boot_id)

# Sampled per arming, NOT once at startup. u_ether hands the host a freshly
# randomised MAC every time the gadget re-enumerates, so the address is only
# good for the boot it was read on. Re-arming with the previous one leaves
# netconsole cheerfully reporting enabled=1 while transmitting at a MAC that no
# longer exists -- which is the worst failure this tool can have. It cost the
# first capture that crossed a reboot, and arm.log looked perfect throughout.
host_ifaces() {
	HOST_USB_MAC=$(cat /sys/class/net/enp5s0f3u2/address 2>/dev/null)
	HOST_WLAN_MAC=$(cat /sys/class/net/wlp3s0/address 2>/dev/null)
	HOST_WLAN_IP=$(ip -4 -br addr show wlp3s0 2>/dev/null | awk '{print $3}' | cut -d/ -f1)
}

# ---------------------------------------------------------------- arm the device
# netconsole is CONFIG_NETCONSOLE=y since kernel r28, so the modprobe is a no-op
# there; it is kept for older images where it was =m. Either way the target is
# configured through configfs at runtime, which is what NETCONSOLE_DYNAMIC is for
# -- do NOT put a target on the cmdline, it cannot follow a DHCP address.
arm_target() {  # name dev local_ip remote_ip remote_mac port
	local n=$1 d=$2 li=$3 ri=$4 rm=$5 rp=$6 p=/sys/kernel/config/netconsole/$1
	# An empty local_ip or remote_mac writes garbage into configfs and the
	# target then reports enabled=1 while going nowhere. wlan0 hits this on
	# every re-arm, because it is still associating when usb0 is already up.
	[ -n "$rm" ] && [ -n "$li" ] || return 0
	dev_run "[ -d $p ] && { echo 0 | sudo -n tee $p/enabled >/dev/null 2>&1; sudo -n rmdir $p 2>/dev/null; }; \
		 sudo -n mkdir -p $p 2>/dev/null || exit 1; \
		 echo $li | sudo -n tee $p/local_ip >/dev/null; \
		 echo $ri | sudo -n tee $p/remote_ip >/dev/null; \
		 echo $rm | sudo -n tee $p/remote_mac >/dev/null; \
		 echo $rp | sudo -n tee $p/remote_port >/dev/null; \
		 echo $d  | sudo -n tee $p/dev_name >/dev/null; \
		 echo 1   | sudo -n tee $p/enabled >/dev/null; \
		 cat $p/enabled"
}

# A reboot takes the configfs target with it, so this has to be re-runnable:
# in forever mode (DUR=0) watch_forever calls it again on every new boot_id.
# WLAN_ARMED is cleared on every new boot and set once wlan0 takes, so the poll
# loop can keep retrying the second transport while the interface associates.
WLAN_ARMED=0

# Try to bring the second transport up. Returns 0 only on the attempt that
# actually arms it, so callers can announce the transition without repeating
# themselves on every poll. Callers decide whether it is wanted at all.
arm_wlan() {
	local wip
	wip=$(dev_run "ip -4 -br addr show wlan0 2>/dev/null | awk '{print \$3}' | cut -d/ -f1" | tr -d '\r')
	[ -n "$wip" ] || return 1
	[ "$(arm_target wifi wlan0 "$wip" "$HOST_WLAN_IP" "$HOST_WLAN_MAC" $((PORTHOLE_CAP_PORT+1)))" = 1 ] || return 1
	WLAN_ARMED=1
}

arm_device() {
host_ifaces
{
	echo "--- armed $(date -Iseconds) boot=$(cap_boot_id) host_mac=$HOST_USB_MAC"
	dev_run 'sudo -n modprobe netconsole 2>/dev/null; true' >/dev/null
	echo "usb0  enabled=$(arm_target usb usb0 "$HOST" 172.16.42.2 "$HOST_USB_MAC" "$PORTHOLE_CAP_PORT")"
	if [ "$PORTHOLE_CAP_WLAN" = 1 ]; then
		arm_wlan && echo "wlan0 enabled=1" \
		         || echo "wlan0 not up yet -- retrying every poll until it is"
	fi

	# A watchdog bark is silence unless the pretimeout governor turns it into a
	# panic, and the panic is what netconsole can carry. CONFIG_WATCHDOG_
	# PRETIMEOUT_GOV_PANIC has always been built in; only the runtime default
	# was noop (fixed in the shipped config from r28, set here too for older
	# images). sysrq is wanted so `echo c > /proc/sysrq-trigger` can prove the
	# channel end to end before trusting it.
	#
	# Report the pretimeout too, not just the governor: the governor only ever
	# runs if a pretimeout is set, so gov=panic with pretimeout=0 is a line that
	# reads armed and is not.
	dev_run 'echo panic | sudo -n tee /sys/class/watchdog/watchdog0/pretimeout_governor >/dev/null 2>&1; \
		 echo 1 | sudo -n tee /proc/sys/kernel/sysrq >/dev/null 2>&1; \
		 echo "watchdog gov=$(cat /sys/class/watchdog/watchdog0/pretimeout_governor 2>/dev/null)" \
		      "pretimeout=$(cat /sys/class/watchdog/watchdog0/pretimeout 2>/dev/null)" \
		      "timeout=$(cat /sys/class/watchdog/watchdog0/timeout 2>/dev/null)" \
		      "panic=$(cat /proc/sys/kernel/panic 2>/dev/null)" \
		      "hung_task=$(cat /proc/sys/kernel/hung_task_timeout_secs 2>/dev/null)"'
} 2>&1 | tee -a "$OUT/arm.log"
}

arm_device

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
#
# select(), NOT a per-socket timeout. Polling each socket in turn with
# settimeout(0.2) costs 200 ms on every socket that has nothing -- so arming a
# second transport that never comes up throttled the whole sink to five lines a
# second and silently dropped the rest. That is precisely backwards: the burst
# this tool exists to catch is a panic dumping forty lines in a millisecond, and
# it was being metered away by the socket that was supposed to make capture more
# reliable. Measured on 2026-08-26: consecutive lines with kernel timestamps
# microseconds apart arriving 200.4 ms apart on the host, until wlan0 was armed.
#
# The receive buffer is enlarged for the same reason. A panic outruns any
# userspace reader, and the default rmem is a few hundred kB.
python3 -c '
import socket, select, sys, time, datetime
ports = [int(sys.argv[1])] + ([int(sys.argv[1]) + 1] if sys.argv[2] == "1" else [])
dur = float(sys.argv[3])
socks, byfd = [], {}
for p in ports:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try: s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
    except OSError: pass
    s.bind(("0.0.0.0", p)); s.setblocking(False)
    socks.append(s); byfd[s] = p
end, seen, live = time.time() + dur, set(), set()
while time.time() < end:
    ready, _, _ = select.select(socks, [], [], 0.5)
    for s in ready:
        p = byfd[s]
        while True:                     # drain, do not meter
            try: d, _ = s.recvfrom(65535)
            except BlockingIOError: break
            except OSError: break
            # Announce each port the first time it delivers anything. Dedup
            # below drops whichever copy loses the race, so without this a
            # dead second transport looks the same as a merely slow one.
            if p not in live:
                live.add(p)
                print(f"{datetime.datetime.now():%H:%M:%S.%f} [nc:{p}] *** transport live ***", flush=True)
            t = d.decode("utf-8", "replace").rstrip("\n")
            if t in seen: continue      # same line on both paths
            seen.add(t)
            if len(seen) > 20000: seen.clear()
            print(f"{datetime.datetime.now():%H:%M:%S.%f} [nc:{p}] {t}", flush=True)
' "$PORTHOLE_CAP_PORT" "$PORTHOLE_CAP_WLAN" "$SINK_DUR" > "$OUT/netconsole.log" 2>&1 &
NC_PID=$!
sleep 0.5

# The sink is a UDP socket and survives a reboot; these two are ssh sessions and
# do not, so they are started through a function the re-arm can call again.
# Append, never truncate -- a restart must not eat what the last boot logged.
start_followers() {
	ssh "${TK_SSH_OPTS[@]}" "$PHONE" 'sudo -n dmesg -w'                       >> "$OUT/dmesg.log"   2>&1 & DM_PID=$!
	ssh "${TK_SSH_OPTS[@]}" "$PHONE" 'sudo -n journalctl -f -o short-iso -n0' >> "$OUT/journal.log" 2>&1 & JR_PID=$!
	echo "$NC_PID $DM_PID $JR_PID" > "$OUT/pids"
}
start_followers

# enabled=1 says the target was accepted, not that a packet reaches the host.
# Push a token through /dev/kmsg and wait for the sink to print it back. This is
# the only statement about netconsole worth trusting, and it is the check that
# catches the stale-MAC case, a firewall, and a sink that never bound its port.
_probe_n=0
verify_channel() {
	local tok
	_probe_n=$((_probe_n + 1))
	tok="tk-capture-probe-$$-$_probe_n"
	dev_run "echo '$tok' | sudo -n tee /dev/kmsg >/dev/null" >/dev/null
	for _ in 1 2 3 4 5 6 7 8 9 10; do
		if grep -q "$tok" "$OUT/netconsole.log" 2>/dev/null; then
			# Name the transports, not just "it works": in forever mode
			# there are meant to be two, and one is the whole point.
			echo ">> netconsole VERIFIED end to end, transports live:$(
				grep -o '\[nc:[0-9]*\] \*\*\* transport live' "$OUT/netconsole.log" 2>/dev/null |
				grep -o '[0-9]\+' | sort -u | tr '\n' ' ' | sed 's/^/ /')" | tee -a "$OUT/arm.log"
			[ "$PORTHOLE_CAP_WLAN" = 1 ] && [ "$WLAN_ARMED" = 0 ] && \
				echo ">> (wlan0 still to come -- usb0 only for now)" | tee -a "$OUT/arm.log"
			return 0
		fi
		sleep 1
	done
	echo ">> netconsole SILENT -- armed, but nothing arrives on the host." | tee -a "$OUT/arm.log"
	echo ">> check the host MAC ($HOST_USB_MAC), the sink, and the firewall." | tee -a "$OUT/arm.log"
	return 1
}
verify_channel

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

# --------------------------------------------------------------- forever mode
# DUR=0 means "leave it armed". A crash on this device is opportunistic -- the
# GPU display-wake SError of 2026-08-26 bit during audio work, six times, and
# every one of those was investigated blind because the capture was not up.
# Poll boot_id; a new one means the configfs target and both ssh followers died
# with the old kernel and have to be put back.
#
# The boot_id comes through dev_run, so this notices a reboot whose sshd is not
# accepting sessions yet -- or never will. Asking over ssh alone is how the boot
# after the 2026-08-26 watchdog reset went entirely uninstrumented: the loop saw
# no answer, read it as "still down", and waited while the device sat there
# fully booted and unarmed for the better part of a minute.
watch_forever() {
	local last=$BOOT_BEFORE now
	while :; do
		sleep "${TK_POLL:-5}"
		now=$(cap_boot_id) || now=""
		[ -z "$now" ] && continue          # genuinely down; nothing to arm yet
		if [ "$now" = "$last" ]; then
			# wlan0 associates long after usb0 answers, so the second
			# transport is normally still missing at re-arm time. Keep
			# trying until it lands.
			if [ "$PORTHOLE_CAP_WLAN" = 1 ] && [ "$WLAN_ARMED" = 0 ] && arm_wlan; then
				echo ">> wlan0 armed (second transport up)" | tee -a "$OUT/arm.log"
			fi
			continue
		fi
		# The old master points at a dead sshd (ph-lib.sh).
		ph_ssh_mux_reset
		echo ">> NEW BOOT $now -- re-arming"
		kill "$DM_PID" "$JR_PID" 2>/dev/null
		WLAN_ARMED=0
		arm_device
		start_followers
		verify_channel
		last=$now
	done
}

echo ">> baseline: gpu_faults=${gpu_faults:-?} hangchecks=${hangchecks:-?} coredumps=${coredumps:-?}"
if [ "$DUR" = 0 ]; then
	echo ">> capturing to $OUT until Ctrl-C, re-arming across reboots"
	watch_forever
else
	echo ">> capturing to $OUT for ${DUR}s (Ctrl-C to stop early)"
	sleep "$DUR"
fi
verdict
cleanup
