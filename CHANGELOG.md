# Changelog

Notable changes. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
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
- The console needs Python 3.10 and `textual`; the CLI and every tool still
  need neither and run on 3.8 with nothing installed.
- 22 tools that opened their own ssh with hand-rolled flags now use the shared
  lib, so they inherit the mandatory host-key options and connection
  multiplexing. Measured: 302 ms → 14 ms per round trip.
- A bare `porthole` orients you rather than dumping usage.
- `porthole doctor` names a distribution-specific install command for anything
  missing, and refuses (in code) to report a failure without a fix.

### Fixed
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
