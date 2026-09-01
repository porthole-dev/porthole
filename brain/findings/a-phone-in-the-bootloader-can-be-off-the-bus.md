---
id: a-phone-in-the-bootloader-can-be-off-the-bus
title: A phone can reach the bootloader and never enumerate, and porthole read that as never reaching it
scope: generic
subsystem: device
severity: finding
confidence: proven
evidence: "taimen, 2026-09-01. `.run/build-fast-20260901-101003.log`: modules pushed and vermagic-verified, then `reboot(RESTART2, \"bootloader\")`, then `nothing happened in 60s -- request swallowed`, then `TIMED OUT after 180.6s -- never reached the bootloader`. The host kernel log for the same window contradicts every word of that verdict: `set 01 10:15:01 usb 1-2: USB disconnect, device number 52` -- one second after the request, so the reboot HAPPENED -- and then no enumeration on 1-2 at all until `set 01 10:41:06 usb 1-2: new high-speed USB device number 56 ... idProduct=4ee0` when the operator unplugged and replugged the cable. The phone sat on the bootloader screen for those 26 minutes. `fastboot devices` was correct and useless: there was genuinely nothing on the bus to list."
refutes: "an empty `fastboot devices` means the phone never left pmOS; a reboot request that produces neither fastboot nor a new boot_id was swallowed; the recovery for a fastboot timeout is to power the phone off and hold Power + Volume-Down; USB IDs cannot discriminate anything useful on a device where lsusb mislabels the gadget"
first-learned: 2026-09-01
---

## What happened

A `fast` rung finished its build, pushed modules, and asked for the bootloader.
The phone went. Nothing ever appeared on the host's USB bus. porthole polled
its 180 s budget, concluded **"never reached the bootloader"**, and told the
operator to power the phone off and hold Power + Volume-Down.

That advice was wrong in the most expensive direction available: the phone was
already exactly where the tool wanted it, and following the advice would have
thrown that away. The real fix was to unplug the cable and plug it back in,
which takes two seconds and keeps the bootloader.

## Why the tool could not tell

`fastboot devices` returns empty stdout for three completely different states:

1. the phone is still running pmOS and ignored the reboot request
2. the phone is in the bootloader, enumerated, but fastboot cannot **open** the
   device -- no udev rule for the bootloader's product ID, or another process
   holding the interface
3. the phone is in the bootloader and **not on the bus at all**

Every wait loop in the repo read that empty string as state 1, because state 1
is the only one anything had ever looked for. State 3 is the one that cost the
session, and it is invisible to `fastboot devices` by construction.

## The discriminator was already in the config and nothing read it

`profiles/google-taimen/device.env` has carried both IDs since the profile was
written:

    PORTHOLE_USB_GADGET_ID="18d1:d001"     # the running pmOS gadget
    PORTHOLE_USB_FASTBOOT_ID="18d1:4ee0"   # the real bootloader

`PORTHOLE_USB_FASTBOOT_ID` had **zero readers in the tree**. `_GADGET_ID` had
one, in a `porthole brief` sentence. The fact that separates the three states
was recorded, committed, documented -- and never consulted by the code that
needed it.

The reason is worth naming, because it is a good rule that overshot. The trap
`usb-ids-cannot-tell-booted-from-bootloader.md` says lsusb mislabels the
running pmOS gadget as "Nexus 4 (fastboot)", so `fastboot devices` is the only
thing that can say *which* of the two states the phone is in. That is true, and
it quietly hardened into "do not look at USB IDs", which is not. The label text
lies. The **product ID does not**: `d001` and `4ee0` are different numbers.
And no ID at all is a third answer that neither the label nor `fastboot
devices` can give.

## What changed

`ph_usb_state` in `lib/porthole.sh` reads `/sys/bus/usb/devices/*/id{Vendor,Product}`
and returns `fastboot` | `gadget` | `absent` | `unknown`. sysfs rather than
lsusb: no binary that can be missing, no label text to be fooled by, and it
reads identically inside the rootless workspace. `unknown` when the profile
names no IDs -- a guess is worse than silence, since the whole value is that an
operator can act on `absent` without checking by hand.

`tools/tk-to-fastboot.sh` now switches every failure message on it, stops
calling a phone that left the bus "swallowed", prints the bus state during the
wait rather than only at the end, and says **"the bootloader never answered"**
instead of "never reached the bootloader" -- the second is a claim about the
phone that this script has no standing to make.

## What is still not known

Why the bootloader's USB did not come up on this host. The host saw a clean
disconnect and then nothing for 26 minutes on a port that had been working; a
replug fixed it immediately. Candidates not yet separated: an xHCI port left in
a state where it misses the reconnect, the bootloader's gadget coming up before
the port was ready, or the cable. Nothing here distinguishes them.

Note what porthole **cannot** do about it either way. `tools/tk-usb-wake.py`
already issues `USBDEVFS_RESET` on the gadget, which is the software equivalent
of a replug -- but it acts on a device node, and in state 3 there is no node to
act on. A recovery would have to power-cycle the **root hub port**, which needs
host root, which `no-host-root` forbids and this project exists to avoid. So
the honest ceiling is the one that was taken: porthole names the state
precisely and tells the operator the one action that works.

## Related

- `brain/traps/usb-ids-cannot-tell-booted-from-bootloader.md` -- true about the
  label, and the reason nothing looked at the bus for a year
- `brain/traps/ab-retry-counter-is-a-countdown-not-a-glitch.md` -- the fallback
  path whose budget the "swallowed" misverdict was spending
