#!/bin/bash
# scope: soc:qcom
# needs: BOOTED
# env: PHONE, TK_ALARM, TK_HOST, TK_SUSPEND_CMD
# exits: 0 ok · non-zero on failure
# One real s2idle cycle, driven from the host, with evidence that survives the
# reset that a failed one ends in.
#
#   tk-suspend-cycle.sh [alarm_sec] [prep_file] [post_file]
#   PHONE=user@host tk-suspend-cycle.sh 20
#
# Three things this encodes, each of which cost real time on 2026-08-02:
#
# 1. THE RESET IS THE RESULT. A resume-side hang ends in a watchdog reset, and
#    ramoops does NOT survive it on this device -- /sys/fs/pstore comes up empty
#    even after a deliberate `echo c > /proc/sysrq-trigger`. So the device half
#    fsyncs every line to /var/log/tk-suspend-try.log BEFORE the suspend write,
#    and the host half reports `btime - SUSPEND_ENTER` when the log has no
#    SUSPEND_RETURN. That delta is the only measurement a hang leaves behind.
#
# 2. VARY THE ALARM TO LOCATE THE HANG. Run it at +5, +20 and +60. If the reset
#    delta tracks the alarm, the hang is on the RESUME side and s2idle entry is
#    fine; if it does not move, the hang is on the suspend side. That one
#    substitution is what retired "s2idle hangs" as a description of this device.
#
# 3. NEVER RUN THIS OVER THE LINK THAT SUSPEND TAKES DOWN unless you have
#    verified the dwc3-qcom resume fix is in the running kernel. Before that fix
#    the USB gadget never came back, so a successful suspend and a dead phone
#    looked identical from the host. TK_HOST over WiFi is the safe default.
#
# prep/post are optional shell fragments SOURCED on the device either side of the
# suspend -- use them to change one variable per run. Note that a prep hook which
# wedges leaves the log stopped mid-prep with no SUSPEND_ENTER; that is a result
# too, not a harness failure.
set -u
# TK_SSH_OPTS (StrictHostKeyChecking=no + a /dev/null known-hosts file) is
# mandatory, not laziness -- see tk-lib.sh's header: the phone's host key
# changes on essentially every boot. This script used bare ssh/scp against a
# hardcoded address until 2026-08-10, and the first run over the phone's WiFi
# address died with "Host key verification failed": every scp silently failed,
# try.sh was never uploaded, and the suspend never happened -- while the host
# side still printed ">> suspending". A harness that cannot reach the phone
# must not look like a device result.
. "$(dirname "$0")/tk-lib.sh"
H=${TK_HOST:-$PHONE}
A=${1:-20}; PREP=${2:-}; POST=${3:-}
LOG=/var/log/tk-suspend-try.log
# ConnectTimeout first: ssh takes the FIRST value given for an option, and
# TK_SSH_OPTS carries a 2 s timeout tuned for liveness polling, not for a
# phone that is mid-resume.
S() { timeout "${2:-25}" ssh -o ConnectTimeout=6 "${TK_SSH_OPTS[@]}" "$H" "$1" 2>&1; }

DEV=$(mktemp); trap 'rm -f "$DEV"' EXIT
cat > "$DEV" <<'DEVEOF'
#!/bin/sh
LOG=/var/log/tk-suspend-try.log
ALARM=${TK_ALARM:-20}
# Path A (default) is the raw sysfs write: it never starts
# systemd-suspend.service, so it runs NEITHER the suspend guard NOR any
# /usr/lib/systemd/system-sleep hook. Path B is what a user's phone actually
# does, and it is the only one that exercises the shipped cpuidle hook -- set
# TK_SUSPEND_CMD='systemctl start systemd-suspend.service' for it.
SUSPEND_CMD=${TK_SUSPEND_CMD:-"echo freeze > /sys/power/state"}
log() { echo "$*" >> $LOG; sync; }
log "=== TRY $(date +%FT%T) alarm=+${ALARM}s btime=$(awk '/btime/{print $2}' /proc/stat) uptime=$(cut -d. -f1 /proc/uptime)s ==="
log "boot_id_before=$(cat /proc/sys/kernel/random/boot_id) cmd=[$SUSPEND_CMD]"
[ -f /tmp/tk-prep.sh ] && { log "--- prep ---"; . /tmp/tk-prep.sh >> $LOG 2>&1; sync; log "--- prep done ---"; }
log "success_before=$(cat /sys/power/suspend_stats/success) fail_before=$(cat /sys/power/suspend_stats/fail)"
echo "+$ALARM" > /sys/class/rtc/rtc0/wakealarm
log "SUSPEND_ENTER $(date +%s)"
eval "$SUSPEND_CMD"
RC=$?
log "boot_id_after=$(cat /proc/sys/kernel/random/boot_id)"
log "SUSPEND_RETURN $(date +%s) rc=$RC success=$(cat /sys/power/suspend_stats/success) fail=$(cat /sys/power/suspend_stats/fail) wakeirq=$(cat /sys/power/pm_wakeup_irq 2>/dev/null)"
log "last_failed_dev=$(cat /sys/power/suspend_stats/last_failed_dev 2>/dev/null)"
log "udc=$(cat /sys/class/udc/a800000.usb/state) speed=$(cat /sys/class/udc/a800000.usb/current_speed)"
[ -f /tmp/tk-post.sh ] && { log "--- post ---"; . /tmp/tk-post.sh >> $LOG 2>&1; sync; log "--- post done ---"; }
log "=== TRY DONE ==="
DEVEOF

S "sudo rm -f /tmp/tk-prep.sh /tmp/tk-post.sh" >/dev/null
[ -n "$PREP" ] && scp -q "${TK_SSH_OPTS[@]}" "$PREP" "$H:/tmp/tk-prep.sh"
[ -n "$POST" ] && scp -q "${TK_SSH_OPTS[@]}" "$POST" "$H:/tmp/tk-post.sh"
scp -q "${TK_SSH_OPTS[@]}" "$DEV" "$H:/tmp/try.sh"
S "sudo cp /tmp/try.sh /usr/local/bin/tk-suspend-try.sh; sudo chmod +x /usr/local/bin/tk-suspend-try.sh; sudo truncate -s0 $LOG; sudo sync"

echo ">> suspending (alarm=+${A}s, prep=${PREP:-none})"
# The trailing sleep matters: without it ssh closes the channel before sudo has
# even exec'd, and the run silently never happens.
timeout 20 ssh -o ConnectTimeout=6 "${TK_SSH_OPTS[@]}" "$H" \
	"sudo TK_ALARM=$A TK_SUSPEND_CMD=\"${TK_SUSPEND_CMD:-}\" setsid /usr/local/bin/tk-suspend-try.sh </dev/null >/dev/null 2>&1 & sleep 4" >/dev/null 2>&1

for _ in $(seq 1 60); do
	[ "$(S "grep -q 'TRY DONE' $LOG && echo READY" 12)" = READY ] && break
done
echo ">> result"
S "echo \"now_btime=\$(awk '/btime/{print \$2}' /proc/stat) uptime=\$(cut -d. -f1 /proc/uptime)s bootreason=\$(grep -o 'bootreason=[a-z]*' /proc/cmdline)\"; cat $LOG" 30 |
	awk '{print}
	     /SUSPEND_ENTER/{e=$2} /SUSPEND_RETURN/{ok=1}
	     /now_btime=/{split($1,a,"="); b=a[2]}
	     END{ if (!ok && e && b && b>e)
	            print "*** HUNG: reset " b-e "s after entering. Re-run with a different alarm:",
	                  "delta tracking the alarm means the RESUME hung. ***" }'
