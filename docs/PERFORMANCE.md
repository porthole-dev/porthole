# Performance

The tools run in tight loops — a 20-cycle suspend test, a soak run, an agent
polling device state. Latency here is a correctness concern, not a nicety: a
probe that costs a second turns a 200-iteration sweep into four minutes of
waiting.

## Where the time actually goes

Configuration is free. Sourcing three small env files is under a millisecond;
python3 starts in ~20 ms cold and ~0 ms warm. **Every other cost is a network
round trip.**

Which is why the CLI is deliberately not on any tool's hot path. The libs parse
the config themselves; shelling out to `porthole config` per probe would cost an
interpreter start per iteration.

## Connection multiplexing

Before porthole, the toolbox opened a fresh ssh connection for every probe —
there was no `ControlMaster` anywhere in it. Each `boot_id` read, each `uptime`,
each one-shot remote command paid a full handshake.

**Measured on the reference device** (Pixel 2 XL, USB gadget, five consecutive
round trips after warming):

| | per ssh round trip |
|---|---|
| `PORTHOLE_NO_MUX=1` (how the toolbox worked before) | **302 ms** |
| multiplexed | **14 ms** |

Reproduce it yourself:

```sh
PORTHOLE_NO_MUX=1 bash -c '. tools/tk-lib.sh; ssh "${TK_SSH_OPTS[@]}" "$PHONE" true
  s=$(date +%s%3N); for i in 1 2 3 4 5; do ssh "${TK_SSH_OPTS[@]}" "$PHONE" true; done
  e=$(date +%s%3N); echo "$(( (e-s)/5 ))ms each"'
```

Multiplexing lives in the shared `TK_SSH_OPTS`, so all ~117 tools inherit it from
one edit:

```
-o ControlMaster=auto
-o ControlPath=$PORTHOLE_RUNDIR/ssh-%C
-o ControlPersist=60s
```

**The correctness price, which must be paid or the optimisation becomes a
hang.** Host keys change on essentially every boot here, so a master socket that
outlives a reboot is a live handle to a dead sshd: the next command inherits the
dead channel and hangs until `ControlPersist` expires instead of failing fast.

So every reboot path calls `ph_ssh_mux_reset` (`ssh -O exit`) first —
`tk_request_reboot`, `tk_request_bootloader`, `tk_rearm_and_boot`, and
`tk_wait_ssh` the moment it sees a changed `boot_id`. Three tests in
`tests/test_shell_lib.sh` assert that wiring is still there.

`%C` hashes host, port and user, so two profiles or two developers on one
machine never share a socket. `PORTHOLE_NO_MUX=1` disables the lot, and is the
first thing to try when diagnosing a strange hang.

## The verbs

A verb that needs no device and no container must not wait on the network.
The test `tests/test_cli.py::test_the_host_only_verbs_stay_under_their_budget`
verifies `--help`, `version`, `devices` and `doctor --no-device` using a
**relative budget** measured against an unrouteable device address:

- **Relative:** each verb must complete within **3 s of a control** (`porthole version`),
  which touches nothing and measures pure interpreter startup plus scheduling jitter.
  A network dial adds a fixed ~5 s that load does not, so a relative delta immune
  to runner load cleanly separates normal operation (~0.6–0.8 s) from a hung probe (~5.5 s).
- **Absolute:** each verb must complete within **8 s** regardless. This covers the
  one case the relative budget misses: if the control itself ever dials the device,
  both rise together and the delta would hide it.

Measured 2026-09-07 on the reference host, before and after the fix:

| | before | after |
|---|---|---|
| `porthole doctor --no-device` | 5.5 s | 0.72–0.91 s |
| `porthole doctor` (with `PORTHOLE_DEVICE_STATE=absent`) | 7.57 s | 2.5 s |

Both figures were one call: `_container_state` asked the phone whether it
accepts the device key, over ssh, with `ConnectTimeout=5`, on a host whose
device was unplugged — including under `--no-device`, whose help says it
skips anything that touches the device.

## Budgets

| operation | budget |
|---|---|
| lib source / import | < 5 ms |
| `porthole config` (cold python) | < 60 ms |
| ssh round trip, warm master | < 30 ms |
| ssh round trip, cold | < 350 ms |
| `tk_device_state`, healthy booted device, warm | < 100 ms |
| `tk_in_fastboot` | < 250 ms (bounded by `fastboot devices`) |

Last measured on the reference device: warm round trip 18 ms, `fastboot devices`
2 ms, `tk_device_state` 18 ms — all inside budget.

Measure rather than claim:

```sh
porthole doctor --bench      # runs the set, prints measured vs budget
PORTHOLE_TIMING=1 <any tool> # per-probe timings to stderr
```

## What is deliberately not optimised

**The mutex double-probe.** `tk-device.sh` checks device state before queueing
and again after taking the lock, because the previous holder may have moved the
device while you waited. That second probe is correctness, not waste.

**Parallelising the state probes.** `tk_device_state` asks fastboot, then ssh,
then ping, in that order. The order encodes hardware semantics — a device in the
bootloader has no USB network at all — and running them concurrently would save
~200 ms and cost the invariant.

**`tk_boot_id`'s retry.** Two attempts at a 12-second timeout looks slow and is
load-bearing: it exists because a single timed-out read returns empty, empty
compares unequal to every real boot id, and a baseline taken through one
transient failure reports a reboot that never happened. The timeout must sit
*above* the known sshd stall or retrying buys nothing. Speed must not
reintroduce that; see `brain/laws/empty-must-mean-unknown-never-changed.md`.

**Caching resolved config to disk.** Parsing takes under a millisecond. A cache
would add a staleness bug to buy nothing.
