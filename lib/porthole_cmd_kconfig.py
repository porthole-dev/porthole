# SPDX-License-Identifier: MIT
"""`porthole kconfig` -- what you asked for versus what you got.

`pmbootstrap kconfig check` already validates a config against pmaports'
kconfigcheck.toml, and this does not duplicate it. It fills the gap that check
leaves, and that gap cost a whole boot hunt:

    olddefconfig resolves an unmet dependency by DROPPING the symbol, and says
    nothing. No error, no warning. The defconfig is what you ASKED for;
    .config is what you GOT, and they are different documents.

The feature is then simply missing at runtime with no diagnostic, which reads
as "the driver is broken" rather than "the driver was never built". Comparing
the two files takes milliseconds and is the check nobody runs.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

from porthole_cli import Bail, EX_FAIL, EX_OK, EX_USAGE

SET_RE = re.compile(r"^(CONFIG_[A-Z0-9_]+)=(.*)$")
UNSET_RE = re.compile(r"^# (CONFIG_[A-Z0-9_]+) is not set$")

# Symbols nobody chooses: the build system probes the toolchain and writes
# these itself. They differ between any two machines and drown the signal --
# the first five "dropped" symbols on a real taimen config were CC_IS_GCC,
# AS_IS_GNU and LD_IS_BFD, none of which anyone asked for.
NOISE = re.compile(
    r"^CONFIG_("
    r"CC_IS_|CC_HAS_|CC_CAN_|CC_VERSION|GCC_VERSION|CLANG_VERSION|"
    r"AS_IS_|AS_HAS_|AS_VERSION|LD_IS_|LD_HAS_|LD_VERSION|"
    r"RUSTC_|RUST_IS_|BINDGEN_|PAHOLE_|TOOLS_SUPPORT_|"
    r"HAVE_|ARCH_HAS_|ARCH_HAVE_|ARCH_SUPPORTS_|ARCH_USE_|ARCH_WANT_|"
    r"GENERIC_|ARCH_MIGHT_|ARCH_SELECT_|ARCH_INLINE_"
    r")")


def parse_config(path: pathlib.Path) -> dict[str, str]:
    """A kernel config as {symbol: value}. `n` for explicitly-unset symbols.

    Both forms matter: `# CONFIG_X is not set` is a decision, and a symbol that
    is simply absent is a different thing again -- it means the symbol did not
    exist in this tree at all.
    """
    out: dict[str, str] = {}
    try:
        text = path.read_text(errors="replace")
    except OSError as exc:
        raise Bail(f"cannot read {path}: {exc}", EX_FAIL) from None
    for line in text.splitlines():
        m = SET_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2).strip()
            continue
        m = UNSET_RE.match(line)
        if m:
            out[m.group(1)] = "n"
    return out


def _tree(ctx) -> pathlib.Path:
    if ctx.cfg.get("PORTHOLE_KERNEL_TREE"):
        path = pathlib.Path(ctx.cfg["PORTHOLE_KERNEL_TREE"]).expanduser()
        if path.is_dir():
            return path
    workdir = ctx.cfg.get("PORTHOLE_WORKDIR")
    if workdir:
        for name in ("linux", "kernel"):
            path = pathlib.Path(workdir).expanduser() / name
            if (path / "Makefile").is_file():
                return path
    raise Bail("no kernel tree found", EX_FAIL,
               "set PORTHOLE_KERNEL_TREE, or PORTHOLE_WORKDIR with a linux/ "
               "checkout inside it")


def _asked_and_got(args, ctx) -> tuple[pathlib.Path, pathlib.Path]:
    """The defconfig you edited, and the .config the build resolved."""
    if args.asked and args.got:
        return pathlib.Path(args.asked), pathlib.Path(args.got)

    tree = _tree(ctx)
    arch = ctx.cfg.get("PORTHOLE_ARCH_DIR") or "arm64"

    asked = pathlib.Path(args.asked) if args.asked else None
    if asked is None:
        name = ctx.cfg.get("PORTHOLE_DEFCONFIG", "")
        if not name:
            raise Bail("no defconfig known", EX_FAIL,
                       "set PORTHOLE_DEFCONFIG in the profile, or pass --asked")
        asked = tree / "arch" / arch / "configs" / name

    got = pathlib.Path(args.got) if args.got else None
    if got is None:
        # envkernel builds out of tree into .output/; an in-tree build puts
        # .config at the root. Prefer .output because that is what pmbootstrap
        # actually built from.
        for candidate in (tree / ".output" / ".config", tree / ".config"):
            if candidate.is_file():
                got = candidate
                break
        if got is None:
            raise Bail("no resolved .config found", EX_FAIL,
                       f"looked in {tree}/.output/.config and {tree}/.config -- "
                       f"build once, or pass --got")
    return asked, got


# -------------------------------------------------------------------- diff --

def cmd_diff(args, ctx) -> int:
    asked_path, got_path = _asked_and_got(args, ctx)
    for path in (asked_path, got_path):
        if not path.is_file():
            raise Bail(f"no such file: {path}", EX_FAIL)

    asked, got = parse_config(asked_path), parse_config(got_path)

    # The symbols that MUST survive. This is the workflow the taimen docs
    # describe -- "keep an explicit list and grep for all of them after every
    # config regeneration" -- turned into something that runs on its own.
    watch = [w.strip() for w in
             (args.watch or ctx.cfg.get("PORTHOLE_KCONFIG_WATCH", "")).replace(
                 ",", " ").split() if w.strip()]
    watch = [w if w.startswith("CONFIG_") else "CONFIG_" + w for w in watch]

    dropped, changed, filtered = [], [], 0
    for symbol, want in asked.items():
        if want == "n":
            continue          # asking for `n` and getting nothing is the same
        if not args.all and symbol not in watch and NOISE.match(symbol):
            filtered += 1
            continue
        have = got.get(symbol)
        if have is None:
            dropped.append({"symbol": symbol, "asked": want, "got": None,
                            "why": "absent from the resolved config -- the "
                                   "symbol does not exist in this tree, or "
                                   "olddefconfig dropped it"})
        elif have == "n":
            dropped.append({"symbol": symbol, "asked": want, "got": "n",
                            "why": "explicitly disabled -- a dependency it "
                                   "needs is not met"})
        elif have != want:
            changed.append({"symbol": symbol, "asked": want, "got": have})

    # Symbols the build turned ON that nobody asked for. Usually a dependency
    # being pulled in, occasionally a surprise worth knowing about.
    added = [{"symbol": s, "got": v} for s, v in got.items()
             if s not in asked and v != "n"] if args.added else []

    # A watched symbol that vanished is the headline: someone declared it
    # load-bearing, and it is gone.
    watch_lost = [d for d in dropped if d["symbol"] in watch]
    watch_missing = [w for w in watch if w not in asked]

    payload = {"asked_file": str(asked_path), "got_file": str(got_path),
               "asked_count": len(asked), "got_count": len(got),
               "dropped": dropped, "changed": changed, "added": added,
               "filtered_as_noise": filtered,
               "watch": watch, "watch_lost": watch_lost,
               "watch_not_requested": watch_missing}

    def render():
        o = ctx.out
        o.heading("asked for vs got")
        o.kv("asked", f"{asked_path}  ({len(asked)} symbols)", 8)
        o.kv("got", f"{got_path}  ({len(got)} symbols)", 8)
        o.blank()

        if watch:
            o.heading(f"watched symbols ({len(watch)})")
            if watch_lost:
                for d in watch_lost:
                    o(f"  {o.paint('LOST', 'red')}  {d['symbol']}  "
                      f"asked {d['asked']}, got {d['got'] or '(absent)'}")
            if watch_missing:
                # Build the painted text first. A multi-line expression inside
                # an f-string is PEP 701, which is Python 3.12+; this project
                # declares a 3.8 floor, so it is a syntax error on every
                # interpreter CI runs.
                note = o.paint("watched, but the defconfig never asks for it",
                               "grey")
                for symbol in watch_missing:
                    o(f"  {o.paint('UNSET', 'yellow')} {symbol}  {note}")
            if not watch_lost and not watch_missing:
                o(o.paint("  all present", "green"))
            o.blank()

        if not dropped and not changed:
            o(o.paint("  every symbol you asked for survived", "green"))
        if dropped:
            o.heading(f"DROPPED ({len(dropped)})")
            o(o.paint("  You asked for these and the build does not have them.\n"
                      "  olddefconfig resolves an unmet dependency by dropping\n"
                      "  the symbol, silently. The feature is then simply\n"
                      "  missing at runtime with no diagnostic at all.", "grey"))
            o.blank()
            width = max(len(d["symbol"]) for d in dropped)
            for d in dropped:
                name = d["symbol"].ljust(width)
                # Not `got`: that name is the outer config dict, and binding it
                # here makes Python treat it as local for the whole closure --
                # so `len(got)` above fails with UnboundLocalError.
                actual = d["got"] or "(absent)"
                o(f"  {o.paint(name, 'red')}  asked {d['asked']}, got {actual}")
                o(f"  {'':<{width}}  {o.paint(d['why'], 'grey')}")
            o.blank()
        if changed:
            o.heading(f"changed ({len(changed)})")
            width = max(len(c["symbol"]) for c in changed)
            for c in changed:
                name = c["symbol"].ljust(width)
                o(f"  {o.paint(name, 'yellow')}  "
                  f"asked {c['asked']}, got {c['got']}")
            o.blank()
        if added:
            o.heading(f"added by the build ({len(added)})")
            for a in added[:30]:
                o(f"  {a['symbol']}={a['got']}")
            if len(added) > 30:
                o(f"  ... and {len(added) - 30} more")
        if filtered:
            o.blank()
            o(o.paint(f"  {filtered} auto-detected symbol(s) hidden "
                      f"(toolchain probes, ARCH_HAS_*, GENERIC_*). "
                      f"`--all` to see them.", "grey"))

    ctx.emit(payload, render)
    # A watchlist makes the verdict sharp: without one, "something got dropped"
    # is usually noise; with one, a lost watched symbol is unambiguous.
    if watch:
        return EX_FAIL if (watch_lost or watch_missing) else EX_OK
    return EX_FAIL if dropped else EX_OK


# ------------------------------------------------------------------- check --

def cmd_check(args, ctx) -> int:
    """Hand off to pmbootstrap, which owns the pmaports rules."""
    if not shutil.which("pmbootstrap"):
        raise Bail("pmbootstrap is not installed", EX_FAIL,
                   "it owns kconfigcheck.toml and the rules; porthole does not "
                   "duplicate them")
    argv = ["pmbootstrap", "kconfig", "check"]
    if args.asked:
        argv += ["--file", args.asked]
    ctx.out(ctx.out.paint("  pmbootstrap owns these rules; porthole does not "
                          "duplicate them.", "grey"))
    ctx.out.blank()
    return subprocess.run(argv).returncode


# ----------------------------------------------------------------- explain --

def cmd_explain(args, ctx) -> int:
    """Which pmOS category wants a symbol, from kconfigcheck.toml."""
    import porthole_pmaports as pmap

    symbol = (args.symbol or "").upper()
    if not symbol:
        raise Bail("name a symbol", EX_USAGE,
                   "porthole kconfig explain CONFIG_BINFMT_ELF")
    if not symbol.startswith("CONFIG_"):
        symbol = "CONFIG_" + symbol

    pmaports = pmap.find_pmaports(ctx.cfg)
    if not pmaports:
        raise Bail("no pmaports checkout found", EX_FAIL)
    path = pmaports / "kconfigcheck.toml"
    if not path.is_file():
        raise Bail(f"{path} not found", EX_FAIL)

    bare = symbol[len("CONFIG_"):]
    hits, section = [], ""
    for line in path.read_text(errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            section = stripped
            continue
        m = re.match(rf"^{re.escape(bare)}\s*=\s*(\S+)", stripped)
        if m:
            hits.append({"section": section, "value": m.group(1)})

    def render():
        o = ctx.out
        if not hits:
            o(f"  {symbol} is not mentioned in kconfigcheck.toml.")
            o.blank()
            o(o.paint("  That means pmOS has no opinion on it -- which is not\n"
                      "  the same as it being unnecessary for your device.",
                      "grey"))
            return
        o.heading(f"{symbol} — required by {len(hits)} rule(s)")
        o.blank()
        for hit in hits:
            o(f"  {o.paint(hit['value'], 'bold')}   {hit['section']}")
        o.blank()
        o(o.paint("  The section says which kernel versions and architectures\n"
                  "  the rule applies to. A device APKBUILD opts into\n"
                  "  categories with options=\"pmb:kconfigcheck-<name>\".", "grey"))

    return ctx.emit({"symbol": symbol, "rules": hits}, render)


ACTIONS = {"diff": cmd_diff, "check": cmd_check, "explain": cmd_explain}


def dispatch(args, ctx) -> int:
    action = args.action or "diff"
    fn = ACTIONS.get(action)
    if not fn:
        raise Bail(f"unknown action {action!r}", EX_USAGE,
                   f"actions: {', '.join(ACTIONS)}")
    return fn(args, ctx)


SPEC = {
    "verb": "kconfig",
    "order": 34,
    "help": "catch the kernel symbols olddefconfig silently dropped",
    "description": (
        "`pmbootstrap kconfig check` validates a config against pmaports'\n"
        "rules, and this does not duplicate it. It fills the gap that check\n"
        "leaves:\n\n"
        "olddefconfig resolves an unmet dependency by DROPPING the symbol and\n"
        "saying nothing. The defconfig is what you asked for; .config is what\n"
        "you got. The feature is then missing at runtime with no diagnostic,\n"
        "which reads as a broken driver rather than an unbuilt one."),
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION", "choices": list(ACTIONS),
                      "help": "diff | check | explain"}),
        (["symbol"], {"nargs": "?", "help": "explain: the symbol"}),
        (["--asked"], {"metavar": "FILE", "help": "the defconfig you edited"}),
        (["--got"], {"metavar": "FILE", "help": "the resolved .config"}),
        (["--added"], {"action": "store_true",
                       "help": "diff: also show what the build turned on"}),
        (["--all"], {"action": "store_true",
                     "help": "diff: include auto-detected toolchain symbols"}),
        (["--watch"], {"metavar": "SYMBOLS",
                       "help": "diff: comma-separated symbols that MUST survive "
                               "(default: PORTHOLE_KCONFIG_WATCH)"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": dispatch,
    "examples": [
        "porthole kconfig diff",
        "porthole kconfig diff --asked a_defconfig --got .config",
        "porthole kconfig explain CONFIG_BINFMT_ELF",
        "porthole kconfig check",
    ],
}
