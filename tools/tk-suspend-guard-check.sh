#!/bin/bash
# scope: generic
# needs: BOOTED
# env: HOST, PHONE, PORTHOLE_HOST, PORTHOLE_USER, TK_HOST
# exits: 0 ok · 1 failed
# tk-suspend-guard-check.sh -- prove the suspend guard actually blocks, WITHOUT
# ever suspending the phone.
#
# WHY THIS EXISTS
#   On 2026-08-02 the guard was "verified" by running `systemctl start
#   suspend.target`. The guard turned out to be attached to the wrong unit, the
#   phone suspended into the ipa hard-hang, and it needed a hands-on power
#   cycle. Do not test a guard by firing the thing it guards against.
#
#   The two things that made that possible are both fixed now, but the check
#   itself is the durable part: it exercises the real assertion, on the real
#   unit, and the worst case is that /bin/true runs.
#
# HOW
#   systemd evaluates a unit's assertions before its ExecStart. So we drop in a
#   temporary override that replaces ExecStart with /bin/true and start the
#   unit for real:
#     * assertion works -> the job fails with "asserts failed", nothing runs
#     * assertion broken -> /bin/true runs, and we have learned that safely
#   The override is removed again either way, including on Ctrl-C.
#
# WHY THE UNIT AND NOT THE TARGET
#   suspend.target is ordered `After=systemd-suspend.service`, so the service
#   suspends the machine before the target's assertions are ever looked at.
#   The guard has to live on systemd-suspend.service. See the header of
#   pmaports/device/testing/device-google-taimen/taimen-suspend-guard.conf.
set -u

# shellcheck source=../lib/porthole.sh
. "$(dirname "${BASH_SOURCE[0]:-$0}")/tk-lib.sh"

HOST=${TK_HOST:-$PORTHOLE_HOST}
PHONE=${PHONE:-$PORTHOLE_USER@$HOST}
UNIT=${1:-systemd-suspend.service}
ARM=/run/taimen-suspend-is-safe
OVERRIDE="/etc/systemd/system/${UNIT}.d/99-tk-guard-check.conf"

sshq() { timeout 30 ssh "${TK_SSH_OPTS[@]}" "$PHONE" "$@" 2>&1; }

cleanup() {
	sshq "sudo -n rm -f '$OVERRIDE' '$ARM'; sudo -n systemctl daemon-reload" >/dev/null
	echo ">> cleaned up (override and arming file removed)"
}
trap cleanup EXIT INT TERM

echo ">> unit under test: $UNIT"
echo ">> assertions systemd has merged:"
sshq "systemctl cat '$UNIT' | grep -n 'Assert' || echo '  (NONE -- the guard is missing or on the wrong unit)'"

# ExecStart= with an empty value resets the list; the second line is what runs.
sshq "sudo -n mkdir -p '/etc/systemd/system/${UNIT}.d' && \
      printf '[Service]\nExecStart=\nExecStart=/bin/true\n' | \
      sudo -n tee '$OVERRIDE' >/dev/null && sudo -n systemctl daemon-reload" >/dev/null

echo
echo ">> ARM ABSENT -- expect the job to FAIL on its assertion"
sshq "sudo -n rm -f '$ARM'; sudo -n systemctl reset-failed '$UNIT' 2>/dev/null
      sudo -n systemctl start '$UNIT'; echo \"exit=\$?\""
blocked=$(sshq "sudo -n journalctl -u '$UNIT' -n 5 --no-pager | grep -c 'asserts failed'")

echo
echo ">> ARM PRESENT -- expect the job to be allowed through (runs /bin/true)"
sshq "sudo -n touch '$ARM'; sudo -n systemctl reset-failed '$UNIT' 2>/dev/null
      sudo -n systemctl start '$UNIT'; echo \"exit=\$?\""
allowed=$(sshq "sudo -n systemctl show '$UNIT' -p ExecMainStatus --value")

echo
if [ "${blocked:-0}" -ge 1 ] && [ "${allowed:-1}" = "0" ]; then
	echo ">> PASS: the guard blocks when disarmed and allows when armed."
	echo ">> Phosh's 900 s idle-suspend and the power menu cannot reach the ipa hang."
	exit 0
fi
echo ">> FAIL: guard did NOT behave correctly (blocked=$blocked armed_exit=$allowed)."
echo ">> Treat the phone as UNPROTECTED. Blacklist ipa; do not leave it idle unattended."
exit 1
