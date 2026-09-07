#!/bin/sh
# SPDX-License-Identifier: MIT
# scope: soc:msm8998
# needs: - (host only, no device)
# env: -
# exits: 0 ok · 1 failed
# tk-fp-probe.sh -- is the fingerprint sensor physically alive?
# Run ON THE DEVICE as root. Reads and toggles two GPIOs. Touches no bus.
#
# WHAT THIS DOES AND DOES NOT SETTLE
#   It does NOT try to make fingerprint work. Fingerprint is struck on four
#   independent grounds (PLAN §4.2) and this cannot change that. What it settles
#   is a narrower and still-open question: does the FPC1020 part respond to
#   Linux at all, or is the Linux half dead too?
#
# WHY IT IS RUNNABLE AT ALL, WHEN THE SPI BUS IS FENCED
#   taimen's sensor is an FPC1020 (`fpc,fpc1020`, msm8998-taimen-fingerprint.dtsi):
#       IRQ    tlmm 86    FP_INT
#       RESET  tlmm 128   FP_RESET_N
#       SPI    BLSP2 QUP6, gpio81-84
#   The signed TrustZone devcfg fences the SPI PADS from HLOS -- but §4.2
#   established that **tlmm 86 and tlmm 128 are not fenced**: 86 sits in PERIPH 4
#   with RW_ACCESS_LIST = {0x03} (HLOS present) and 128 appears in no record at
#   all. So Linux owns reset and interrupt; it only lacks the bus.
#
# THE SEQUENCE IS THE VENDOR'S OWN, NOT INVENTED
#   fpc1020_platform_tee.c:242 hw_reset() does exactly this, then reads the IRQ
#   line and prints "IRQ after reset %d". We reproduce it with sysfs GPIO:
#       reset HIGH, 100-200 us
#       reset LOW,  5000-5100 us
#       reset HIGH, 5000+ us
#       read IRQ
#
# READING THE RESULT
#   IRQ changes state across the reset  -> the part is present, powered and
#       responding. Linux's half works, and the ONLY missing piece is the TEE
#       side -- see the note below, which is the real reason this is struck.
#   IRQ never moves -> either the sensor is unpowered (vdd_io is pm8998_s4, and
#       nothing in mainline turns it on) or it is not responding. Check the
#       regulator before concluding the part is dead.
#
# THE REAL REASON FINGERPRINT IS OUT, which this makes concrete:
#   The vendor driver is `fpc1020_platform_tee.c` -- *_tee*. 658 lines, ZERO SPI
#   transfer calls; every `spi` in the file is vreg_setup(fpc1020, "vcc_spi").
#   The sensor is owned by the TrustZone/TEE by design: Linux does power, reset,
#   IRQ and a wakelock, and all image traffic happens inside the TEE. The devcfg
#   fence is therefore not a lockout to defeat, it is the architecture behaving
#   correctly -- and mainline has no Qualcomm TEE fingerprint client to be the
#   other half. That is a cleaner statement of the verdict than "TZ blocks us",
#   and it explains all four grounds at once.
#
# ponytail: sysfs gpio, not libgpiod -- it is three writes and a read.
set -u

IRQ_PIN=${IRQ_PIN:-86}
RST_PIN=${RST_PIN:-128}
GPIO=/sys/class/gpio

# tlmm's gpiochip base is not always 0; find it rather than assuming.
BASE=$(cat /sys/class/gpio/gpiochip*/base 2>/dev/null | sort -n | head -1)
[ -n "$BASE" ] || { echo "no gpiochip found"; exit 1; }
IRQ=$((BASE + IRQ_PIN))
RST=$((BASE + RST_PIN))
echo ">> gpiochip base $BASE -> irq gpio$IRQ_PIN=$IRQ  reset gpio$RST_PIN=$RST"

ex() { [ -d "$GPIO/gpio$1" ] || echo "$1" > "$GPIO/export" 2>/dev/null; }
un() { [ -d "$GPIO/gpio$1" ] && echo "$1" > "$GPIO/unexport" 2>/dev/null; }
cleanup() { un "$IRQ"; un "$RST"; }
trap cleanup EXIT INT TERM

ex "$IRQ" || true
ex "$RST" || true
[ -d "$GPIO/gpio$IRQ" ] || { echo "cannot export IRQ gpio -- is it claimed by a driver?"; exit 1; }

echo in  > "$GPIO/gpio$IRQ/direction" 2>/dev/null
echo ">> IRQ at rest: $(cat "$GPIO/gpio$IRQ/value")"

if [ ! -d "$GPIO/gpio$RST" ]; then
	echo ">> reset gpio not exportable; reporting IRQ only"
	exit 0
fi
echo out > "$GPIO/gpio$RST/direction" 2>/dev/null

# The vendor sequence, fpc1020_platform_tee.c:242.
echo 1 > "$GPIO/gpio$RST/value"; usleep 200   2>/dev/null || sleep 0.001
echo 0 > "$GPIO/gpio$RST/value"; usleep 5100  2>/dev/null || sleep 0.006
B=$(cat "$GPIO/gpio$IRQ/value")
echo 1 > "$GPIO/gpio$RST/value"; usleep 6000  2>/dev/null || sleep 0.007
A=$(cat "$GPIO/gpio$IRQ/value")

echo ">> IRQ while in reset: $B"
echo ">> IRQ after reset:    $A   (vendor prints this exact value)"
if [ "$B" != "$A" ]; then
	echo ">> ALIVE: the IRQ line moved across the reset. The part is present and"
	echo ">> responding; Linux's half works and only the TEE side is missing."
else
	echo ">> NO RESPONSE: IRQ did not move. Check vdd_io (pm8998_s4) is actually on"
	echo ">> before concluding the sensor is dead -- nothing in mainline enables it."
fi
