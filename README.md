# porthole

**A device bring-up toolkit for postmarketOS, plus the knowledge it encodes.**

Porting a phone to mainline Linux is mostly not writing drivers. It is moving a
device between states without bricking it, proving which kernel actually
answered, and not spending a night on an experiment that never ran.

porthole is the tooling and the accumulated traps from doing that on a Google
Pixel 2 XL, made generic — so the next device starts from month three instead of
day one.

```
117 tools · 96 knowledge notes · 30 commands · zero third-party dependencies in the CLI
```

> **Work in progress.** This is under active development against real hardware,
> and it has rough edges — verbs that do not yet cover every case, tools proven
> on one device and not the next, bugs still being found in ordinary use. It is
> used daily for real bring-up work, which is exactly why the sharp bits are
> documented rather than hidden: `brain/` is largely a record of what went
> wrong. Expect breaking changes before 1.0, read what a command says it will
> do before passing `--yes`, and please report anything that bites you.

Every push to `main` publishes the documentation to GitHub Pages; `make
docs-serve` renders the same site locally.

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

**porthole's CLI has no third-party dependencies.** No pip install, no npm, no
virtualenv — every verb starts on a bare Python 3.8. What some of them then need to
finish the job (fastboot to reach a bootloader, pmbootstrap to build) is in the table
below, and `porthole doctor` names the install command for your distribution.

The **console** (`porthole tui`) is the one optional extra: it needs Python
3.10+ and `textual`. Nothing else does, and nothing else ever will — a host
that is already broken is exactly where `porthole next` has to keep working
with nothing installed.

| | needed for | if missing |
|---|---|---|
| **python3 ≥ 3.8** | the CLI and half the tools | nothing works — install it first |
| **openssh client** | every command that talks to a booted device | device tools fail; host-only tools still work |
| **fastboot** | reaching and leaving the bootloader | flashing and recovery unavailable |
| **flock** *(util-linux)* | serialising parallel workers on one device | the mutex cannot serialise; fine if you work alone |
| **textual** *(optional)* | `porthole tui`, the full-screen console | the console says so and names the install command for your distro; every verb still works |
| adb | talking to a stock or recovery system | optional, rarely needed |
| pmbootstrap | building and flashing images | you can still probe and debug a running device |
| shellcheck | `make lint` when contributing | optional |

**You do not need all of it to start.** Run `porthole doctor` and it will tell
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
git clone https://github.com/porthole-dev/porthole.git
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

  → porthole init <codename>   set this host up

`porthole --help` for all verbs.
```

Then:

```sh
porthole init                     # interactive, or:
porthole init google-taimen --user myname --host 172.16.42.1
porthole doctor
```

`init` sets this host up and is **safe to re-run**: it reads what is already
there, offers it back as the default for every question, and rewrites only the
lines you change. Comments and keys it does not know about are left alone, so
running it on a half-configured machine converges rather than clobbers.

It writes `~/.config/porthole/config.env`. That file is **yours** — your
username, your device's address, your tool paths — and is never committed.

It also asks the one question that decides what this machine needs at all:
**where builds run.** In the workspace (the default) the container image
carries pmbootstrap and its helpers pinned to each other, so *you do not
install pmbootstrap here*. On the host tier, `init` finds or clones the
checkout itself.

Full walkthrough: **[`docs/NEW-HOST.md`](docs/NEW-HOST.md)**.

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
porthole sandbox up                          # build the image, start the workspace
porthole sandbox shell --command <command>   # one command; works with no TTY
porthole sandbox shell                       # a shell, for a human
porthole sandbox status                      # what is up, what is missing
```

So don't have root at all. Builds run in a **persistent rootless container**
where you are root inside — pmbootstrap therefore uses no sudo whatsoever — and
your own unprivileged uid outside. **The default install grants no sudoers
entry and no standing privilege**, so there is nothing for a mistake, a
dependency or an injection to spend. Only what it mounts is reachable; your
`~/.ssh`, `/etc` and home directory are not there.

The workspace keeps its **own pmbootstrap work directory**
(`~/.local/var/porthole-sandbox`), which it creates and owns — a rootless
container maps your uid and nothing else, so it cannot use one that host root
built, and cannot be given one either. `porthole sandbox up` configures it and
the chroots bootstrap on first build. Your host work dir is left alone;
`porthole build --host` still uses it.

There is no second, weaker path. A validating privilege broker (`ph-sudo`)
used to exist for a host without podman; it is gone. It granted a real sudoers
entry, its own documentation admitted it could not contain a determined chroot
payload, and keeping it meant every reader had to work out which tier they were
on. Podman is the one host prerequisite, and `porthole doctor` says how to
install it.

The threat model — including what this honestly does *not* stop — is in
**[`docs/SANDBOX.md`](docs/SANDBOX.md)**. Read it before trusting it.

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
  porthole_tui/         the console (`porthole tui`) — needs python 3.10+
                        and textual; gate.py itself runs on 3.8 to say so
tools/                  ~95 tk-* tools: boot, flash, probe, benchmark, soak
profiles/
  _template/            every device key, documented
  google-taimen/        the reference device: facts as data + its own tools
brain/                  55 scoped knowledge notes
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
`BOOTED` · `INITRAMFS` (stopped in the pmOS debug shell -- `tools/tsh.py` will say why) · `FROZEN` (kernel alive, userspace gone) · `FASTBOOT` · `ABSENT`.
See `porthole brain frozen-is-not-hung`.

**Boot verdicts seem wrong.**
Never judge a boot by the screen — mainline often never lights the panel, so a
booted device and a hung one look identical. `porthole brain
never-judge-a-boot-by-the-screen`.

---

## Contributing

See **[`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md)**.

```sh
make test        # the test suite, no device needed
make lint        # shellcheck + python syntax
make check       # both
make ci          # every job GitHub runs -- green here is green there
```

Adding a tool, a device, a CLI verb or a brain note each takes one file. The
tests enforce the contracts so that stays true.

## Status

Proven against one device (`google-taimen`, MSM8998), with a second
(`google-cheetah`, GS201) in progress. That is stated as a coverage limit rather
than papered over: every tool declares its scope, and a probe encoding a vendor
protocol lives in that device's profile rather than pretending to be portable.

**The most useful thing you can do is bring a second device.** Not because the
tools need testing — because the line between "this is how phones work" and
"this is how *this* phone works" is only visible from two devices, and every
note in `brain/` that is wrongly marked `scope: generic` is a trap waiting for
the next person. `porthole new-device <codename>` scaffolds a profile in one
command.

## Acknowledgements

The original Pixel 2 XL mainline work this port builds on is by **Caleb
Connolly**, **Yassine Oudjana**, **Joel Selvaraj**, **Jami Kettunen**,
**AngeloGioacchino Del Regno** and **Konrad Dybcio**. postmarketOS and
pmbootstrap are the ground everything here stands on.

Much of this codebase was written with **Claude** (Anthropic) as a pair
programmer, over sessions that also produced most of `brain/` — the traps in
there are the record of what went wrong while doing it. The commit log is kept
free of assistant trailers deliberately: the work is the author's, the mistakes
are the author's, and a log full of tool attribution helps nobody reading it in
two years.

## Licence

MIT — see [`LICENSE`](LICENSE).
