# porthole

**A device bring-up toolkit for postmarketOS, plus the knowledge it encodes.**

Porting a phone to mainline Linux is mostly not writing drivers. It is moving a
device between states without bricking it, proving which kernel actually
answered, and not spending a night on an experiment that never ran.

porthole is the tooling and the accumulated traps from doing that on a Google
Pixel 2 XL, made generic — so the next device starts from month three instead of
day one.

```
114 tools · 50 knowledge notes · 18 commands · zero third-party dependencies
```

Docs: `make docs-serve`, or set the `PUBLISH_DOCS` repository
variable to publish them to GitHub Pages.

---

## Table of contents

- [Requirements](#requirements) — and what happens if you're missing things
- [Install](#install)
- [First run](#first-run)
- [The one-time device setup everyone hits](#the-one-time-device-setup-everyone-hits)
- [Daily use](#daily-use)
- [Porting a new device](#porting-a-new-device)
- [Configuration](#configuration)
- [For agents and LLMs](#for-agents-and-llms)
- [Speed](#speed)
- [Repository layout](#repository-layout)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)

---

## Requirements

**porthole has no third-party dependencies.** No pip install, no npm, no
virtualenv. If you have Python 3.8+ and OpenSSH you can already run most of it.

| | needed for | if missing |
|---|---|---|
| **python3 ≥ 3.8** | the CLI and half the tools | nothing works — install it first |
| **openssh client** | every command that talks to a booted device | device tools fail; host-only tools still work |
| **fastboot** | reaching and leaving the bootloader | flashing and recovery unavailable |
| **flock** *(util-linux)* | serialising parallel workers on one device | the mutex cannot serialise; fine if you work alone |
| adb | talking to a stock or recovery system | optional, rarely needed |
| pmbootstrap | building and flashing images | you can still probe and debug a running device |
| shellcheck | `make lint` when contributing | optional |

**You do not need all of it to start.** Run `porthole next      # where am I, and what is next?
porthole doctor` and it will tell
you exactly what is missing, what each thing is for, and the install command
**for your distribution** — it reads `/etc/os-release` and adjusts.

```console
$ porthole doctor
    ok  host: python          3.11.9 at /usr/bin/python3
    ok  host: ssh             /usr/bin/ssh
  FAIL  host: fastboot        not found -- the only reliable way to reach the bootloader
        fix: sudo apt install android-sdk-platform-tools
  warn  host: pmbootstrap     not found -- needed to build and flash, not to probe
        see: pipx install pmbootstrap
```

Only `FAIL` lines are fatal. Warnings are things you can work without.

### Getting the essentials

<details>
<summary><strong>Debian / Ubuntu / Mint / Pop!_OS</strong></summary>

```sh
sudo apt install python3 openssh-client android-sdk-platform-tools util-linux
pipx install pmbootstrap        # optional: only to build images
```
</details>

<details>
<summary><strong>Arch / Manjaro</strong></summary>

```sh
sudo pacman -S python openssh android-tools util-linux
pipx install pmbootstrap
```
</details>

<details>
<summary><strong>Fedora / RHEL</strong></summary>

```sh
sudo dnf install python3 openssh-clients android-tools util-linux
pipx install pmbootstrap
```
</details>

<details>
<summary><strong>Alpine / postmarketOS</strong></summary>

```sh
sudo apk add python3 openssh-client android-tools util-linux
```
</details>

<details>
<summary><strong>macOS</strong></summary>

```sh
brew install python android-platform-tools flock
```
ssh is preinstalled. pmbootstrap needs Linux, so builds happen elsewhere;
probing and debugging a running device work fine.
</details>

### USB access without root

On Linux, fastboot needs a udev rule or it only works under `sudo`:

```sh
sudo tee /etc/udev/rules.d/51-android.rules >/dev/null <<'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="18d1", MODE="0666", GROUP="plugdev"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG plugdev "$USER"     # log out and back in
```

`18d1` is Google. Substitute your vendor's ID — `lsusb` while the device is in
the bootloader will show it.

---

## Install

There is nothing to build. Clone it and put `bin/` on your `PATH`:

```sh
git clone https://github.com/<you>/porthole.git
cd porthole
export PATH="$PWD/bin:$PATH"          # add to ~/.bashrc or ~/.zshrc to persist
```

Optional, but pleasant:

```sh
porthole completion bash > ~/.local/share/bash-completion/completions/porthole
porthole completion zsh  > "${fpath[1]}/_porthole"
porthole completion fish > ~/.config/fish/completions/porthole.fish
```

Verify:

```sh
porthole version
```

---

## First run

```console
$ porthole
porthole 0.1.0

  · device    none selected
  · identity  not configured
    profiles  1 (google-taimen)

  → porthole init <codename>   set up, once

`porthole --help` for all verbs.
```

Then:

```sh
porthole init                     # interactive, or:
porthole init google-taimen --user myname --host 172.16.42.1
porthole doctor
```

`init` writes `~/.config/porthole/config.env`. That file is **yours** — your
username, your device's address, your tool paths — and is never committed.

---

## The one-time device setup everyone hits

This is the single most common "all the tools are broken" report, so it gets its
own section.

Nearly every tool calls `sudo -n` on the device. The `-n` means *never prompt* —
so without passwordless sudo it does not ask for a password, it just **fails
silently**. Every tool appears to do nothing, with no error anywhere.

**On the device**, once per install:

```sh
echo "$USER ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/99-porthole-dev
sudo chmod 0440 /etc/sudoers.d/99-porthole-dev
```

`porthole init` prints this with your username filled in, and `porthole doctor`
detects when it is missing.

> **This must not ship in a device package other people install.** It belongs on
> your development device only. That is precisely why porthole prints it instead
> of applying it — and why an agent should hand it to you rather than retrying.

---

## Daily use

```sh
porthole brief          # where am I, what state is the device in, what now
porthole doctor         # is everything working
porthole tools          # what can I run
porthole brain <query>  # what do we already know about this
```

### Finding a tool

```console
$ porthole tools --needs BOOTED
  tk-fps.py            Measure real frame delivery on the device [BOOTED]
  tk-suspend-cycle.sh  One real s2idle cycle, with evidence [BOOTED]
  ...

$ porthole tools --grep suspend
$ porthole tools tk-suspend-cycle.sh          # read its contract
```

Every tool documents itself in its first lines, so `head -20 <tool>` also works:

```
# scope: generic
# needs: BOOTED
# env:   PHONE, TK_ALARM, TK_HOST
# exits: 0 ok · non-zero on failure
```

### Talking to the device safely

There is one physical device and possibly several of you (or several agents).
Everything that touches it goes through the mutex, **declaring the state it
needs**:

```sh
TK_AGENT=$USER tools/tk-device.sh --need-booted ssh "$PHONE" 'uname -a'
```

- exit **75** — could not get the lock. Someone else has it. **Retry.**
- exit **76** — device is in the wrong state. **Do not retry**; something has to
  physically move it first.

That distinction exists because an agent once queued ten minutes for a phone
another agent had left in the bootloader, then died at its own timeout having
done nothing.

---

## Porting a new device

```sh
porthole new-device oneplus-enchilada
```

This scaffolds `profiles/oneplus-enchilada/` with:

- **`device.env`** — every key documented, seeded with whatever can honestly be
  determined from `fastboot getvar all` and any existing pmaports `deviceinfo`.
  Everything else is left blank, because *a blank you can see is a question you
  know to ask.*
- **`checklist.md`** — the bring-up order, each item linked to the playbook that
  explains it.
- **`tools/`** — where probes specific to your device go.

It deliberately does **not** invent a `deviceinfo`, defconfig or DTS. A
confidently wrong one costs more than a blank: you end up debugging the device
instead of the file.

Then:

```sh
porthole init oneplus-enchilada
porthole brain search --severity law     # ten notes. Read them before you start.
cat profiles/oneplus-enchilada/checklist.md
```

**If the framework gets something wrong for your device — a key that does not
fit, an assumption that does not hold — that is the most valuable bug report
this project can receive.** It has only ever been proven against one device.

---

## Device trees

```sh
porthole dts sources              where the real values come from
porthole dts labels               what the SoC dtsi already defines for you
porthole dts new                  scaffold, inheriting the board-family dtsi
porthole dts compare <sibling>    what they configure that you have not
porthole dts check                does it compile
```

A device tree is layered: the SoC dtsi is written, a board-family dtsi often
covers most of the rest, and your `.dts` is a few hundred lines describing the
board. `compare` follows `#include` chains, so it does not report inherited
nodes as gaps.

Background: `porthole brain 25-device-tree`.

## Working on pmaports

```sh
porthole aports status            branch, what changed, which of it is yours
porthole aports start <topic>     a feature branch off the right base
porthole aports diff --mine       just your device's packages
porthole aports patch             a series, with a pre-submission lint
porthole channel                  see and switch release channel
porthole ui                       see and switch compositor
```

## A serial console

```sh
porthole serial hardware   what to buy, how to wire it, how to enable earlycon
porthole serial list       what is attached
porthole serial console    attach (terminal built in — no picocom needed)
```

ssh needs userspace and the USB gadget needs driver probe. A UART needs
neither: it is the only channel that talks during early boot, and the only one
that says anything when a kernel dies before console handover.

## Configuration

Two files, two questions.

| question | file | committed? |
|---|---|---|
| who am I, where is my tooling | `~/.config/porthole/config.env` | **no** |
| which device is this | `profiles/<codename>/device.env` | yes |

Resolution runs lowest to highest:

```
built-in defaults → profiles/<device>/device.env → ~/.config/porthole/config.env
                  → ./.env → the process environment
```

The process environment always wins, so a one-off override just works:

```sh
PHONE=other@host tools/tk-fps.py
```

To see what resolved and why:

```console
$ porthole config PHONE
user@172.16.42.1

$ porthole config --source profile
PORTHOLE_ACTIVE_SLOT  b     # profile
PORTHOLE_SOC          msm8998   # profile
...
```

Full key reference: **[`docs/CONFIG.md`](docs/CONFIG.md)**.

### Device facts are data, not folklore

The profile is where hard-won knowledge stops being tribal:

```sh
PORTHOLE_SLOT_FORBIDDEN="a"      # no known-good image — porthole refuses to arm it
PORTHOLE_BOOT_RETRIES="3"        # the every-3rd-boot drop is a countdown, not a glitch
PORTHOLE_WATCHDOG_MAX_S="30"     # above this the timeout DISARMS the watchdog
PORTHOLE_USB_LIES_AS_FASTBOOT=1  # lsusb mislabels the running gadget
```

Each of those cost someone a session. Now they are one `porthole config` away,
and `porthole brief` reads them back to you as sentences.

---

## Running pmbootstrap safely

pmbootstrap needs root. The usual workaround —
`Defaults:you timestamp_timeout=9999` — is a **167-hour root credential cache**:
for a week, every process running as you gets silent, unlimited root. That is
not something to hand an agent.

```sh
porthole sandbox status     # what is configured, what the gaps are
porthole sandbox install    # writes a script; read it, then run it
porthole sandbox shell      # rootless container: root maps to YOUR uid
porthole sandbox audit --denied
```

Two tiers: a **broker** that validates every root request pmbootstrap makes
against an allowlist derived empirically (11 verbs, all paths confined to
declared roots, every decision audited), and a **rootless container** where
container-root maps to your own unprivileged uid.

The threat model — including what each tier honestly does *not* stop — is in
**[`docs/SANDBOX.md`](docs/SANDBOX.md)**. Read it before trusting either.

## For agents and LLMs

**[`AGENTS.md`](AGENTS.md)** is the front door, written for any LLM rather than
one vendor's format.

The single command to run first:

```sh
porthole brief --json
```

It returns the device and its state, this device's encoded traps as prose, the
rules that cost sessions when broken, the tool catalogue pointer, the laws, and
suggested next steps. That replaces four or five exploratory commands, each of
which can be got wrong.

Everything else an agent needs is machine-readable:

```sh
porthole tools --json          # the full tool catalogue with contracts
porthole config --json         # every resolved value and its source
porthole doctor --all --json   # health, with a fix for every failure
porthole brain --json --severity law
```

Design rules that make this work:

- **Exit codes are an API.** `0` ok · `1` the thing under test failed · `64`
  usage · `75` lock unavailable (retry) · `76` wrong device state (do not retry)
  · `124` killed at the hold ceiling. An agent that cannot tell "the tool broke"
  from "the answer is no" reports broken tools as findings.
- **Never interactive unless stdin is a tty**, so headless bootstrap works.
- **No colour when piped**, and `NO_COLOR` is honoured.
- **Every tool is self-describing** in its first 20 lines.

There is also a Claude skill at [`skills/porthole-bringup/`](skills/porthole-bringup/).

---

## Speed

These tools run in tight loops — a 20-cycle suspend test, a soak run, an agent
polling device state. Latency is a correctness concern, not a nicety.

Config resolution costs under a millisecond. Everything else is a network round
trip, which is why the shared ssh options carry connection multiplexing.
**Measured on a Pixel 2 XL over USB:**

| | per ssh round trip |
|---|---|
| without multiplexing | **302 ms** |
| with multiplexing | **14 ms** |

All 115 tools inherit that from one place. It is only safe because every reboot
path tears the control master down first — host keys change on essentially every
boot here, so a socket that outlives a reboot is a live handle to a dead sshd.

Measure it yourself rather than trusting this table:

```sh
porthole doctor --bench
PORTHOLE_TIMING=1 tools/tk-fps.py      # per-probe timings to stderr
PORTHOLE_NO_MUX=1 ...                  # disable multiplexing (first thing to try
                                       # when diagnosing a strange hang)
```

Details: **[`docs/PERFORMANCE.md`](docs/PERFORMANCE.md)**.

---

## Repository layout

```
bin/porthole            the CLI — a thin launcher
lib/
  porthole.py           config resolution + device transport (python tools)
  porthole.sh           the same semantics for shell tools
  porthole_cli.py       command registry and output helpers
  porthole_cmd_*.py     one module per verb — drop one in to add a verb
tools/                  ~95 tk-* tools: boot, flash, probe, benchmark, soak
profiles/
  _template/            every device key, documented
  google-taimen/        the reference device: facts as data + its own tools
brain/                  47 scoped knowledge notes
docs/                   architecture, config, performance, contributing
tests/                  everything runs with no device attached
```

---

## Troubleshooting

**"Every tool does nothing."**
Passwordless sudo on the device. See
[the one-time setup](#the-one-time-device-setup-everyone-hits), or run
`porthole doctor`.

**A tool hangs where it used to work.**
Try `PORTHOLE_NO_MUX=1 <tool>`. If that fixes it, a stale ssh control master is
the cause — `rm -rf .run/` clears them, and please open an issue, because a
reboot path is failing to tear the master down.

**`porthole: no profile for device 'x'`**
`porthole devices` lists what exists; `porthole new-device x` creates one. This
is deliberately fatal rather than silently resolving to no device facts — that
is how you flash the wrong DTB.

**The device is unreachable but powered.**
It has four states, not two. `porthole brief` tells you which:
`BOOTED` · `FROZEN` (kernel alive, userspace gone) · `FASTBOOT` · `ABSENT`.
See `porthole brain frozen-is-not-hung`.

**Boot verdicts seem wrong.**
Never judge a boot by the screen — mainline often never lights the panel, so a
booted device and a hung one look identical. `porthole brain
never-judge-a-boot-by-the-screen`.

---

## Contributing

See **[`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md)**.

```sh
make test        # everything, no device needed
make lint        # shellcheck + python syntax
make check       # both
```

Adding a tool, a device, a CLI verb or a brain note each takes one file. The
tests enforce the contracts so that stays true.

## Status

Proven against one device (`google-taimen`, MSM8998). That is stated as a
coverage limit rather than papered over: tools are scoped honestly, and a probe
encoding a vendor protocol lives in that device's profile rather than pretending
to be portable.

Second devices very welcome.

## Licence

MIT — see [`LICENSE`](LICENSE).
