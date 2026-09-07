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
EX_INTERRUPT = 130
# The tool could not run at all. Distinct from EX_FAIL, which means the thing
# under test failed: an agent that conflates them reports broken tools as
# findings, which AGENTS.md section 6 forbids.
EX_UNAVAILABLE = 69

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

    # ONE vocabulary for "how did it go", because five modules had grown their
    # own. `porthole_progress` paints a finished build ✔ green and a failed
    # one ✘ red; `doctor` wrote `  ok` in green and `FAIL` in red; `devices`,
    # `use`, `ui`, `soc` and `channel` each hand-rolled the same
    # `paint(sym("●", "*"), "green")` for "this is the active one". A reader
    # should not have to learn a second alphabet per verb.
    MARKS = {"ok": ("\u2714", "+"), "warn": ("\u26a0", "!"),
             "fail": ("\u2718", "x"), "active": ("\u25cf", "*"),
             "skip": ("\u00b7", "."), "note": ("\u2192", "->")}
    TONES = {"ok": "green", "warn": "yellow", "fail": "red",
             "active": "green", "skip": "grey", "note": "cyan"}

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

    def mark(self, kind: str, on: bool = True) -> str:
        """The one-glyph verdict, in its own colour -- or a blank the same
        width, so a column of them stays a column."""
        fancy, plain = self.MARKS.get(kind, ("\u00b7", "."))
        glyph = self.sym(fancy, plain)
        if not on:
            return " " * len(glyph)
        return self.paint(glyph, self.TONES.get(kind, "grey"))

    def status(self, kind: str, text: str = "", width: int = 4) -> str:
        """`✔ ok` -- the glyph for the eye, the word for the grep.

        Both, deliberately: a glyph alone is unreadable in a log somebody
        pastes into an issue, and a word alone is what made a wall of doctor
        rows impossible to scan.
        """
        word = (text or kind)[:max(width, len(text or kind))]
        # The glyph always carries its colour; the WORD only does when it is
        # a problem. `✔ ok` twice over in green spends the emphasis budget on
        # the rows nobody needs to read, and a wall of them is exactly the
        # scanning problem the glyph was added to fix.
        tone = self.TONES.get(kind, "grey")
        return "{}  {}".format(
            self.mark(kind),
            self.paint("{:>{}}".format(word, width),
                       tone if kind in ("warn", "fail") else "grey"))

    def __call__(self, *parts):
        print(*parts, file=self.stream)

    def blank(self):
        print(file=self.stream)

    def heading(self, text: str):
        print(self.paint(text, "bold"), file=self.stream)

    def kv(self, key: str, value: str, width: int = 0, note: str = ""):
        """A labelled row. The LABEL is dimmed, not the value.

        Emphasis is a budget, and in a row like `elapsed  2m41s` the label is
        the half the reader already knows -- they asked for it. Dimming it is
        what lets a column of values read as the content rather than as a
        wall of evenly-lit text. Same rule the build display follows one
        module over.
        """
        line = "  {}  {}".format(self.paint("{:<{}}".format(key, width),
                                            "grey"), value)
        if note:
            line += self.paint(f"   {note}", "grey")
        print(line, file=self.stream)

    def hint(self, text: str):
        print("  {} {}".format(self.mark("note"), self.paint(text, "cyan")),
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
                # `-d` reaches load_config as an environment value, which is
                # indistinguishable from a forgotten export. Say which it was,
                # or the drift guard refuses a documented flag.
                env[porthole.DELIBERATE_DEVICE] = "1"
            try:
                self._cfg = porthole.load_config(root=self.root, env=env)
            except porthole.ProfileNotFound as exc:
                raise Bail(str(exc), EX_FAIL) from None
            self._derive(self._cfg)
        return self._cfg

    def reload(self) -> None:
        """Drop the cached config so the next read sees the files as they are.

        For a command that WRITES config.env and then asks a question about
        the host it has just changed. `porthole init` is the whole reason: it
        loads the config before writing (there may be nothing to load), so the
        `porthole next` step it prints at the end was answered against the
        host as it was BEFORE setup -- on a genuinely fresh machine, "no
        device selected", and the one next step it promises was silently
        absent from the first run it exists for.
        """
        self._cfg = None

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


# ------------------------------------------------------- child processes --

# Variables a child process must never inherit, and the incident that put each
# one here. A dict rather than a tuple because "not in the list" is an accident
# and reads exactly like a decision -- the same reason the permissions table
# has a deny half.
STALE_EXPORTS = {
    "PMB_SUDO":
        "the privilege broker it names was deleted from porthole on "
        "2026-08-29, but an export outlives the file. pmbootstrap invokes "
        "PMB_SUDO directly, prefixing nothing, so a stale one kills a build "
        "with exit 78 deep inside pmbootstrap while naming nothing",
}


def child_env(base=None, cfg=None) -> dict:
    """The environment a child process gets: `base`, minus the stale exports.

    Pure -- a mapping in, a mapping out -- so what crosses into a child is
    testable with no device and no subprocess.

    WHY IT IS SHARED RATHER THAN PER-VERB
        This was `build_env` in porthole_cmd_build, and it worked: `porthole
        build` has not carried PMB_SUDO since. `porthole pkg` never got the
        same treatment, so the workaround people actually typed --
        `env -u PMB_SUDO porthole ...` -- was redundant on the verb that had
        the fix and load-bearing on the verb that did not (#63). Nobody could
        tell which, so it was applied uniformly and forever.

        One scrub, in the place every verb already imports, is a smaller thing
        to keep right than a scrub per caller. `tests/test_conventions.py`
        fails on a `dict(os.environ)` that does not come through here.

    `cfg`, when given, overlays the resolved PORTHOLE_*/TK_ values on top --
    the config a verb resolved must beat whatever the shell was carrying, which
    is the same reason a build refuses on config drift.
    """
    env = dict(os.environ if base is None else base)
    for key in STALE_EXPORTS:
        env.pop(key, None)
    for key, value in (cfg or {}).items():
        if key.startswith(("PORTHOLE_", "TK_")) and isinstance(value, str):
            env[key] = value
    return env


# --------------------------------------------------------------- registry --

def _cache_path(root) -> pathlib.Path:
    base = os.environ.get("XDG_CACHE_HOME") or (pathlib.Path.home() / ".cache")
    return pathlib.Path(base) / "porthole" / "registry.json"


def _registry_key(lib: pathlib.Path) -> str:
    """A fingerprint of the verb modules: names, sizes and mtimes.

    Cheap (one stat per module, no reads) and it changes whenever a verb is
    added, removed or edited -- which is exactly when the cache must not be
    trusted. Content hashing would be more precise and would cost more than the
    thing it is saving.
    """
    import hashlib
    h = hashlib.sha256()
    for path in sorted(lib.glob(f"{CMD_PREFIX}*.py")):
        st = path.stat()
        h.update(f"{path.name}:{st.st_size}:{st.st_mtime_ns}".encode())
    return h.hexdigest()[:16]


def lookup(root: pathlib.Path, verb: str):
    """The SPEC for ONE verb, importing only its module.

    `discover()` imports all two dozen verb modules to read their SPECs,
    because a SPEC holds a function reference -- 51ms on every invocation,
    including `porthole version`. Dispatch needs exactly one of them.

    So the verb -> module mapping is cached, keyed by the fingerprint above. On
    a hit we import one module; on a miss, or any error at all, we fall back to
    full discovery. A cache that can change behaviour is not worth having, so
    every failure path here ends in the slow, correct answer.
    """
    lib = pathlib.Path(root) / "lib"
    try:
        cache = json.loads(_cache_path(root).read_text())
        if cache.get("key") != _registry_key(lib):
            return None
        module_name = cache["verbs"].get(verb)
        if not module_name:
            return None                      # unknown verb: let the slow path
        sys.path.insert(0, str(lib))         # explain and suggest
        module = importlib.import_module(module_name)
        spec = getattr(module, "SPEC", None)
        if not spec or spec.get("verb") != verb:
            return None
        _defaults(spec)
        return spec
    except Exception:  # noqa: BLE001 -- any doubt at all means the slow path
        return None


def write_cache(root: pathlib.Path, specs: list[dict]) -> None:
    """Record verb -> module after a full discovery. Best effort."""
    lib = pathlib.Path(root) / "lib"
    try:
        path = _cache_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "key": _registry_key(lib),
            "verbs": {s["verb"]: s["_module"] for s in specs if s.get("_module")},
        }))
    except OSError:
        pass


def _defaults(spec: dict) -> dict:
    spec.setdefault("order", 50)
    spec.setdefault("args", [])
    spec.setdefault("help", "")
    spec.setdefault("examples", [])
    # Declarative facts about the verb, each defaulting to the common case so
    # an existing SPEC needs no edit. tests/test_cli_rules.py checks the whole
    # registry against these: a rule enforced verb by verb drifts the moment
    # someone adds a tenth verb without reading the other nine.
    spec.setdefault("device_flag", True)     # does -d/--device apply?
    spec.setdefault("reports", True)         # does it print a report? (--json)
    spec.setdefault("escapes_scope", False)  # writes beyond its own profile?
    return spec


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
        spec["_module"] = name
        specs.append(_defaults(spec))
    specs.sort(key=lambda s: (s["order"], s["verb"]))
    write_cache(root, specs)
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


def with_device(parser: argparse.ArgumentParser,
                device_flag: bool = True) -> argparse.ArgumentParser:
    """Accept the global flags after the verb as well as before it.

    `porthole doctor --no-color` and `porthole doctor -d taimen` are what people
    actually type; requiring a global flag before the verb is a papercut that
    makes a tool feel hostile. BOTH spellings are added here, because `-d`
    working before the verb and not after is the same papercut wearing a hat.

    `device_flag=False` is for a verb whose POSITIONAL names the device --
    `new-device <codename>`, `init <codename>`. There, `--device` is a second
    way to say the same thing that means the opposite: the selector picks an
    EXISTING profile, and those two verbs are handed the profile to create or
    adopt. Injecting it made `new-device --device X` set PORTHOLE_DEVICE to a
    profile that does not exist yet, so the command died in ProfileNotFound
    about the very thing it had been asked to create.

    SUPPRESS is load-bearing: without it argparse overwrites the global value
    with the subparser's default whenever the flag is absent after the verb, so
    `porthole --no-color doctor` would silently regain colour.
    """
    if device_flag:
        parser.add_argument("-d", "--device", default=argparse.SUPPRESS,
                            metavar="CODENAME",
                            help="act on this device profile")
    parser.add_argument("--no-color", "--no-colour", dest="no_color",
                        default=argparse.SUPPRESS, action="store_true",
                        help="plain output (also honours NO_COLOR)")
    return parser


# What a reader is trying to do, in the order they do it. `order` still sorts
# WITHIN a group; the group is what makes a 35-verb list scannable at all.
GROUPS = ("start", "build", "device", "sources", "knowledge", "meta")
GROUP_TITLES = {
    "start":     "getting set up",
    "build":     "building",
    "device":    "the device in front of you",
    "sources":   "pmaports and the kernel tree",
    "knowledge": "what this port knows",
    "meta":      "the toolkit itself",
}


def add_args(parser, args) -> None:
    """Attach a SPEC's args, honouring the porthole-only `group` key.

    ONE function, called from both the fast path and the full parser. Two
    copies of this loop is how `--no-color` came to work before the verb and
    not after it.
    """
    groups = {}
    for flags, kwargs in args:
        kwargs = dict(kwargs)
        name = kwargs.pop("group", "")
        target = parser
        if name:
            if name not in groups:
                groups[name] = parser.add_argument_group(name)
            target = groups[name]
        target.add_argument(*flags, **kwargs)


def build(root: pathlib.Path, specs: list[dict]) -> tuple[Parser, dict]:
    epilog = []
    width = max((len(s["verb"]) for s in specs), default=10)
    for group in GROUPS:
        rows = [s for s in specs if s.get("group") == group]
        if not rows:
            continue
        epilog.append("")
        epilog.append(GROUP_TITLES[group] + ":")
        for spec in rows:
            epilog.append(f"  {spec['verb']:<{width}}  {spec['help']}")
    epilog += [
        "",
        "examples:",
        "  porthole next                   where am I, and what is next?",
        "  porthole doctor                 is my host ready?",
        "  porthole init NAME              set this host up (safe to re-run)",
        "  porthole tools --needs BOOTED   which tools need a booted device",
        "  porthole brain search --severity law   the ten notes worth reading",
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
                spec["verb"],
                description=spec.get("description", spec["help"]),
                epilog=("examples:\n  " + "\n  ".join(spec["examples"])
                        if spec["examples"] else None),
                formatter_class=argparse.RawDescriptionHelpFormatter),
                device_flag=spec.get("device_flag", True))
            add_args(child, spec["args"])
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
        out.hint("porthole init <codename>   set this host up")
        if not profiles:
            out.hint("porthole new-device <codename>      port something new")
    else:
        out.hint("porthole next                       where am I, what is next?")
        out.hint("porthole doctor                     check the host and device")
        out.hint("porthole tools                      what can I run?")
    out.blank()
    out("`porthole --help` for all verbs.")
    return EX_OK


def main(argv: list[str], root: pathlib.Path) -> int:
    sys.path.insert(0, str(pathlib.Path(root) / "lib"))

    if argv and argv[0] in ("-V", "--version"):
        print(f"porthole {version(root)}")
        return EX_OK

    # The fast path: run ONE verb, importing only its module. Everything that
    # needs to see every SPEC -- the help epilogue, completion, an unknown verb
    # we want to suggest for -- falls through to full discovery, and none of
    # those is the hot path.
    spec = None
    if argv and not argv[0].startswith("-") and "--help" not in argv \
            and "-h" not in argv:
        spec = lookup(root, argv[0])

    if spec is not None:
        parser = Parser(prog="porthole", add_help=False)
        parser.add_argument("-d", "--device", metavar="CODENAME")
        parser.add_argument("--no-color", "--no-colour", action="store_true",
                            dest="no_color", default=False)
        sub = parser.add_subparsers(dest="verb")
        child = with_device(
            sub.add_parser(spec["verb"], add_help=True,
                           description=spec.get("description", spec["help"]),
                           formatter_class=argparse.RawDescriptionHelpFormatter),
            device_flag=spec.get("device_flag", True))
        add_args(child, spec["args"])
        child.set_defaults(_spec=spec)
        args = parser.parse_args(argv)
        out = Out(force_colour=False if getattr(args, "no_color", False) else None)
        return _run(args, out, root)

    specs = discover(root)
    parser, table = build(root, specs)

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
    return _run(args, out, root)


def _run(args, out: Out, root: pathlib.Path) -> int:
    """Invoke a verb and render its failures consistently."""
    ctx = Ctx(root, args, out)
    try:
        return args._spec["run"](args, ctx)
    except porthole.FastbootUnavailable as exc:
        # 69, not 1: no verb should have to restate this. A tool that could
        # not run is not a measurement that came back negative.
        out.error(str(exc))
        print(out.paint(
            f"  {out.sym('→', '->')} point FASTBOOT at a fastboot that exists "
            f"here, or install one -- `porthole doctor` names how.", "cyan"),
            file=sys.stderr)
        return EX_UNAVAILABLE
    except Bail as exc:
        out.error(exc.message)
        if exc.hint:
            # stderr, not stdout: an error and its remedy must not be split
            # across streams, or `2>log` captures the problem and loses the fix.
            print(out.paint(f"  {out.sym('→', '->')} {exc.hint}", "cyan"),
                  file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        return EX_INTERRUPT
    except BrokenPipeError:
        return EX_OK
