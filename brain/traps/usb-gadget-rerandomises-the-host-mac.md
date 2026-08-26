---
id: usb-gadget-rerandomises-the-host-mac
title: The USB gadget hands the host a new MAC on every boot
scope: generic
subsystem: netconsole
severity: trap
confidence: proven
evidence: taimen 2026-08-26. Two reboots, host MAC 92:fe:35:3a:2a:40 -> b2:c4:15:50:52:59 -> 36:31:25:96:6e:ec on the same cable and the same interface name. Re-check with `cat /sys/class/net/<usb-iface>/address` before and after a reboot.
first-learned: 2026-08-26
---

**Symptom** — netconsole was armed, `enabled=1` reads back from configfs, the
interface is up with the address it always has, and not one packet arrives on
the host. Usually noticed after a reboot: the capture worked, the phone
rebooted, and everything downstream of that is silence. `arm.log` looks
perfect for the whole dead window.

**Cause** — `u_ether` generates the host-side address randomly at every
enumeration unless the gadget is configured with a fixed `host_addr`. The
interface *name* is stable (udev names it by USB path) and so is the IP if
something assigns it statically, which is exactly what makes this hard to see:
the only thing that moved is the one field nothing displays. Any tool that
sampled the MAC once and reuses it is now transmitting at an address that no
host owns, and netconsole has no way to tell you — it hands the frame to the
netpoll transmit path and that succeeds.

**What to do** — never cache the host MAC across a reboot; read it at the
moment you arm, next to the write that consumes it. And do not accept
`enabled=1` as proof: push a token through `/dev/kmsg` and require the sink to
print it back, which is the only statement about this channel worth trusting.
`tools/tk-capture.sh` does both, and its forever mode re-reads the address on
every re-arm.

The same caution applies to anything else that pins the peer by hardware
address over the gadget link — static ARP entries, a packet filter matched on
MAC, a bridge with a fixed FDB entry.

Related: [[netconsole-is-the-only-witness-of-a-panic]],
[[an-instrument-that-fails-quietly-is-worse-than-none]]
