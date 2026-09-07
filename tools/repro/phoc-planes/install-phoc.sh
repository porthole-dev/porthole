#!/bin/bash
# SPDX-License-Identifier: MIT
# scope: device (taimen)
# needs: on the host, with the device BOOTED; the apks built in the workspace
# env: PORTHOLE_* (via ph-lib.sh)
# exits: 0 installed and the session is back · 1 push/install failed · 2 no session
# install-phoc.sh 0.57.0-r55  -- push a locally built phoc and restart the session.
#
# The session restart is `systemctl restart greetd`, NOT a reboot: greetd runs
# its `initial_session` on startup, which autologins, so nothing has to type a
# password. Killing phoc directly does the same teardown but leaves greetd
# showing the phrog lock screen, which wants a human thumb.
set -uo pipefail
cd "$(dirname "$0")/../../.." || exit 1
source tools/ph-lib.sh
P=$HOME/.local/var/porthole-sandbox/packages/edge/aarch64
V=${1:?phoc version, e.g. 0.57.0-r55}
PKGS="phoc phoc-schemas"

tk_run "mkdir -p /tmp/phoc-apk && rm -f /tmp/phoc-apk/*.apk"
for p in $PKGS; do
    scp "${TK_SSH_OPTS[@]}" "$P/$p-$V.apk" "$PHONE:/tmp/phoc-apk/" || { echo "PUSH FAILED $p"; exit 1; }
done
TK_RUN_TIMEOUT=180 tk_run "sudo -n apk add --allow-untrusted /tmp/phoc-apk/*.apk 2>&1 | tail -6" || exit 1

OLD=$(tk_run 'pgrep -x phoc | head -1')

# greetd's initial_session autologins on startup, so a restart normally lands
# straight back in the user session -- but not always: it sometimes stops at the
# phrog greeter instead, which wants a thumb nobody is there to provide. So wait
# for PHOSH, not for a new phoc: the greeter has a phoc of its own and waiting on
# that reports success while the phone sits on the lock screen. Restart again if
# it does, which has always been enough.
for attempt in 1 2 3; do
    tk_run 'sudo -n systemctl restart greetd' >/dev/null 2>&1
    END=$(tk_deadline_ms 75)
    while ! tk_expired "$END"; do
        tk_run 'pgrep -x phosh >/dev/null' 2>/dev/null && break
    done
    tk_run 'pgrep -x phosh >/dev/null' 2>/dev/null && break
    echo ">> attempt $attempt landed on the greeter, not the session; restarting greetd"
done
if ! tk_run 'pgrep -x phosh >/dev/null' 2>/dev/null; then
    # Last resort: talk greetd's own IPC, which is what the greeter does. Needs
    # the login password in the environment on purpose -- a flag would put it in ps.
    if [ -n "${TK_LOGIN_PASSWORD:-}" ]; then
        scp "${TK_SSH_OPTS[@]}" tools/ph-greetd-login.py "$PHONE:/tmp/" >/dev/null 2>&1
        tk_run "sudo -n env TK_LOGIN_PASSWORD='$TK_LOGIN_PASSWORD' python3 /tmp/ph-greetd-login.py $PORTHOLE_USER phosh-session" >/dev/null 2>&1
        END=$(tk_deadline_ms 75)
        while ! tk_expired "$END"; do
            tk_run 'pgrep -x phosh >/dev/null' 2>/dev/null && break
        done
    fi
    tk_run 'pgrep -x phosh >/dev/null' 2>/dev/null || {
        echo "NO SESSION -- still on the greeter. Unlock the phone, or export TK_LOGIN_PASSWORD."
        exit 2
    }
fi
NEW=$(tk_run 'pgrep -x phoc | head -1' 2>/dev/null)

echo "phoc $(tk_run 'apk info -v | grep -m1 "^phoc-0"')  pid $OLD -> $NEW  (phosh up)"
