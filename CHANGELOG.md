# Changelog

Notable changes. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **`porthole build image`** -- the whole postmarketOS system, built from
  pmaports as it stands, and the only rung that compiles no kernel tree. Every
  other rung goes through `_ph_make`, so on a host that had just been set up
  `porthole build` printed "tree: not set", offered six rungs, and not one of
  them could run -- with nothing on the screen saying which of the two missing
  things was the problem or that a build was possible at all. The kernel comes
  from the aport, which is also what gives the export a verification reference
  that exists: without the pin, `porthole flash` refused the image the rung had
  just produced, naming a `.dtb` that was never built.
- `porthole init` asks for **the working repo**, which is the key every build
  verb needed and nothing ever asked for. It looks for a directory named after
  the device beside the porthole checkout and under the usual roots in `$HOME`,
  offers what it finds, and offers to create one. It never picks between two
  candidates on its own -- the same bar `_autoselect_tree` holds.
- `porthole init` writes `PORTHOLE_WORKDIR_<CODENAME>`, never the bare key.
  `--workdir` wrote the bare one, which `porthole.load_config` ignores outright
  the moment a second device declares its own -- so on any two-device host the
  value it wrote was present, looked right, and was not used.
- `porthole init` explains **how to reach the device** instead of printing
  `172.16.42.1` as a bare default. Both routes are named -- the USB gadget, the
  same address on every postmarketOS device and up before wifi is configured,
  and wifi, which is faster and changes -- and it checks whether the gadget is
  enumerated on this host right now, then pings whatever you answer. The number
  was always correct and unrecognisable as anything but a hardcoded guess.
- `porthole build`'s preview says **where the build will run** -- workspace or
  host, with the reason when it is the host -- which work dir it will use, and
  whether there is a kernel tree. It was printed only once the build had
  started, which is after the point at which anybody could act on it.
- `porthole sandbox status`, `porthole sandbox up` and `porthole build` all
  report a workspace **started before the working repo was set**. Container
  mounts are fixed at creation, so such a workspace has no `/work` and every
  build in it dies on ph-build.sh's own `${PORTHOLE_WORKDIR:?}` -- naming
  neither the container nor the fix, while `sandbox status` said "sandbox is
  configured" throughout.
- `porthole build` refuses `kernel` and `image` up front when
  `TK_PMOS_PASSWORD` is unset, rather than letting a shell parameter expansion
  kill the run after the chroot work.

### Added
- **`porthole build ccache`** -- what the compiler cache is doing, and
  `--max` to raise its ceiling. Nothing could answer "is my rebuild going to
  be fast": the cache lives in `cache_ccache_<arch>` under the work dir, is
  mounted into the chroot for *that* arch and no other, and belongs to the
  build user -- so `pmbootstrap chroot -- ccache -s`, the obvious command,
  reports an empty cache next to 400 MB on disk. Three facts from
  pmbootstrap's source to read one number. The ceiling was ccache's own
  default of 5G with nothing mentioning it; a single kernel build puts ~0.4G
  in, and past the ceiling ccache evicts and a rebuild silently becomes a full
  build. Measured on this host: a forced kernel rebuild went from ~12 minutes
  to **2m33s**, hits 4 -> 3280.

### Fixed
- **`pmbootstrap install` now actually runs in the workspace.** The whole
  zero-privilege design rests on `--no-image` -- `docs/SANDBOX-PROVISIONING.md`
  says so, and `sandbox/Containerfile` fails the image build if pmbootstrap
  ever drops the flag -- and nothing ever passed it. Nobody had noticed
  because no rung in the workspace had run `install` to completion: `mod`,
  `boot`, `fast` and `upgrade` never call it. It died after twelve minutes on
  `modprobe: can't change directory to '/lib/modules'`, having built
  everything correctly first.
- The install rungs pre-build the kernel aport. `pmbootstrap install` resolves
  the whole world in one apk transaction and the device's own kernel
  subpackage depends on that aport, so with nothing having built it the run
  failed at resolution with "no such package" -- naming a package that is
  right there in pmaports.
- The install rungs survive pmbootstrap building a package for itself. It
  builds in strict mode, with no flag to change that, and a strict build ends
  in `zap_buildroots()`, which a rootless namespace cannot complete. The zap
  runs *after* the apk is written, so each attempt completes one more package;
  the rungs now retry that one failure and only that one.
- **A stale rootfs image can no longer be flashed.** A run that died at
  `modprobe loop` had already run `truncate -s 1482M`, leaving a 1.5 GB file
  with nothing in it exactly where `flash_rootfs` looks, beside a perfectly
  good `boot.img`. `porthole flash` now refuses a rootfs image meaningfully
  older than the boot image, and an install that cannot make one moves the old
  one aside rather than leaving it to be found.
- The kernel rungs take the buildroot lock. `porthole pkg build` has held it
  since a `checksum` run destroyed an active kernel build; `porthole build`
  never took it, and `pkg`'s own code described a running kernel build as a
  foreign process that "holds no lock ... but owns the buildroot all the
  same". The new `--wait` flag on `porthole build` queues instead of failing.
- `porthole build` recognises every pmbootstrap subcommand that owns the
  chroots, not only `build`. A running `pmbootstrap install` read as an idle
  buildroot, so `porthole build clean` unstacked `/mnt/linux` underneath a
  live install and nothing objected.
- `porthole sandbox up` sets pmbootstrap's `kernel` from the device package.
  It defaults to `stable` and only `pmbootstrap init` -- interactive, and the
  thing this tier exists to avoid -- ever changed it, so a device offering
  only `mainline` failed inside `install`, after the rootfs chroot was built,
  telling you to run the command you cannot run.
- `porthole sandbox up` works with a forked pmaports. pmbootstrap reads
  channels.cfg from `<upstream>/main` and identifies the upstream by matching
  a remote URL against two hardcoded postmarketOS ones, so a bring-up fork --
  the normal state for this tool -- died with a message about a remote that
  never mentioned channels.cfg. `PMB_CHANNELS_CFG` is pmbootstrap's own
  documented override, so no patch and no write to anybody's repository.
- `porthole build`'s failure diagnosis no longer blames `--lax` for a
  dependency-resolution failure. An install that recovered from one zap and
  then died on apk left both signatures in the window, and the stale one was
  the advice being handed out.
- **A rootless workspace cannot make the rootfs disk image, and now says so
  instead of failing.** `pmbootstrap install` attaches the image file to a
  loop device, and `/dev/loop-control` is `root:disk` -- the loop ioctls want
  `CAP_SYS_ADMIN` in the namespace that owns the device, which a user
  namespace never does. The install rungs pass `--no-image` where no loop
  device exists, so the rootfs chroot is populated and `boot.img` is exported
  and verified; the message says what you got, what you did not, and spells
  out that `--host` means running the build on this machine rather than in
  the workspace.
- **A stale rootfs image can no longer reach the phone.** A run that died at
  `modprobe loop` had already run `truncate -s 1482M`, leaving 1.5 GB of
  nothing exactly where `flash_rootfs` looks, beside a good `boot.img`.
  `porthole flash` now refuses a rootfs image meaningfully older than the boot
  image, and an install that cannot replace one moves it aside.
- **Prose about a step no longer becomes the step.** `porthole build status`
  reported `phase flash` about a finished `image` run, which flashes nothing:
  every `>>` line was read as an announcement, so detail lines and quoted
  commands set the phase. ph-build.sh's own convention answers it -- `>> `
  announces, `>>   ` explains, and 63 of its detail lines already follow that.
- **A finished pmbootstrap invocation is no longer reported as a running
  build.** The status line read `device-google-taimen  [ unknown ]  --
  42m50s  build  · reattached`, where the name came from a staged APKBUILD an
  unrelated build left 42 minutes earlier and the freshness came from a
  `ccache -s` five seconds before. The log is shared by the whole workspace,
  so its mtime says only that some pmbootstrap ran. A running build has not
  printed `DONE!`.
- **`porthole pkg status` no longer replaces a live snapshot with a guess.**
  `reattach_from_log` returns `None` for two opposite reasons -- "the tracker
  is alive, its own file is better" and "there is nothing to reattach to" --
  and this treated them the same, so a running `pkg build linux-...` was
  reported as `pkg:device-google-taimen` at `39m30s` with a note saying it had
  been started outside porthole. Every field wrong, about a build whose own
  status file was correct and one second old.
- `porthole build` names who holds the buildroot, from the lock sidecar,
  instead of asserting the build "was started outside porthole" about one
  `porthole pkg build` had started a minute earlier. `--wait` now queues for a
  foreign build too, not only for the flock.
- **The status line can tell you whether a build is alive.** A kernel rung has
  no percentage, so the row was `image  [ unknown ]  --` -- byte for byte the
  same in a build's first second, its tenth minute, and one that had quietly
  stopped saying anything, while `porthole build status` had the phase, the
  elapsed and the last line from the same snapshot. It now carries elapsed,
  phase, a line count that ticks up, and `quiet <duration>` when nothing has
  been said for a while. A `stale` run no longer gets a spinner frame beside
  the word for "not moving".
- `porthole sandbox up` finds pmaports wherever the config says it is. It
  looked only in `$PORTHOLE_PMB_DIR/cache_git/pmaports` -- pmbootstrap's own
  clone -- so a host whose checkout is named by `PORTHOLE_PMAPORTS` or the
  per-device key was told "the workspace has nothing to build from, run
  `pmbootstrap init` on the host": false, and the exact thing the workspace
  tier exists to spare you. `porthole init` had already asked for that
  checkout and written the key. The workspace then came up with no pmaports
  mounted at all, and nothing said so.
- `porthole sandbox status` reports whether the workspace can see pmaports,
  and distinguishes "there is no checkout on this host" from "one is
  configured and this container started before it was" -- different problems
  with different fixes.
- Every "no pmaports checkout found" now names `porthole init`, which adopts
  or clones one, rather than `pmbootstrap init`, which is the host-tier
  answer offered on the tier that exists to avoid it.

### Changed
- `porthole init`'s prompts and menus use the toolkit's own output vocabulary.
  It held the only hand-rolled interaction in the repository -- a bare
  `input()` and numbered menus with the indentation typed in by hand -- and it
  is the first command a newcomer runs. Menus now return the option's key
  rather than its number, which is what stopped the two existing ones
  disagreeing about what `3` meant.
- `porthole build`'s "PORTHOLE_WORKDIR is not set in the profile" now names the
  thing that is missing and the command that sets it. It is not a profile key
  and never was, so the old wording sent people to read a committed file that
  correctly says nothing about it.
- `porthole build`'s preview no longer claims a kernel tree at `.`. With no
  working repo `_tree` returns a bare `Path()`, and run from a checkout with a
  `Makefile` of its own that passed the existence test.

### Fixed
- `porthole init` answers its own "next step" against the host as it now is.
  It loads the config before writing -- there may be nothing to load -- so on a
  genuinely fresh machine the one next step it promises was computed against
  "no device selected" and silently omitted, on the first run it exists for.
- Two test suites guarded themselves with the tester's config and then ran
  subprocesses with `XDG_CONFIG_HOME` isolated, so on a host whose pmaports is
  named by `config.env` rather than sitting in pmbootstrap's `cache_git`, seven
  tests failed for a reason unrelated to what they test.

### Added
- `porthole init` sets the whole host up and is safe to re-run on a
  half-configured one. It reads what is already there, offers it back as the
  default for every question, and rewrites only the lines you change --
  through the same `set_key` `porthole use` has always used, so comments,
  hand-added keys and deliberately overridden tool paths survive. Every key is
  reported `kept`/`changed`/`added`, and a run that changed nothing says so:
  on a half-set-up machine "it did not complain" and "it agreed with what was
  there" otherwise look identical.
- `porthole init` asks the one question that decides what a machine needs at
  all -- **where builds run**. On the workspace tier it writes no pmbootstrap
  keys and says the image carries pmbootstrap, because it does; the reference
  host had a hand-installed pmbootstrap that no build was using. On the host
  tier it finds an existing checkout or clones one at the tag matching the
  installed CLI, the same rule the Containerfile follows.
- `porthole init` resolves pmaports: adopt what is already there, point at a
  checkout, or clone one. `--tier` and `--pmaports` drive both steps headless.
- `docs/NEW-HOST.md` -- what a new machine actually needs, in one page.
- `porthole doctor` reports `host: pmaports` and `host: work dir` with **the
  key each path came from**. "Which variable is effective" was unanswerable
  without reading the source, and the two pmbootstrap work dirs are different
  directories that a reader who has seen one of them assumes is the one their
  build used.

### Changed
- `porthole init` output follows the same status-row shape as `doctor` and
  `sandbox status`. It held 11 of the 16 hand-indented output calls in the
  repository: the first command a newcomer runs was the one that looked
  unlike the rest of the tool.

### Removed
- `PORTHOLE_ENVKERNEL`. It and `PORTHOLE_PMBOOTSTRAP_SRC` answered one
  question -- where `helpers/envkernel.sh` is -- and two knobs for one
  question is the reliable way to be unsure which one a build used. Nothing
  set it. `find_pmaports` now reports which candidate answered, so the
  explanation and the resolution order are the same code.

### Added
- `porthole doctor` and `porthole sandbox status` report whether the DEVICE
  accepts the workspace ssh key, not merely whether the key file exists. The
  old check was green on a phone whose `authorized_keys` had been lost in a
  fresh install, so doctor said everything was fine while every workspace push
  failed with `scp: Connection closed`. Three states, because an unreachable
  device is not evidence a key is bad. The fix is printed and never run —
  installing a key is a privileged write to the device.
- Every host tool in `doctor` is now proved to RUN, not merely to be on PATH.
  A `+x` script whose shebang interpreter no longer exists — the ordinary pipx
  failure, and routine on an rpm-ostree host — passes `shutil.which` and dies
  at exec. Each tool is asked the version flag it actually supports, because
  `ssh --version` is not one.

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

### Fixed
- `porthole build auto` routed on what one `make` invocation happened to
  touch, and the preview runs a real incremental make — so a preview consumed
  the evidence the run needed. `porthole build` followed by `porthole build
  auto --yes` reported "make rebuilt nothing" about a tree with a real change
  in it, which made the documented preview-then-run flow self-defeating. It
  now routes on what the device has not received, recorded when a rung
  actually pushes, so preview and run agree and running it twice is safe.
- `porthole build` defaulted to `$PORTHOLE_WORKDIR/linux` and stopped there,
  so a repo whose product branch lives in a sibling worktree needed
  `PORTHOLE_KERNEL_TREE` typed on every invocation. It now builds the one
  sibling tree on `PORTHOLE_KERNEL_BRANCH` when exactly one is, and says so.
  Never when two are: choosing silently is how a half-finished branch gets
  flashed.
- A relative `PORTHOLE_KERNEL_TREE` meant three different directories in three
  places. `linux-ws` built in the workspace and died on `--host` with
  `pushd: linux-ws: No such file or directory`. It now resolves against the
  device working repo everywhere, and a test compares the shell and Python
  answers.
- `porthole build mod` never offered the device ssh key: `tkmod`'s two `scp`s
  and its two `ssh`s omitted `TK_SSH_OPTS`, which is where
  `-i $PORTHOLE_SSH_KEY` lives — so in the workspace, where that key is the
  only one the container has, every push failed with an error that named
  nothing. Same one-line defect in `ph-mic-check.sh`. A contract test covers
  the build path now.
- The `boot` rung seeds its own base image from the device's active boot
  partition, cached under `.run` keyed on the device's kernel release. The old
  default was `/tmp/tk-base-boot.img` with an instruction to seed it by hand
  that could not be followed from where builds run — the workspace container
  does not mount the host's `/tmp`. The check also fired after the compile
  rather than before it.
- The `boot` rung no longer claims to need "no pmbootstrap". It compiles
  through envkernel, which is pmbootstrap; it needs no *packaging* step, which
  is what made it cheap. `--host` says what host building actually requires.
- `tk_expired` with an unusable deadline printed `[: : integer expected` from
  inside the library rather than naming the caller that passed it. It now
  names the caller, and reports "not expired" rather than giving up instantly
  on a device that was fine.

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
  `tools/ph-lib.sh`.
