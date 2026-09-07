#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: soc:qcom
# needs: BOOTED
# env: -
# exits: 0 ok · 1 failed
# ph-sensor-chain.sh -- watch the WHOLE ambient-light / proximity chain at once:
# the kernel's raw IIO values, what iio-sensor-proxy publishes on D-Bus, and
# what the backlight actually does about it.
#
# Run ON the phone. Needs an operator: none of this moves on its own.
#
#     ph-sensor-chain.sh            # 90 s at ~4 Hz
#     SECS=180 ph-sensor-chain.sh   # longer
#
# Cover the earpiece window with a fingertip for ~10 s, uncover for ~10 s, and
# repeat two or three times. Covering is a real light change AND a proximity
# transition, so one gesture exercises both halves.
#
# Why all four columns in one file: each of them can be right while the next one
# is wrong, and the failures look identical from any single layer.
#
#   prox/lux    the driver. Raw Q16.16 counts, x scale for units.
#   near/level  iio-sensor-proxy. It only POLLS A CLAIMED SENSOR, so this
#               script claims both itself via monitor-sensor -- without a claim
#               ProximityNear is always false and LightLevel never updates,
#               whatever the sensor is doing.
#   backlight   gsd-power acting on level. Requires ambient-enabled=true, which
#               is what /etc/dconf/db/local.d/01-taimen-sensors is for; a flat
#               column with a moving level means that setting is off.
#
# ponytail: a log, not a verdict. The transitions are obvious in the numbers and
# a threshold here would only be one more thing to be wrong about.
set -u

# shellcheck source=../lib/porthole.sh
. "$(dirname "$0")/ph-lib.sh"

SECS=${SECS:-90}
BL=$(ls -d /sys/class/backlight/* 2>/dev/null | head -1)
DEV=$(for d in /sys/bus/iio/devices/iio:device*; do
	case "$(cat "$d/name" 2>/dev/null)" in *prox-light) echo "$d";; esac
done | head -1)
[ -n "$DEV" ] || { echo "no qcom-smgr-prox-light device"; exit 1; }

echo "device:    $DEV"
echo "backlight: ${BL:-none}"
echo "ambient-enabled: $(gsettings get org.gnome.settings-daemon.plugins.power \
	ambient-enabled 2>/dev/null || echo '?')"
echo

# A claim needs an ACTIVE seat session; over ssh polkit refuses one, so this
# runs under sudo. It is also what makes the proxy poll at all.
# shellcheck disable=SC2024  # sudo is for monitor-sensor itself;
# the redirect target is /tmp and needs no privilege.
sudo -n monitor-sensor --proximity --light >/tmp/tk-sensor-chain.mon 2>&1 &
MON=$!
trap 'kill $MON 2>/dev/null' EXIT INT TERM
sleep 2

printf '%8s %10s %10s %6s %9s %5s\n' \
	time prox_raw lux_raw near level bl
i=0
while [ "$i" -lt $((SECS * 4)) ]; do
	# One decimal second, from uptime: `date +%s%N` returns 0 on this busybox.
	printf '%8s %10s %10s %6s %9s %5s\n' \
		"$(cut -d' ' -f1 /proc/uptime)" \
		"$(cat "$DEV/in_proximity_raw" 2>/dev/null || echo ERR)" \
		"$(cat "$DEV/in_illuminance_raw" 2>/dev/null || echo ERR)" \
		"$(busctl get-property net.hadess.SensorProxy /net/hadess/SensorProxy \
			net.hadess.SensorProxy ProximityNear 2>/dev/null | cut -d' ' -f2)" \
		"$(busctl get-property net.hadess.SensorProxy /net/hadess/SensorProxy \
			net.hadess.SensorProxy LightLevel 2>/dev/null | cut -d' ' -f2)" \
		"$(cat "$BL/brightness" 2>/dev/null || echo -)"
	i=$((i + 1))
done

echo
echo "-- monitor-sensor transitions --"
grep -iE "proximity|light" /tmp/tk-sensor-chain.mon | tail -40
