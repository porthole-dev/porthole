<!-- porthole | handoff | 2026-08-30 -->
# porthole should build packages, not only kernels

> **STATUS 2026-08-30 — the verb landed.** `porthole pkg
> build|watch|status|outdated` exists, the four numbered requirements below
> are implemented and tested, and both defects are fixed.
>
> Four things surfaced only by RUNNING it, none of them guessable from the
> code. **(1)** pmbootstrap puts only its own `=> step` lines on stdout and
> sends the actual build output — abuild, meson, every ninja `[N/M]` — to its
> own `$WORK/log.txt`. Parsing stdout gave a bar that never moved, and no
> amount of unbuffering fixes that, because those lines are never written to
> stdout at all; the tracker now tails `log.txt` in a background thread.
> **(2)** pmbootstrap prefixes every line with `[HH:MM:SS]`, so the anchored
> `[N/M]` match must strip it or the fraction is permanently unknown.
> **(3)** the tracker published only when a line arrived, so a quiet phase
> froze `elapsed` — indistinguishable from a hung build; there is now a 5 s
> heartbeat. **(4)** the last six lines of a failed `pmbootstrap build` are
> its version banner, so the failure tail prefers lines that name a cause.
>
> Verified live on a real `gst-plugins-good` build:
> `[====>             ]  27% build       3m10s eta   8m24s`.
>
> **The buildroot collision is fixed too.** `porthole pkg build` now takes an
> flock on the pmbootstrap work dir for the length of the build and records
> the holder, so a second build is refused with exit 75 naming who is building
> what, instead of silently deleting their source tree. `--wait SECONDS`
> queues. Two holes that recreate the collision were closed with it: a killed
> build used to leave pmbootstrap running inside the container, and a
> `pgrep -f "pmbootstrap.*build"` guard in a shell one-liner matches its own
> command line and waits forever. See
> `brain/traps/two-pmbootstrap-builds-destroy-each-other.md`.
>
> **What is still open is the WebKit work at the bottom of this file** — the
> patch is written and the package unbuilt. `porthole pkg build
> webkit2gtk-6.0 --detach` starts it so it outlives the session, and
> `porthole pkg watch` follows it in any terminal without anyone having to sit
> on the output.

**For the agent picking this up.** This is a design gap, not a bug report. It
was found the hard way on 2026-08-30: a multi-hour `webkit2gtk-6.0` build was
started, failed after ninety seconds, and **nobody noticed for half an hour**,
because a package build in this toolbox reports nothing at all. The user asked
"why can't I see the progress bar" and that question is what surfaced the dead
build. That is the whole case for this work in one sentence.

## What exists today

`porthole build` is the envkernel loop and nothing else. Its own help says so:
*"build the kernel and package it, through envkernel"*, with actions
`auto | status | mod | boot | fast | kernel | upgrade | clean | purge`. Every
rung is a kernel artifact.

It is also where all the instrumentation lives:

| thing | where |
|---|---|
| live bar, phase, ETA | `lib/porthole_cmd_build.py` → `porthole_progress.Tracker` |
| `.run/build-status.json` | `lib/porthole_cmd_build.py`, `lib/porthole_progress.py` |
| `.run/build-<rung>-<stamp>.log` | same |
| `porthole build status --json` | same |

A **userspace aport** has no verb. The only route is:

```sh
porthole sandbox shell --command 'pmbootstrap build --lax <aport> --arch aarch64'
```

`porthole sandbox shell` is a bare command runner. No tracker, no status file,
no ETA, no log rotation, no completion signal. So the longest builds in the
tree — webkit is hours, phoc is ~6 minutes, gst-plugins-good similar — are
exactly the ones that report nothing, while a 40-second module push has a
progress bar with an ETA.

**`porthole sandbox build` is not what the name suggests.** It builds the
*container image* (see its own `--force` help: "build: rebuild even if the tag
exists"). Anyone reaching for "build a package with porthole" finds this verb,
tries it, and gets something unrelated. Renaming it, or at minimum making the
help text say "container image", would stop the next person losing the same
five minutes.

## Before anything else: the buildroot needs a lock

While writing this handoff, the webkit build it describes was destroyed by a
second agent running `pmbootstrap build gst-plugins-good` in the same
workspace. One buildroot chroot per arch, `abuild` cleans `$srcdir` before
unpacking, no lock anywhere -- 37 minutes of an 8233-object build gone, and the
failure blamed clang++ for missing source files rather than naming the
collision. See `brain/traps/two-pmbootstrap-builds-destroy-each-other.md`.

The phone already has this guard: `tools/tk-device.sh` takes an flock and exits
75 when it cannot, because one physical device cannot serve two callers. The
buildroot has exactly the same property. A package-build verb is the natural
home for the same idiom, and shipping the verb *without* it would make the
collision easier to hit, not harder -- more agents running more long builds.

Record the holder like the device mutex does, so the second caller is told who
is building what instead of queueing blind or, worse, proceeding.

## What to build

A package-build verb that reuses the existing progress machinery rather than
growing a second copy of it. Shape suggestion, not a requirement:

```sh
porthole pkg build <aport> [--arch aarch64] [--yes]
porthole pkg status
```

Requirements that are not obvious and were each paid for:

1. **`--lax` is mandatory in the workspace.** A non-lax `pmbootstrap build`
   cannot run there at all: `zap_buildroots()` umounts the chroot and the
   recursive `/dev` bind leaves propagated sub-mounts that cannot be umounted
   by path — `umount: /pmb/chroot_native/dev/shm: not mounted` (exit 32) — so
   the build dies at "Zapping buildroots" before it starts. `tools/ph-build.sh`
   already passes `--lax` automatically in the workspace; a hand-rolled
   `pmbootstrap` call does not, and this is the first thing that bites.
   See `brain/findings/what-a-rootless-workspace-cannot-do.md` §5.

2. **Progress has a real source to parse.** Package builds emit
   `[N/M]` ninja lines and abuild's `>>> pkg: phase` markers. That is enough
   for a genuine percentage, unlike the kernel rungs which had to be estimated.
   cmake configure (~90 s for webkit, no `[N/M]` at all) needs its own phase or
   the bar sits at 0% looking hung.

3. **Verify the artifact, not the exit code.** This is already the stated rule
   for the kernel rungs — `pmbootstrap build` can write an apk and then fail
   before refreshing the index — and it applies identically here. Assert the
   `.apk` exists at the expected `pkgver-pkgrel` before declaring success.

4. **Fail loudly.** The failure that motivated this printed
   `ERROR: Couldn't build aarch64/webkit2gtk-6.0-2.48.1-r50.apk!` into a log
   file nobody was watching. Whatever this verb does, a dead build must be
   visible from `status`.

## Progress: `[N/M]` alone will lie, and here is exactly how

Measured on the webkit rebuild, so a progress implementation can be designed
against the real shape rather than the assumed one.

**1. ccache replay makes the percentage jump for free.** The killed run had
compiled 2658 objects. On restart ccache replayed them almost instantly, so the
bar went 0% -> 32% in about fifteen minutes, of which six were cmake configure.
Any ETA derived from that window is nonsense: it is measuring cache lookups,
not compilation. A rate must be computed from a window that contains real
compiles, or the first ETA shown to the user will be wildly optimistic and then
wildly pessimistic minutes later.

**2. The counter stalls completely on single-threaded generator steps.** Sampled
over four minutes mid-build:

    2660 objects -> 2665 objects   (+5 in 240s)

which naively extrapolates to **74 hours remaining**. The build was perfectly
healthy. It was blocked on one step:

    [2665/8233] Generating .../JavaScriptCore/DerivedSources/LLIntDesiredOffsets.h
    99.6%  qemu-aarch64-static /usr/bin/ruby .../offlineasm/generate_offset_extractor.rb

Load average 1.82 on a 16-core host, 91.8% idle -- fifteen cores doing nothing
while one emulated Ruby process worked. WebKit has several of these (offlineasm,
the bindings generators); other packages have their own. Extrapolating a rate
across such a stall produces a number that is not merely wrong but alarming, and
an agent watching it will conclude the build is broken and kill a working run.

**What follows for the implementation:**

- Track a phase, not just a count. abuild and ninja both say what they are doing
  (`Generating` vs `Building CXX object`); a stalled counter during `Generating`
  is normal and should be displayed as such rather than folded into an ETA.
- Compute the rate over compiles only, and discard windows with near-zero
  progress instead of dividing by them.
- Show elapsed always; show ETA only once there is a defensible rate. "elapsed
  25m, generating" is honest and useful. "eta 74h" is neither.

**3. crossdirect covers compilers only.** Its wrapper directory holds
`cc c++ clang clang++ gcc g++ cpp cargo rustc` plus archivers and compression
tools -- **no ruby, no perl, no python**. So every Ruby and Perl generator step
in a package runs fully emulated under qemu-aarch64, single-threaded. For webkit
that is a large fraction of wall clock and it is invisible in the object count.
Worth measuring before assuming the compile itself is the thing to optimise.

## Third: package builds run emulated, not cross-compiled

Measured during the webkit build on a 16-core host, fully loaded (92% user,
load 32):

```
19 qemu-aarch64 -> clang++
17 qemu-aarch64 -> perl
15 qemu-aarch64 -> ccache
 1 qemu-aarch64 -> ninja
```

The compiler itself is running under qemu-user emulation. `crossdirect` appears
in the build log ten times, so it is being set up, but clang is still reached
through `qemu-aarch64` rather than run natively against an aarch64 sysroot.
webkit took ~1.7 h for 8233 objects this way; native cross-compilation should
be several times faster, and this is the single biggest lever on the iteration
loop for userspace packages.

ccache IS active (15 processes), so repeat builds get the existing cache win --
this is specifically about the first build of a package.

Worth checking whether `PORTHOLE_*` or pmbootstrap's crossdirect is silently
falling back, and whether a package verb should assert "compiles are native"
the way the kernel rungs assert their artifacts. A build that is 5x slower than
it needs to be, with nothing saying so, is the same class of silent-wrong as
the missing progress bar.

## Second defect, cheap to fix, found alongside

> Fixed. `porthole_progress.liveness()` decides from the pid whether a run is
> still alive, and `status_report()` draws a bar and an ETA only for one that
> is. Anything else says what happened and how long ago: `FAILED  97m19s ago`.

`porthole build status` reports a **stale run as though it were live**. After
the failed `porthole build auto` at 11:45 it kept printing:

```
  [>                 ] starting
  rung        auto
  state       failed
  elapsed     0s
  eta         6s
```

for hours, with no indication that this finished long ago. `state failed` is
there, but the bar and ETA read as a run in flight. A finished or stale entry
should say so, or `status` should refuse to render a bar for a run whose pid is
gone.

## A pmbootstrap trap worth a brain note

> Written up as `brain/traps/pmbootstrap-never-runs-the-shell-in-an-apkbuild.md`,
> and `porthole pkg build` now warns about it before starting rather than
> letting it surface ninety seconds into cmake.

`pmb/parse/_apkbuild.py` parses APKBUILDs **line by line and never executes the
shell** (`_parse_attributes(path, lines, ...)`). So this, which is how Alpine
itself writes it, is invisible to pmbootstrap:

```sh
case "$CARCH" in
s390x) ;;
*)
	makedepends="$makedepends libjxl-dev"
	;;
esac
```

`libjxl-dev` is never installed into the buildroot, and the build dies ~90 s
later inside cmake with `libjxl is required for USE_JPEGXL` — which reads like
a missing distro package, not a parser limitation. `apk search` shows the
package exists for the arch, which makes it more confusing, not less. Any aport
forked from Alpine that conditionally appends dependencies needs them listed
statically as well. This cost one full failed webkit configure.

## Work in flight that this unblocks

`pmaports/temp/webkit2gtk-6.0` (forked from `community`, 2.48.1, `pkgrel=50`)
carries `0001-Bind-every-V4L2-node-into-the-sandbox-not-just-video.patch`.

WebKit's `bindV4l()` hardcodes `/dev/video0`, `/dev/video1`, `/dev/video2` —
its own comment calls itself "a stop-gap". V4L2 node numbers are handed out in
probe order, so on msm8998 qcom-camss takes video0–video5 and the Venus decoder
is `/dev/video7`, which therefore does not exist inside the web process'
sandbox. The v4l2 decoder elements cannot open the device,
`GStreamerRegistryScanner` drops them, and playback falls back to `avdec_h264`
**silently — nothing is logged anywhere**.

Proven with the sandbox-off control on a 720p H.264 clip:

| | sandboxed | `WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1` |
|---|---|---|
| element created | `avdec_h264` | **`v4l2h264dec`** |
| decoder subdevice | `suspended` | **`active`** |
| CPU | **154%** of a core | **41%** |

The patch enumerates `/dev` and binds every `video*`/`media*` node through the
existing `bindIfExists()` helper. It applies cleanly to pristine 2.48.1 and is
written to go upstream — it exposes no class of device `bindV4l()` was not
already binding, it only stops the set depending on enumeration order.

**Remaining:** finish the build, install, and verify `hwdec` goes YES with CPU
near 41% on the same clip. The measurement harness is `tools/tk-mempressure.sh`
(its `hwdec` field reads the decoder subdevice's `runtime_status`, which is
valid only while `power/control` is `auto` — it reports `pinned` otherwise).

## Related notes

- `brain/traps/a-444-test-clip-makes-working-hardware-decode-look-broken.md` —
  read before testing any decoder; a synthetic clip is part of the code under
  test.
- `brain/findings/lax-build-buys-nothing-measurable.md` — `--lax` is a
  correctness requirement in the workspace, not a speed knob.
- `tools/tk-pkgcheck.sh` now covers `pmaports/temp/`, which is how a fork that
  upstream has overtaken gets caught.
