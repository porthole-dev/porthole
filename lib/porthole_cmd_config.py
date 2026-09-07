# SPDX-License-Identifier: MIT
"""`porthole config` -- what did every knob resolve to, and from where."""
from __future__ import annotations

from porthole_cli import Bail, EX_FAIL, EX_OK


def cmd_config(args, ctx) -> int:
    cfg = ctx.cfg

    if args.key:
        if args.key not in cfg:
            near = _suggest(args.key, cfg)
            raise Bail(f"no such key: {args.key}", EX_FAIL,
                       f"did you mean {near}?" if near else
                       "`porthole config` lists every key")
        # Bare value, so `PHONE=$(porthole config PHONE)` works in a script.
        print(cfg[args.key])
        return EX_OK

    keys = sorted(cfg)
    if args.source:
        keys = [k for k in keys if cfg.source(k) == args.source]
        if not keys:
            raise Bail(f"nothing came from layer {args.source!r}", EX_FAIL,
                       "layers are: default, profile, user-config, root-env, "
                       "environment, derived")

    payload = {k: {"value": cfg[k], "source": cfg.source(k)} for k in keys}

    def render():
        width = max((len(k) for k in keys), default=0)
        for key in keys:
            ctx.out.kv(key, cfg[key], width, f"# {cfg.source(key)}")

    return ctx.emit(payload, render)


def _suggest(word, options):
    import difflib
    hit = difflib.get_close_matches(word.upper(), list(options), n=1, cutoff=0.5)
    return hit[0] if hit else ""


SPEC = {
    "verb": "config",
    "order": 30,
    "group": "start",
    "help": "print the resolved config and where each value came from",
    "description": (
        "Every knob, its value, and which of the five layers supplied it.\n"
        "The provenance is the fastest way to answer 'why is this talking to\n"
        "the wrong device' -- usually a stale config.env or an exported PHONE."),
    "args": [
        (["key"], {"nargs": "?", "help": "print just this key's value, bare"}),
        (["--source"], {"metavar": "LAYER",
                        "help": "only keys that came from this layer"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_config,
    "examples": [
        "porthole config",
        "porthole config PHONE",
        "porthole config --source profile",
        "porthole config --json",
    ],
}
