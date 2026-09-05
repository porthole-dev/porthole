---
id: the-device-keeps-a-default-route-through-a-link-that-is-gone
title: The device keeps its USB default route after the host drops the link, and then has no internet at all
scope: generic
subsystem: network
severity: trap
confidence: proven
evidence: taimen 2026-09-05 -- with the host's gadget profile set to autoconnect=no, the phone still had `default via 172.16.42.2 dev usb0 metric 100` alongside `default via <wlan-gateway> dev wlan0 metric 600`; wget to youtube.com failed with "can't connect to remote host" until `ip route del default via 172.16.42.2 dev usb0`, after which the same wget returned the page
first-learned: 2026-09-05
---

**Symptom** — the phone is reachable over ssh and `ip addr` looks perfect, but
nothing on it can reach the internet. In a browser this is a blank or
"Problem Displaying Page"; in a shell it is `wget: can't connect to remote
host`. Every arm that loads a page is silently void, and the numbers it
prints describe an error page.

**Cause** — the USB gadget's default route is installed with a **lower metric**
than wifi (100 against DHCP's 600), so it wins. It is a static route nothing
withdraws: when the HOST stops configuring its end -- unplugged, or the NM
profile set to `autoconnect no`, which is the recommended fix for
[[the-usb-gadget-can-steal-the-hosts-default-route]] -- the phone keeps
routing every packet at a gateway that is no longer there.

The two fixes therefore collide: making the host safe makes the device
internet-less, and neither end says so.

**What to do** — when you move a device onto wifi, delete the gadget default
route on the DEVICE as well:

    sudo ip route del default via <host ip> dev usb0
    ip route            # exactly one default, via wlan0

**Check the route before believing any browser arm**, and force IPv4 when you
check reachability: DNS may answer AAAA and succeed over IPv6 while every IPv4
path is black-holed, which is how this hid for a whole session
(`wget -qO- https://example.com` succeeded while Epiphany showed an error
page). `document.title` is the cheapest witness that a page is the page.

**Related** — [[the-usb-gadget-can-steal-the-hosts-default-route]].
