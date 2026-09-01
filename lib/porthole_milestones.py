# SPDX-License-Identifier: MIT
"""The port's state: what is done, what is next, and what proves it.

A bring-up is a long sequence of things that must happen roughly in order, and
the question a porter asks all day is "where am I, and what is next". Before
this, `new-device` wrote a 26-item checklist and **nothing ever read it again**
-- so the answer lived in one person's memory.

The design rule here is that **a probe always outranks a tick.** Where the tool
can see whether something is true, what a human wrote in a markdown box is at
best a second opinion and at worst a stale claim. `brain/laws/` already says an
empty reading means unknown and never "changed"; a progress display that
believed a checkbox over the filesystem would be exactly that mistake with a
nicer font.

So each milestone carries a `probe`, and the checklist markdown is a fallback
for the ones no probe can see -- "read the laws", "first boot achieved". Where
the two disagree, the probe wins and `next` says so out loud.

Milestones are generic. A probe reads the profile, the workdir and pmaports, so
a device tree milestone works for any SoC without that SoC being named here.
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

# Verdict states, in the order `next` cares about them.
DONE, TODO, BLOCKED, UNKNOWN = "done", "todo", "blocked", "unknown"


class Verdict:
    """What a probe found, and the fact that proves it.

    `evidence` is not decoration. A milestone reported done with no evidence is
    a claim, and this whole module exists because claims were what we had.
    """

    __slots__ = ("state", "evidence")

    def __init__(self, state: str, evidence: str = ""):
        self.state, self.evidence = state, evidence

    def __repr__(self):
        return f"<{self.state}: {self.evidence}>"


def done(evidence: str) -> Verdict:
    return Verdict(DONE, evidence)


def todo(evidence: str = "") -> Verdict:
    return Verdict(TODO, evidence)


def blocked(reason: str) -> Verdict:
    return Verdict(BLOCKED, reason)


def unknown() -> Verdict:
    """No probe can see this one. The checklist tick is the only signal."""
    return Verdict(UNKNOWN)


class Milestone:
    __slots__ = ("id", "phase", "title", "why", "how", "playbook", "probe",
                 "safe")

    def __init__(self, id, phase, title, why="", how="", playbook="",
                 probe=None, safe=False):
        self.id, self.phase, self.title = id, phase, title
        self.why, self.how, self.playbook = why, how, playbook
        self.probe = probe or (lambda ctx: unknown())
        # `safe` gates whether `next` may OFFER to run `how`. Read-only or
        # trivially reversible on the host only. Anything that flashes, writes
        # to the device or changes a slot is printed and never offered --
        # the same boundary `porthole sandbox` draws.
        self.safe = safe


# --------------------------------------------------------------- the probes --
#
# Every probe takes a Ctx and returns a Verdict. They must be cheap (this runs
# on every `next` and inside `brief`) and must never touch the device: `brief
# --no-device` has to work offline, and a probe that hangs on a dead phone
# would make the one command an agent runs first the one that hangs.

def _cfg(ctx, key, default=""):
    try:
        return ctx.cfg.get(key, default) or default
    except Exception:  # noqa: BLE001 -- a broken profile must not break `next`
        return default


def _workdir(ctx) -> pathlib.Path | None:
    path = _cfg(ctx, "PORTHOLE_WORKDIR")
    if not path:
        return None
    p = pathlib.Path(path).expanduser()
    return p if p.is_dir() else None


_TEMPLATE_CACHE: dict = {}


def _template_defaults(ctx) -> dict[str, str]:
    """The values profiles/_template/device.env ships.

    Needed because a key can be non-empty and still tell you nothing: the
    template ships PORTHOLE_REBOOT_BUDGET_S="120" as a documented starting
    point, and reporting that as a MEASURED budget would be precisely the
    confidently-wrong-in-the-optimistic-direction failure this module exists to
    prevent. A value still equal to the shipped default has not been
    established; it has been left alone.
    """
    root = str(ctx.root)
    if root not in _TEMPLATE_CACHE:
        vals = {}
        path = pathlib.Path(root) / "profiles" / "_template" / "device.env"
        try:
            for line in path.read_text(errors="replace").splitlines():
                m = re.match(r'^([A-Z_]+)="([^"]*)"', line.strip())
                if m:
                    vals[m.group(1)] = m.group(2)
        except OSError:
            pass
        _TEMPLATE_CACHE[root] = vals
    return _TEMPLATE_CACHE[root]


def _established(ctx, key) -> bool:
    """Set, and not merely the template's shipped default."""
    value = _cfg(ctx, key)
    if not value:
        return False
    return value != _template_defaults(ctx).get(key, object())


def _keys(ctx, *names):
    """done() when every key is set, todo() naming the ones that are not."""
    missing = [n for n in names if not _cfg(ctx, n)]
    if missing:
        return todo(f"unset: {', '.join(missing)}")
    return done(", ".join(f"{n}={_cfg(ctx, n)}" for n in names))


# Directories that are enormous and never hold a device tree we wrote. Pruning
# them is the difference between reading a few thousand entries and reading two
# million: a kernel tree plus its build output under one workdir is normal, and
# a probe that walks all of it is a probe that hangs the caller.
PRUNE = {".git", ".ccache", "out", "build", ".output", "node_modules",
         "__pycache__", ".venv", "target", "objs", ".cache", "_build"}

# Where a device tree actually lives, cheapest first. The kernel convention is
# arch/<arch>/boot/dts; ours is dts/. Looking in the likely places first means
# the walk below is a fallback rather than the normal path.
DTS_HINTS = ("", "dts", "arch/arm64/boot/dts", "arch/arm/boot/dts",
             "arch/riscv/boot/dts", "kernel", "linux/arch/arm64/boot/dts")

MAX_DIRS = 4000


def _find(work: pathlib.Path, name: str, hints=DTS_HINTS, walk=True):
    """First file called `name` under `work`, or None -- WITHOUT walking
    everything.

    `work.rglob(name)` reads every directory beneath the workdir. A device repo
    with a kernel tree in it has ~2 million files, so that took minutes and
    froze the TUI on its very first frame. It also cost nothing to avoid: a
    device tree is in one of a handful of places, and if it is not, a bounded
    walk that gives up is a better answer than one that never returns.

    Returns (path, exhausted). `exhausted=True` means the search hit its
    ceiling, so "not found" means "not found cheaply" and the caller must say
    so rather than reporting an absence it did not establish.
    """
    for hint in hints:
        base = work / hint if hint else work
        if not base.is_dir():
            continue
        direct = base / name
        if direct.is_file():
            return direct, False
        try:
            for found in base.glob(f"*/{name}"):
                if found.is_file():
                    return found, False
        except OSError:
            pass

    if not walk:
        # Absence is the NORMAL state for this caller (a build artefact that
        # has not been built yet), and walking thousands of directories to
        # confirm a routine "no" cost 367ms of a 400ms probe run. Looking where
        # the thing would be is the whole search.
        return None, False

    seen = 0
    stack = [work]
    while stack and seen < MAX_DIRS:
        current = stack.pop()
        seen += 1
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in PRUNE and not entry.name.startswith("."):
                            stack.append(pathlib.Path(entry.path))
                    elif entry.name == name:
                        return pathlib.Path(entry.path), False
        except OSError:
            continue
    return None, bool(stack)


def probe_soc(ctx):
    return _keys(ctx, "PORTHOLE_SOC")


def probe_sibling(ctx):
    """Has anyone done this silicon? The highest-leverage question there is."""
    soc = _cfg(ctx, "PORTHOLE_SOC")
    if not soc:
        return blocked("PORTHOLE_SOC is not set yet")
    try:
        import porthole_pmaports as pmap
        pmaports = pmap.find_pmaports(ctx.cfg)
        if not pmaports:
            return blocked("no pmaports checkout found")
        devices = pmap.load_devices(pmaports)
        # Zero devices means the read failed, not that the silicon is new.
        # "no sibling" drawn from an empty list is an artefact of a broken
        # checkout wearing the evidence of a finding.
        if not devices:
            return blocked(f"{pmaports} lists no devices; "
                           f"'no sibling' would be an artefact of that")
        codename = _cfg(ctx, "PORTHOLE_CODENAME") or _cfg(ctx, "PORTHOLE_DEVICE")
        matches = [d for d in devices
                   if d.soc and d.soc.endswith(soc) and d.codename != codename]
        if matches:
            return done(f"{len(matches)} sibling(s): "
                        f"{', '.join(d.codename for d in matches[:3])}")
        # Not a failure. New silicon has no siblings BY DEFINITION, and that is
        # the case this toolkit exists for -- saying "todo" would imply there is
        # something to go and find.
        return done(f"no pmaports device on {soc} — new silicon, nothing to inherit")
    except Exception as exc:  # noqa: BLE001
        return blocked(f"could not read pmaports: {exc}")


def probe_offsets(ctx):
    return _keys(ctx, "PORTHOLE_BOOTIMG_BASE", "PORTHOLE_BOOTIMG_PAGESIZE",
                 "PORTHOLE_BOOTIMG_HEADER_VERSION")


def probe_dtb_named(ctx):
    return _keys(ctx, "PORTHOLE_DTB")


def probe_dts_exists(ctx):
    """A device tree source in the working repo, named by the profile."""
    work = _workdir(ctx)
    if not work:
        return blocked("PORTHOLE_WORKDIR is not set")
    dtb = _cfg(ctx, "PORTHOLE_DTB")
    stem = pathlib.Path(dtb).name if dtb else ""
    if not stem:
        return blocked("PORTHOLE_DTB is not set")
    hit, exhausted = _find(work, f"{stem}.dts")
    if hit:
        return done(str(hit.relative_to(work)))
    if exhausted:
        # Say what was actually established. "Not found in the first few
        # thousand directories" is not "absent", and reporting the second when
        # you only checked the first is the class of lie this module exists to
        # stop.
        return todo(f"no {stem}.dts in the usual places (search capped at "
                    f"{MAX_DIRS} directories)")
    return todo(f"no {stem}.dts under {work}")


def probe_dts_compiles(ctx):
    """Compiled at least once, evidenced by a dtb next to the source.

    Deliberately does NOT run dtc: `next` is called constantly, including from
    `brief`, and a compile is seconds not milliseconds. The port's own
    verify script is where compilation is proved; this reports whether it has
    ever succeeded.
    """
    work = _workdir(ctx)
    if not work:
        return blocked("PORTHOLE_WORKDIR is not set")
    dtb = _cfg(ctx, "PORTHOLE_DTB")
    stem = pathlib.Path(dtb).name if dtb else ""
    if not stem:
        return blocked("PORTHOLE_DTB is not set")
    source, _ = _find(work, f"{stem}.dts")
    newest_src = source.stat().st_mtime if source else 0
    # A compiled blob sits beside its source or in a build directory next to
    # it -- never somewhere only an exhaustive walk would find.
    hints = DTS_HINTS
    if source:
        rel = source.parent.relative_to(work)
        hints = (str(rel), str(rel / ".."), *DTS_HINTS)
    for name in (f"{stem}.dtb", f"{stem}.dtbo"):
        hit, _ = _find(work, name, hints=hints, walk=False)
        if hit:
            st = hit.stat()
            # A file merely NAMED .dtb proves nothing. A real one has a
            # header and some content, and one older than its own source
            # describes a tree that no longer exists.
            if st.st_size < 1024:
                return todo(f"{hit.relative_to(work)} is {st.st_size} bytes — "
                            f"not a device tree blob")
            if newest_src and st.st_mtime < newest_src:
                return todo(f"{hit.relative_to(work)} is older than its "
                            f"source — recompile before trusting it")
            return done(f"{hit.relative_to(work)} ({st.st_size // 1024} KiB)")
    return todo("no compiled dtb in the working repo")


def probe_verify_script(ctx):
    """An offline gate the port can actually run.

    Generic on purpose: any port benefits from one command that checks
    everything checkable without hardware, and naming it `verify.sh` in the
    working repo is the cheapest possible convention.
    """
    work = _workdir(ctx)
    if not work:
        return blocked("PORTHOLE_WORKDIR is not set")
    for name in ("verify.sh", "verify"):
        path = work / name
        if path.is_file():
            # A gate nobody can execute is not a gate. Reporting `done` with
            # the evidence "NOT executable" was this module's own failure mode
            # -- a confident claim contradicted by the fact printed beside it.
            if not path.stat().st_mode & 0o111:
                return todo(f"{name} exists but is not executable")
            return done(f"{name} (executable)")
    return todo(f"no verify.sh in {work}")


def probe_device_pkg(ctx):
    return _pmaports_pkg(ctx, _cfg(ctx, "PORTHOLE_DEVICE_PKG")
                         or f"device-{_cfg(ctx, 'PORTHOLE_DEVICE')}")


def probe_builds(ctx):
    """Did the packages actually build? Look for the apks, not for a tick.

    Reported from the redfin port: `builds` was the one packaging milestone
    with no probe, so a session that had genuinely produced both apks still
    had to hand-tick a checklist -- and the design rule here is that a probe
    always outranks a tick. The evidence is sitting in the package dir at a
    filename that states the exact pkgver-pkgrel, which is a stronger claim
    than "someone ticked a box" and cannot go stale after a pkgrel bump.
    """
    device = _cfg(ctx, "PORTHOLE_DEVICE_PKG")
    kernel = _cfg(ctx, "PORTHOLE_KERNEL_PKG")
    wanted = [name for name in (device, kernel) if name and name != "device-"]
    if not wanted:
        return todo("no device or kernel package named in the profile")
    try:
        import porthole_cmd_pkg as pkgverb
        import porthole_pmaports as pmap

        pmaports = pmap.find_pmaports(ctx.cfg)
        if not pmaports:
            return blocked("no pmaports checkout found")
        arch = _cfg(ctx, "PORTHOLE_ARCH") or "aarch64"
        # Both package dirs: a build may have run in the workspace or on the
        # host, and the milestone is about whether the apk exists, not about
        # which of the two produced it.
        found, missing = [], []
        for name in wanted:
            directory = pkgverb.find_aport(pmaports, name)
            if directory is None:
                missing.append(f"{name} (no aport)")
                continue
            fields = pkgverb.apkbuild_fields(
                (directory / "APKBUILD").read_text(errors="replace"))
            hit = None
            for packages in _package_dirs(ctx):
                candidate = pkgverb.expected_apk(packages, arch, fields)
                if candidate is not None and candidate.exists():
                    hit = candidate
                    break
            (found if hit else missing).append(hit.name if hit else name)
        if missing:
            return todo(f"not built: {', '.join(missing)}")
        return done(", ".join(found))
    except Exception as exc:  # noqa: BLE001
        return blocked(f"could not read the package dir: {exc}")


def _package_dirs(ctx):
    """Where a built apk can land: the workspace's work dir and the host's."""
    import pathlib as _p

    out = []
    try:
        import porthole_cmd_sandbox as sandbox
        out.append(sandbox._sandbox_pmb(ctx.cfg) / "packages")
    except Exception:  # noqa: BLE001
        pass
    host = _cfg(ctx, "PORTHOLE_PMB_DIR") or "~/.local/var/pmbootstrap"
    out.append(_p.Path(host).expanduser() / "packages")
    return [d for d in out if d.is_dir()]


def verdict_for_series(problems, count: int = 0):
    """A verdict from a series check. Pure, so the severity rule is testable.

    BLOCKED rather than TODO: a series that cannot apply is a precondition of
    the whole packaging phase, not a task anyone can pick up. `next` renders
    BLOCKED in the "in the way" list and TODO as the next action, and offering
    "fix this" as the next action for a patch nobody has diagnosed yet is the
    wrong instruction.
    """
    fatal = [msg for kind, msg in problems
             if kind not in ("duplicate", "stripped")]
    if fatal:
        return blocked("the series does not apply: " + "; ".join(fatal[:2]))
    return done("{} patch(es) check out".format(count) if count
                else "the series checks out")


def probe_series_applies(ctx):
    """Does the kernel aport's patch series actually apply?

    Read off disk, never built and never on the device: this runs inside every
    `next` and every `brief`, including `brief --no-device`.

    It exists because the taimen series was broken for the life of a port and
    the only way to find out was a two-minute build that failed naming
    something else, so work drifted to a tree that had none of the patches.
    """
    pkgname = _cfg(ctx, "PORTHOLE_KERNEL_PKG")
    if not pkgname:
        return unknown()
    try:
        import porthole_cmd_aports as aports

        pmaports = aports._pmaports(ctx)
        pkg_dir = aports._pkg_dir(pmaports, pkgname)
    except Exception:  # noqa: BLE001 -- no pmaports is not a broken series
        return unknown()
    if pkg_dir is None:
        return unknown()
    count = len(list(pkg_dir.glob("*.patch")))
    return verdict_for_series(aports._series_problems(pkg_dir), count)


def probe_kernel_pkg(ctx):
    name = _cfg(ctx, "PORTHOLE_KERNEL_PKG")
    if not name:
        return todo("PORTHOLE_KERNEL_PKG is not set")
    return _pmaports_pkg(ctx, name)


def _pmaports_pkg(ctx, name):
    if not name or name == "device-":
        return todo("no package name in the profile")
    try:
        import porthole_pmaports as pmap
        pmaports = pmap.find_pmaports(ctx.cfg)
        if not pmaports:
            return blocked("no pmaports checkout found")
        for pattern in (f"*/*/{name}", f"*/{name}"):
            for path in pmaports.glob(pattern):
                if (path / "APKBUILD").is_file():
                    return done(str(path.relative_to(pmaports)))
        return todo(f"{name} does not exist in pmaports")
    except Exception as exc:  # noqa: BLE001
        return blocked(f"could not read pmaports: {exc}")


def probe_slot_policy(ctx):
    """A/B safety, and the reason it is not optional.

    Where the device genuinely has no slots this is done-by-not-applying:
    reporting "todo" forever for absent hardware is how a checklist teaches
    people to skim it.

    But "no slots" must be ESTABLISHED, not assumed. The template ships
    PORTHOLE_HAS_AB_SLOTS="0" and the config layer defaults it to "0", so
    before this fix every freshly scaffolded device silently auto-completed the
    one milestone whose own `why` says "after the first bad flash is too late
    to decide this". A safety milestone that completes itself is worse than one
    that does not exist, because it also tells you it is handled.
    """
    if not _cfg(ctx, "PORTHOLE_SLOTS_PROBED"):
        return todo("slots never probed — HAS_AB_SLOTS is still the shipped "
                    "default; `fastboot getvar all` is what answers this")
    if _cfg(ctx, "PORTHOLE_HAS_AB_SLOTS") != "1":
        return done("no A/B slots on this device (probed)")
    if _cfg(ctx, "PORTHOLE_SLOT_FORBIDDEN") or _cfg(ctx, "PORTHOLE_ACTIVE_SLOT"):
        return done(f"forbidden={_cfg(ctx, 'PORTHOLE_SLOT_FORBIDDEN') or '(none)'}, "
                    f"active={_cfg(ctx, 'PORTHOLE_ACTIVE_SLOT') or '(unset)'}")
    return todo("A/B device with neither ACTIVE_SLOT nor SLOT_FORBIDDEN set")


def probe_identity(ctx):
    try:
        configured = ctx.cfg.source("PORTHOLE_USER") != "default"
    except Exception:  # noqa: BLE001
        return todo("no config")
    if configured:
        import porthole
        return done(porthole.resolve_phone(ctx.cfg))
    return todo("porthole init has not been run")


# How old a cached device state may be and still be reported as a fact. Past
# this the honest answer is that nobody has looked recently.
STATE_MAX_AGE_S = 300.0


def reachable_verdict(forced: str, cached):
    """Is the device answering, from evidence that already exists? Pure.

    `forced` is TK_DEVICE_STATE / PORTHOLE_DEVICE_STATE -- a value a human or
    a tool SET, so a positive reading is an assertion and the evidence says
    so. `cached` is `(state, age)` from Device.cached_state, or None.

    Never probes. `brief --no-device` has to work offline and a probe that
    hangs on a dead phone would make the one command every agent runs first
    the one that hangs. A cache read is not a probe.
    """
    if forced:
        if forced.upper() in ("BOOTED", "SSH"):
            return done("declared {} (asserted, not probed — "
                        "`porthole doctor` measures it)".format(forced))
        return blocked("device state is {}, not BOOTED".format(forced))
    if cached is None:
        return blocked("device state not probed in the last "
                       "{:.0f}m — run `porthole doctor`".format(
                           STATE_MAX_AGE_S / 60))
    state, age = cached
    if state.upper() in ("BOOTED", "SSH"):
        return done("{} (probed {} ago)".format(state, _ago(age)))
    return blocked("device was {} when last probed, {} ago".format(
        state, _ago(age)))


def _ago(seconds: float) -> str:
    """`62s`, `14m`, `3h` -- short enough to sit inside an evidence string."""
    seconds = int(max(0, seconds))
    if seconds < 90:
        return "{}s".format(seconds)
    if seconds < 5400:
        return "{}m".format(seconds // 60)
    return "{}h".format(seconds // 3600)


def probe_reachable(ctx):
    """Is the device answering? Read from the cache doctor and brief write.

    Never probes -- see the note above. `forced` is a human's or a tool's
    assertion; absent that, the last measured state comes from the cache
    `Device.state()` writes on every real probe. A cache read is a file
    read, not a probe: it never touches the device.
    """
    forced = _cfg(ctx, "TK_DEVICE_STATE") or _cfg(ctx, "PORTHOLE_DEVICE_STATE")
    cached = None
    if not forced:
        try:
            max_age = float(_cfg(ctx, "PORTHOLE_STATE_MAX_AGE_S")
                            or STATE_MAX_AGE_S)
            import porthole

            cached = porthole.Device(ctx.cfg).cached_state(max_age)
        except Exception:  # noqa: BLE001 -- a bad cache must not break `next`
            cached = None
    return reachable_verdict(forced, cached)


def probe_measured_budget(ctx):
    """Measured, never estimated -- the profile says so in its own comment."""
    key = "PORTHOLE_REBOOT_BUDGET_S"
    if _established(ctx, key):
        return done(f"{key}={_cfg(ctx, key)}")
    if _cfg(ctx, key):
        return todo(f"{key} is still the template default "
                    f"({_cfg(ctx, key)}s) — measure it, do not inherit it")
    return todo(f"{key} is unset")


def probe_brain_scoped(ctx):
    """Has anything been written down for THIS device or SoC?

    The project's stated purpose is to share what a port cost you. A port that
    has produced no scoped note has either learned nothing or written nothing
    down, and the second is much more likely.
    """
    root = pathlib.Path(ctx.root)
    codename = _cfg(ctx, "PORTHOLE_DEVICE")
    soc = _cfg(ctx, "PORTHOLE_SOC")
    if not codename:
        return blocked("no device selected")
    wanted = {f"device:{codename}"} | ({f"soc:{soc}"} if soc else set())
    hits = []
    for path in (root / "brain").rglob("*.md"):
        try:
            head = path.read_text(errors="replace")[:600]
        except OSError:
            continue
        m = re.search(r"^scope:\s*(.+)$", head, re.M)
        if m and m.group(1).strip() in wanted:
            hits.append(path.stem)
    if hits:
        return done(f"{len(hits)} scoped note(s): {', '.join(hits[:3])}")
    return todo("no brain note scoped to this device or SoC yet")


# ------------------------------------------------------------------- table --
#
# Order is the dependency order. There is no graph: this sequence IS the plan,
# and a milestone that genuinely cannot start says so through its own probe.

MILESTONES = [
    # -- 0. before the device ------------------------------------------------
    Milestone(
        "read-laws", "before the device",
        "Read the laws — ten notes, and they are what stops you wasting weeks",
        why="Every one of them was learned by losing a session to it.",
        how="porthole brain search --severity law",
        playbook="brain/laws/", safe=True),
    Milestone(
        "soc", "before the device", "SoC identified in the profile",
        why="Nearly every later answer is looked up by SoC.",
        how="porthole soc list",
        playbook="brain/playbooks/00-device-protocol.md",
        probe=probe_soc, safe=True),
    Milestone(
        "sibling", "before the device",
        "Closest pmaports sibling found (or established that there is none)",
        why="A sibling's deviceinfo holds values that are known to boot. "
            "Guessing them costs days.",
        how="porthole soc", playbook="brain/playbooks/00-device-protocol.md",
        probe=probe_sibling, safe=True),
    Milestone(
        "identity", "before the device", "Host identity configured",
        why="Without it every tool resolves the wrong device, or none.",
        how="porthole init", probe=probe_identity, safe=True),

    # -- 1. first boot -------------------------------------------------------
    Milestone(
        "offsets", "first boot", "Boot image offsets sourced, not guessed",
        why="A wrong offset produces a device that does not boot and gives no "
            "diagnostic at all.",
        how="porthole soc inherit <sibling>",
        playbook="brain/playbooks/10-first-boot.md",
        probe=probe_offsets, safe=True),
    Milestone(
        "ram-boot-known", "first boot",
        "Established whether this device can RAM-boot at all",
        why="`fastboot boot` is the usual safety net and it is NOT universal. "
            "Where it is absent, every test image is a flash.",
        how="porthole brain search --scope device:<codename>",
        playbook="brain/playbooks/10-first-boot.md", safe=True),
    Milestone(
        "slot-policy", "first boot", "A/B slot policy set before the first write",
        why="Recovery depends on a known-good image on the other slot. After "
            "the first bad flash is too late to decide this.",
        how="porthole config | grep SLOT",
        playbook="brain/playbooks/10-first-boot.md",
        probe=probe_slot_policy, safe=True),
    Milestone(
        "dtb-named", "first boot", "Device tree named in the profile",
        why="The build and the boot image both resolve it from here.",
        how="porthole dts sources", probe=probe_dtb_named, safe=True),
    Milestone(
        "dts", "first boot", "Device tree source exists in the working repo",
        why="A mainline DTS is layered; the SoC dtsi may already be written.",
        how="porthole dts sources", playbook="brain/playbooks/25-device-tree.md",
        probe=probe_dts_exists, safe=True),
    Milestone(
        "dts-compiles", "first boot", "Device tree compiles",
        why="dtc is the only thing that catches an unresolved phandle or an "
            "overlapping region.",
        how="porthole dts check", playbook="brain/playbooks/25-device-tree.md",
        probe=probe_dts_compiles, safe=True),
    Milestone(
        "verify", "first boot", "An offline gate exists (verify.sh)",
        why="One command that checks everything checkable without hardware is "
            "what keeps a locked or absent device from stopping all work.",
        how="write verify.sh in the working repo",
        probe=probe_verify_script, safe=False),
    Milestone(
        "console", "first boot",
        "A channel out that works before the display: earlycon, UART or gadget",
        why="A boot that fails before console handover says nothing on screen. "
            "A UART needs no userspace at all.",
        how="porthole serial hardware",
        playbook="brain/playbooks/10-first-boot.md", safe=True),
    Milestone(
        "first-boot", "first boot", "The kernel boots far enough to say so",
        why="Everything after this is debugging; everything before is guessing.",
        how="porthole run boot-probe.sh",
        playbook="brain/playbooks/10-first-boot.md", safe=False),

    # -- 2. storage, usb, ssh ------------------------------------------------
    Milestone(
        "storage", "storage, usb, ssh", "Storage probes and partitions enumerate",
        why="Without it the rootfs never appears and the boot looks exactly "
            "like a hang.",
        how="porthole run tk-sysstate.sh",
        playbook="brain/playbooks/20-storage-usb-ssh.md",
        probe=probe_reachable, safe=False),
    Milestone(
        "ssh", "storage, usb, ssh", "ssh answers on the device",
        why="Almost every tool in the box needs it.",
        how="porthole doctor",
        playbook="brain/playbooks/20-storage-usb-ssh.md",
        probe=probe_reachable, safe=True),
    Milestone(
        "sudo", "storage, usb, ssh", "Passwordless sudo installed ON THE DEVICE",
        why="Tools call `sudo -n`, which never prompts — it just fails. "
            "Without this the whole toolbox silently does nothing.",
        how="porthole init      # prints the snippet to run on the device",
        playbook="brain/playbooks/20-storage-usb-ssh.md", safe=True),
    Milestone(
        "doctor", "storage, usb, ssh", "porthole doctor is clean",
        why="It is the difference between a broken port and a broken host.",
        how="porthole doctor", probe=probe_reachable, safe=True),
    Milestone(
        "budget", "storage, usb, ssh",
        "Reboot budget MEASURED, not estimated",
        why="Every unattended wait uses it. An estimate that is too short "
            "reports failures that did not happen.",
        how="porthole config PORTHOLE_REBOOT_BUDGET_S",
        probe=probe_measured_budget, safe=True),

    # -- 3. packaging --------------------------------------------------------
    Milestone(
        "device-pkg", "packaging", "pmaports device package exists",
        why="Nothing installs without it.",
        how="porthole aports new <codename>",
        probe=probe_device_pkg, safe=True),
    Milestone(
        "kernel-pkg", "packaging", "pmaports kernel package exists",
        why="It pins the tree and the config the port actually builds.",
        how="porthole aports status", probe=probe_kernel_pkg, safe=True),
    Milestone(
        "series-applies", "packaging", "The kernel patch series applies",
        why="A series that cannot apply makes every aport build fail for a "
            "reason nothing surfaces, and work drifts to a tree that does not "
            "carry the patches. taimen lost a whole subsystem this way.",
        how="porthole aports lint", probe=probe_series_applies, safe=True),
    Milestone(
        "builds", "packaging", "The packages build",
        why="A package that builds on your host is the first thing anyone else "
            "can reproduce.",
        how="porthole pkg build <aport>", probe=probe_builds, safe=False),

    # -- 4. subsystems -------------------------------------------------------
    Milestone(
        "display", "subsystems", "Display brings up",
        why="Until it does, every boot verdict comes from a log rather than a "
            "screen — and a screen can lie either way.",
        how="porthole brain search display",
        playbook="brain/playbooks/30-display.md", safe=True),
    Milestone(
        "suspend", "subsystems", "Suspend and resume survive a cycle",
        why="This dominates whether the port is a daily driver.",
        how="porthole run --lock tk-suspend-cycle.sh",
        playbook="brain/playbooks/40-suspend.md", safe=False),
    Milestone(
        "radios", "subsystems", "Wifi, bluetooth, modem",
        why="The last things that make a phone a phone.",
        how="porthole brain search wifi",
        playbook="brain/playbooks/50-wifi-bt-modem.md", safe=True),

    # -- 5. give it back -----------------------------------------------------
    Milestone(
        "brain", "upstream", "What this port cost you is written down",
        why="This project exists to share knowledge, not to fix one phone. A "
            "session that learned something and wrote nothing down is unfinished.",
        how="porthole brain new <kebab-id> --section traps",
        playbook="brain/README.md", probe=probe_brain_scoped, safe=True),
    Milestone(
        "upstream", "upstream", "Commits written as if being sent today",
        why="Anything that benefits other SoCs is worth splitting out while you "
            "still remember why.",
        how="porthole aports patch",
        playbook="brain/playbooks/90-upstreaming.md", safe=True),
]

PHASES = []
for _m in MILESTONES:
    if _m.phase not in PHASES:
        PHASES.append(_m.phase)

BY_ID = {m.id: m for m in MILESTONES}


# --------------------------------------------------------------- checklist --

MARKER = re.compile(r"<!--\s*([a-z0-9-]+)\s*-->")
ITEM = re.compile(r"^-\s*\[( |x|X)\]\s*(.*)$", re.M)


def read_ticks(path) -> dict[str, bool]:
    """Which boxes are ticked, keyed by milestone id.

    Items with no marker are ignored rather than guessed at: a checklist
    predating the marker convention should produce "no manual signal", not a
    wrong one. `--regenerate` is how such a file gets markers.
    """
    try:
        text = pathlib.Path(path).read_text(errors="replace")
    except OSError:
        return {}
    out = {}
    for mark, body in ITEM.findall(text):
        m = MARKER.search(body)
        if m:
            out[m.group(1)] = mark.lower() == "x"
    return out


def render_checklist(codename: str, ticks: dict[str, bool] | None = None) -> str:
    """Generate checklist.md from the table, preserving ticks.

    Generated rather than hand-written so a milestone added today reaches every
    existing device, instead of only the ones created after it.
    """
    ticks = ticks or {}
    out = [f"# {codename} — bring-up checklist", "",
           "Generated by porthole from the milestone table. `porthole next`",
           "reads it, and **a probe always outranks a tick**: where the tool can",
           "check something itself, this file is a second opinion.",
           "",
           "    porthole next              where am I, and what is next",
           "    porthole next --regenerate rebuild this file, keeping ticks",
           ""]
    phase = None
    for i, m in enumerate(MILESTONES):
        if m.phase != phase:
            phase = m.phase
            out += ["", f"## {phase}", ""]
        box = "x" if ticks.get(m.id) else " "
        out.append(f"- [{box}] {m.title} <!-- {m.id} -->")
        if m.why:
            out.append(f"      {m.why}")
    out += ["", "---", "",
            "Ticking a box that a probe can check does nothing: the probe wins.",
            "Tick the ones nothing can see for you — reading the laws, the",
            "first boot, the console you got working at 3am.", ""]
    return "\n".join(out)


def match_legacy_ticks(text: str) -> tuple[dict[str, bool], list[str]]:
    """Carry ticks over from a checklist written before markers existed.

    Matched on the first few significant words of the title, which is what
    survives someone rewording an item. Anything unmatched is RETURNED, not
    dropped -- silently losing a tick would make the migration untrustworthy,
    and this runs once per port.
    """
    # Three letters, not four: "ssh" and "usb" are the whole content of some
    # items, and a four-letter floor silently dropped them. Function words are
    # excluded instead, because those are what a length floor was really for.
    NOISE = {"the", "and", "not", "for", "with", "that", "this", "its", "are",
             "was", "you", "its", "into", "from", "before", "your", "them"}

    def key(s):
        words = [w for w in re.findall(r"[a-z]{3,}", s.lower()) if w not in NOISE]
        return set(words[:8])

    ticked, unmatched = {}, []
    for mark, body in ITEM.findall(text):
        if mark.lower() != "x":
            continue
        body = MARKER.sub("", body).strip()
        best, score = None, 0
        for m in MILESTONES:
            overlap = len(key(body) & key(m.title))
            if overlap > score:
                best, score = m, overlap
        if best and score >= 2:
            ticked[best.id] = True
        else:
            unmatched.append(body[:70])
    return ticked, unmatched


# ------------------------------------------------------------------- state --

def evaluate(ctx, ticks: dict[str, bool] | None = None) -> list[dict]:
    """Every milestone's state, with its source and its evidence."""
    ticks = ticks if ticks is not None else {}
    rows = []
    for m in MILESTONES:
        verdict = m.probe(ctx)
        ticked = ticks.get(m.id, False)

        if verdict.state == DONE:
            state, source = DONE, "derived"
        elif verdict.state == UNKNOWN:
            state, source = (DONE, "manual") if ticked else (TODO, "manual")
        elif verdict.state == BLOCKED:
            # A tick cannot unblock a precondition, but it can record that a
            # human did the thing before the precondition became unobservable.
            state, source = (DONE, "manual") if ticked else (BLOCKED, "derived")
        else:
            state, source = TODO, "derived"

        stale = ticked and verdict.state == TODO
        rows.append({
            "id": m.id, "phase": m.phase, "title": m.title,
            "state": state, "source": "stale" if stale else source,
            "evidence": verdict.evidence, "ticked": ticked,
            "why": m.why, "how": m.how, "playbook": m.playbook,
            "safe": m.safe,
        })
    return rows


def summarise(rows: list[dict]) -> dict:
    """Progress, the next action, and everything getting in the way."""
    done_n = sum(1 for r in rows if r["state"] == DONE)
    nxt = next((r for r in rows if r["state"] == TODO), None)
    phase = nxt["phase"] if nxt else (rows[-1]["phase"] if rows else "")
    return {
        "progress": {
            "done": done_n, "total": len(rows),
            "percent": round(100 * done_n / len(rows)) if rows else 0,
            "phase": phase,
            "phase_index": (PHASES.index(phase) + 1) if phase in PHASES else 0,
            "phases": len(PHASES),
        },
        "next": ({"id": nxt["id"], "title": nxt["title"], "why": nxt["why"],
                  "command": nxt["how"], "playbook": nxt["playbook"],
                  "safe": nxt["safe"], "evidence": nxt["evidence"]}
                 if nxt else None),
        "blocked": [{"id": r["id"], "title": r["title"], "reason": r["evidence"]}
                    for r in rows if r["state"] == BLOCKED],
        "stale": [{"id": r["id"], "title": r["title"], "detail": r["evidence"]}
                  for r in rows if r["source"] == "stale"],
    }
