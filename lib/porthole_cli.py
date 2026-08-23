# SPDX-License-Identifier: MIT
"""Command registry, output helpers and argument plumbing for `porthole`.

Adding a verb is a drop-in: create `lib/porthole_cmd_<name>.py` exporting a
`SPEC` dict. Nothing else needs editing -- no central list to update, no import
to add. That is deliberate: a registry you have to remember to update is a
registry that goes stale, and this project expects contributors who have never
read `bin/porthole`.

    SPEC = {
        "verb":  "doctor",
        "help":  "one line, shown in `porthole --help`",
        "order": 20,                       # sort key in help output
        "args":  [                         # argparse add_argument() specs
            (["--json"], {"action": "store_true", "help": "machine-readable"}),
        ],
        "run":   cmd_doctor,               # (args, ctx) -> int
        "examples": ["porthole doctor", "porthole doctor --bench"],
    }

`run` receives a `Ctx` (root path, lazy config, output helpers) so a command
never has to rediscover the checkout or re-implement colour handling.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import pathlib
import re
import sys

import porthole

# Exit codes are an API. See docs/ARCHITECTURE.md; these names exist so a
# command says `return EX_USAGE` rather than a bare 64 nobody can grep for.
EX_OK = 0
EX_FAIL = 1
EX_USAGE = 64
EX_LOCK = 75
EX_STATE = 76
EX_TIMEOUT = 124

CMD_PREFIX = "porthole_cmd_"


# ----------------------------------------------------------------- output --

class Out:
    """Terminal output that degrades honestly.

    Colour only when stdout is a tty and NO_COLOR is unset -- piping into a
    file or an agent must never embed escape codes. Unicode only when the
    encoding can represent it, because a bring-up host is often a minimal
    container where stdout is ASCII and a UnicodeEncodeError would crash the
    tool rather than the cosmetics.
    """

    COLOURS = {"red": "31", "green": "32", "yellow": "33", "blue": "34",
               "magenta": "35", "cyan": "36", "grey": "90", "bold": "1"}

    def __init__(self, stream=None, force_colour: bool | None = None):
        self.stream = stream or sys.stdout
        if force_colour is None:
            self.colour = (self.stream.isatty()
                           and not os.environ.get("NO_COLOR")
                           and os.environ.get("TERM") != "dumb")
        else:
            self.colour = force_colour
        enc = (getattr(self.stream, "encoding", None) or "ascii").lower()
        self.unicode = "utf" in enc

    def paint(self, text: str, colour: str) -> str:
        code = self.COLOURS.get(colour)
        return f"\033[{code}m{text}\033[0m" if (self.colour and code) else text

    def sym(self, fancy: str, plain: str) -> str:
        return fancy if self.unicode else plain

    def __call__(self, *parts):
        print(*parts, file=self.stream)

    def blank(self):
        print(file=self.stream)

    def heading(self, text: str):
        print(self.paint(text, "bold"), file=self.stream)

    def kv(self, key: str, value: str, width: int = 0, note: str = ""):
        line = f"  {key:<{width}}  {value}"
        if note:
            line += self.paint(f"   {note}", "grey")
        print(line, file=self.stream)

    def hint(self, text: str):
        print(self.paint(f"  {self.sym('→', '->')} {text}", "cyan"),
              file=self.stream)

    def warn(self, text: str):
        print(self.paint(f"warning: {text}", "yellow"), file=sys.stderr)

    def error(self, text: str):
        print(self.paint(f"porthole: {text}", "red"), file=sys.stderr)


class Bail(Exception):
    """Abort a command with a message and an exit code.

    Raised rather than sys.exit() so the top level can render it consistently
    and, where it helps, append a suggestion.
    """

    def __init__(self, message: str, code: int = EX_FAIL, hint: str = ""):
        super().__init__(message)
        self.message, self.code, self.hint = message, code, hint


# -------------------------------------------------------------------- ctx --

class Ctx:
    """What every command gets. Config loads lazily.

    Lazily because `porthole version` and `porthole completion` must work in a
    checkout with no profile selected, and a missing profile is fatal by design
    (see porthole.ProfileNotFound).
    """

    def __init__(self, root: pathlib.Path, args, out: Out):
        self.root, self.args, self.out = pathlib.Path(root), args, out
        self._cfg = None

    @property
    def cfg(self) -> porthole.Config:
        if self._cfg is None:
            env = dict(os.environ)
            device = getattr(self.args, "device", None)
            if device:
                env["PORTHOLE_DEVICE"] = device
            try:
                self._cfg = porthole.load_config(root=self.root, env=env)
            except porthole.ProfileNotFound as exc:
                raise Bail(str(exc), EX_FAIL) from None
            self._derive(self._cfg)
        return self._cfg

    @staticmethod
    def _derive(cfg: porthole.Config) -> None:
        """Expose the composed values, so `config` shows what tools will use."""
        for key, value in (("PHONE", porthole.resolve_phone(cfg)),
                           ("HOST", porthole.resolve_host(cfg))):
            if not cfg.get(key):
                cfg.set(key, value, "derived")

    def device(self) -> porthole.Device:
        return porthole.Device(self.cfg)

    def emit(self, payload, render=None) -> int:
        """Print JSON if --json was passed, else call render()."""
        if getattr(self.args, "json", False):
            print(json.dumps(payload, indent=2, default=str))
            return EX_OK
        if render:
            render()
        return EX_OK


# --------------------------------------------------------------- registry --

def discover(root: pathlib.Path) -> list[dict]:
    """Find every porthole_cmd_*.py in lib/ and collect its SPEC.

    A module that fails to import is reported and skipped rather than taking
    the whole CLI down: one broken third-party verb must not stop you running
    `porthole doctor` to find out why.
    """
    lib = pathlib.Path(root) / "lib"
    specs = []
    for path in sorted(lib.glob(f"{CMD_PREFIX}*.py")):
        name = path.stem
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            print(f"porthole: skipping {name}: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            continue
        spec = getattr(module, "SPEC", None)
        if not spec or "verb" not in spec or "run" not in spec:
            continue
        spec.setdefault("order", 50)
        spec.setdefault("args", [])
        spec.setdefault("help", "")
        spec.setdefault("examples", [])
        specs.append(spec)
    specs.sort(key=lambda s: (s["order"], s["verb"]))
    return specs


def suggest(word: str, options) -> str:
    """Nearest verb by a cheap edit distance. `porthole doctro` should help."""
    import difflib
    match = difflib.get_close_matches(word, list(options), n=1, cutoff=0.6)
    return match[0] if match else ""


class Parser(argparse.ArgumentParser):
    """argparse that exits 64 on usage errors instead of 2.

    2 is not in our exit-code table and an agent reading it learns nothing.
    """

    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"porthole: {message}", file=sys.stderr)
        raise SystemExit(EX_USAGE)


def with_device(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Accept the global flags after the verb as well as before it.

    `porthole doctor --no-color` and `porthole init --device taimen` are what
    people actually type; requiring a global flag before the verb is a papercut
    that makes a tool feel hostile.

    SUPPRESS is load-bearing: without it argparse overwrites the global value
    with the subparser's default whenever the flag is absent after the verb, so
    `porthole --no-color doctor` would silently regain colour.
    """
    parser.add_argument("--device", default=argparse.SUPPRESS, metavar="CODENAME",
                        help="act on this device profile")
    parser.add_argument("--no-color", "--no-colour", dest="no_color",
                        default=argparse.SUPPRESS, action="store_true",
                        help="plain output (also honours NO_COLOR)")
    return parser


def build(root: pathlib.Path, specs: list[dict]) -> tuple[Parser, dict]:
    epilog = ["verbs:"]
    width = max((len(s["verb"]) for s in specs), default=10)
    for spec in specs:
        epilog.append(f"  {spec['verb']:<{width}}  {spec['help']}")
    epilog += [
        "",
        "examples:",
        "  porthole doctor                 is my host ready?",
        "  porthole init --device NAME     set up your identity, once",
        "  porthole tools --needs BOOTED   which tools need a booted device",
        "  porthole brain --severity law   the ten notes worth reading",
        "",
        "every read verb takes --json. Docs: README.md, AGENTS.md for agents.",
    ]

    parser = Parser(
        prog="porthole",
        description="postmarketOS device bring-up toolkit",
        epilog="\n".join(epilog),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-d", "--device", metavar="CODENAME",
                        help="act on this device profile")
    parser.add_argument("--no-color", "--no-colour", action="store_true",
                        dest="no_color", default=False,
                        help="plain output (also honours NO_COLOR)")
    parser.add_argument("-V", "--version", action="store_true",
                        help="print the version and exit")

    sub = parser.add_subparsers(dest="verb", metavar="<verb>")
    table = {}
    for spec in specs:
        # A malformed SPEC must cost you that one verb, not the whole CLI.
        # discover() already guards a bad import; this guards a bad argument
        # (a flag colliding with a global one, say), which otherwise raised
        # out of build() and made `porthole doctor` unrunnable -- precisely
        # the failure the registry exists to survive.
        try:
            child = with_device(sub.add_parser(
                spec["verb"], help=spec["help"],
                description=spec.get("description", spec["help"]),
                epilog=("examples:\n  " + "\n  ".join(spec["examples"])
                        if spec["examples"] else None),
                formatter_class=argparse.RawDescriptionHelpFormatter))
            for flags, kwargs in spec["args"]:
                child.add_argument(*flags, **kwargs)
        except (argparse.ArgumentError, TypeError, ValueError) as exc:
            print(f"porthole: skipping verb {spec['verb']!r}: {exc}",
                  file=sys.stderr)
            continue
        child.set_defaults(_spec=spec)
        table[spec["verb"]] = spec
    return parser, table


VERSION_RE = re.compile(r"^\s*(\d+\.\d+\.\d+)\s*$")


def version(root: pathlib.Path) -> str:
    path = pathlib.Path(root) / "VERSION"
    try:
        m = VERSION_RE.match(path.read_text())
        if m:
            return m.group(1)
    except OSError:
        pass
    return "0.0.0+unknown"


def overview(root: pathlib.Path, out: Out) -> int:
    """`porthole` with no verb: say where you are and what to do next.

    A bare usage dump is the least useful thing to print to someone who has
    just cloned this. Answer their actual first questions instead: is it
    configured, which device, what now.
    """
    out.heading(f"porthole {version(root)}")
    out()
    try:
        cfg = porthole.load_config(root=root)
        device = cfg.get("PORTHOLE_DEVICE", "")
        configured = cfg.source("PORTHOLE_USER") != "default"
    except porthole.ProfileNotFound as exc:
        out.error(str(exc))
        return EX_FAIL

    tick = out.sym("✓", "ok")
    cross = out.sym("·", "-")
    profiles = porthole.list_profiles(root)

    out.kv(f"{out.paint(tick if device else cross, 'green' if device else 'grey')} device",
           device or out.paint("none selected", "grey"), 10)
    out.kv(f"{out.paint(tick if configured else cross, 'green' if configured else 'grey')} identity",
           porthole.resolve_phone(cfg) if configured
           else out.paint("not configured", "grey"), 10)
    out.kv(f"  profiles", f"{len(profiles)} ({', '.join(profiles) or 'none'})", 10)
    out.blank()

    if not device or not configured:
        out.hint("porthole init --device <codename>   set up, once")
        if not profiles:
            out.hint("porthole new-device <codename>      port something new")
    else:
        out.hint("porthole doctor                     check the host and device")
        out.hint("porthole tools                      what can I run?")
        out.hint("porthole brain --severity law       what should I know?")
    out.blank()
    out("`porthole --help` for all verbs.")
    return EX_OK


def main(argv: list[str], root: pathlib.Path) -> int:
    sys.path.insert(0, str(pathlib.Path(root) / "lib"))
    specs = discover(root)
    parser, table = build(root, specs)

    if argv and argv[0] in ("-V", "--version"):
        print(f"porthole {version(root)}")
        return EX_OK

    # An unknown verb should suggest, not just refuse. Only intercept a bare
    # word: anything starting with - is argparse's business.
    if argv and not argv[0].startswith("-") and argv[0] not in table:
        near = suggest(argv[0], table)
        print(f"porthole: unknown verb {argv[0]!r}", file=sys.stderr)
        if near:
            print(f"porthole: did you mean {near!r}?", file=sys.stderr)
        print(f"porthole: verbs are {', '.join(sorted(table))}", file=sys.stderr)
        return EX_USAGE

    args = parser.parse_args(argv)
    out = Out(force_colour=False if getattr(args, "no_color", False) else None)

    if getattr(args, "version", False):
        print(f"porthole {version(root)}")
        return EX_OK
    if not getattr(args, "_spec", None):
        return overview(root, out)

    ctx = Ctx(root, args, out)
    try:
        return args._spec["run"](args, ctx)
    except Bail as exc:
        out.error(exc.message)
        if exc.hint:
            # stderr, not stdout: an error and its remedy must not be split
            # across streams, or `2>log` captures the problem and loses the fix.
            print(out.paint(f"  {out.sym('→', '->')} {exc.hint}", "cyan"),
                  file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return EX_OK
