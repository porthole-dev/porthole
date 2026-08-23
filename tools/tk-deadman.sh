#!/bin/bash
# scope: soc:msm8998
# needs: BOOTED
# env: HOST, PHONE, PORTHOLE_HOST, PORTHOLE_USER, TK_HOST
# exits: 0 ok · 2 usage
# tk-deadman.sh -- arm/disarm a self-reboot on the PHONE before a risky test.
#
# THE PROBLEM THIS SOLVES
#   Autonomous testing on this device keeps producing states where the phone is
#   unreachable and a human has to walk over and hold the power button. That is
#   the single thing that stops unattended work.
#
# WHAT ALREADY COVERS YOU (do not duplicate it):
#   * DPM_WATCHDOG=y / TIMEOUT=20  -- a hang INSIDE a device suspend or resume
#     callback panics after 20 s.
#   * DETECT_HUNG_TASK=y + BOOTPARAM_HUNG_TASK_PANIC=y -- a task stuck in D
#     state panics.
#   * panic=10 (cmdline) + CONFIG_PANIC_TIMEOUT=10 -- any panic reboots in 10 s.
#   * The TZ/firmware watchdog SOMETIMES catches a true SoC lockup. On
#     2026-08-01 20:57 it did: bootreason=watchdog, back in ~45 s. On
#     2026-08-02 it did NOT -- same hang class (suspend into ipa), the phone
#     stayed off the USB bus indefinitely and needed a long-press power cycle
#     (PLAN-daily-driver-v2.md 6.5a). ONE recovery is not a guarantee. Never
#     run a hang-producing arm unattended on the strength of this line.
#   * /dev/watchdog EXISTS as of the 2026-08-02 image (verified: char 10,130
#     plus watchdog0 247,0), and systemd owns it -- RuntimeWatchdogUSec=1min
#     from /etc/systemd/system.conf.d/20-taimen-watchdog.conf. So the wedged-
#     userspace case is already covered by systemd, without this script.
#
# THE GAP THIS FILLS
#   Narrower than when this was written. systemd's RuntimeWatchdogSec now
#   handles "kernel alive, userspace wedged" on its own. What is left is the
#   bounded-window case: arm a reboot BEFORE a specific risky test so recovery
#   does not wait on a 60 s ping timeout, and so it still fires if the test
#   wedges something that keeps systemd petting.
#
# HONEST LIMIT: this runs in userspace. If the SoC hard-locks, this timer dies
# with everything else and the TZ watchdog is your only recourse. It covers the
# wedged-userspace case, not the dead-silicon case.
#
# Usage:
#   tk-deadman.sh arm 300     # reboot in 300 s unless disarmed
#   tk-deadman.sh disarm      # call this the moment the test survives
#   tk-deadman.sh status
set -u

# shellcheck source=../lib/porthole.sh
. "$(dirname "${BASH_SOURCE[0]:-$0}")/tk-lib.sh"

HOST=${TK_HOST:-$PORTHOLE_HOST}
UNIT=tk-deadman
ACTION=${1:-status}
SECS=${2:-300}

ssh_d() { timeout 20 ssh "${TK_SSH_OPTS[@]}" "$PORTHOLE_USER@$HOST" "$@"; }

case "$ACTION" in
arm)
	# --on-active is relative to now; the unit reboots unless we stop the timer.
	ssh_d "sudo systemd-run --on-active=${SECS} --unit=${UNIT} --timer-property=AccuracySec=1s \
	       systemctl reboot" 2>&1 | tail -2
	echo ">> deadman ARMED: phone reboots in ${SECS}s unless disarmed"
	echo ">> DISARM WITH: $0 disarm"
	;;
disarm)
	ssh_d "sudo systemctl stop ${UNIT}.timer 2>/dev/null; sudo systemctl reset-failed ${UNIT}.timer ${UNIT}.service 2>/dev/null; true"
	echo ">> deadman DISARMED"
	;;
status)
	ssh_d "systemctl list-timers ${UNIT}.timer --no-pager 2>/dev/null | head -3; \
	       systemctl is-active ${UNIT}.timer 2>/dev/null" || echo "  (phone unreachable)"
	;;
*)
	echo "usage: $0 {arm SECONDS|disarm|status}"; exit 2 ;;
esac

# THE REAL FIX -- LANDED 2026-08-02, DO NOT RE-SCHEDULE IT INTO THE CONFIG ROUND.
#   CONFIG_WATCHDOG + CONFIG_QCOM_WDT and the APSS watchdog DT node
#   (watchdog@17817000, "qcom,apss-wdt-msm8998"/"qcom,kpss-wdt", sleep_clk,
#   GIC SPI 3 and 4) are already in the running image. Verified on device:
#   /dev/watchdog is char 10,130 and /dev/watchdog0 is 247,0, and
#   `systemctl show -p RuntimeWatchdogUSec` reads 1min. So a total userspace
#   wedge, PID-1 freeze included, is already a hardware reset with no host
#   involvement -- which is strictly better than this script.
#
#   Appendix B of PLAN-daily-driver-v2.md may still list it as pending. It is
#   not. Adding a settled CONFIG symbol back into the ONE CONFIG batch is the
#   exact failure mode that document is built to prevent -- one Kconfig change
#   stops 274 installed modules from loading.
