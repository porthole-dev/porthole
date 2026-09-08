# SPDX-License-Identifier: MIT
"""`porthole disk` -- what porthole's own build state is spending, and where.

/var/home was measured at 75% (351 G of 475 G) with porthole owning 40 G of
it across TWO divergent work dirs -- `~/.local/var/porthole-sandbox` (the
workspace's own pmbootstrap, 22 G) and `~/.local/var/pmbootstrap` (the host's,
18 G) -- holding different pkgvers of the same packages
(`device-google-taimen-0.1-rNN` on the host, `-1-rNN` in the sandbox). Nothing
prunes either one and nothing reports what is growing.

READ-ONLY BY DEFAULT
    A bare `porthole disk` only reports. `--prune --yes` deletes apks this
    module's own `prunable()` names; `--retire-host --yes` deletes the WHOLE
    host work dir. Neither runs on a bare invocation.

THE TWO WORK DIRS ARE NEVER MERGED
    They hold different builds of the same packages -- picking a winner is a
    decision about which build is right, and porthole must not make that
    decision silently. `divergence()` reports the mismatch; `--retire-host`
    is the one explicit, named way to resolve it (by discarding the host
    side entirely, never by copying one apk over the other).
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

from porthole_cli import Bail, EX_FAIL

# Same shape porthole_cmd_pkg._outdated_locals already parses apk filenames
# with: strip ".apk", then take the last two "-"-separated fields as version
# and "rN". One parser for the shape, not two.


def _parse_apk(name: str):
    """(pkgname, pkgver, rel:int) for an apk filename, or None. Pure."""
    if not name.endswith(".apk"):
        return None
    parts = name[:-4].rsplit("-", 2)
    if len(parts) != 3:
        return None
    pkgname, pkgver, relpart = parts
    if not relpart.startswith("r") or not relpart[1:].isdigit():
        return None
    return pkgname, pkgver, int(relpart[1:])


_VERSION_PART = re.compile(r"\d+|\D+")


def _version_key(version: str):
    """A naive natural-sort key: numeric runs compare as numbers.

    ponytail: not a real apk version comparator (that is what `apk version`
    exists for) -- good enough to rank the common case, same base version
    bumped by pkgrel. Upgrade path: shell out to `apk version -t` if two
    differently-shaped versions of one package ever need ranking for real.
    """
    return tuple(int(p) if p.isdigit() else p
                for p in _VERSION_PART.findall(version))


def prunable(apk_names, keep_revisions=2) -> list:
    """Which of these apk filenames are safe to delete. Pure.

    Two independent rules:
      - an envkernel dev snapshot (`_pTIMESTAMP`) is prunable WHATEVER its
        age -- it sorts above `-rNN` in apk's own version order, so one left
        behind wins the world resolution and blocks every install after it
        (porthole_cmd_build.dev_snapshots, the same check a build already
        refuses on).
      - within a pkgname, only the `keep_revisions` newest survive; older
        ones are prunable.
    """
    import porthole_cmd_build as build

    doomed = set(build.dev_snapshots(apk_names))

    groups: dict = {}
    for name in apk_names:
        if name in doomed:
            continue
        parsed = _parse_apk(name)
        if not parsed:
            continue
        pkgname, pkgver, rel = parsed
        groups.setdefault(pkgname, []).append((pkgver, rel, name))

    for entries in groups.values():
        try:
            entries.sort(key=lambda e: (_version_key(e[0]), e[1]),
                         reverse=True)
        except TypeError:
            # Two differently-shaped versions of one pkgname (rare) --
            # `_version_key` can't compare int against str at the same
            # position. Fall back to plain string order rather than crash.
            entries.sort(key=lambda e: (e[0], e[1]), reverse=True)
        for _pkgver, _rel, name in entries[keep_revisions:]:
            doomed.add(name)

    return [n for n in apk_names if n in doomed]


def _ordered_versions(entries) -> list:
    """{(pkgver, rel)} -> ["pkgver-rN", ...] in real ascending version order.

    Plain `sorted()` on the joined string put `0.1-r10` before `0.1-r2` --
    visible in a real divergence row (`host 0.1-r0, 0.1-r1, 0.1-r10, ...`) --
    because "1" < "2" lexicographically. `_version_key` ranks by number, so
    this is also what makes `_range()`'s first/last a genuine min/max.
    """
    try:
        ranked = sorted(entries, key=lambda e: (_version_key(e[0]), e[1]))
    except TypeError:
        ranked = sorted(entries, key=lambda e: (e[0], e[1]))
    return [f"{pkgver}-r{rel}" for pkgver, rel in ranked]


def divergence(host_names, sandbox_names) -> list:
    """Packages present in BOTH work dirs at a different pkgver-rel. Pure.

    Each row is `(pkgname, host_versions, sandbox_versions)`, both lists in
    ascending real-version order. Reports the mismatch; chooses nothing. Two
    work dirs holding different builds of the same package is not a bug this
    function fixes -- it is a decision `--retire-host` makes explicit, on
    request, never here.
    """
    def by_pkg(names):
        out: dict = {}
        for name in names:
            parsed = _parse_apk(name)
            if parsed:
                pkgname, pkgver, rel = parsed
                out.setdefault(pkgname, set()).add((pkgver, rel))
        return out

    host, sandbox = by_pkg(host_names), by_pkg(sandbox_names)
    rows = []
    for pkgname in sorted(set(host) & set(sandbox)):
        if host[pkgname] != sandbox[pkgname]:
            rows.append((pkgname, _ordered_versions(host[pkgname]),
                        _ordered_versions(sandbox[pkgname])))
    return rows


def _range(versions, ellipsis: str = "…") -> str:
    """`"61 (0.1-r0 … 1-r34)"` -- count and span, not a 61-item wall.
    `versions` must already be in ascending order (divergence() returns it
    that way). Pure; the caller passes the ASCII fallback for a dumb tty."""
    if not versions:
        return "0"
    if len(versions) == 1:
        return f"1 ({versions[0]})"
    return f"{len(versions)} ({versions[0]} {ellipsis} {versions[-1]})"


# ------------------------------------------------------------------ shell --

def _apk_names(workdir: pathlib.Path) -> list:
    if not workdir.is_dir():
        return []
    return [p.name for p in workdir.glob("packages/*/*/*.apk")]


def _dir_size_bytes(path: pathlib.Path):
    """(bytes, error) via `du -sb` -- a real filesystem walk in C, not
    Python, over a tree measured at tens of thousands of files.

    `du` exits 1 on the FIRST unreadable entry it hits -- a stale chroot
    bind-mount, a device node under a leftover /dev, a file a rootless
    build left owned by a subordinate uid -- while still printing a correct
    total as its last stdout line. Every real pmbootstrap work dir has at
    least one such entry, so treating a nonzero exit as failure meant both
    rows always came back unreadable: measured against a live host, this
    reproduced 100% of the time. The exit code only decides what the
    reported ERROR says; it never overrides a total that parsed.
    """
    if not path.is_dir():
        return None, ""
    try:
        proc = subprocess.run(["du", "-sb", str(path)],
                              capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        return None, "du is not on PATH"
    except subprocess.TimeoutExpired:
        return None, "du timed out after 120s"
    except OSError as exc:
        return None, str(exc)
    line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    try:
        return int(line.split()[0]), ""
    except (ValueError, IndexError):
        reason = (proc.stderr.strip().splitlines()[-1] if proc.stderr.strip()
                  else f"du exited {proc.returncode} with no usable output")
        return None, reason


def _fmt_gb(n_bytes, error: str = "") -> str:
    if n_bytes is not None:
        return f"{n_bytes / 1e9:.1f} G"
    return f"size unknown ({error})" if error else "size unknown"


def _work_dirs(ctx):
    import porthole_cmd_sandbox as sandbox

    host = pathlib.Path(
        ctx.cfg.get("PORTHOLE_PMB_DIR")
        or "~/.local/var/pmbootstrap").expanduser()
    return host, sandbox._sandbox_pmb(ctx.cfg)


def _delete_apks(workdir: pathlib.Path, names) -> list:
    deleted = []
    wanted = set(names)
    for path in workdir.glob("packages/*/*/*.apk"):
        if path.name in wanted:
            try:
                path.unlink()
                deleted.append(str(path))
            except OSError:
                pass
    return deleted


def _looks_like_pmb_workdir(path: pathlib.Path):
    """(ok, reason) -- a pmbootstrap work dir has a packages/ directory and
    at least one chroot_* beside it. Checked before --retire-host's rmtree
    so a mis-resolved PORTHOLE_PMB_DIR (unset, or pointed at $HOME by a
    typo) cannot send a recursive delete at an arbitrary directory."""
    if not (path / "packages").is_dir():
        return False, f"{path} has no packages/ directory"
    try:
        has_chroot = any(p.name.startswith("chroot_") and p.is_dir()
                         for p in path.iterdir())
    except OSError as exc:
        return False, str(exc)
    if not has_chroot:
        return False, f"{path} has no chroot_* directory"
    return True, ""


def cmd_disk(args, ctx) -> int:
    import porthole_cmd_build as build

    host_dir, sandbox_dir = _work_dirs(ctx)
    host_apks, sandbox_apks = _apk_names(host_dir), _apk_names(sandbox_dir)

    if args.retire_host:
        existed = host_dir.is_dir()
        size, size_err = _dir_size_bytes(host_dir) if existed else (None, "")
        shape_ok, shape_reason = (
            _looks_like_pmb_workdir(host_dir) if existed else (True, ""))
        armed = args.yes and args.discard_host_workdir

        def render_preview():
            ctx.out.heading("would retire (delete) the HOST work dir")
            ctx.out.kv("path", str(host_dir), 6)
            ctx.out.kv("size", _fmt_gb(size, size_err) if existed
                       else "does not exist", 6)
            if existed and not shape_ok:
                ctx.out.warn(f"refusing: {shape_reason}")
            ctx.out.blank()
            ctx.out.hint(
                "porthole disk --retire-host --yes --discard-host-workdir")

        if not armed:
            payload = {"would_retire": str(host_dir), "existed": existed,
                      "bytes": size, "shape_ok": shape_ok}
            if not args.yes:
                return ctx.emit(payload, render_preview)
            # `--yes` alone confirms `--prune`, but not this -- the flag
            # that names the specific loss is required in addition, same
            # rule `porthole flash full` applies to `--replace-rootfs`.
            raise Bail(
                "retiring the host work dir deletes it outright -- that "
                "needs --discard-host-workdir as well as --yes", EX_FAIL,
                "porthole disk --retire-host --yes --discard-host-workdir")

        if existed and not shape_ok:
            raise Bail(f"{host_dir} does not look like a pmbootstrap work "
                      f"dir ({shape_reason}) -- refusing to delete it",
                      EX_FAIL,
                      "check PORTHOLE_PMB_DIR; this refusal is what stops "
                      "a mis-resolved path from being rm -rf'd")

        if existed:
            shutil.rmtree(host_dir)
        payload = {"retired": str(host_dir), "existed": existed,
                  "bytes": size}
        return ctx.emit(payload, lambda: ctx.out(
            f"retired {host_dir} ({_fmt_gb(size, size_err)})" if existed
            else f"{host_dir} did not exist"))

    host_prune = prunable(host_apks, args.keep_revisions)
    sandbox_prune = prunable(sandbox_apks, args.keep_revisions)

    if args.prune:
        if not args.yes:
            raise Bail("--prune deletes apks -- pass --yes too", EX_FAIL,
                      "porthole disk --prune --yes")
        deleted = (_delete_apks(host_dir, host_prune)
                  + _delete_apks(sandbox_dir, sandbox_prune))
        payload = {"deleted": deleted}
        return ctx.emit(payload, lambda: ctx.out(
            f"deleted {len(deleted)} apks"))

    rows = divergence(host_apks, sandbox_apks)
    host_bytes, host_err = _dir_size_bytes(host_dir)
    sandbox_bytes, sandbox_err = _dir_size_bytes(sandbox_dir)
    payload = {
        "host": {"path": str(host_dir), "bytes": host_bytes,
                 "size_error": host_err, "free_bytes": None,
                 "prunable": host_prune},
        "sandbox": {"path": str(sandbox_dir), "bytes": sandbox_bytes,
                    "size_error": sandbox_err, "prunable": sandbox_prune},
        "divergence": [{"package": n, "host": h, "sandbox": s}
                      for n, h, s in rows],
    }
    try:
        payload["host"]["free_bytes"] = int(
            build._free_gb(str(host_dir if host_dir.is_dir()
                               else host_dir.parent)) * (1024 ** 3))
    except OSError:
        pass

    def render():
        ctx.out.heading("work dirs")
        ctx.out.kv("host", f"{host_dir}  {_fmt_gb(host_bytes, host_err)}", 7)
        ctx.out.kv("sandbox",
                   f"{sandbox_dir}  {_fmt_gb(sandbox_bytes, sandbox_err)}", 7)
        if rows:
            ctx.out.blank()
            ctx.out.heading("divergent (same package, different build -- "
                            "never merged)")
            ellipsis = ctx.out.sym("…", "...")
            for pkgname, host_v, sandbox_v in rows:
                if args.verbose:
                    detail = (f"host {', '.join(host_v)}  vs  "
                             f"sandbox {', '.join(sandbox_v)}")
                else:
                    detail = (f"host {_range(host_v, ellipsis)}   "
                             f"sandbox {_range(sandbox_v, ellipsis)}")
                ctx.out.kv(pkgname, detail)
            if not args.verbose:
                ctx.out.hint("porthole disk --json",
                             "the full revision list, per package")
            ctx.out.hint("porthole disk --retire-host",
                         "see what retiring the host work dir would do")
        total_prune = len(host_prune) + len(sandbox_prune)
        if total_prune:
            ctx.out.blank()
            ctx.out.hint("porthole disk --prune --yes",
                         f"{total_prune} apks are old revisions "
                         f"(keeping {args.keep_revisions} per package)")

    return ctx.emit(payload, render)


SPEC = {
    "verb": "disk",
    "order": 39,
    "group": "build",
    "help": "report the disk two divergent pmbootstrap work dirs are "
            "spending, and what is prunable",
    "description": (
        "porthole keeps two pmbootstrap work dirs -- the host's and the\n"
        "workspace's own -- and neither is ever pruned automatically.\n"
        "`porthole disk` reports their size, which apks are old revisions,\n"
        "and where the two dirs hold different builds of the same package.\n"
        "Nothing is deleted without --prune --yes, or --retire-host --yes\n"
        "--discard-host-workdir (--retire-host alone only previews)."),
    "escapes_scope": True,
    "args": [
        (["--keep-revisions"], {"type": int, "default": 2, "metavar": "N",
                                "dest": "keep_revisions",
                                "help": "how many revisions per package to "
                                        "keep (default 2)"}),
        (["--verbose"], {"action": "store_true",
                         "help": "print every divergent revision instead of "
                                 "a count and range"}),
        (["--prune"], {"action": "store_true",
                       "help": "delete old-revision apks (destructive; "
                               "needs --yes)"}),
        (["--retire-host"], {"action": "store_true", "dest": "retire_host",
                             "help": "preview retiring the HOST work dir; "
                                     "add --yes --discard-host-workdir to "
                                     "actually delete it"}),
        (["--discard-host-workdir"], {"action": "store_true",
                                      "dest": "discard_host_workdir",
                                      "help": "names the loss --retire-host "
                                              "--yes causes: the whole host "
                                              "work dir, gone"}),
        (["--yes"], {"action": "store_true",
                     "help": "confirm --prune / --retire-host"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_disk,
    "examples": [
        "porthole disk",
        "porthole disk --json",
        "porthole disk --verbose",
        "porthole disk --prune --yes",
        "porthole disk --retire-host",
        "porthole disk --retire-host --yes --discard-host-workdir",
    ],
}
