---
id: the-usb-gadget-can-steal-the-hosts-default-route
title: The USB gadget is a DHCP server, and NetworkManager lets it take the host's default route and DNS
scope: generic
subsystem: network
severity: trap
confidence: proven
evidence: host 2026-09-05 -- the gadget's auto-created profile "Wired connection 2" (enp5s0f3u2) had ipv4.method=auto, ipv4.never-default=no, ipv4.ignore-auto-dns=no and connection.autoconnect=yes; the user lost internet repeatedly while the phone was plugged in. `porthole doctor` now checks it, and flipping ipv4.never-default back to `no` makes the check warn again
first-learned: 2026-09-05
---

**Symptom** — the HOST loses internet, on and off, for as long as the phone is
plugged in. Web pages stop loading and `git push` hangs, but nothing looks like
a networking failure: `ping 1.1.1.1` may still work, the wifi/ethernet link is
up, and **the agent in the terminal keeps working perfectly** because ssh to the
device is on the link that still works. It reads as "the internet is flaky
today", and it gets blamed on the router.

**Cause** — the USB gadget runs a **DHCP server**. NetworkManager auto-creates
a wired profile for the new interface with `autoconnect=yes` and stock
defaults, and the stock defaults accept everything DHCP offers -- including a
**default route** and **nameservers**. So plugging in a phone that has no
upstream reroutes the host's traffic into it, and points the host's resolver at
it. Every re-enumeration re-applies it.

It is easy to miss twice over: the profile is created silently, and its name is
the generic `Wired connection N`, so nothing on screen connects it to the
phone.

**What to do** — leave the address, refuse the route and the DNS. `porthole
doctor` reports this as `host: device link` and prints the fix with the right
UUID already in it:

    nmcli connection modify <uuid> \
        ipv4.never-default yes ipv6.never-default yes \
        ipv4.ignore-auto-dns yes ipv6.ignore-auto-dns yes \
        ipv4.route-metric 4000 ipv6.route-metric 4000
    nmcli connection up <uuid>

ssh to the device is unaffected -- the subnet route stays, only the default
route and the nameservers stop being the phone's to give.

**Do not** match the profile by interface name. udev names the gadget from the
USB path, so it is `enp5s0f3u2` on one machine and `enp0s20f0u4` on the next,
and a different USB port on the SAME machine creates a fresh profile with the
stock defaults again. The check resolves it from `ip route get $PORTHOLE_HOST`
instead, which is true wherever the device actually is.

**Related** — [[usb-gadget-rerandomises-the-host-mac]], the other way this
link's defaults are not what they appear to be.
