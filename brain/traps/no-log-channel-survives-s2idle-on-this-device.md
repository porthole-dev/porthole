---
id: no-log-channel-survives-s2idle-on-this-device
title: No log channel survives s2idle here: netconsole is gagged and its netdev is down
scope: device:google-taimen
subsystem: debug
severity: trap
confidence: proven
evidence: taimen 7.2.2 #76, 2026-09-20. ph-capture.sh armed netconsole on BOTH transports and verified them end to end ('netconsole VERIFIED end to end', usb0 6666 and wlan0 6667 both live), with watchdog gov=panic pretimeout=10 timeout=30. Across three watchdog resets in s2idle it delivered NOTHING after 'printk: Suspending console(s)'. Setting /sys/module/printk/parameters/console_suspend=N removed that line and still delivered nothing, so the transport is the blocker, not the console gag. pstore has never worked on 7.2.2 and usb0 drops off the bus in suspend (tk_device_state reads ABSENT). Also: /sys/class/watchdog/watchdog0/timeout is -r--r--r-- and cannot be written at all -- the handoff recipe 'echo 60 > .../timeout' fails with 'Permission denied' even as root, because the watchdog core only settles the timeout through the WDIOC_SETTIMEOUT ioctl, and systemd owns the device at RuntimeWatchdogUSec=30s
first-learned: 2026-09-20
---

**Symptom** — you arm every channel, the tool says the channel is verified end
to end, the phone takes a watchdog reset inside s2idle, and you get nothing:

```
12:23:52 [nc:6666] [  687.981885] printk: Suspending console(s) (use no_console_suspend to debug)
<nothing, ever>
```

**Cause, in two layers.** The first is the obvious one and it is not the real
one. netconsole *is* a console, so `suspend_console()` gags it on the way down.
That is fixable at runtime, no rebuild:

```sh
echo N > /sys/module/printk/parameters/console_suspend
```

Do that and the `Suspending console(s)` line disappears -- and the capture is
**still** empty. The second layer is the transport: netpoll has to transmit
through a netdev that suspend has taken down. wlan0 still *receives* while
suspended, which is why a magic packet wakes this phone, but that is the
firmware's doing and it does not give netpoll a working TX path.

So on this device, for a failure inside s2idle:

| channel | why it cannot witness |
|---|---|
| netconsole / usb0 | the gadget drops off the bus in suspend -- `tk_device_state` reads ABSENT |
| netconsole / wlan0 | receives, cannot transmit; console gag is only the first half |
| pstore / ramoops | has never produced a record on 7.2.2 -- [[pstore-has-never-worked-on-7-2]] |
| `dmesg` / journal | die with the reset |
| watchdog pretimeout panic | `gov=panic pretimeout=10` is armed and still reaches no transport |

**And the disarm recipe that would have rescued it does not exist.**
`/sys/class/watchdog/watchdog0/timeout` is `-r--r--r--`:

```
/tmp/ph-prep.sh: line 10: can't create /sys/class/watchdog/watchdog0/timeout: Permission denied
```

Root does not help. The watchdog core exposes `timeout` read-only and settles it
only through the `WDIOC_SETTIMEOUT` ioctl, and systemd already owns the device
at `RuntimeWatchdogUSec=30s`. Changing it means `RuntimeWatchdogSec=` plus a
re-exec of PID 1, which is itself a known way to freeze this phone. A run that
believed it had disarmed the watchdog was really just another armed run, and
its prep-hook error was the only tell.

**What to do instead** — stop buying a witness and make the oracle binary.
`ph-suspend-cycle.sh` fsyncs every line *before* the suspend write, so
`btime - SUSPEND_ENTER` survives, and a reset is a new `boot_id`. Then bisect by
toggle: one variable per cycle, interleaved, and count resets. That is how the
armed-tap hang was pinned in under an hour with no kernel log at all --
[[a-tap-on-an-armed-suspended-screen-hangs-the-resume]].

`pm_test` is the one instrument that does keep a log, because the machine never
goes down -- but it cannot reach the real sleep state, and not even all of its
own rungs exist here:
[[pm-test-cannot-go-above-platform-on-s2idle]].
