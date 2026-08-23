---
id: 25-device-tree
title: "Playbook: writing a device tree"
scope: generic
subsystem: kernel
severity: technique
confidence: proven
evidence: taimen linux/arch/arm64/boot/dts/qcom/msm8998-google-taimen.dts and its wahoo.dtsi
first-learned: 2026-07-26
---

**Goal:** a device tree that describes your board accurately enough for the
kernel to find the hardware.

**Done when:** the device boots on your DTS and the drivers you expect probe.

## The shape, which is most of the work

```
<soc>.dtsi                        the SoC. Someone else wrote it. Do not touch.
  <soc>-<family>.dtsi             optional: a board family shared by siblings
    <soc>-<vendor>-<codename>.dts YOUR device
```

Taimen's own `.dts` is short because `msm8998-google-wahoo.dtsi` carries
everything it shares with walleye, which in turn includes `msm8998.dtsi` — 321
labelled nodes someone else already wrote. **Your file describes the board**:
which regulator feeds what, which GPIO is which button, which I2C bus the
touchscreen sits on.

If a sibling on your SoC has a family dtsi and your device is in that family,
inherit it. That is why a device DTS can be two hundred lines.

## Every value is a fact to be found, not chosen

A device tree describes hardware you did not design. There is a correct answer
for every property and it exists somewhere:

| source | what it gives you |
|---|---|
| the downstream kernel DTS | every regulator, GPIO and I2C address, as the vendor measured them |
| the device's own dtb/dtbo | what the bootloader applies *right now* — authoritative even with no source |
| `/proc/device-tree` on stock | the live tree, easiest to obtain |
| a mainline sibling | the mainline node names and bindings the SoC dtsi expects |
| `Documentation/devicetree/bindings/` | what each property means and which are required |

`porthole dts sources` prints this with the search terms that work.

**Downstream and mainline device trees are not interchangeable.** Downstream
describes the same hardware with different bindings, different node names, and
properties invented by the vendor. Read it for *values*; take *structure* from
the mainline sibling.

## Order of attack

1. **UART / console.** You cannot debug what cannot talk, and this is the only
   channel that exists before driver probe. See
   [[a-hard-hang-writes-nothing-to-disk]].
2. **Regulators.** Nearly everything else depends on them, and a driver whose
   supply is missing fails at probe with a message about the supply, not about
   itself.
3. **Storage.** No rootfs without it.
4. **USB.** The network, and the shell.
5. **Everything else**, in whatever order the device makes painful.

## What goes wrong

**A DTS that compiles is not a DTS that is right.** `dtc` checks syntax and a
handful of structural rules. It cannot know that your regulator is the wrong
one or that an I2C address is off by a bit. Probe failures are the real test.

**A node you did not define may already be defined.** Before adding anything,
check what the included dtsi already says — `porthole dts labels` lists every
label the SoC dtsi defines, and `porthole dts compare <sibling>` follows
includes so it does not report inherited nodes as missing.

**Volume-down is often not a GPIO.** On many phones it is wired to the PMIC
RESIN pin, which is also why Power+VolDown is the hardware reset combo. People
lose a day to a key that was never on the SoC.

**A wrong memory node resets the device before anything can say why.** Take the
base and size from the downstream DTS, not from arithmetic.

**Add the file to the Makefile.** A `.dts` that is not listed is never built,
and the build succeeds, and you flash the previous DTB and wonder why nothing
changed. Same family as [[timestamps-cannot-prove-a-build-is-fresh]].

## Tooling

```sh
porthole dts sources              where the values come from
porthole dts labels               what the SoC dtsi already defines
porthole dts new                  scaffold, inheriting the family dtsi
porthole dts compare <sibling>    what they configure that you have not
porthole dts check                does it compile
```

Related: [[10-first-boot]], [[dtbo-must-match-the-kernel]],
[[prove-which-kernel-answered]].
