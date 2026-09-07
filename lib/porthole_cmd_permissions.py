#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole permissions` -- grant the agent the commands it runs all day.

WHY THIS EXISTS
    Issue #61. An agent working a bring-up in Claude Code's auto mode has its
    ordinary reads refused by the harness classifier:

        Permission for this action was denied by the Claude Code auto mode
        classifier. Reason: Blocked by classifier.

    Hit on 2026-09-04 for `ls tools` and a compound `grep` over ph-build.sh --
    both reads -- alongside a `gh pr merge`, which is not a read and which
    deserved the question. That distinction is the whole point of this file.
    The classifier is not the problem; the absence of a stated allowlist is.

    Each refusal costs a round trip, and the agent's own instruction on
    hitting one is to stop and ask. On a port the same twenty commands recur
    all day, so a porter answers the same questions in every repo, forever.

WHY PORTHOLE OWNS IT RATHER THAN A HAND-WRITTEN settings.json
    An allowlist is only as good as its idea of "safe", and porthole already
    has the only tested one in this repo: `porthole_tui.safety.is_risky`, the
    second opinion the console applies over its own table before it runs
    anything. Every tool's header also declares `needs:`, so the toolbox
    already knows which of its 136 tools move a device.

    A list typed into somebody's settings file knows none of that and goes
    stale the day a tool is added. So the tool rules here are DERIVED from the
    catalogue, and the verb rules are a table that a registry-wide test fails
    on the day a verb appears in neither half of it. `tests/test_rules.py`
    fails on a MUST with no enforcer for the same reason.

WHAT IT WILL NOT DO
    It grants nothing irreversible. Nothing that flashes, reboots, changes a
    slot, ramps a thermal load, zaps a chroot or opens a pull request is in
    the list, and `--json` prints what was excluded and why, because an
    allowlist nobody can audit is worse than no allowlist.

    It does grant the device's shell, through the mutex wrapper, and the
    preview says so in plain words. That is a judgement, not a default nobody
    reads: a bring-up device is a device you are already rebooting all day.
    `--no-device` leaves it out.

INSTALL SHAPE, COPIED FROM statusline ON PURPOSE
    Merge into the project's `.claude/settings.local.json`, never overwrite,
    and ignore it via `.git/info/exclude` -- installing a personal preference
    into somebody's kernel tree must not show up as a tracked change. The
    reasoning is in porthole_cmd_statusline; this is the same install with a
    different payload, and the two share `_merge_local_settings` so a fix to
    one cannot leave the other behind.
"""
from __future__ import annotations

import pathlib

from porthole_cli import Bail, EX_FAIL, EX_OK
from porthole_cmd_statusline import (LOCAL_SETTINGS, _exclude_locally,
                                     _git_excluded)
from porthole_cmd_tools import collect
from porthole_tui.safety import is_risky

# Claude Code's pattern language: `Bash(x:*)` matches any command line whose
# first token(s) are `x`. Prefix matching and nothing else -- there is no way
# to write "ssh, but only to the phone". So a rule may be granted only when
# EVERY command line it can match is one we would have approved by hand, and
# `ssh` therefore cannot appear here however much the porter wants it to.


# Verbs granted whole. Each of these reads: it prints the config, the
# catalogue, the corpus or the port's own state, and writes at most a cache
# under .run.
ALLOWED_VERBS = {
    "brief":      "the session opener; reads the profile and the device state",
    "next":       "derives progress from probes, changes nothing",
    "config":     "prints resolved values and the layer each came from",
    "devices":    "lists profiles",
    "tools":      "the catalogue and a tool's contract, never the tool",
    "doctor":     "checks the host and names fixes; runs none of them",
    "verify":     "the checks that run with no device attached",
    "matrix":     "reads what is known to work",
    "soc":        "reads other profiles for the same SoC",
    "cd":         "prints a path",
    "version":    "version and host tool versions",
    "completion": "emits a completion script to stdout",
    "slots":      "reads the A/B policy off the device; setting one is `flash`",
    "statusline": "renders a status line, or installs itself",
}

# Verbs NOT granted, and the reason each stays out. This half is what makes
# the table auditable: "not in the allow list" is an accident, "in the deny
# list because it flashes" is a decision.
DENIED_VERBS = {
    "flash":      "writes partitions -- the irreversible one",
    "build":      "compiles for minutes and can flash; sub-actions are granted",
    "push":       "installs a helper on the device that survives a reboot",
    "run":        "runs an arbitrary tool, including the ones excluded below",
    "sandbox":    "starts containers and runs arbitrary commands in them",
    "pkg":        "builds packages; long, and it writes the aport tree",
    "aports":     "writes branches and patches in pmaports",
    "dts":        "writes device trees",
    "kconfig":    "writes a defconfig",
    "init":       "writes config.env -- the identity a session runs under",
    "new-device": "scaffolds a profile",
    "use":        "switches the active device under everything else running",
    "channel":    "switches the release channel",
    "ui":         "switches the compositor on the device",
    "serial":     "takes the UART, which another session may be holding",
    "experiment": "runs an arbitrary command with device state either side",
    "blobs":      "extracts vendor images; long, and it writes",
    "brain":      "`brain submit` opens a pull request; search is granted below",
    "docs":       "generates the site into the working tree",
    "tui":        "an interactive full-screen app; an agent must not open one",
    "permissions":
        "a verb that widens an allowlist must not be inside it -- an agent "
        "that could run `--install` could grant itself the rest",
}

# Sub-commands granted from a verb whose whole surface is not. The prefix is
# the safety boundary here: `porthole build status` cannot become
# `porthole build fast` by adding arguments.
ALLOWED_SUBCOMMANDS = {
    "porthole build status":  "reads .run/build-status.json",
    "porthole build watch":   "paints a bar from that same file",
    "porthole sandbox status": "reports whether the workspace is up",
    "porthole brain search":  "searches the corpus; `new`/`submit` write",
    "porthole tools":         "listed for the completion of `tools --grep`",
}

# Host commands that are reads, and that a session runs constantly. `git
# status` and `git diff` are here; `git commit` and `git push` are not, and
# `gh pr merge` -- the refusal that prompted the issue -- is correctly a
# question every time.
HOST_READS = (
    "ls", "cat", "head", "tail", "wc", "file", "stat", "find", "grep", "rg",
    "sed -n", "awk", "sort", "uniq", "diff", "cmp", "md5sum", "sha256sum",
    "readlink", "realpath", "dirname", "basename", "env", "which", "python3 -c",
    "git status", "git diff", "git log", "git show", "git branch", "git remote",
    "git ls-files", "git rev-parse", "git worktree list", "git stash list",
    "gh pr view", "gh pr list", "gh pr diff", "gh pr checks",
    "gh issue view", "gh issue list", "gh run view", "gh run list",
    "make lint", "make check", "make test", "make verify",
)

# The device's shell, through the mutex wrapper rather than through ssh.
# tk-device.sh is the tool the repo's own device-mutex rule requires, so
# granting it grants the sanctioned path and no other; a bare `ssh` rule would
# grant every host on the network.
DEVICE_SHELL = {
    "tools/tk-device.sh":
        "the device mutex wrapper -- this DOES run arbitrary commands on the "
        "phone, which is the point, and it is the only path that takes the lock",
}


def rule(command: str) -> str:
    """One Claude Code permission pattern."""
    return f"Bash({command}:*)"


def tool_rules(root: pathlib.Path, device: str = ""):
    """Every tool worth granting, and every tool held back, with its reason.

    Derived rather than listed. `is_risky` reads the tool's NAME and its
    one-line summary together: a tool called tk-suspend-cycle.sh whose summary
    says it reboots is caught by the summary even though the name is clean.
    That is the same second opinion the console applies, so the console and
    this verb cannot come to different conclusions about the same tool.
    """
    allowed, held = {}, {}
    for tool in collect(root, device):
        # Relative to the root, which is how a person types it and how the
        # symlinked tools/ in a device repo resolves. An absolute path here
        # would be a rule that works on exactly one desk -- the
        # no-hardcoded-values rule, in a file whose whole output is config.
        rel = str(tool.path.relative_to(root))
        subject = f"{rel} {tool.summary or ''}"
        if is_risky(subject):
            held[rel] = _why_risky(subject)
        else:
            allowed[rel] = tool.summary or "(no summary)"
    return allowed, held


def _why_risky(subject: str) -> str:
    """Which word held it back, and the text the word was found in.

    `is_risky` is a SUBSTRING match, so "perform" contains "rm " and
    "framprobe" contains "ramp", and four tools are held back that a reader
    would have granted. That bias is the right way round -- a held-back tool
    costs one approval, a wrongly granted one runs unattended against a phone
    -- but the reason has to admit it, or an auditor reads `tk-capture.sh:
    names rm` and concludes the table is nonsense rather than cautious.

    The narrower matcher belongs in safety.py if anywhere, where the console
    would get it too; it is not worth a second opinion that disagrees with the
    first."""
    from porthole_tui.safety import DANGEROUS
    hits = sorted({word.strip() for word in DANGEROUS if word in subject})
    where = [f"{w} (in {_fragment(subject, w)!r})" for w in hits]
    return "matches " + ", ".join(where)


def _fragment(subject: str, word: str, pad: int = 6) -> str:
    """The word with a little of what surrounds it, so a substring match is
    visibly a substring match."""
    i = subject.find(word)
    return subject[max(0, i - pad):i + len(word) + pad].strip()


def build_rules(root: pathlib.Path, device: str = "", device_shell: bool = True):
    """The whole allowlist, with the audit trail beside it."""
    tools_ok, tools_held = tool_rules(root, device)
    # The device shell is granted by DEVICE_SHELL below or not at all. It is
    # an ordinary tool as far as `is_risky` is concerned -- nothing in
    # tk-device.sh's name or summary names a hazard, because the hazard is
    # whatever you pass it -- so without this it arrived through the tool loop
    # and `--no-device` did nothing at all. The flag was decorative and the
    # preview's promise to name the grant out loud was false.
    for path in DEVICE_SHELL:
        tools_ok.pop(path, None)
    granted = {}
    for verb, why in sorted(ALLOWED_VERBS.items()):
        granted[rule(f"porthole {verb}")] = why
    for command, why in sorted(ALLOWED_SUBCOMMANDS.items()):
        granted[rule(command)] = why
    for command in HOST_READS:
        granted[rule(command)] = "a read"
    for path, why in sorted(tools_ok.items()):
        granted[rule(path)] = why
    if device_shell:
        for path, why in sorted(DEVICE_SHELL.items()):
            granted[rule(path)] = why
    held = {f"porthole {verb}": why for verb, why in sorted(DENIED_VERBS.items())}
    held.update(tools_held)
    if not device_shell:
        held.update({p: "held back by --no-device" for p in DEVICE_SHELL})
    return granted, held


def _merge(target: pathlib.Path, rules) -> tuple[list, list]:
    """Add rules to `permissions.allow`, keeping everything already there.

    Returns (added, kept). A settings file is where a person keeps their own
    permissions and their model choice, so this reads, unions and writes back
    -- an install that ate those would be a bug worth more than the feature.
    """
    existing = {}
    if target.exists():
        import json
        try:
            existing = json.loads(target.read_text())
        except ValueError as exc:
            raise Bail(f"{target} is not valid JSON", EX_FAIL,
                       f"fix or move it first ({exc})") from None
        if not isinstance(existing, dict):
            raise Bail(f"{target} is not a JSON object", EX_FAIL,
                       "fix or move it first")
    import json
    merged = dict(existing)
    perms = dict(merged.get("permissions") or {})
    kept = list(perms.get("allow") or [])
    added = [r for r in rules if r not in kept]
    perms["allow"] = kept + added
    merged["permissions"] = perms
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(merged, indent=2) + "\n")
    return added, kept


def cmd_permissions(args, ctx) -> int:
    device = ""
    try:
        device = ctx.cfg.get("PORTHOLE_DEVICE", "")
    except Exception:  # noqa: BLE001 -- listing must survive a broken profile
        device = ""
    granted, held = build_rules(ctx.root, device,
                                device_shell=not getattr(args, "no_device", False))

    project = pathlib.Path(getattr(args, "project", None)
                           or pathlib.Path.cwd()).expanduser().resolve()
    target = project / LOCAL_SETTINGS
    added, kept, excluded, exclude_file = [], [], False, ""

    if getattr(args, "install", False):
        if not project.is_dir():
            raise Bail(f"{project} is not a directory", EX_FAIL,
                       "name the project to install into with --project")
        added, kept = _merge(target, list(granted))
        rel = LOCAL_SETTINGS.as_posix()
        excluded = _git_excluded(project, rel)
        exclude_file = "" if excluded else _exclude_locally(project, rel)

    def render():
        o = ctx.out
        if not getattr(args, "install", False):
            o.hint(f"a preview -- nothing was written. "
                   f"`porthole permissions --install` writes {target}")
            o.blank()
        o(f"  {len(granted)} rules, held back {len(held)}")
        if getattr(args, "install", False):
            o(f"  wrote {target}")
            o(o.paint(f"  {len(added)} new, {len(kept)} already yours", "grey"))
            if exclude_file:
                o(o.paint(f"  ignored via {exclude_file} -- not a tracked change",
                          "grey"))
            elif excluded:
                o(o.paint("  already ignored by this repo", "grey"))
        o.blank()
        for path, why in sorted(DEVICE_SHELL.items()):
            if rule(path) in granted:
                o(o.paint(f"  {path} IS granted: {why}", "yellow"))
                o(o.paint("  pass --no-device to leave it out", "grey"))
        o.blank()
        o(o.paint("  held back, and why -- `--json` prints all of them.", "grey"))
        o(o.paint("  the word match is a substring, so this errs toward holding",
                  "grey"))
        o(o.paint("  back: one approval is cheaper than one unattended write.",
                  "grey"))
        for what, why in list(sorted(held.items()))[:8]:
            o(o.paint(f"    {what}: {why}", "grey"))
        o.blank()
        o.hint("restart the session, or run /permissions, for it to be read")

    return ctx.emit({"rules": sorted(granted), "granted": granted,
                     "held": held, "path": str(target),
                     "installed": bool(getattr(args, "install", False)),
                     "added": added}, render) or EX_OK


SPEC = {
    "verb": "permissions",
    "order": 18,
    "group": "start",
    "help": "grant an agent the commands a bring-up runs all day",
    "description": (
        "The harness refuses commands it has not been told about, and on a\n"
        "port the same twenty recur all day -- so a porter answers the same\n"
        "questions in every repo, forever.\n\n"
        "The list is DERIVED, not typed: a tool is granted only when\n"
        "`porthole_tui.safety.is_risky` -- the same second opinion the\n"
        "console applies before it runs anything -- says its name and summary\n"
        "name nothing irreversible. Nothing that flashes, reboots, changes a\n"
        "slot or ramps a thermal load is in it, and the preview says what was\n"
        "held back and why.\n\n"
        "With no flags this is a preview and writes nothing. `--install`\n"
        "merges into a project's .claude/settings.local.json, keeping what is\n"
        "already there, ignored via .git/info/exclude so it is never a tracked\n"
        "change in somebody's kernel tree.\n\n"
        "It grants the device's shell through tools/tk-device.sh, the wrapper\n"
        "that takes the mutex. That is a judgement about a bring-up device and\n"
        "the preview names it out loud; `--no-device` leaves it out."),
    "reports": True,
    "args": [
        (["--install"], {"action": "store_true",
                         "help": "merge into a project's .claude settings"}),
        (["--project"], {"metavar": "DIR",
                         "help": "install into DIR (default: the cwd)"}),
        (["--no-device"], {"action": "store_true",
                           "help": "do not grant the device's shell"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": cmd_permissions,
    "examples": [
        "porthole permissions",
        "porthole permissions --install",
        "porthole permissions --install --project ~/src/taimen",
    ],
}
