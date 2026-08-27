---
id: wifi-dies-while-still-reporting-connected
title: WiFi dies while every layer still reports connected -- and it is not the CPU, the GPU, or board-2.bin
scope: device:google-taimen
subsystem: wifi
severity: finding
confidence: proven
evidence: caught live 2026-08-28 on kernel #99 -- wlan0 UP, <wlan-ip>/24, default route present, nmcli "connected", and 100% packet loss to the gateway AND to 8.8.8.8, host->phone too, for ~36 minutes; only a reboot recovered it
refutes: the phone feels slow because the CPU or GPU underperform; the WiFi board calibration (board-2.bin) is missing; every suspend breaks WiFi; the BP-06 16-minute idle-suspend chain is what kills it now
first-learned: 2026-08-28
---

**The question** — the user reports the phone is "flaky" and that YouTube in
Firefox is extra slow. Is that the CPU, the GPU, or something else?

**The answer** — the WiFi link dies while every layer above it still believes
it is up. Caught live: `wlan0` UP with the right address and default route,
`nmcli` reporting `connected`, and **100% packet loss in both directions** --
phone to gateway, phone to 8.8.8.8, and host to phone -- for about 36 minutes.
It did not self-heal. `apk add` had worked over that same link twenty minutes
earlier, so it degraded from working, not from never working.

The failing boot had **3 `Key negotiation completed` events against 13+
associations**, and a run of `CTRL-EVENT-DISCONNECTED reason=4`
(DISASSOC_DUE_TO_INACTIVITY) that the phone generated itself
(`locally_generated=1`). That is the shape of an association that completes
while the key install does not -- which is precisely what this port's live
WiFi work is about: the `wifi-disablekey-test` branch, and series patches
`0114` (restart the firmware when a SET_KEY install times out) and `0166`
(don't wait for DISABLE_KEY acks).

**What this rules out**

- *"The CPU and GPU underperform."* Measured on the same kernel, same session:
  `cpu_capacity` 549 silver / 1024 gold (correct), every heavy app scheduled
  onto gold cores, GPU reaching its top OPP of 710 MHz, `grid-fling` holding
  **59.7 fps** with p50 16.6 ms and 0.4 % dropped frames, **zero** GPU faults
  across 12 boots and under gesture load, no memory pressure (zram 0 B used),
  no I/O pressure, no thermal throttling. The hardware is fine.
- *"board-2.bin is missing."* It is not. `failed to fetch board data for
  bus=snoc,qmi-board-id=0,qmi-chip-id=0` appears **only after a manual
  `modprobe -r ath10k_snoc; modprobe ath10k_snoc`**, where QMI does not
  re-handshake and both ids come back 0. A normal boot reads
  `board_id 0xff chip_id 0x30214` and loads it. **A driver reload is not a
  substitute for a reboot here, and its failures are its own.**
- *"Every suspend breaks it."* Suspend does deauthenticate
  (`wlan0: deauthenticating ... by local choice (Reason: 3=DEAUTH_LEAVING)`,
  NetworkManager's normal sleep handling) and resume re-associates ~4 s later.
  **8 of 8** RTC suspend/resume cycles recovered with 0 % loss, alternating
  across both APs. So the teardown is not the bug.
- *"BP-06's 16-minute idle-suspend chain is the trigger."* Not this time: one
  suspend request in the whole failing boot, 35 minutes before the failure,
  and the `10-taimen-broken.conf` guard BP-06 documents is gone now.

**What is NOT closed** — the reproduction. 15-second RTC suspends do not
trigger it; the failure followed roughly half an hour of real idle. So the
8/8 pass is a pass on a path that is not the reported one. The next arm is a
long-sleep soak (10-20 minutes per cycle, data-path check after each), not
more short cycles.

**Do not diagnose this over the WiFi link.** And do not use
`nmcli dev disconnect wlan0` to test it: that suppresses autoconnect until an
explicit reconnect, and the PSK is agent-owned, so `nmcli con up` as root
fails with "Secrets were required" and as the user with "Not authorized to
control networking". The phone then sits disconnected until a reboot. Use the
usb0 link for control and let NetworkManager own wlan0.
