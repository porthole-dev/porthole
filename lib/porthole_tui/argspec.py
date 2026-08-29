# SPDX-License-Identifier: MIT
"""argparse specs -> form fields, and back to a command string.

The old palette built `porthole <verb>` for every verb and stopped there, so it
could reach a verb's default behaviour and nothing past it: `blobs unsparse
vendor.img` was unreachable, and so was `serial console`. There was no argument
entry anywhere in the application and no filesystem navigation of any kind.

Every verb already declares its arguments, because bin/porthole needs them for
argparse. Reading those declarations costs nothing per verb and cannot go stale
-- the same property the glob registry gives `porthole --help`. A verb added
tomorrow gets a form the day it lands.

Pure, and deliberately so: tests/test_tui_argspec.py runs it over every verb
the registry discovers, on any interpreter, with nothing installed.
"""
from __future__ import annotations

import re
import shlex

KINDS = ("select", "check", "int", "float", "path", "text")

# Options that exist for pipes and agents. Inside the console they are noise --
# it renders its own output and never shells JSON anywhere.
HIDDEN = ("json", "no_color", "help")

# A dest whose value is a filesystem path. Checked against the dest and the
# metavar; the help text is the third signal, below.
PATH_DESTS = ("path", "out", "dir", "log", "dir_of", "port", "file",
              "outdir", "output", "src", "dest", "workdir", "pmaports")
PATH_METAVARS = ("PATH", "FILE", "DIR", "FILENAME", "DIRECTORY")
PATH_HELP = re.compile(r"\b(path|file|director|image|archive)", re.I)

# porthole_cli.with_device() injects these into EVERY verb's parser, so they
# appear in no verb's own `args` -- but they do appear in documented examples.
# A form must not offer them (the console already knows the device), and an
# example that uses one must still decompose.
GLOBAL_FLAGS = ("-d", "--device", "--no-color", "--no-colour")
_GLOBAL_TAKES_VALUE = ("-d", "--device")

# A bare shell operator means the example documents shell usage, not a verb
# invocation: `cd "$(porthole cd)"`, `porthole completion bash > ~/...`. Those
# cannot become form fields and must not be offered as clickable "try:" rows.
SHELL_OPERATORS = frozenset((">", ">>", "<", "|", "||", "&&", ";", "&"))


class Field:
    """One row of a generated form."""

    __slots__ = ("dest", "label", "kind", "flag", "choices", "help",
                 "required", "browse", "dangerous", "default", "variadic")

    def __init__(self, dest, label, kind, flag="", choices=None, help="",
                 required=False, browse=False, dangerous=False, default=None,
                 variadic=False):
        self.dest, self.label, self.kind = dest, label, kind
        self.flag, self.choices, self.help = flag, list(choices or []), help
        self.required, self.browse = required, browse
        self.dangerous, self.default = dangerous, default
        self.variadic = variadic

    def __repr__(self):
        return "Field({!r}, {!r})".format(self.dest, self.kind)


def _dest_of(flags, kw):
    if "dest" in kw:
        return kw["dest"]
    return flags[0].lstrip("-").replace("-", "_")


def _is_path(dest, kw):
    if (kw.get("metavar") or "").upper() in PATH_METAVARS:
        return True
    if dest in PATH_DESTS:
        return True
    return bool(PATH_HELP.search(kw.get("help") or ""))


def _kind(dest, kw):
    if kw.get("action") in ("store_true", "store_false"):
        return "check"
    if kw.get("choices"):
        return "select"
    typ = kw.get("type")
    if typ is int:
        return "int"
    if typ is float:
        return "float"
    if _is_path(dest, kw):
        return "path"
    return "text"


def fields(spec):
    """Turn a verb's SPEC into the rows of its form.

    Positionals keep their declared order and come first; that is the order
    build() reassembles them in, and the order argparse expects.
    """
    from .safety import DANGEROUS_FIELDS
    out = []
    for flags, kw in spec.get("args", []):
        dest = _dest_of(flags, kw)
        if dest in HIDDEN:
            continue
        positional = not flags[0].startswith("-")
        kind = _kind(dest, kw)
        out.append(Field(
            dest=dest,
            label=(dest.upper().replace("_", "-") if positional
                   else max(flags, key=len)),
            kind=kind,
            flag="" if positional else max(flags, key=len),
            choices=kw.get("choices"),
            help=kw.get("help") or "",
            required=positional and kw.get("nargs") not in ("?", "*"),
            browse=(kind == "path"),
            dangerous=any(w in dest for w in DANGEROUS_FIELDS),
            default=kw.get("default"),
            variadic=kw.get("nargs") in ("*", "+", "..."),
        ))
    return out


def needs_arguments(spec):
    """Does this verb do anything interesting beyond its bare form?"""
    return bool(fields(spec))


# `~` is safe here and deliberately left unquoted: the command line is shown to
# a human before it runs, and `'~/dl/vendor.img'` reads as a mistake. It is
# expanded in jobs.py before exec, because exec has no shell to expand it.
_SAFE = re.compile(r"^[\w@%+=:,./~-]+$")


def _render(value):
    text = str(value)
    return text if _SAFE.match(text) else shlex.quote(text)


def _render_value(field, value):
    # A variadic value holds several arguments as one whitespace-separated
    # string, so quoting it as ONE token would send `brain 'new my-note-id'`
    # -- a single argv element the verb cannot parse. Render each token
    # separately so build() emits them as separate argv elements.
    if getattr(field, "variadic", False):
        return " ".join(_render(tok) for tok in str(value).split())
    return _render(value)


def build(verb, fields_, values):
    """Assemble the command. Positionals in declared order, then flags.

    Empty strings, None and False are omitted rather than passed along: a form
    with eight optional fields would otherwise build a command of eight empty
    arguments, and argparse would take them literally.
    """
    parts = ["porthole", verb]
    for f in fields_:
        if f.flag:
            continue
        value = values.get(f.dest)
        if value in (None, "", False):
            continue
        parts.append(_render_value(f, value))
    for f in fields_:
        if not f.flag:
            continue
        value = values.get(f.dest)
        if value in (None, "", False):
            continue
        if f.kind == "check":
            parts.append(f.flag)
        else:
            parts.extend([f.flag, _render_value(f, value)])
    return " ".join(parts)


def tokenise(example):
    """Shell-split an example, stripping any trailing `# comment`."""
    try:
        return shlex.split(example, comments=True)
    except ValueError:
        return None


def is_direct(example, verb):
    """Is this a plain `porthole <verb> ...` invocation the form can fill from?

    False for the examples that document shell usage rather than a verb call.
    The form must not offer those as "try:" rows -- pressing one would
    silently do nothing.
    """
    tokens = tokenise(example)
    return bool(tokens) and tokens[:2] == ["porthole", verb] \
        and not (SHELL_OPERATORS & set(tokens))


def parse_example(example, verb, fields_):
    """Decompose a SPEC example back into field values, or None.

    The form's "try:" rows use this: pressing one back-fills every field, which
    is only honest if the example actually maps onto the fields. The test
    asserts it for every direct-invocation example in the repository.
    """
    tokens = tokenise(example)
    if not tokens or tokens[:2] != ["porthole", verb]:
        return None
    rest = tokens[2:]
    by_flag = {}
    for f in fields_:
        if f.flag:
            by_flag[f.flag] = f
            by_flag[f.flag.replace("_", "-")] = f
    positionals = [f for f in fields_ if not f.flag]
    values, index = {}, 0
    while rest:
        token = rest.pop(0)
        if token.startswith("-"):
            flag, _, inline = token.partition("=")
            field = by_flag.get(flag)
            if field is None:
                if flag in GLOBAL_FLAGS:
                    # Injected into every verb's parser by with_device(), so no
                    # verb's own fields know it. Skipping the flag without also
                    # consuming its value would leak the codename in as the
                    # next positional.
                    if flag in _GLOBAL_TAKES_VALUE and not inline and rest:
                        rest.pop(0)
                    continue
                if flag.lstrip("-").replace("-", "_") in HIDDEN:
                    continue
                return None
            if field.kind == "check":
                values[field.dest] = True
            elif field.variadic:
                # nargs="..." on a flag means "everything after it is the
                # value" (porthole sandbox shell --command pmbootstrap
                # status): a single rest.pop(0) would strand later tokens
                # as unmatched positionals, same failure mode as below.
                group = [inline] if inline else []
                group.extend(rest)
                rest.clear()
                values[field.dest] = " ".join(group)
            else:
                values[field.dest] = inline or (rest.pop(0) if rest else "")
        else:
            if index >= len(positionals):
                return None
            field = positionals[index]
            if field.variadic:
                group = [token]
                while rest and not rest[0].startswith("-"):
                    group.append(rest.pop(0))
                values[field.dest] = " ".join(group)
            else:
                values[field.dest] = token
            index += 1
    return values
