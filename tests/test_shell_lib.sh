#!/bin/bash
# SPDX-License-Identifier: MIT
# lib/porthole.sh: resolution, the legacy-alias contract, and agreement with
# lib/porthole.py.
#
# Two implementations of one semantics will drift unless something compares
# them. The agreement tests at the bottom are the only thing stopping a fix
# landing in the python half and not the shell half.
#
# Needs no device. Run: bash tests/test_shell_lib.sh
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PASS=0; FAIL=0

ok()   { PASS=$((PASS+1)); }
bad()  { FAIL=$((FAIL+1)); echo "FAIL $1"; echo "     $2"; }
is()   { # is NAME ACTUAL EXPECTED
    if [ "$2" = "$3" ]; then ok; else bad "$1" "got '$2', want '$3'"; fi
}
has()  { # has NAME HAYSTACK NEEDLE
    case $2 in *"$3"*) ok ;; *) bad "$1" "'$3' missing from: $2" ;; esac
}
hasnt() {
    case $2 in *"$3"*) bad "$1" "'$3' should be absent from: $2" ;; *) ok ;; esac
}

# Run a snippet in a clean bash with lib/porthole.sh sourced.
#
# `env -i` matters: the developer running the tests has PHONE or TK_HOST
# exported often enough that inheriting the environment makes these tests lie.
#
# `--norc --noprofile` matters too, and was added after a broken line in a
# developer's ~/.bashrc.d printed to stderr and was captured as the value of
# every resolved key. A test that reads the tester's shell config is a test
# that fails on someone else's laptop for reasons that have nothing to do with
# the code.
phsh() { # phsh 'VAR=x VAR=y' 'echo $PHONE'
    env -i PATH="$PATH" HOME="$HOME" PORTHOLE_ROOT="$ROOT" \
        XDG_CONFIG_HOME="$TMPXDG" $1 \
        bash --norc --noprofile -c ". '$ROOT/lib/porthole.sh'; $2" 2>&1
}

TMPXDG=$(mktemp -d)
trap 'rm -rf "$TMPXDG"' EXIT

# ------------------------------------------------------------- defaults ----

is "default PHONE composes user@host" \
   "$(phsh '' 'echo $PHONE')" "user@172.16.42.1"
is "default poll" "$(phsh '' 'echo $TK_POLL')" "0.5"
is "default fastboot" "$(phsh '' 'echo $FASTBOOT')" "fastboot"

# -------------------------------------------------------------- profile ----

is "profile supplies the SoC" \
   "$(phsh 'PORTHOLE_DEVICE=google-taimen' 'echo $PORTHOLE_SOC')" "msm8998"
is "profile supplies the forbidden slot" \
   "$(phsh 'PORTHOLE_DEVICE=google-taimen' 'echo $PORTHOLE_SLOT_FORBIDDEN')" "a"
is "profile supplies a quoted value intact" \
   "$(phsh 'PORTHOLE_DEVICE=google-taimen' 'echo $PORTHOLE_DEVICE_NAME')" \
   "Google Pixel 2 XL"
is "an inline comment is not part of the value" \
   "$(phsh 'PORTHOLE_DEVICE=google-taimen' 'echo $PORTHOLE_HAS_AB_SLOTS')" "1"

out=$(phsh 'PORTHOLE_DEVICE=nosuchdevice' 'echo unreachable')
has "a missing profile is fatal, not silent" "$out" "no profile for device"

# ------------------------------------------------- the legacy alias table ----
# One assertion per row of spec section 4.3. These are the never-break-taimen
# tests: every one of them corresponds to command lines printed in taimen docs.

is "PHONE verbatim beats a composed one" \
   "$(phsh 'PHONE=olduser@172.16.42.1 PORTHOLE_USER=alice PORTHOLE_HOST=10.0.0.5' \
           'echo $PHONE')" "olduser@172.16.42.1"
is "HOST overrides PORTHOLE_HOST" \
   "$(phsh 'HOST=172.16.42.1 PORTHOLE_HOST=10.0.0.5' 'echo $HOST')" "172.16.42.1"
is "TK_HOST overrides PORTHOLE_HOST" \
   "$(phsh 'TK_HOST=172.16.42.9 PORTHOLE_HOST=10.0.0.5' 'echo $HOST')" "172.16.42.9"
is "HOST beats TK_HOST" \
   "$(phsh 'HOST=1.1.1.1 TK_HOST=2.2.2.2' 'echo $HOST')" "1.1.1.1"
is "HOST is mined from PHONE when only PHONE is set" \
   "$(phsh 'PHONE=bob@192.168.7.7' 'echo $HOST')" "192.168.7.7"
is "TK_POLL beats PORTHOLE_POLL" \
   "$(phsh 'TK_POLL=0.1 PORTHOLE_POLL=9' 'echo $TK_POLL')" "0.1"
is "TK_AGENT beats PORTHOLE_AGENT" \
   "$(phsh 'TK_AGENT=claude PORTHOLE_AGENT=agentname' 'echo $TK_AGENT')" "claude"
is "TK_AGENT falls back to PORTHOLE_AGENT" \
   "$(phsh 'PORTHOLE_AGENT=agentname' 'echo $TK_AGENT')" "agentname"
is "FASTBOOT passes through" \
   "$(phsh 'FASTBOOT=/opt/fb' 'echo $FASTBOOT')" "/opt/fb"
is "the frozen tk_* surface is defined" \
   "$(phsh '' 'for f in tk_boot_id tk_uptime tk_device_state tk_in_fastboot \
                       tk_request_reboot tk_request_bootloader tk_rearm_and_boot \
                       tk_wait_ssh tk_wait_fastboot tk_now_ms tk_since \
                       tk_deadline_ms tk_expired tk_have_python; do \
                 declare -F $f >/dev/null || echo "MISSING $f"; done; echo ok')" "ok"

# ------------------------------------------------------------- ssh opts ----

opts=$(phsh '' 'echo "${TK_SSH_OPTS[@]}"')
has "host keys change every boot: no strict checking" "$opts" "StrictHostKeyChecking=no"
has "host keys change every boot: null known_hosts"   "$opts" "UserKnownHostsFile=/dev/null"
has "batch mode, so a prompt fails instead of hanging" "$opts" "BatchMode=yes"
has "connect timeout bounds the poll loop"             "$opts" "ConnectTimeout=2"
has "multiplexing is on by default"                    "$opts" "ControlMaster=auto"
has "control path is keyed on host+port+user"          "$opts" "ControlPath="
has "control master expires on its own"                "$opts" "ControlPersist=60s"

opts=$(phsh 'PORTHOLE_NO_MUX=1' 'echo "${TK_SSH_OPTS[@]}"')
hasnt "PORTHOLE_NO_MUX=1 disables multiplexing" "$opts" "ControlMaster"
has   "PORTHOLE_NO_MUX=1 keeps the mandatory flags" "$opts" "StrictHostKeyChecking=no"

opts=$(phsh 'PORTHOLE_SSH_PORT=2222' 'echo "${TK_SSH_OPTS[@]}"')
has "a non-default port reaches ssh" "$opts" "-p 2222"

# ------------------------------------------------- reboot paths reset mux ----
# A master socket that outlives a reboot is a live handle to a dead sshd. If
# any of these stops calling ph_ssh_mux_reset, the toolbox hangs instead of
# failing fast, and the cause is invisible.
for fn in tk_request_reboot tk_request_bootloader tk_rearm_and_boot; do
    body=$(phsh '' "declare -f $fn")
    has "$fn tears down the ssh master" "$body" "ph_ssh_mux_reset"
done
has "tk_wait_ssh resets the master on a new boot_id" \
    "$(phsh '' 'declare -f tk_wait_ssh')" "ph_ssh_mux_reset"

# --------------------------------------------- the forbidden-slot guard ----

body=$(phsh '' 'declare -f tk_rearm_and_boot')
has "rearm refuses the forbidden slot" "$body" "PORTHOLE_SLOT_FORBIDDEN"

out=$(phsh 'PORTHOLE_DEVICE=google-taimen PORTHOLE_ACTIVE_SLOT=a FASTBOOT=/bin/true' \
           'tk_rearm_and_boot; echo "rc=$?"')
has "set_active on the forbidden slot is refused" "$out" "refusing to set_active a"
has "and it reports failure"                      "$out" "rc=1"

# --------------------------------- a missing fastboot is not "no device" ----
# A $FASTBOOT that cannot run exits 127 with EMPTY STDOUT, and tk_in_fastboot
# reads emptiness as "not in the bootloader" -- so the TOOL being absent looked
# exactly like the PHONE being absent. In the sandbox (config.env names a host
# path that does not exist in the container) that cost a `fast` build 181.2s of
# waiting for a phone that was already sitting in fastboot.

out=$(phsh 'FASTBOOT=/nonexistent/platform-tools/fastboot' \
           'tk_in_fastboot; echo "PROBE ANSWERED rc=$?"'); rc=$?
is    "an unrunnable fastboot exits 69, not a probe answer" "$rc" "69"
hasnt "so the probe never answers at all"    "$out" "PROBE ANSWERED"
has   "the refusal names the offending path" "$out" "/nonexistent/platform-tools/fastboot"
has   "and blames the tool, not the phone"   "$out" "TOOL is missing"

# It exits rather than returning precisely so the wait loops cannot run: a
# return is indistinguishable from "not yet", and the loop would poll to its
# deadline before reporting a device that was never probed.
out=$(phsh 'FASTBOOT=/nonexistent/fastboot' \
           'tk_wait_fastboot $(( $(date +%s%3N) + 30000 )); echo "POLLED TO THE DEADLINE"')
rc=$?
is    "a wait loop refuses instead of polling" "$rc" "69"
hasnt "and never reaches its deadline"         "$out" "POLLED TO THE DEADLINE"

# The positive control: a fastboot that RUNS and lists nothing is a real "no",
# and must still be answered as one. Without this the guard could "pass" by
# refusing everything.
out=$(phsh 'FASTBOOT=/bin/true' 'tk_in_fastboot; echo "rc=$?"')
is "an installed fastboot listing nothing is still a real no" "$out" "rc=1"

# And there is exactly ONE fastboot probe. ph-build.sh carried a second copy --
# `"$FASTBOOT" devices 2>/dev/null | grep -q fastboot` -- with the same
# conflation and no timeout, so the fix above would have missed the rung that
# actually flashes.
# Comment lines are dropped first: the fix in ph-build.sh explains itself by
# quoting the pattern it removed, and a raw grep would read the explanation as
# the thing it explains.
raw=$(grep -rn '\$FASTBOOT" devices' "$ROOT/lib" "$ROOT/tools" 2>/dev/null \
      | grep -vE ':[0-9]+:[[:space:]]*#' \
      | grep -vE '/(porthole|tk-lib)\.sh:' | sort | tr '\n' ' ')
is "nothing rolls its own fastboot devices probe" "$raw" ""

# ------------------------------------------------------ python agreement ----
# The two libs must resolve identically or the toolbox behaves differently
# depending on which language a tool happens to be written in.

agree() { # agree ENV KEY  -- compare shell vs python for one resolved key
    local env=$1 key=$2 sh py
    sh=$(phsh "$env" "echo \$$key")
    py=$(env -i PATH="$PATH" HOME="$HOME" PORTHOLE_ROOT="$ROOT" \
             XDG_CONFIG_HOME="$TMPXDG" $env python3 -c "
import sys; sys.path.insert(0, '$ROOT/lib')
import porthole
cfg = porthole.load_config()
key = '$key'
if key == 'PHONE':  print(porthole.resolve_phone(cfg))
elif key == 'HOST': print(porthole.resolve_host(cfg))
else:               print(cfg.get(key, ''))
" 2>&1)
    is "shell and python agree on $key ($env)" "$sh" "$py"
}

agree '' PHONE
agree '' HOST
agree 'PHONE=olduser@172.16.42.1' PHONE
agree 'PHONE=bob@192.168.7.7' HOST
agree 'HOST=1.1.1.1 TK_HOST=2.2.2.2' HOST
agree 'TK_HOST=172.16.42.9' HOST
agree 'PORTHOLE_USER=alice PORTHOLE_HOST=10.0.0.5' PHONE
agree 'PORTHOLE_DEVICE=google-taimen' PORTHOLE_SOC
agree 'PORTHOLE_DEVICE=google-taimen' PORTHOLE_SLOT_FORBIDDEN
agree 'PORTHOLE_DEVICE=google-taimen' PORTHOLE_DEVICE_NAME
agree 'PORTHOLE_DEVICE=google-taimen' PORTHOLE_WATCHDOG_MAX_S

# tk_pkill exists so nobody types the short form. `pkill -f <pattern>` over ssh
# matches the ssh command line CARRYING the pattern, and kills the session
# running it -- twice in one session, and once as `pkill -f
# xdg-permission-store` on 2026-08-20. Refusing the flag is the whole feature,
# so it is asserted rather than trusted.
out=$(phsh '' 'tk_pkill -f something 2>&1; echo "rc=$?"')
has  "tk_pkill refuses -f"        "$out" "refusing the flag"
has  "tk_pkill -f exits usage"    "$out" "rc=64"
out=$(phsh '' 'tk_pkill 2>&1; echo "rc=$?"')
has  "tk_pkill needs a name"      "$out" "rc=64"
out=$(phsh '' 'type tk_pkill 2>&1')
hasnt "tk_pkill never uses -f"    "$out" "pkill -f"

# tk_expired with an empty deadline printed `[: : integer expected` five times
# during tk-to-fastboot -- in exactly the window where a human is watching for
# whether the device moved. The message named a line inside this library, not
# the caller that passed nothing, so it was unattributable: the handoff that
# reported it blamed tk_wait_fastboot, and nothing in the repo calls that.
out=$(phsh '' 'tk_expired "" 2>&1; echo "rc=$?"')
hasnt "an empty deadline prints no raw test noise" "$out" "integer expected"
has   "an empty deadline names the helper"         "$out" "tk_expired: bad deadline"
# NOT expired. A wait loop that treats an unknown deadline as expired gives up
# instantly on a device that was fine -- worse than the noise it replaces.
has   "an unknown deadline is not treated as expired" "$out" "rc=1"

out=$(phsh '' 'tk_expired abc 2>&1; echo "rc=$?"')
has   "a non-numeric deadline is rejected too" "$out" "bad deadline 'abc'"

# The happy paths must be untouched: this helper is polled in every wait loop.
out=$(phsh '' 'tk_expired 1 && echo EXPIRED')
is    "a past deadline is still expired" "$out" "EXPIRED"
out=$(phsh '' 'tk_expired $(( $(date +%s%3N) + 60000 )) || echo PENDING')
is    "a future deadline is still pending" "$out" "PENDING"

# ------------------------------------------------------ the usb bus state ----
#
# ph_usb_state answers the question `fastboot devices` cannot: is the phone on
# the bus AT ALL. An empty answer from fastboot covers three states that want
# three different fixes, and porthole told the operator to do the wrong one for
# 26 minutes on 2026-09-01 because nothing looked. PORTHOLE_USB_SYSFS is what
# makes all four answers reachable with no phone plugged in -- the positive
# control included, so an "absent" pass cannot come from a helper that always
# says absent.
USBFAKE=$(mktemp -d)
trap 'rm -rf "$TMPXDG" "$USBFAKE"' EXIT
USBIDS='PORTHOLE_USB_FASTBOOT_ID=18d1:4ee0 PORTHOLE_USB_GADGET_ID=18d1:d001'
mkdir -p "$USBFAKE/1-2" "$USBFAKE/usb1"
printf '1d6b\n' > "$USBFAKE/usb1/idVendor"; printf '0002\n' > "$USBFAKE/usb1/idProduct"

printf '18d1\n' > "$USBFAKE/1-2/idVendor"; printf 'd001\n' > "$USBFAKE/1-2/idProduct"
is "the running pmOS gadget on the bus reads as gadget" \
   "$(phsh "$USBIDS PORTHOLE_USB_SYSFS=$USBFAKE" 'ph_usb_state')" "gadget"

printf '4ee0\n' > "$USBFAKE/1-2/idProduct"
is "the bootloader ID on the bus reads as fastboot" \
   "$(phsh "$USBIDS PORTHOLE_USB_SYSFS=$USBFAKE" 'ph_usb_state')" "fastboot"

# THE CASE THAT COST THE SESSION: a populated bus with a root hub on it and no
# phone. Not an empty directory -- the helper must distinguish "I looked and
# the phone is not here" from "I could not look".
rm -rf "$USBFAKE/1-2"
is "a bus with no phone on it reads as absent" \
   "$(phsh "$USBIDS PORTHOLE_USB_SYSFS=$USBFAKE" 'ph_usb_state')" "absent"

is "a profile that names no IDs gets unknown, never a guess" \
   "$(phsh "PORTHOLE_USB_SYSFS=$USBFAKE" 'ph_usb_state')" "unknown"
is "no usb sysfs to read is unknown, not absent" \
   "$(phsh "$USBIDS PORTHOLE_USB_SYSFS=$USBFAKE/gone" 'ph_usb_state')" "unknown"

# Only one of the two IDs filled in must still work: the template ships both
# empty and a bring-up fills them in one at a time.
mkdir -p "$USBFAKE/1-2"
printf '18d1\n' > "$USBFAKE/1-2/idVendor"; printf '4ee0\n' > "$USBFAKE/1-2/idProduct"
is "the bootloader ID alone is enough" \
   "$(phsh "PORTHOLE_USB_FASTBOOT_ID=18d1:4ee0 PORTHOLE_USB_SYSFS=$USBFAKE" \
           'ph_usb_state')" "fastboot"
is "the gadget ID alone does not claim the bootloader" \
   "$(phsh "PORTHOLE_USB_GADGET_ID=18d1:d001 PORTHOLE_USB_SYSFS=$USBFAKE" \
           'ph_usb_state')" "absent"

echo "$PASS/$((PASS+FAIL)) passed"
[ "$FAIL" -eq 0 ]
