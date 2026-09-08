# porthole

**A device bring-up toolkit for postmarketOS, plus the knowledge it encodes.**

Porting a phone to mainline Linux is mostly not writing drivers. It is moving a
device between states without bricking it, proving which kernel actually
answered, and not spending a night on an experiment that never ran.

porthole is the tooling and the accumulated traps from doing that on a Google
Pixel 2 XL, made generic — so the next device starts from month three instead of
day one.

```
150 tools · 184 knowledge notes · 35 commands · zero third-party dependencies in the CLI
```

> **Work in progress.** This is under active development against real hardware,
> and it has rough edges — verbs that do not yet cover every case, tools proven
> on one device and not the next, bugs still being found in ordinary use. It is
> used daily for real bring-up work, which is exactly why the sharp bits are
> documented rather than hidden: `brain/` is largely a record of what went
> wrong. Expect breaking changes before 1.0, read what a command says it will
> do before passing `--yes`, and please report anything that bites you.

---

## Start here

Three steps, and only the third is specific to you: install porthole, tell it
who you are, then give your device passwordless sudo. `porthole doctor` checks
all of it and names the fix for anything missing.

### Install

There is nothing to build. Clone it and put `bin/` on your `PATH`:

```sh
git clone https://github.com/porthole-dev/porthole.git
cd porthole
export PATH="$PWD/bin:$PATH"          # add to ~/.bashrc or ~/.zshrc to persist
porthole version
```

Shell completion is optional but pleasant:

```sh
porthole completion bash > ~/.local/share/bash-completion/completions/porthole
porthole completion zsh  > "${fpath[1]}/_porthole"
porthole completion fish > ~/.config/fish/completions/porthole.fish
```

### Set this host up

```sh
porthole init                     # interactive, or:
porthole init google-taimen --user myname --host 172.16.42.1
porthole doctor
```

`init` is **safe to re-run**. It reads what is already there, offers it back as
the default for every question, and rewrites only the lines you change —
comments and keys it does not know about are left alone, so a half-configured
machine converges rather than gets clobbered. It writes
`~/.config/porthole/config.env`, which is **yours** and is never committed.

It asks five things, and each one is a decision you would otherwise make by
losing an afternoon to it:

- **who you are on the device** — the ssh login every tool uses.
- **how to reach it** — over the USB gadget (`172.16.42.1`, the same on every
  postmarketOS device and up before wifi is configured) or over wifi. `init`
  explains both, checks whether the gadget is enumerated on this host right
  now, and pings whatever you answer.
- **where builds run** — the workspace, or this host. In the workspace, the
  default, the container image carries pmbootstrap and its helpers pinned to
  each other, so *you do not install pmbootstrap on this host*. On the host
  tier `init` finds or clones the checkout itself.
- **pmaports** — adopt what is already here, point at a checkout, or clone one.
- **the working repo for this device** — your notes, your logs, and the kernel
  tree if you build one. `porthole build`, `verify`, `dts` and half of
  `porthole next` cannot answer anything without it, and it is the key nothing
  used to ask for.

Then you can build. **You do not need a kernel tree to build a system image**:

```sh
porthole sandbox up                        # start the workspace, once
porthole build                             # the rung ladder — what each one costs
porthole build image --yes                 # the whole OS from pmaports, no tree needed
porthole flash full --yes --replace-rootfs # rootfs and boot
```

Every rung except `image` compiles a kernel tree. `porthole build` says which
rungs it can run here, where the build will go (workspace or host) and what is
missing, before it starts.

Full walkthrough, including moving to a second machine:
**[`docs/NEW-HOST.md`](docs/NEW-HOST.md)**.

### The one device step everyone hits

This is the single most common "all the tools are broken" report.

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

### What you need

**porthole's CLI has no third-party dependencies.** No pip install, no npm, no
virtualenv — every verb starts on a bare Python 3.8. What some of them then need
to finish the job is below, and **you do not need all of it to start**.

| | needed for | if missing |
|---|---|---|
| **python3 ≥ 3.8** | the CLI and half the tools | nothing works — install it first |
| **openssh client** | every command that talks to a booted device | device tools fail; host-only tools still work |
| **fastboot** | reaching and leaving the bootloader | flashing and recovery unavailable |
| **podman** | building images in the rootless workspace | builds unavailable; probing and debugging still work |
| **flock** *(util-linux)* | serialising parallel workers on one device | the mutex cannot serialise; fine if you work alone |
| **textual** *(optional)* | `porthole tui`, the full-screen console | the console says so; every verb still works |
| adb | talking to a stock or recovery system | optional, rarely needed |

`porthole doctor` reads `/etc/os-release` and prints the install command **for
your distribution**, so you never have to match this table to a package name
yourself. Only `FAIL` lines are fatal; warnings are things you can work without.

```console
$ porthole doctor
    ok  host: python          3.11.9 at /usr/bin/python3
  FAIL  host: fastboot        not found -- the only reliable way to reach the bootloader
        fix: sudo apt install android-sdk-platform-tools
    ok  host: pmaports        ~/.local/var/pmbootstrap/cache_git/pmaports  (via PORTHOLE_PMB_DIR/cache_git)
```

The console (`porthole tui`) is the one optional extra: it needs Python 3.10+
and `textual`. Nothing else does, and nothing else ever will — a host that is
already broken is exactly where `porthole next` has to keep working with nothing
installed.

Package names per distribution, and the udev rule that lets fastboot work
without `sudo`, are in **[`docs/NEW-HOST.md`](docs/NEW-HOST.md)**.

---
## Daily use

Four commands answer almost everything:

```sh
porthole brief          # where am I, what state is the device in, what now
porthole doctor         # is everything working
porthole tools          # what can I run
porthole brain <query>  # what do we already know about this
```

### Finding a tool

Tools are searchable by what they need and what they do, so you never have to
remember a filename:

```console
$ porthole tools --needs BOOTED
  ph-fps.py            Measure real frame delivery on the device [BOOTED]
  ph-suspend-cycle.sh  One real s2idle cycle, with evidence [BOOTED]

$ porthole tools --grep suspend
$ porthole tools ph-suspend-cycle.sh          # read its contract
```

Every tool documents itself in its first lines, so `head -20 <tool>` works too:

```
# scope: generic
# needs: BOOTED
# env:   PHONE, PORTHOLE_ALARM, TK_HOST
# exits: 0 ok · non-zero on failure
```

### Talking to the device safely

There is one physical device and possibly several of you, or several agents.
Everything that touches it goes through the mutex, **declaring the state it
needs**:

```sh
TK_AGENT=$USER tools/ph-device.sh --need-booted ssh "$PHONE" 'uname -a'
```

Two exit codes carry the whole protocol:

- exit **75** — could not get the lock. Someone else has it. **Retry.**
- exit **76** — device is in the wrong state. **Do not retry**; something has to
  physically move it first.

That distinction exists because an agent once queued ten minutes for a phone
another agent had left in the bootloader, then died at its own timeout having
done nothing.

### A serial console

```sh
porthole serial hardware   what to buy, how to wire it, how to enable earlycon
porthole serial list       what is attached
porthole serial console    attach (terminal built in — no picocom needed)
```

ssh needs userspace and the USB gadget needs driver probe. A UART needs neither:
it is the only channel that talks during early boot, and the only one that says
anything when a kernel dies before console handover.

---

## Porting a device

```sh
porthole new-device oneplus-enchilada
porthole init oneplus-enchilada
porthole brain search --severity law     # ten notes. Read them before you start.
cat profiles/oneplus-enchilada/checklist.md
```

`new-device` scaffolds `profiles/oneplus-enchilada/` with **`device.env`** (every
key documented, seeded with whatever can honestly be determined from `fastboot
getvar all` and any existing pmaports `deviceinfo`), **`checklist.md`** (the
bring-up order, each item linked to the playbook explaining it), and **`tools/`**
for probes specific to your device.

It deliberately does **not** invent a `deviceinfo`, defconfig or DTS. A
confidently wrong one costs more than a blank: you end up debugging the device
instead of the file. *A blank you can see is a question you know to ask.*

> **If the framework gets something wrong for your device — a key that does not
> fit, an assumption that does not hold — that is the most valuable bug report
> this project can receive.** It has only ever been proven against one device.

### Device trees

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
nodes as gaps. Background: `porthole brain 25-device-tree`.

### Working on pmaports

```sh
porthole aports status            branch, what changed, which of it is yours
porthole aports start <topic>     a feature branch off the right base
porthole aports diff --mine       just your device's packages
porthole aports patch             a series, with a pre-submission lint
porthole aports worktree          give this device its own checkout
porthole channel                  see and switch release channel
porthole ui                       see and switch compositor
```

---

## How it works

Four ideas carry most of the design: configuration is layered data, device facts
are committed rather than remembered, builds hold no privilege, and latency is a
correctness concern.

### Configuration

Two files, two questions.

| question | file | committed? |
|---|---|---|
| who am I, where is my tooling | `~/.config/porthole/config.env` | **no** |
| which device is this | `profiles/<codename>/device.env` | yes |

Resolution runs lowest to highest, and the process environment always wins — so
a one-off override just works:

```
built-in defaults → profiles/<device>/device.env → ~/.config/porthole/config.env
                  → ./.env → the process environment
```

`porthole config` prints every resolved value **and the layer it came from**, so
"which variable is actually effective" is a question you answer by running
something rather than by reading. Full key reference:
**[`docs/CONFIG.md`](docs/CONFIG.md)**.

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

### Builds run in a rootless container

pmbootstrap needs root. The usual workaround —
`Defaults:you timestamp_timeout=9999` — is a **167-hour root credential cache**:
for a week, every process running as you gets silent, unlimited root. That is
not something to hand an agent.

So don't have root at all. Builds run in a persistent **rootless** container
where you are root inside — pmbootstrap therefore uses no sudo whatsoever — and
your own unprivileged uid outside. The default install grants no sudoers entry
and no standing privilege, so there is nothing for a mistake, a dependency or an
injection to spend. Only what it mounts is reachable: your `~/.ssh`, `/etc` and
home directory are not there.

```sh
porthole sandbox up                          # build the image, start the workspace
porthole sandbox shell --command <command>   # one command; works with no TTY
porthole sandbox shell                       # a shell, for a human
porthole sandbox status                      # what is up, what is missing
```

There is no second, weaker path — a privilege broker used to exist for hosts
without podman and was removed, because a weaker path that still exists is the
one a stuck agent reaches for.

The mounts, the work-directory rules, and what this honestly does *not* stop are
in **[`docs/SANDBOX.md`](docs/SANDBOX.md)**. Read it before trusting it.

### Speed

These tools run in tight loops — a 20-cycle suspend test, a soak run, an agent
polling device state — so latency is a correctness concern, not a nicety. Config
resolution costs under a millisecond; everything else is a network round trip,
which is why the shared ssh options carry connection multiplexing. **Measured on
a Pixel 2 XL over USB:**

| | per ssh round trip |
|---|---|
| without multiplexing | **302 ms** |
| with multiplexing | **14 ms** |

Every tool inherits that from one place. It is only safe because every reboot
path tears the control master down first — host keys change on essentially every
boot here, so a socket that outlives a reboot is a live handle to a dead sshd.
When a tool hangs strangely, `PORTHOLE_NO_MUX=1` is the first thing to try.

Measure it yourself with `porthole doctor --bench`. Details:
**[`docs/PERFORMANCE.md`](docs/PERFORMANCE.md)**.

### Repository layout

```
bin/porthole            the CLI — a thin launcher
lib/
  porthole.py           config resolution + device transport (python tools)
  porthole.sh           the same semantics for shell tools
  porthole_cli.py       command registry and output helpers
  porthole_cmd_*.py     one module per verb — drop one in to add a verb
  porthole_tui/         the console (`porthole tui`) — needs python 3.10+
                        and textual; gate.py itself runs on 3.8 to say so
tools/                  150 tk-* tools: boot, flash, probe, benchmark, soak
profiles/
  _template/            every device key, documented
  google-taimen/        the reference device: facts as data + its own tools
brain/                  184 scoped knowledge notes
docs/                   new-host, sandbox, config, performance, contributing
tests/                  everything runs with no device attached
```

---
## For agents and LLMs

**[`AGENTS.md`](AGENTS.md)** is the front door, written for any LLM rather than
one vendor's format. There is also a Claude skill at
[`skills/porthole-bringup/`](skills/porthole-bringup/).

The single command to run first:

```sh
porthole brief --json
```

It returns the device and its state, this device's encoded traps as prose, the
rules that cost sessions when broken, the tool catalogue pointer, the laws, and
suggested next steps — replacing four or five exploratory commands, each of
which can be got wrong.

Everything else an agent needs is machine-readable:

```sh
porthole tools --json          # the full tool catalogue with contracts
porthole config --json         # every resolved value and its source
porthole doctor --all --json   # health, with a fix for every failure
porthole brain --json --severity law
```

Four design rules make that work:

- **Exit codes are an API.** `0` ok · `1` the thing under test failed · `64`
  usage · `75` lock unavailable (retry) · `76` wrong device state (do not retry)
  · `124` killed at the hold ceiling. An agent that cannot tell "the tool broke"
  from "the answer is no" reports broken tools as findings.
- **Never interactive unless stdin is a tty**, so headless bootstrap works.
- **No colour when piped**, and `NO_COLOR` is honoured.
- **Every tool is self-describing** in its first 20 lines.

---

## Troubleshooting

**"Every tool does nothing."**
Passwordless sudo on the device — see
[the one device step everyone hits](#the-one-device-step-everyone-hits), or run
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
It has four states, not two, and `porthole brief` tells you which: `BOOTED` ·
`INITRAMFS` (stopped in the pmOS debug shell — `tools/tsh.py` will say why) ·
`FROZEN` (kernel alive, userspace gone) · `FASTBOOT` · `ABSENT`. See
`porthole brain frozen-is-not-hung`.

**Boot verdicts seem wrong.**
Never judge a boot by the screen — mainline often never lights the panel, so a
booted device and a hung one look identical. See
`porthole brain never-judge-a-boot-by-the-screen`.

**A build is running but nothing shows it.**
`porthole pkg status` reads the workspace log and the buildroot, so it reports
builds started outside `porthole pkg` too. If it says a build finished while one
is clearly running, that is a bug worth reporting.

---

## Project

### Contributing

See **[`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md)**.

```sh
make test        # the test suite, no device needed
make lint        # shellcheck + python syntax
make check       # both
make ci          # every job GitHub runs -- green here is green there
```

Adding a tool, a device, a CLI verb or a brain note each takes one file. The
tests enforce the contracts so that stays true.

Every push to `main` publishes the documentation to GitHub Pages; `make
docs-serve` renders the same site locally.

### Status

Proven against one device (`google-taimen`, MSM8998), with a second
(`google-cheetah`, GS201) in progress. That is stated as a coverage limit rather
than papered over: every tool declares its scope, and a probe encoding a vendor
protocol lives in that device's profile rather than pretending to be portable.

**The most useful thing you can do is bring a second device.** Not because the
tools need testing — because the line between "this is how phones work" and
"this is how *this* phone works" is only visible from two devices, and every
note in `brain/` wrongly marked `scope: generic` is a trap waiting for the next
person. `porthole new-device <codename>` scaffolds a profile in one command.

### Acknowledgements

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

### Licence

MIT — see [`LICENSE`](LICENSE).
