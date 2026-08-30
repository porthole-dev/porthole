<!-- porthole | handoff | 2026-08-30 -->
# porthole should build packages, not only kernels

> **STATUS 2026-08-30 — the verb landed.** `porthole pkg build|watch|status|
> outdated` exists, the four numbered requirements below are implemented and
> tested, and both defects are fixed. Two things surfaced only by running it:
> pmbootstrap prefixes every relayed line with `[HH:MM:SS]`, so the ninja
> `[N/M]` match had to strip it or the "real percentage" would have been
> permanently unknown; and the last six lines of a failed `pmbootstrap build`
> are its version banner, so the failure tail now prefers lines that name a
> cause. **What is still open is the WebKit work at the bottom of this file** —
> the patch is written and unbuilt. `porthole pkg build webkit2gtk-6.0
> --detach` is now the way to run it, and `porthole pkg watch` the way to
> follow it without sitting on the output.

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
