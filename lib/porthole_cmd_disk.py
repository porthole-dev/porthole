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


def divergence(host_names, sandbox_names) -> list:
    """Packages present in BOTH work dirs at a different pkgver-rel. Pure.

    Reports the mismatch; chooses nothing. Two work dirs holding different
    builds of the same package is not a bug this function fixes -- it is a
    decision `--retire-host` makes explicit, on request, never here.
    """
    def by_pkg(names):
        out: dict = {}
        for name in names:
            parsed = _parse_apk(name)
            if parsed:
                pkgname, pkgver, rel = parsed
                out.setdefault(pkgname, set()).add(f"{pkgver}-r{rel}")
        return out

    host, sandbox = by_pkg(host_names), by_pkg(sandbox_names)
    rows = []
    for pkgname in sorted(set(host) & set(sandbox)):
        if host[pkgname] != sandbox[pkgname]:
            rows.append((pkgname, sorted(host[pkgname]),
                        sorted(sandbox[pkgname])))
    return rows


# ------------------------------------------------------------------ shell --

def _apk_names(workdir: pathlib.Path) -> list:
    if not workdir.is_dir():
        return []
    return [p.name for p in workdir.glob("packages/*/*/*.apk")]


def _dir_size_bytes(path: pathlib.Path):
    """`du -sb` -- a real filesystem walk in C, not Python, over a tree that
    was measured at tens of thousands of files. None if the dir is absent or
    du fails; never raises."""
    if not path.is_dir():
        return None
    try:
        proc = subprocess.run(["du", "-sb", str(path)],
                              capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            return None
        return int(proc.stdout.split()[0])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _fmt_gb(n_bytes) -> str:
    return "?" if n_bytes is None else f"{n_bytes / 1e9:.1f} G"


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


def cmd_disk(args, ctx) -> int:
    import porthole_cmd_build as build

    host_dir, sandbox_dir = _work_dirs(ctx)
    host_apks, sandbox_apks = _apk_names(host_dir), _apk_names(sandbox_dir)

    if args.retire_host:
        if not args.yes:
            raise Bail(f"--retire-host deletes {host_dir} entirely -- "
                      "pass --yes too", EX_FAIL,
                      "porthole disk --retire-host --yes")
        existed = host_dir.is_dir()
        if existed:
            shutil.rmtree(host_dir, ignore_errors=True)
        payload = {"retired": str(host_dir), "existed": existed}
        return ctx.emit(payload, lambda: ctx.out(
            f"retired {host_dir}" if existed
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
    host_bytes, sandbox_bytes = (_dir_size_bytes(host_dir),
                                 _dir_size_bytes(sandbox_dir))
    payload = {
        "host": {"path": str(host_dir), "bytes": host_bytes,
                 "free_bytes": None, "prunable": host_prune},
        "sandbox": {"path": str(sandbox_dir), "bytes": sandbox_bytes,
                    "prunable": sandbox_prune},
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
        ctx.out.kv("host", f"{host_dir}  {_fmt_gb(host_bytes)}", 7)
        ctx.out.kv("sandbox", f"{sandbox_dir}  {_fmt_gb(sandbox_bytes)}", 7)
        if rows:
            ctx.out.blank()
            ctx.out.heading("divergent (same package, different build -- "
                            "never merged)")
            for pkgname, host_v, sandbox_v in rows:
                ctx.out.kv(pkgname, f"host {', '.join(host_v)}  vs  "
                                    f"sandbox {', '.join(sandbox_v)}")
            ctx.out.hint("porthole disk --retire-host --yes",
                         "drop the host work dir entirely; the sandbox "
                         "keeps its own build")
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
        "Nothing is deleted without --prune --yes or --retire-host --yes."),
    "escapes_scope": True,
    "args": [
        (["--keep-revisions"], {"type": int, "default": 2, "metavar": "N",
                                "dest": "keep_revisions",
                                "help": "how many revisions per package to "
                                        "keep (default 2)"}),
        (["--prune"], {"action": "store_true",
                       "help": "delete old-revision apks (destructive; "
                               "needs --yes)"}),
        (["--retire-host"], {"action": "store_true", "dest": "retire_host",
                             "help": "delete the HOST work dir entirely "
                                     "(destructive; needs --yes)"}),
        (["--yes"], {"action": "store_true",
                     "help": "confirm --prune / --retire-host"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_disk,
    "examples": [
        "porthole disk",
        "porthole disk --json",
        "porthole disk --prune --yes",
        "porthole disk --retire-host --yes",
    ],
}
