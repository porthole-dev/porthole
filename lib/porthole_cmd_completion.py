# SPDX-License-Identifier: MIT
"""`porthole completion <shell>` -- emit a shell completion script.

Generated from the live registry rather than hand-maintained, so a new verb
gets completion for free and a stale completion file cannot drift out of sync
with the CLI. That is the whole reason not to check these in as static files.
"""
from __future__ import annotations

from porthole_cli import Bail, EX_OK, EX_USAGE, discover, grouped_help

BASH = """\
# porthole bash completion. Install with:
#   porthole completion bash > ~/.local/share/bash-completion/completions/porthole
_porthole() {{
    local cur prev verbs
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"
    verbs="{verbs}"

    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=($(compgen -W "$verbs --help --version" -- "$cur"))
        return
    fi

    case "$prev" in
        -d|--device)
            COMPREPLY=($(compgen -W "$(porthole devices --json 2>/dev/null \\
                | sed -n 's/.*\\"codename\\": \\"\\([^\\"]*\\)\\".*/\\1/p')" -- "$cur"))
            return ;;
    esac

    case "${{COMP_WORDS[1]}}" in
{cases}
    esac
}}
complete -F _porthole porthole
"""

ZSH = """\
#compdef porthole
# porthole zsh completion. Install with:
#   porthole completion zsh > "${{fpath[1]}}/_porthole"
_porthole() {{
    local -a verbs
    verbs=(
{verbs}
    )
    _arguments -C \\
        '1: :->verb' \\
        '*:: :->args'
    case $state in
        verb) _describe 'verb' verbs ;;
        args)
            case $words[1] in
{cases}
            esac ;;
    esac
}}
_porthole "$@"
"""

FISH = """\
# porthole fish completion. Install with:
#   porthole completion fish > ~/.config/fish/completions/porthole.fish
complete -c porthole -f
{lines}
"""


def cmd_completion(args, ctx) -> int:
    specs = discover(ctx.root)

    if args.shell == "bash":
        cases = "\n".join(
            f"        {s['verb']})\n"
            f"            COMPREPLY=($(compgen -W \"{_flags(s)}\" -- \"$cur\"))\n"
            f"            return ;;"
            for s in specs)
        print(BASH.format(verbs=" ".join(s["verb"] for s in specs), cases=cases))
    elif args.shell == "zsh":
        verbs = "\n".join(f"        '{s['verb']}:{s['help']}'" for s in specs)
        cases = "\n".join(
            f"                {s['verb']}) _arguments {_zsh_flags(s)} ;;"
            for s in specs)
        print(ZSH.format(verbs=verbs, cases=cases))
    elif args.shell == "fish":
        lines = []
        for spec in specs:
            lines.append(f"complete -c porthole -n __fish_use_subcommand "
                         f"-a {spec['verb']} -d {_q(spec['help'])}")
            for flags, kw in spec["args"]:
                for flag in flags:
                    if flag.startswith("--"):
                        lines.append(
                            f"complete -c porthole -n '__fish_seen_subcommand_from "
                            f"{spec['verb']}' -l {flag.lstrip('-')} "
                            f"-d {_q(grouped_help(kw))}")
        print(FISH.format(lines="\n".join(lines)))
    else:
        raise Bail(f"unsupported shell: {args.shell}", EX_USAGE,
                   "supported: bash, zsh, fish")
    return EX_OK


def _flags(spec) -> str:
    out = ["--device", "--help"]
    for flags, _ in spec["args"]:
        out += [f for f in flags if f.startswith("-")]
    return " ".join(dict.fromkeys(out))


def _zsh_flags(spec) -> str:
    parts = []
    for flags, kw in spec["args"]:
        for flag in flags:
            if flag.startswith("-"):
                parts.append(f"'{flag}[{grouped_help(kw)}]'")
    return " ".join(parts) or "'--help[show help]'"


def _q(text: str) -> str:
    return "'" + text.replace("'", "") + "'"


SPEC = {
    "verb": "completion",
    "order": 90,
    "group": "meta",
    "help": "emit a shell completion script (bash, zsh, fish)",
    "description": (
        "Generated from the live command registry, so a new verb gets\n"
        "completion for free and this can never drift out of sync."),
    "args": [(["shell"], {"choices": ["bash", "zsh", "fish"],
                          "help": "which shell"})],
    # Not a report: stdout is a shell script being redirected into a
    # completions directory.
    "reports": False,
    "run": cmd_completion,
    "examples": [
        "porthole completion bash > ~/.local/share/bash-completion/completions/porthole",
        "porthole completion fish > ~/.config/fish/completions/porthole.fish",
    ],
}
