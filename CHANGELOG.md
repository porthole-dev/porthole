# Changelog

Notable changes. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- `porthole pkg search` and `porthole pkg fork` — pmbootstrap keeps pmaports
  and Alpine's aports side by side and `pmbootstrap build` reads only the
  first, so Alpine's twelve thousand packages were present, useful and
  unbuildable, with nothing to say so. `pkg build posh` answered "no aport
  named posh" and pointed at `porthole aports`, which lists the packages
  named after your device and could not have found it under any
  circumstances. `search` looks in both trees, says which one a name is in,
  and falls back to `difflib` so `posh` returns `phosh`; `fork` is the
  missing rung that copies an Alpine aport into pmaports where a build can
  see it, instead of reaching past porthole to `pmbootstrap aportgen`.
- `porthole aports status` lists the packages you have forked, not only the
  ones named after your device. Ten of them were invisible on the taimen
  port — `temp/gst-plugins-good`, `temp/libcamera`, `temp/phoc`,
  `temp/webkit2gtk-6.0`, `main/modemmanager` and more — because what makes a
  package yours is a commit, not a name.
- `porthole slots` — A/B slot state read from the device by `fastboot getvar`
  rather than from a note somebody wrote down, and the retry counters that
  explain a boot landing in the bootloader by itself.
- `porthole pkg stop` — cancel a package build on both sides of the container
  boundary. Killing the `podman exec` client never killed what it exec'd, so
  cancelling meant two kills by hand and still left the buildroot locked.
- `porthole pkg watch`, `pkg status --json` and a detached `pkg build`, so a
  four-hour build is watchable from a second terminal and pollable by an agent
  without burning a request every thirty seconds.
- Exit code **69 — the tool could not run at all**. `aports lint` printed
  "lint found problems" for a subcommand pmbootstrap 3.11.1 had removed, and
  `channel <name> --yes` printed "pmbootstrap refused the channel change" for
  a config key that no longer exists: both exit 1, both a broken tool wearing
  the costume of a finding about the user's work. Documented in AGENTS.md §6,
  `brain/laws/exit-codes-are-an-api.md` and `docs/ARCHITECTURE.md`.
- `lib/porthole_pmb_api.py` — porthole asks the installed pmbootstrap which
  subcommands and config keys it has instead of assuming, and
  `tests/test_pmb_api.py` fails here rather than in front of a user when the
  next one disappears.
- `porthole brief` says whether anything is building and who holds the
  buildroot. Two agents collided in this repo because nothing answered that.
- The console is rebuilt on Textual. Argument forms, generated from the
  argparse specs each verb already declares — every verb is now reachable
  with real arguments, where previously the palette could run a verb's
  default and nothing past it, so `blobs unsparse vendor.img` could not be
  reached from the console at all.
- A file picker, starting at the port's working directory.
- A job drawer: commands stream in-app instead of dropping out of the
  console, so a long build stays watchable while you read a note.
- `porthole soc` — find devices sharing your SoC in pmaports (664 device
  packages, indexed in 55ms) and inherit their known-good boot image offsets.
  `new-device` now seeds from the closest sibling automatically.
- `porthole dts` — device-tree workflow: where the values come from, what the
  SoC dtsi already defines, scaffolding, and a `compare` that follows
  `#include` chains so inherited nodes are not reported as gaps.
- `porthole aports` — pmaports workflow: status, feature branches off the right
  base, scoped diffs, and a patch series with a pre-submission lint.
- `porthole channel` and `porthole ui` — switch release channel and compositor,
  saying what the switch costs first.
- `porthole serial` — UART console with the terminal built in (termios, stdlib),
  plus hardware guidance for getting a UART onto a phone.
- `porthole docs` — a generated MkDocs Material site published to GitHub Pages.
- `brain/playbooks/25-device-tree.md`.
- `porthole brief` — the agent entry point: device, state, this device's encoded
  traps as prose, the rules that cost sessions, and next steps, in one call.
- `porthole tools` — search 115 tools by scope, required device state, or name;
  read a tool's contract without opening it. `--json` is the agent catalogue.
- `porthole completion` for bash, zsh and fish, generated from the live command
  registry so it cannot drift out of sync.
- `porthole version` — environment summary for bug reports.
- Extensible command registry: a verb is one `lib/porthole_cmd_<name>.py` file.
- `tests/test_tools.py` — enforces the tool contract: self-describing headers,
  valid scopes, no personal paths, device-scoped tools living in a profile.
- CI across python 3.8/3.11/3.13, plus a fresh-clone smoke test.
- `Makefile` with `test`, `lint`, `check`, `install-completion`.

### Changed
- `aports build` delegates to `pkg build`, so there is one build path with the
  progress bar, `--lax` handling, the artifact check and `--detach`, instead of
  two doors with only one of them good.
- The workspace image ships `dtc`, and `verify` probes for it inside the
  container before promising a command that would have compiled nothing.
- The console needs Python 3.10 and `textual`; the CLI and every tool still
  need neither and run on 3.8 with nothing installed.
- 22 tools that opened their own ssh with hand-rolled flags now use the shared
  lib, so they inherit the mandatory host-key options and connection
  multiplexing. Measured: 302 ms → 14 ms per round trip.
- A bare `porthole` orients you rather than dumping usage.
- `porthole doctor` names a distribution-specific install command for anything
  missing, and refuses (in code) to report a failure without a fix.

### Fixed
- `porthole aports` computed its base ref from `pmbootstrap config channel`,
  a key removed in pmbootstrap 3.x — 150ms spent on a subprocess whose only
  possible answer was an argparse error, after which every caller fell back
  to `origin/master`. Where upstream's trunk is `main`, that base spanned
  1676 commits and 2054 directories instead of the 226 commits the port
  wrote, so `aports patch` would have written 1676 patch files and called
  them your series. The channel is now read from `pmaports.cfg`, and the
  remote-tracking ref is preferred over a stale local branch of the same
  name.
- `pkg build --force --detach` built without `--force`, and `--wait N --detach`
  returned success while the child bailed on the busy buildroot: the detached
  argv was rebuilt by hand and dropped both flags.
- `pkg stop` trusted `state: running` in the status file, so a snapshot left
  behind by a killed build or a reboot could send SIGTERM to whatever pid the
  OS had recycled; and with no status file at all it wrote one saying a build
  had failed.
- `porthole channel` read the channel from the git branch — this checkout sits
  on `taimen-bringup` while pmaports.cfg declares `edge` — and switching it
  shelled a config key pmbootstrap 3.x removed.
- `slots` ran `fastboot getvar all` first, which blocks for a full minute with
  no device attached instead of failing; `fastboot devices` answers instantly
  and is the authoritative check, since lsusb mislabels the running gadget.
- The containerised `dtc` was invoked without `-i`, and `podman exec` drops
  stdin without it, so every device tree compiled to `<stdin>:0.0 syntax
  error` — a tooling gap that read as a broken device tree.
- `brief` probed the host pmbootstrap dir for the buildroot lock, which a
  workspace build never takes, so a foreign build showed as "buildroot free".
- `pkg build` arms the native ccache the way the kernel path has since the
  chroot_native fix; packages had never inherited it and paid qemu to hash
  every preprocessed source.
- `panes/console.py` was imported by nothing, so the device-log colouring it
  contained had never been reachable.
- The reader's `r` binding was shadowed by an earlier `elif` and refreshed
  instead of running, while the footer advertised "run it".
- `T.BAR` was declared with no colour pair, so the chrome had no identity.
- Filtering a list and opening a result took two identical-looking
  keystrokes, only the second of which did anything.
- The palette built every entry as a bare `porthole <verb>`, so it reached a
  verb's default and nothing past it — `blobs unsparse vendor.img` was
  unreachable, and nothing in the app navigated a filesystem.
- An error's remedy went to stdout while the error went to stderr, so
  `2>log` captured the problem and lost the fix.
- Built-in defaults beat the device profile — exactly backwards.
- Both config parsers kept the quotes on `KEY="a"  # comment`, which silently
  defeated the forbidden-slot guard.

## [0.1.0] — 2026-08-23

Initial extraction from the taimen (Pixel 2 XL, MSM8998) port.

### Added
- Layered `KEY=value` config: defaults → device profile → user config → checkout
  `.env` → process environment.
- `lib/porthole.sh` and `lib/porthole.py`, held together by a test that diffs
  their resolution on every key.
- 115 tools, de-hardcoded and scoped.
- `brain/` — 47 scoped knowledge notes from two ports.
- SSH connection multiplexing in the shared options, with every reboot path
  tearing the control master down first.
- Full backward compatibility with the taimen invocations: `PHONE`, `HOST`,
  `TK_HOST`, `FASTBOOT`, `TK_POLL`, `TK_AGENT`, `TK_DEVICE_*`, and a sourceable
  `tools/tk-lib.sh`.
