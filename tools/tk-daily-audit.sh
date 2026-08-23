#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: soc:msm8998
# needs: on-device (run it on the device; prints evidence, not verdicts)
# env: PORTHOLE_CODENAME, PORTHOLE_USER
# exits: 0 ok · non-zero on failure
# Daily-driver readiness audit. Run ON THE DEVICE.
#
# The checklist is postmarketOS's own launch requirements for a phone people
# actually rely on (calls, SMS, data, camera, sensors, buttons, charging,
# audio in and out, a browser, encryption) plus the integrations Phosh expects
# (feedbackd haptics, gmobile device profile, callaudiod routing, fprintd,
# torch, rotation, MMS).
#
# Every line prints the evidence, not a verdict, because "the package is
# installed" and "the feature works" are different claims and this tree has
# been burned by conflating them before. Read the right-hand column.

say() { printf '%-26s %s\n' "$1" "$2"; }
have() { command -v "$1" >/dev/null 2>&1 && echo yes || echo NO; }
pkg() { apk info -q 2>/dev/null | grep -qx "$1" && echo installed || echo MISSING; }
unit() { systemctl is-active "$1" 2>/dev/null || echo inactive; }

echo "=== telephony ==="
say "ModemManager"      "$(unit ModemManager.service)"
say "modem present"     "$(mmcli -L 2>/dev/null | head -1 | sed 's/^ *//' || echo 'mmcli failed')"
say "SIM lock"          "$(mmcli -m any 2>/dev/null | grep -iE 'state:|lock' | tr -s ' ' | tr '\n' ' ')"
say "calls app"         "$(pkg gnome-calls)"
say "callaudiod"        "$(unit callaudiod.service) / $(pkg callaudiod)"
say "SMS app (chatty)"  "$(pkg chatty)"
say "MMS (mmsd-tng)"    "$(pkg mmsd-tng) / $(unit mmsd-tng.service)"
say "eg25-manager"      "$(pkg eg25-manager) (pinephone-only, not expected here)"

echo
echo "=== power ==="
say "suspend states"    "$(cat /sys/power/state 2>/dev/null)"
say "mem_sleep"         "$(cat /sys/power/mem_sleep 2>/dev/null || echo none)"
# The guard that stops phosh's idle-suspend walking into the ipa hard-hang.
# It must be on systemd-suspend.service: suspend.target is ordered After= that
# service, so a guard on the TARGET is decorative -- which is what it was until
# 2026-08-02, when the phone suspended anyway and needed a hands-on power cycle.
# Read the assertion and the arming file. NEVER `systemctl show -p
# AssertPathExists` (prints nothing even when installed), and NEVER by
# attempting a suspend -- use tools/tk-suspend-guard-check.sh. See PLAN v2 6.5a.
say "suspend guard"     "$(systemctl cat systemd-suspend.service 2>/dev/null | grep -c '^AssertPathExists') assertion(s) on systemd-suspend.service"
say "suspend ALLOWED"   "$([ -e /run/taimen-suspend-is-safe ] && echo 'YES -- HAZARD, ipa hang is armed' || echo 'no (guarded)')"
say "wakeup sources"    "$(grep -c . /sys/kernel/debug/wakeup_sources 2>/dev/null || echo '?') entries"
say "armed wakeups"     "$(for d in /sys/class/wakeup/*/; do [ -e "$d/../device/power/wakeup" ] && cat "$d/../device/power/wakeup"; done 2>/dev/null | grep -c enabled) enabled"
say "battery"           "$(cat /sys/class/power_supply/*/capacity 2>/dev/null | head -1)% $(cat /sys/class/power_supply/*/status 2>/dev/null | head -1)"
say "zram swap"         "$(swapon --show=NAME,SIZE --noheadings 2>/dev/null | tr '\n' ' ' || echo none)"
say "rtc"               "$(cat /sys/class/rtc/rtc0/since_epoch 2>/dev/null || echo none) since_epoch"

echo
echo "=== feedback / phosh integration ==="
say "feedbackd"         "$(pkg feedbackd)"
say "vibrator (ff)"     "$(ls /dev/input/by-path/*rumble* /sys/class/leds/*vibrator* 2>/dev/null | head -1 || echo 'none found')"
say "notification LED"  "$(ls -d /sys/class/leds/*:status* /sys/class/leds/*rgb* /sys/class/leds/*charging* 2>/dev/null | head -3 | tr '\n' ' ' || echo none)"
say "all leds"          "$(ls /sys/class/leds/ 2>/dev/null | tr '\n' ' ' || echo none)"
say "torch/flash"       "$(ls /sys/class/leds/*flash* /dev/v4l-subdev* 2>/dev/null | head -1 || echo none)"
say "gmobile profile"   "$(grep -rl 'google,taimen' /usr/share/gmobile/ 2>/dev/null || echo 'MISSING -- phosh warns \"No info for google,taimen\"')"

echo
echo "=== input ==="
say "touchscreen"       "$(grep -c ftm4 /proc/interrupts) irq line(s)"
say "volume keys"       "$(grep -cE 'Volume Up|pm8998_resin|resin' /proc/interrupts) irq line(s)"
say "power key"         "$(ls /sys/class/input/ | head -0; grep -rl 'pwrkey' /sys/class/input/*/device/name 2>/dev/null | head -1 || echo 'check evtest')"
say "fingerprint"       "$(ls /dev/fprint* 2>/dev/null; pkg fprintd) / $(unit fprintd.service)"

echo
echo "=== media ==="
say "camera (v4l2)"     "$(ls /dev/video* 2>/dev/null | tr '\n' ' ' || echo 'no /dev/video*')"
say "camss driver"      "$(grep -c camss /proc/modules 2>/dev/null; dmesg 2>/dev/null | grep -ci camss) module/dmesg hits"
say "libcamera"         "$(pkg libcamera)"
say "camera app"        "$(pkg megapixels || true) $(ls /usr/share/applications/ | grep -i camera | tr '\n' ' ')"
say "audio card"        "$(cat /proc/asound/cards 2>/dev/null | head -2 | tr '\n' ' ')"
say "UCM profile"       "$(ls /usr/share/alsa/ucm2/conf.d/msm8998/ 2>/dev/null | tr '\n' ' ' || echo MISSING)"
say "pipewire"          "$(unit pipewire.service 2>/dev/null; systemctl --user is-active pipewire 2>/dev/null || echo '(user bus)')"

echo
echo "=== connectivity ==="
say "wifi"              "$(nmcli -t -f DEVICE,STATE dev 2>/dev/null | grep ^wlan | tr '\n' ' ')"
say "bluetooth"         "$(bluetoothctl show 2>/dev/null | grep -E 'Powered|Alias' | tr -s ' ' | tr '\n' ' ')"
say "gps"               "$(unit geoclue.service)"
say "usb tethering"     "$(nmcli -t -f DEVICE,STATE dev 2>/dev/null | grep ^usb | tr '\n' ' ')"

echo
echo "=== apps / platform ==="
say "browser"           "$(have firefox-esr) -- see HANDOFF: content processes die"
say "flatpak"           "$(have flatpak)"
say "software centre"   "$(pkg gnome-software)"
say "full disk encrypt" "$(lsblk -o NAME,FSTYPE 2>/dev/null | grep -c crypto_LUKS) LUKS volume(s)"
say "XDG_CURRENT_DESKTOP" "$(tr '\0' '\n' < /proc/$(pgrep -x phosh)/environ 2>/dev/null | grep XDG_CURRENT || echo UNSET)"

echo
echo "=== phase-a baseline ==="
# The F1 hypothesis, tested in one place: haptics and ALS/prox are BOTH
# modules, and CONFIG_MODVERSIONS makes every .ko refuse to load after a
# kernel rebuild unless the module tree was staged wholesale. If drv2624
# fails to load with a vermagic/symbol error, that is one stale module tree
# explaining both regressions. If it loads clean and the phone still does not
# buzz, the hypothesis is DEAD -- re-plan toward evdev/feedbackd.
say "modversions"        "$(zgrep -h '^CONFIG_MODVERSIONS' /proc/config.gz 2>/dev/null || echo 'not readable')"
# timeout: modprobe runs the driver's probe(), which does I2C. On a wedged bus
# that blocks forever and takes the whole audit down with it -- and a wedged
# bus is exactly what this line exists to detect. Report the timeout AS the
# evidence rather than hanging.
mp=$(timeout 15 modprobe drv2624 2>&1); mprc=$?
mp=$(printf '%s\n' "$mp" | head -1)
[ "$mprc" = 124 ] && mp='TIMED OUT after 15s -- probable wedged i2c bus'
[ -z "$mp" ] && mp='(no output)'
lm=$(lsmod 2>/dev/null | grep -q '^drv2624' && echo loaded || echo 'NOT loaded')
say "drv2624 load"       "$mp / $lm"
vm=$(dmesg 2>/dev/null | grep -iE 'version magic|disagrees about version|no symbol version' | tail -2 | tr '\n' ' ')
say "vermagic mismatch"  "${vm:-none}"
ff=$(for d in /sys/class/input/event*/device/capabilities/ff; do [ -e "$d" ] && [ "$(cat "$d")" != 0 ] && echo "$d=$(cat "$d")"; done 2>/dev/null | head -2 | tr '\n' ' ')
say "ff rumble dev"      "${ff:-NONE -- feedbackd will not list vibration}"
iio=$(grep -l 'qcom-smgr-prox-light' /sys/bus/iio/devices/*/name 2>/dev/null | head -1)
say "iio prox/light"     "${iio:-MISSING}"
lux=$(cat /sys/bus/iio/devices/*/in_illuminance_raw 2>/dev/null | head -1)
say "iio light raw"      "${lux:-no illuminance channel}"
# The script runs under sudo -n, so a bare `systemctl --user` here would query
# root's user manager, not the login user's -- always a bus error, verifying nothing.
say "ambient nudge"      "$(sudo -n -u "$PORTHOLE_USER" XDG_RUNTIME_DIR=/run/user/$(id -u "$PORTHOLE_USER") systemctl --user is-enabled ${PORTHOLE_CODENAME}-ambient-nudge.service 2>&1 | head -1)"
# Phase B's before/after number. of_devfreq_cooling_register() does NOT check
# #cooling-cells (only cpufreq_cooling.c:636 does), so the Adreno driver
# (msm_gpu_devfreq.c:257) has been registering a thermal-devfreq-0 cooling
# device all along under CONFIG_DEVFREQ_THERMAL=y. The true pre-change count
# was ~1 (the GPU devfreq one), not 0. After the cooling-maps change there
# must additionally be TWO cpufreq-* cooling devices, one per cluster policy.
tmax=$(for z in /sys/class/thermal/thermal_zone*/temp; do [ -e "$z" ] && cat "$z"; done 2>/dev/null | sort -n | tail -1)
say "thermal max"        "${tmax:-unknown} mC"
cdevs=$(for c in /sys/class/thermal/cooling_device*/type; do [ -e "$c" ] && cat "$c"; done 2>/dev/null)
cdevcount=$(printf '%s\n' "$cdevs" | grep -c .)
say "cooling devices"    "${cdevcount:-0} registered: $(printf '%s' "$cdevs" | tr '\n' ' ')"
# drivers/thermal/thermal_of.c:984 -- if a cooling map fails to parse,
# thermal_of_build_thermal_zone() jumps to free_tbps and THE ENTIRE ZONE FAILS
# TO REGISTER, silently taking its 110C critical-shutdown trip with it. A
# dropped zone count is the signature of that failure and the single most
# important post-flash check. Before the cooling-maps change this failure
# mode did not exist.
tzones=$(for t in /sys/class/thermal/thermal_zone*/type; do [ -e "$t" ] && cat "$t"; done 2>/dev/null)
tzcount=$(printf '%s\n' "$tzones" | grep -c .)
say "thermal zones"      "${tzcount:-0}: $(printf '%s' "$tzones" | tr '\n' ' ')"
# Registration alone does not prove BINDING; an empty cdev0_trip_point value
# does prove the absence of it.
cdevbound=$(for f in /sys/class/thermal/thermal_zone*/cdev0_trip_point; do
	[ -e "$f" ] || continue
	z=$(dirname "$f")
	printf '%s=%s ' "$(cat "$z/type" 2>/dev/null)" "$(cat "$f" 2>/dev/null)"
done 2>/dev/null)
say "cdev bound"          "${cdevbound:-none}"
say "cpu0 policy"        "$(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_governor 2>/dev/null) $(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq 2>/dev/null)"
say "cpu4 policy"        "$(cat /sys/devices/system/cpu/cpufreq/policy4/scaling_governor 2>/dev/null) $(cat /sys/devices/system/cpu/cpufreq/policy4/scaling_cur_freq 2>/dev/null)"
say "gpu devfreq"        "$(cat /sys/class/devfreq/*.gpu/governor 2>/dev/null | head -1) @ $(cat /sys/class/devfreq/*.gpu/cur_freq 2>/dev/null | head -1)"
say "tail_pipeline"      "$(cat /sys/module/msm/parameters/tail_pipeline 2>/dev/null || echo 'param absent')"
say "uclamp"             "$(zgrep -h '^CONFIG_UCLAMP_TASK' /proc/config.gz 2>/dev/null || echo 'not readable')"
say "firefox"            "$(command -v firefox >/dev/null 2>&1 && echo installed || echo 'not installed')"
