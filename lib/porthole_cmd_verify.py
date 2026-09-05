#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole verify` -- every check that runs with no device attached.

**A locked or absent device is the normal day-one state.** cheetah is
bootloader-locked; taimen spent weeks where the only honest question was "does
this compile and did I invent anything". So the offline gate is the one thing
that works on day one -- and it was the one thing porthole neither scaffolded
nor ran.

The `verify` milestone demanded a `verify.sh`, and `probe_verify_script` only
checked that the file existed and was executable. cheetah wrote 60 bespoke
lines because `porthole dts check` hardcodes its include paths and has no
baseline concept. Every device would have written its own.

Three properties this must have, all learned elsewhere in this repo:

  - **A skipped check is not a pass.** cheetah's script printed PASS with exit
    0 while silently skipping the only check that reads `reg` properties.
  - **Every check names what it proved**, not just green or red.
  - **It runs the device's own script if there is one**, because a port will
    always have a check the toolkit cannot guess.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

from porthole_cli import Bail, EX_FAIL, EX_OK, child_env

OK, FAIL, SKIP = "ok", "fail", "skip"

# Warnings every mainline tree emits. Failing a port for one it inherited is
# how a check gets ignored; PORTHOLE_DTC_IGNORE adds a device's own.
DEFAULT_DTC_IGNORE = ("unique_unit_address", "simple_bus_reg",
                      "avoid_default_addr_size", "graph_child_address",
                      "unit_address_vs_reg")


class Check:
    __slots__ = ("name", "status", "detail")

    def __init__(self, name, status, detail=""):
        self.name, self.status, self.detail = name, status, detail


def _workdir(ctx) -> pathlib.Path:
    path = ctx.cfg.get("PORTHOLE_WORKDIR", "")
    if not path:
        raise Bail("PORTHOLE_WORKDIR is not set", EX_FAIL,
                   "porthole use <codename> --workdir <path>")
    work = pathlib.Path(path).expanduser()
    if not work.is_dir():
        raise Bail(f"{work} does not exist", EX_FAIL)
    return work


def _dtc() -> str:
    """dtc, wherever it is. A kernel tree builds one at scripts/dtc/dtc.

    Falls back to the workspace. dtc is absent from a stock host and the
    device-tree check is the ONLY one that reads reg properties, so without
    this `porthole verify` reports INCOMPLETE forever -- and a verdict that
    can never be clean is one people stop reading.
    """
    found = shutil.which(os.environ.get("DTC", "dtc")) or shutil.which("dtc")
    if found:
        return found
    try:
        import porthole_cmd_sandbox as sandbox

        # A container that predates this image's dtc addition is still
        # "running" -- _container_running() alone would hand back a command
        # that fails inside, turning an honest "this check did not run" into
        # a false "your device tree is broken". Same confusion `aports lint`
        # was fixed for on this branch: a tooling gap is not a check failure.
        # Probe for real before promising a working command.
        if sandbox._container_running():
            probe = subprocess.run(
                ["podman", "exec", sandbox.CONTAINER, "sh", "-c",
                 "command -v dtc"],
                capture_output=True, text=True, timeout=20)
            if probe.returncode == 0:
                # -i is not optional: `podman exec` drops stdin without it,
                # proved directly -- `echo test | podman exec CONTAINER cat`
                # prints nothing, `podman exec -i CONTAINER cat` prints
                # "test". Every call site pipes the .dts source in on stdin,
                # so a containerised dtc without -i compiles nothing and
                # reports `<stdin>:0.0 syntax error` -- a tooling gap wearing
                # the costume of a broken device tree.
                return f"podman exec -i {sandbox.CONTAINER} dtc"
    except Exception:  # noqa: BLE001
        pass
    return ""


# ------------------------------------------------------------------ checks --

def check_dts_compiles(ctx, work) -> Check:
    """The only check that reads `reg` properties.

    `dts port verify` matches on unit addresses alone, so a node at the right
    address with a wrong reg passes it clean and only dtc catches the mismatch.
    That is why this cannot be optional, and why its absence is a SKIP that
    fails the run rather than a quiet pass.
    """
    dtb = ctx.cfg.get("PORTHOLE_DTB", "")
    stem = pathlib.Path(dtb).name if dtb else ""
    if not stem:
        return Check("device tree compiles", SKIP, "PORTHOLE_DTB is not set")

    import porthole_milestones as ms
    source, _ = ms._find(work, f"{stem}.dts")
    if not source:
        return Check("device tree compiles", SKIP, f"no {stem}.dts found")

    dtc = _dtc()
    if not dtc:
        return Check("device tree compiles", SKIP,
                     "dtc is not installed — this is the ONLY check that reads "
                     "reg properties")

    # Include paths cannot be guessed. A board .dts pulls in dt-bindings from
    # a kernel tree that may not be under the workdir at all -- cheetah's is
    # not -- so the profile names them, and a tree we cannot preprocess is
    # reported as UNKNOWN rather than as broken. Claiming a device tree is
    # broken when we merely failed to find its headers is the same
    # over-claiming this command exists to stop.
    includes = []
    for hint in (ctx.cfg.get("PORTHOLE_DTS_INCLUDES", "") or "").split():
        path = pathlib.Path(hint)
        path = path if path.is_absolute() else work / hint
        if path.is_dir():
            includes += ["-I", str(path)]
    for guess in ("include", "dts", source.parent, source.parent.parent):
        path = work / guess if isinstance(guess, str) else guess
        if path.is_dir():
            includes += ["-I", str(path)]
    kernel = ctx.cfg.get("PORTHOLE_KERNEL_TREE", "") or str(work / "linux")
    for sub in ("include", f"arch/{ctx.cfg.get('PORTHOLE_ARCH_DIR', 'arm64')}"
                           "/boot/dts"):
        path = pathlib.Path(kernel, sub)
        if path.is_dir():
            includes += ["-I", str(path)]

    pre = subprocess.run(
        ["cpp", "-nostdinc", *includes, "-undef", "-x", "assembler-with-cpp",
         str(source)], capture_output=True, text=True)
    if pre.returncode != 0:
        missing = [l for l in pre.stderr.splitlines() if "No such file" in l]
        detail = (missing[0].split(":")[-1].strip() if missing
                  else "preprocessing failed")
        return Check("device tree compiles", SKIP,
                     f"{detail} — set PORTHOLE_DTS_INCLUDES")

    # dtc may be the container fallback ("podman exec porthole-sandbox dtc"),
    # a three-word string that is one argv element short if passed straight
    # through -- split it so both the host path and the podman prefix work.
    proc = subprocess.run(dtc.split() + ["-I", "dts", "-O", "dtb", "-o",
                                         os.devnull],
                          input=pre.stdout, capture_output=True, text=True)
    # Warnings need a baseline. Upstream .dtsi files emit some unconditionally
    # -- msm8998.dtsi trips simple_bus_reg, a USI block muxes i2c/serial/spi
    # onto one address by design -- and failing a port for a warning it
    # inherited teaches people to ignore the check. Only ERRORS fail by
    # default; a device adds its own tolerances with PORTHOLE_DTC_IGNORE.
    ignore = set(DEFAULT_DTC_IGNORE)
    ignore.update((ctx.cfg.get("PORTHOLE_DTC_IGNORE", "") or "").split())
    lines = [l for l in proc.stderr.splitlines() if l.strip()]
    errors = [l for l in lines
              if "Warning" not in l and "also defined at" not in l]
    warnings = [l for l in lines
                if "Warning" in l and not any(w in l for w in ignore)]

    # A BASELINE, not a growing ignore list. Upstream emits warnings a port did
    # not cause and cannot fix, and a hardcoded list of them is wrong for the
    # next device. Record what a tree emits today; fail only on what is NEW.
    baseline_path = _baseline_path(ctx, work)
    known = _read_baseline(baseline_path)
    fresh = [w for w in warnings if _fingerprint(w) not in known]

    if proc.returncode != 0 or errors:
        return Check("device tree compiles", FAIL,
                     (errors or ["dtc failed"])[0])
    if fresh:
        return Check("device tree compiles", FAIL,
                     f"{len(fresh)} new warning(s): {fresh[0][:120]}")
    note = f"{source.name}"
    if warnings:
        note += f" ({len(warnings)} baselined warning(s))"
    return Check("device tree compiles", OK, note)


def _baseline_path(ctx, work) -> pathlib.Path:
    named = ctx.cfg.get("PORTHOLE_DTC_BASELINE", "")
    if named:
        path = pathlib.Path(named)
        return path if path.is_absolute() else work / named
    return work / "dts" / "dtc-baseline.txt"


def _fingerprint(warning: str) -> str:
    """The check name and the node, without the line numbers.

    Line numbers move whenever anything above them is edited, so a baseline
    keyed on them would go stale on the first unrelated change and teach people
    to regenerate it without reading -- which is the same as not having one.
    """
    import re
    kind = re.search(r"Warning \(([^)]+)\)", warning)
    node = re.search(r": (/[^:]*):", warning)
    return f"{kind.group(1) if kind else '?'}|{node.group(1) if node else '?'}"


def _read_baseline(path) -> set:
    try:
        return {l.strip() for l in path.read_text().splitlines()
                if l.strip() and not l.startswith("#")}
    except OSError:
        return set()


def check_delta_justified(ctx, work) -> Check:
    """Every moved address cited, every sibling node covered."""
    deltas = list(work.rglob("delta.toml"))
    if not deltas:
        return Check("device tree derivation", SKIP, "no delta.toml")
    import porthole_dtsdelta as dd
    dtb = ctx.cfg.get("PORTHOLE_DTB", "")
    stem = pathlib.Path(dtb).name if dtb else ""
    doc = dd.load_toml(deltas[0])
    origin = doc.get("meta", {}).get("origin", "")
    if not origin:
        return Check("device tree derivation", FAIL,
                     "delta.toml has no origin — its citations name a file "
                     "and line in a tree nobody can identify")
    return Check("device tree derivation", OK,
                 f"{len(doc.get('block', []))} block(s), origin recorded")


def check_kconfig(ctx, work) -> Check:
    """olddefconfig drops symbols silently; the defconfig is what you asked
    for and .config is what you got."""
    asked = ctx.cfg.get("PORTHOLE_DEFCONFIG", "")
    if not asked:
        return Check("kernel config", SKIP, "PORTHOLE_DEFCONFIG is not set")
    watch = ctx.cfg.get("PORTHOLE_KCONFIG_WATCH", "")
    if not watch:
        return Check("kernel config", SKIP,
                     "PORTHOLE_KCONFIG_WATCH is empty — nothing to check")
    return Check("kernel config", SKIP,
                 "run `porthole kconfig diff` against a built tree")


def check_device_script(ctx, work) -> Check:
    """The port's own gate. A device always has a check the toolkit cannot
    guess -- cheetah checks clock-ID parity against gs101, which means nothing
    anywhere else."""
    for name in ("verify.sh", "verify"):
        script = work / name
        if not script.is_file():
            continue
        if not os.access(script, os.X_OK):
            return Check(f"{name}", FAIL, "exists but is not executable")
        env = child_env(os.environ)
        env.setdefault("PORTHOLE", str(pathlib.Path(ctx.root) / "bin" / "porthole"))
        dtc = _dtc()
        if dtc:
            env.setdefault("DTC", dtc)
        proc = subprocess.run([str(script)], cwd=str(work), env=env,
                              capture_output=True, text=True, timeout=900)
        tail = (proc.stdout + proc.stderr).strip().splitlines()
        detail = tail[-1] if tail else ""
        if proc.returncode == 0:
            return Check(name, OK, detail)
        # Exit 2 is this project's convention for "passed but incomplete".
        return Check(name, SKIP if proc.returncode == 2 else FAIL, detail)
    return Check("the port's own gate", SKIP,
                 "no verify.sh in the working repo")


CHECKS = (check_dts_compiles, check_delta_justified, check_kconfig,
          check_device_script)


def cmd_verify(args, ctx) -> int:
    work = _workdir(ctx)
    if args.update_baseline:
        return _write_baseline(ctx, work)
    results = []
    for fn in CHECKS:
        try:
            results.append(fn(ctx, work))
        except Exception as exc:  # noqa: BLE001
            results.append(Check(fn.__name__, FAIL, f"{type(exc).__name__}: {exc}"))

    failed = [c for c in results if c.status == FAIL]
    skipped = [c for c in results if c.status == SKIP]
    payload = {
        "device": ctx.cfg.get("PORTHOLE_DEVICE", ""),
        "checks": [{"name": c.name, "status": c.status, "detail": c.detail}
                   for c in results],
        "failed": len(failed), "skipped": len(skipped),
        # A skipped check is NOT a pass. cheetah's own script printed PASS with
        # exit 0 while skipping the one check that reads reg properties.
        "verdict": "fail" if failed else ("incomplete" if skipped else "pass"),
    }

    def render():
        o = ctx.out
        colour = {OK: "green", FAIL: "red", SKIP: "yellow"}
        for c in results:
            o(f"  {o.paint(c.status.ljust(4), colour[c.status])} "
              f"{c.name:<26} {o.paint(c.detail, 'grey')}")
        o.blank()
        if failed:
            o(o.paint(f"  FAILED — {len(failed)} check(s)", "red"))
        elif skipped:
            o(o.paint(f"  INCOMPLETE — {len(skipped)} check(s) did not run. "
                      f"That is not a clean bill.", "yellow"))
        else:
            o(o.paint("  PASS — every offline check", "green"))

    ctx.emit(payload, render)
    if failed:
        return EX_FAIL
    return 2 if skipped and not args.allow_skips else EX_OK


def _write_baseline(ctx, work) -> int:
    """Record the warnings this tree emits today, so future runs fail only on
    new ones. Deliberately a separate action: a check that silently absorbs
    whatever it finds is not a check."""
    check = check_dts_compiles(ctx, work)
    dtb = ctx.cfg.get("PORTHOLE_DTB", "")
    stem = pathlib.Path(dtb).name if dtb else ""
    import porthole_milestones as ms
    source, _ = ms._find(work, f"{stem}.dts") if stem else (None, False)
    if not source:
        raise Bail("no device tree to baseline", EX_FAIL)

    warnings = _collect_warnings(ctx, work, source)
    path = _baseline_path(ctx, work)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ["# dtc warnings this tree already emitted when the baseline was",
            "# taken. `porthole verify` fails only on warnings NOT listed here.",
            "# Keyed on check name and node, not line numbers -- those move.",
            ""]
    body += sorted({_fingerprint(w) for w in warnings})
    path.write_text("\n".join(body) + "\n")

    def render():
        ctx.out(f"{ctx.out.paint(ctx.out.sym('✓', 'ok'), 'green')} "
                f"baselined {len(set(_fingerprint(w) for w in warnings))} "
                f"warning(s) -> {path}")
        ctx.out.hint("porthole verify")
    return ctx.emit({"baseline": str(path), "warnings": len(warnings)}, render)


def _collect_warnings(ctx, work, source) -> list:
    dtc = _dtc()
    if not dtc:
        return []
    includes = []
    for hint in (ctx.cfg.get("PORTHOLE_DTS_INCLUDES", "") or "").split():
        path = pathlib.Path(hint)
        path = path if path.is_absolute() else work / hint
        if path.is_dir():
            includes += ["-I", str(path)]
    for guess in ("include", "dts", source.parent, source.parent.parent):
        path = work / guess if isinstance(guess, str) else guess
        if path.is_dir():
            includes += ["-I", str(path)]
    kernel = ctx.cfg.get("PORTHOLE_KERNEL_TREE", "") or str(work / "linux")
    for sub in ("include", f"arch/{ctx.cfg.get('PORTHOLE_ARCH_DIR', 'arm64')}"
                           "/boot/dts"):
        path = pathlib.Path(kernel, sub)
        if path.is_dir():
            includes += ["-I", str(path)]
    pre = subprocess.run(["cpp", "-nostdinc", *includes, "-undef", "-x",
                          "assembler-with-cpp", str(source)],
                         capture_output=True, text=True)
    if pre.returncode != 0:
        raise Bail("cannot preprocess the device tree", EX_FAIL,
                   "set PORTHOLE_DTS_INCLUDES")
    proc = subprocess.run(dtc.split() + ["-I", "dts", "-O", "dtb", "-o",
                                         os.devnull],
                          input=pre.stdout, capture_output=True, text=True)
    return [l for l in proc.stderr.splitlines() if "Warning" in l]


SPEC = {
    "verb": "verify",
    "order": 24,
    "help": "every check that runs with no device attached",
    "description": (
        "A locked or absent device is the normal day-one state, so the offline\n"
        "gate is the only thing that works on day one.\n\n"
        "A skipped check is NOT a pass: it exits 2, because a script that\n"
        "printed PASS while silently skipping the only check that reads reg\n"
        "properties is the kind of soft claim this whole toolkit exists to\n"
        "replace. Runs the port's own verify.sh too — a device always has a\n"
        "check the toolkit cannot guess."),
    "args": [
        (["--update-baseline"], {"action": "store_true",
                                 "dest": "update_baseline",
                                 "help": "record the warnings this tree emits "
                                         "today, so later runs fail only on new "
                                         "ones"}),
        (["--allow-skips"], {"action": "store_true", "dest": "allow_skips",
                             "help": "exit 0 even when a check could not run"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_verify,
    "examples": [
        "porthole verify",
        "porthole verify --json",
    ],
}
