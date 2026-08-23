# porthole

A device bring-up toolkit for postmarketOS, plus the knowledge it encodes.

Porting a phone to mainline Linux is mostly not writing drivers. It is moving a
device between states without bricking it, proving which kernel actually
answered, and not spending a night on an experiment that never ran. porthole is
the tooling and the accumulated traps from doing that on a Google Pixel 2 XL —
made generic, so the next device starts from month three instead of day one.

```sh
git clone <this repo> porthole && cd porthole
./bin/porthole devices                  # what profiles exist
./bin/porthole init --device <codename> # your identity, once
./bin/porthole doctor                   # what is missing, and how to fix it
```

`porthole doctor` is the honest starting point. It checks your host tools, your
profile, whether the device answers, and whether passwordless sudo is set up —
and names the fix for anything that fails, rather than letting a hundred scripts
fail obscurely.

## Porting a device nobody has ported

```sh
./bin/porthole new-device <codename>    # scaffolds a profile + a checklist
./bin/porthole brain --severity law     # read these first. Ten notes.
```

`new-device` seeds what can honestly be known — from `fastboot getvar all` and
any existing pmaports device package — and marks everything else as a question
you now know to ask. It does not invent a `deviceinfo`: a confidently wrong one
costs more than a blank, because you end up debugging the device instead of the
file.

Then `profiles/<codename>/checklist.md` is the order to work in, and each item
links to the playbook that explains it.

## What is here

| | |
|---|---|
| `bin/porthole` | the CLI — `init`, `doctor`, `config`, `devices`, `new-device`, `brain`, `run` |
| `lib/porthole.sh`, `lib/porthole.py` | config resolution + device transport, for the shell and python halves of the toolbox |
| `tools/` | ~95 `tk-*` tools: boot, flash, probe, benchmark, soak |
| `profiles/<codename>/` | device facts as data, and device-specific probes |
| `brain/` | the second brain — 47 scoped notes |
| `docs/` | architecture, config reference, performance budgets, contributing |

## Configuration

Two files, two questions. *Who am I and where is my tooling* goes in
`~/.config/porthole/config.env` and is never committed. *Which device is this*
goes in `profiles/<codename>/device.env` and is.

Resolution runs lowest to highest: built-in defaults → the device profile → your
`config.env` → a per-checkout `.env` → the process environment. So a one-off

```sh
PHONE=other@host tools/tk-fps.py
```

still overrides everything, which is how these tools have always been used and
will keep working. Full key reference in [`docs/CONFIG.md`](docs/CONFIG.md).

## Speed

The tools run in tight loops — a 20-cycle suspend test, a soak run, an agent
polling device state — so latency is a correctness concern, not a nicety.
Config resolution costs under a millisecond. Everything else is a network round
trip, which is why the shared ssh options carry connection multiplexing: a warm
round trip is ~15 ms against ~200 ms for a fresh handshake, and all ~95 tools
inherit it from one place.

That is only safe because every reboot path tears the control master down first
— host keys change on essentially every boot here, so a socket that outlives a
reboot is a live handle to a dead sshd. Budgets and how to measure them:
[`docs/PERFORMANCE.md`](docs/PERFORMANCE.md).

```sh
./bin/porthole doctor --bench       # measured, not claimed
```

## For agents

[`AGENTS.md`](AGENTS.md) is the front door, and it is written for any LLM rather
than one harness. The short version: run `porthole doctor`, read
`brain/workflow/agent-protocol.md`, and read `brain/laws/` before reporting any
result.

Every tool carries a header naming its scope, the device state it needs, the
environment it reads and its exit codes — so `head -20 <tool>` answers those
questions without opening the file.

## Status

The framework is proven against one device (`google-taimen`, MSM8998). That is
stated as the coverage limit rather than papered over: tools are scoped
honestly, and a probe that encodes a vendor protocol lives in that device's
profile rather than pretending to be portable.

Second devices very welcome. `porthole new-device` exists precisely to find out
what the framework got wrong.

## Licence

See [`LICENSE`](LICENSE).
