#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: any (the phone is expected to be asleep)
# env: PORTHOLE_HOST, PORTHOLE_WOL_MAC, PORTHOLE_WOL_BROADCAST
# exits: 0 the phone answered · 1 it did not · 64 usage · 65 no MAC to send to
# lib-exempt: sends a UDP broadcast and pings; it never opens a session on the
#   phone, so there is nothing to serialise. It is also what ph-device.sh calls
#   when it finds the phone ABSENT, and taking the mutex here would deadlock it.
"""Wake a sleeping phone over wifi with a WoWLAN magic packet, without hands.

The sibling of ph-usb-wake.py, for the case that tool explicitly refuses: the
phone is not enumerated on USB at all, because an ordinary s2idle takes the
gadget down with it. That is not "a reboot or a cable" -- it is the normal
resting state of a phone nobody is driving, and it costs a power press every
time unless something can reach it over the air.

Measured on taimen, 2026-09-19: two control suspends slept their full 100 s
alarm and woke on the RTC, while the arm that got a magic packet returned in
the same second the host sent it, on WLAN_CE_2 -- the IRQ
ath10k_snoc_hif_suspend() arms with enable_irq_wake(). The association
survives suspend, so the AP still delivers to it.

    ph-wol.py                 # wake the configured phone, wait for it
    ph-wol.py --mac <addr>    # spell the MAC out
    ph-wol.py --wait 90       # how long to wait for it to answer

TWO THINGS HAVE TO BE TRUE or the packet goes nowhere, and neither is loud
about failing:

  * WoWLAN has to be ARMED, which only happens on the logind suspend path.
    A raw `echo freeze > /sys/power/state` or an rtcwake skips every
    /usr/lib/systemd/system-sleep hook, so nothing arms it.
  * The phone has to have been ASSOCIATED when it went down. A phone that
    suspended with wlan0 disconnected cannot be woken this way at all.

SEND TO THE BROADCAST ADDRESS, which is what this does by default. A unicast
packet needs an ARP entry, and the host's entry for a sleeping phone goes
FAILED -- so the packet is never put on the wire and the wake looks broken
when it is the sender that gave up.
"""
import argparse
import os
import re
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))


CACHE = os.path.expanduser("~/.porthole/wol-mac")


def cached_mac(host):
    """The MAC we last saw this host use. Outside the repo on purpose: a MAC
    identifies a device and has no documentary value, so it is never committed
    -- but a fresh session still has to be able to wake the phone without
    anyone exporting anything."""
    try:
        for line in open(CACHE):
            h, _, m = line.strip().partition(" ")
            if h == host:
                return m
    except OSError:
        pass
    return None


def remember_mac(host, mac):
    """Refresh the cache while the phone is awake -- the only time the ARP
    entry is any good."""
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        keep = [l for l in open(CACHE).read().splitlines()
                if l.split(" ")[:1] != [host]] if os.path.exists(CACHE) else []
        keep.append(f"{host} {mac}")
        with open(CACHE, "w") as f:
            f.write("\n".join(keep) + "\n")
    except OSError:
        pass


def arp_mac(host):
    """The phone's MAC as the host last saw it -- works while it is awake."""
    try:
        out = subprocess.run(["ip", "neigh"], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if line.split()[:1] == [host] and "lladdr" in line:
            return line.split("lladdr")[1].split()[0]
    return None


def broadcast_for(host):
    """The subnet broadcast for the phone's address, /24 assumed."""
    parts = host.split(".")
    if len(parts) == 4:
        return ".".join(parts[:3] + ["255"])
    return "255.255.255.255"


def send(mac, dests):
    payload = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "").replace("-", "")) * 16
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    for d in dests:
        for port in (9, 7):
            try:
                s.sendto(payload, (d, port))
            except OSError as e:
                print(f"   {d}:{port} -- {e}", file=sys.stderr)
    s.close()


def answers(host, timeout=1):
    return subprocess.run(["ping", "-c1", "-W", str(timeout), host],
                          capture_output=True).returncode == 0


def main():
    ap = argparse.ArgumentParser(description="wake a sleeping phone over wifi")
    ap.add_argument("--mac", help="the phone's wlan MAC (default: the ARP cache, then PORTHOLE_WOL_MAC)")
    ap.add_argument("--host", default=os.environ.get("PORTHOLE_HOST"),
                    help="the phone's address, for the broadcast and the poll")
    ap.add_argument("--wait", type=int, default=60, help="seconds to wait for an answer")
    args = ap.parse_args()

    if not args.host:
        print("ph-wol: no PORTHOLE_HOST and no --host", file=sys.stderr)
        return 64

    if answers(args.host):
        seen = args.mac or arp_mac(args.host)
        if seen:
            remember_mac(args.host, seen)
        print(f">> {args.host} already answers -- nothing to wake")
        return 0

    mac = (args.mac or os.environ.get("PORTHOLE_WOL_MAC")
           or arp_mac(args.host) or cached_mac(args.host))
    if not mac or not re.fullmatch(r"([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}", mac):
        print("ph-wol: no MAC to send to. The ARP entry for a sleeping phone",
              file=sys.stderr)
        print("        goes FAILED, so record it while the phone is awake:",
              file=sys.stderr)
        print("          export PORTHOLE_WOL_MAC=$(ssh $PHONE 'cat /sys/class/net/wlan0/address')",
              file=sys.stderr)
        print(f"        or run this once while it IS awake, to fill {CACHE}",
              file=sys.stderr)
        return 65

    dests = [os.environ.get("PORTHOLE_WOL_BROADCAST") or broadcast_for(args.host),
             "255.255.255.255"]
    print(f">> magic packet for {mac} -> {', '.join(dests)}")
    send(mac, dests)

    deadline = time.time() + args.wait
    while time.time() < deadline:
        if answers(args.host):
            print(f">> {args.host} answered after {args.wait - int(deadline - time.time())}s")
            return 0
        time.sleep(2)

    print(f">> no answer in {args.wait}s. Either WoWLAN was not armed (only the",
          file=sys.stderr)
    print("   logind suspend path runs the sleep hook) or wlan0 was not",
          file=sys.stderr)
    print("   associated when it suspended. Both need a power press to fix.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
