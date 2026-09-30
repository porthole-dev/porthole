# SPDX-License-Identifier: MIT
"""`porthole docs` -- assemble the documentation site.

The site is *generated*, never hand-maintained. Its CLI reference comes from
the command registry, its tool catalogue from the tool headers, its profile-key
reference from `profiles/_template/device.env`, and its knowledge base from
`brain/`. Every page therefore has exactly one source of truth, and a page that
disagrees with the code is impossible rather than merely unlikely.

The published site is plain static HTML. Markdown is rendered at build time;
the browser gets no framework, theme runtime, or remote font requests.
"""
from __future__ import annotations

import os
import pathlib
import posixpath
import re
import shutil
import subprocess
import sys
import json

from porthole_cli import (Bail, EX_FAIL, EX_OK, EX_USAGE, discover,
                          child_env, grouped_help, version)

SITE_SRC = "site-src"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ------------------------------------------------------------- generators --

def page_cli(root: pathlib.Path) -> str:
    """The CLI reference, from the live command registry."""
    specs = discover(root)
    out = ["# Command reference", "",
           "Generated from the command registry. Every verb is one "
           "`lib/porthole_cmd_<name>.py` file exporting a `SPEC` dict.", "",
           "| verb | what it does |", "|---|---|"]
    for spec in specs:
        out.append(f"| [`{spec['verb']}`](#{_slug(spec['verb'])}) | {spec['help']} |")
    out.append("")

    for spec in specs:
        out += [f"## {spec['verb']}", ""]
        desc = spec.get("description") or spec["help"]
        out += [desc, ""]
        if spec.get("args"):
            out += ["| argument | description |", "|---|---|"]
            for flags, kwargs in spec["args"]:
                names = ", ".join(f"`{f}`" for f in flags)
                help_text = grouped_help(kwargs).replace("|", "\\|")
                choices = kwargs.get("choices")
                if choices:
                    help_text += f" *(one of: {', '.join(map(str, choices))})*"
                out.append(f"| {names} | {help_text} |")
            out.append("")
        if spec.get("examples"):
            out += ["```console"]
            for ex in spec["examples"]:
                out.append(f"$ {ex}")
            out += ["```", ""]
    return "\n".join(out)


def page_tools(root: pathlib.Path) -> str:
    """The tool catalogue, from each tool's own header."""
    sys.path.insert(0, str(root / "lib"))
    from porthole_cmd_tools import collect

    profiles = [p.name for p in sorted((root / "profiles").iterdir())
                if p.is_dir() and not p.name.startswith("_")]
    tools = collect(root)
    for profile in profiles:
        tools += [t for t in collect(root, profile) if "profiles" in t.path.parts]

    out = ["# Tool catalogue", "",
           f"{len(tools)} tools, generated from their own headers.", "",
           "`needs` is the device state a tool requires: `-` host-only, "
           "`BOOTED`, `FASTBOOT`, `on-device` (it runs *on* the device), or "
           "`any` (it probes and handles several).", "",
           "!!! tip \"Searching this from a shell\"",
           "    `porthole tools --grep <what>` and `porthole tools --needs "
           "BOOTED` do the same filtering, and `porthole tools <name>` prints "
           "one tool's contract.", ""]

    by_scope: dict[str, list] = {}
    for tool in tools:
        by_scope.setdefault(tool.scope or "generic", []).append(tool)

    for scope in sorted(by_scope, key=lambda s: (s != "generic", s)):
        out += [f"## `{scope}`", "",
                "| tool | needs | what it does |", "|---|---|---|"]
        for tool in sorted(by_scope[scope], key=lambda t: t.name):
            needs = (tool.needs or "-").split("(")[0].strip() or "-"
            summary = (tool.summary or "").replace("|", "\\|")[:110]
            out.append(f"| `{tool.name}` | {needs} | {summary} |")
        out.append("")
    return "\n".join(out)


def page_profile_keys(root: pathlib.Path) -> str:
    """The profile-key reference, from the documented template."""
    template = root / "profiles" / "_template" / "device.env"
    out = ["# Device profile keys", "",
           "Every key a device profile can set, from "
           "`profiles/_template/device.env`.", "",
           "Keys marked **TRAP** encode a lesson from a previous port. Getting "
           "one wrong does not produce an error -- it produces a confident "
           "wrong answer.", ""]
    if not template.is_file():
        return "\n".join(out + ["*(template missing)*"])

    section = None
    pending: list[str] = []
    for raw in template.read_text().splitlines():
        line = raw.rstrip()
        header = re.match(r"^#\s*-{4,}\s*(.+?)\s*-{2,}$", line)
        if header:
            section = header.group(1).strip()
            out += ["", f"## {section.title()}", ""]
            pending = []
            continue
        if line.startswith("#"):
            body = line.lstrip("#").strip()
            if body and not set(body) <= set("-= "):
                pending.append(body)
            continue
        m = re.match(r'^([A-Z_]+)=(.*)$', line)
        if not m:
            continue
        key, default = m.group(1), m.group(2)
        inline = ""
        if "#" in default:
            default, _, inline = default.partition("#")
        default = default.strip().strip('"')
        notes = " ".join(pending + ([inline.strip()] if inline.strip() else []))
        pending = []
        trap = "**TRAP** " if "(TRAP)" in notes else ""
        notes = notes.replace("(TRAP)", "").strip()
        default_txt = f"`{default}`" if default else "*(unset)*"
        out.append(f"### `{key}`")
        out.append("")
        out.append(f"default: {default_txt}")
        if notes:
            out += ["", f"{trap}{notes}"]
        out.append("")
    return "\n".join(out)


def _brain_map(brain: pathlib.Path) -> dict[str, pathlib.Path]:
    """note id -> its path, so a [[wikilink]] can be resolved across sections.

    A law is linked from a workflow note and a trap from a playbook; resolving
    the link relative to the *linking* file points at a sibling that does not
    exist. The id is globally unique by convention, so map it once.
    """
    out: dict[str, pathlib.Path] = {}
    for path in sorted(brain.rglob("*.md")):
        if path.name == "INDEX.md":
            continue
        out[path.stem] = path.relative_to(brain)
        # Prefer the frontmatter id where it differs from the filename.
        try:
            head = path.read_text(errors="replace")[:400]
        except OSError:
            continue
        m = re.search(r"^id:\s*(\S+)", head, re.M)
        if m:
            out[m.group(1)] = path.relative_to(brain)
    return out


def _resolve_links(body: str, here: pathlib.Path,
                   index: dict[str, pathlib.Path]) -> str:
    """Turn [[note-id]] into a working relative markdown link."""
    def replace(match):
        target = match.group(1).split("/")[-1]
        dest = index.get(target)
        if dest is None:
            # A link to a note nobody has written yet is a marker, not an
            # error -- brain/README.md says to link liberally. Render it as
            # plain text rather than a broken link.
            return f"`{target}`"
        rel = os.path.relpath(dest, here.parent)
        return f"[{target}]({pathlib.PurePosixPath(rel)})"
    return re.sub(r"\[\[([a-z0-9/-]+)\]\]", replace, body)


def copy_brain(root: pathlib.Path, dest: pathlib.Path) -> dict[str, list]:
    """Copy brain notes, turning frontmatter into a rendered header."""
    sections: dict[str, list] = {}
    topics = {}
    brain = root / "brain"
    index = _brain_map(brain)
    for path in sorted(brain.rglob("*.md")):
        rel = path.relative_to(brain)
        if rel.name == "INDEX.md":
            continue
        section = rel.parts[0]
        text = path.read_text(errors="replace")
        meta, body = {}, text
        m = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", text, re.S)
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip().strip('"')
            body = m.group(2)

        header = []
        if meta.get("title"):
            header += [f"# {meta['title']}", ""]
        chips = []
        for field in ("scope", "severity", "confidence", "subsystem"):
            if meta.get(field):
                chips.append(f"**{field}**: `{meta[field]}`")
        if chips:
            header += [" · ".join(chips), ""]
        if meta.get("evidence"):
            header += [f'!!! quote "Evidence"', f"    {meta['evidence']}", ""]

        body = _resolve_links(body, rel, index)

        target = dest / "brain" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(header) + body)
        sections.setdefault(section, []).append(
            (meta.get("title", rel.stem), f"brain/{rel.as_posix()}"))
        topic = meta.get("subsystem", "general")
        topics.setdefault(topic, []).append((meta.get("title", rel.stem), rel, meta))
    directory = dest / "brain" / "topics"
    directory.mkdir(parents=True, exist_ok=True)
    browse = ["# Knowledge base", "", "Find device notes, troubleshooting lessons, and development playbooks by topic. Use the site search for a symptom, command, or note title.", "", "## Start with the essentials", "", "[Evidence laws](laws/every-test-needs-a-positive-control.md) · [Agent workflow](workflow/agent-protocol.md) · [Playbooks](playbooks/00-device-protocol.md)", "", "## Browse by topic", "", "| Topic | Notes |", "|---|---|"]
    for topic, notes in sorted(topics.items()):
        slug = re.sub(r"[^a-z0-9-]+", "-", topic.lower()).strip("-") or "general"
        browse.append("| [{}](topics/{}.md) | {} |".format(topic.capitalize(), slug, len(notes)))
        page = ["# " + topic.capitalize(), "", "[All topics](../browse.md)", "", "| Note | Type | Scope | Confidence |", "|---|---|---|---|"]
        for title, rel, meta in notes:
            page.append("| [{}](../{}) | {} | {} | {} |".format(title.replace("|", "\\|"), rel.as_posix(), meta.get("severity", rel.parts[0]), meta.get("scope", "general"), meta.get("confidence", "—")))
        (directory / (slug + ".md")).write_text("\n".join(page) + "\n")
    (dest / "brain" / "browse.md").write_text("\n".join(browse) + "\n")

    return sections


def _refresh_agents(ctx) -> bool:
    path = pathlib.Path(ctx.root) / "AGENTS.md"
    try:
        fresh = render_agents(ctx.root)
    except Exception:  # noqa: BLE001
        return False
    if fresh != path.read_text():
        path.write_text(fresh)
        return True
    return False


def _docs_links(text: str, pages) -> str:
    """Rewrite links BETWEEN copied pages, because the copy renames them.

    `docs/NEW-HOST.md` is published as `new-host.md`, so a sibling page linking
    to `NEW-HOST.md` -- which is the correct link in the repository, and what
    GitHub renders -- points at nothing on the site. The built-site link check
    catches this before publication.

    Done here rather than by lowercasing the filenames on disk: the repository
    is the primary artefact and `docs/NEW-HOST.md` is the name people link to
    from commit messages and issues. The site is the copy, so the site adapts.

    A FRAGMENT SURVIVES THE REWRITE. This matched on the closing paren --
    `text.replace(f"](docs/{page})", ...)` -- so an anchored cross-reference
    like `](docs/CONFIG.md#legacy-names)` matched nothing and reached the site
    still pointing at `docs/CONFIG.md`, which does not exist there. mkdocs
    check turns that into a failed docs build: one anchored link added to
    AGENTS.md took CI down while every unanchored link on the page kept
    working, so the rewrite looked correct right up until somebody used an
    anchor.
    """
    for page in pages:
        low = page.lower()
        # `](PAGE.md` or `](docs/PAGE.md`, then either `)` or `#fragment)`.
        pattern = re.compile(r"\]\((?:docs/)?" + re.escape(page)
                             + r"(#[^)]*)?\)")
        text = pattern.sub(lambda m, low=low: f"]({low}{m.group(1) or ''})",
                           text)
    return text


def cmd_build(args, ctx) -> int:
    agents_changed = _refresh_agents(ctx)
    root = ctx.root
    src = root / SITE_SRC
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)

    # -- hand-written pages, copied verbatim --
    (src / "index.md").write_text(
        _readme_as_index((root / "README.md").read_text()))
    pages = ["NEW-HOST.md", "SANDBOX.md", "CONFIG.md", "ARCHITECTURE.md",
             "PERFORMANCE.md", "CONTRIBUTING.md", "AGENTS-RULES.md",
             "RELEASES.md", "PROJECT-STATUS.md", "WORKING-GUIDE.md", "TOOLS.md",
             "UPSTREAM-NAMING.md"]
    for name, title in [("NEW-HOST.md", "Setting up a new host"),
                        ("SANDBOX.md", "Running pmbootstrap safely"),
                        ("CONFIG.md", "Configuration"),
                        ("ARCHITECTURE.md", "Architecture"),
                        ("PERFORMANCE.md", "Performance"),
                        ("CONTRIBUTING.md", "Contributing"),
                        # The narrative behind section 1, split out of
                        # AGENTS.md. It has to be a page: AGENTS.md links to
                        # it, and _docs_links rewrites docs/ links for the
                        # flat site, so an unregistered target 404s there
                        # while working fine in the repository.
                        ("AGENTS-RULES.md", "The rules, and what they cost")]:
        source = root / "docs" / name
        if source.is_file():
            (src / name.lower()).write_text(_docs_links(source.read_text(), pages))
    agents = root / "AGENTS.md"
    if agents.is_file():
        # THROUGH _docs_links, like every other copied page. This was a plain
        # copy, so AGENTS.md's links into docs/ reached the site unrewritten.
        # It went unnoticed because AGENTS.md happened to contain none until
        # one `](docs/CONFIG.md#legacy-names)` was added, and mkdocs strict
        # failed the whole docs build on it.
        (src / "agents.md").write_text(_docs_links(agents.read_text(), pages))

    # -- generated reference --
    (src / "cli.md").write_text(page_cli(root))
    (src / "tools.md").write_text(page_tools(root))
    (src / "profile-keys.md").write_text(page_profile_keys(root))
    from porthole_cmd_release import catalogue, markdown_catalogue, markdown_downloads
    release_data = catalogue(root, getattr(args, "catalog", None) or root / "releases")
    public_devices = root / ".run/public-downloads/devices.json"
    markdown_catalogue(release_data, src / "devices",
                       json.loads(public_devices.read_text()) if public_devices.is_file() else None)
    (src / "downloads.md").write_text(markdown_downloads(release_data))
    public_downloads = root / ".run" / "public-downloads"
    for name, title in (("packages", "Signed APK packages"), ("images", "Device downloads")):
        source = public_downloads / (name + ".md")
        text = source.read_text() if source.is_file() else "# " + title + "\n\nThe published catalog is refreshed by the website deployment workflow.\n"
        (src / (name + ".md")).write_text(text)

    for name in ("RELEASES.md", "PROJECT-STATUS.md", "WORKING-GUIDE.md", "PIPELINES.md", "ABOUT.md"):
        (src / name.lower()).write_text(_readme_as_index(_docs_links(
            (root / "docs" / name).read_text(), pages)))
    (src / "upstream-naming.md").write_text(_docs_links(
        (root / "docs" / "UPSTREAM-NAMING.md").read_text(), pages))

    # -- the brain --
    sections = copy_brain(root, src)

    nav = [
        ("Home", "index.md"),
        ("Downloads", [("Device images", "images.md"),
                       ("Signed APK packages", "packages.md"),
                       ("Release evidence", "downloads.md")]),
        ("Release pipelines", "pipelines.md"),
        ("Devices", [("Device directory", "devices/index.md")] + [
            (d["policy"]["name"], "devices/{}.md".format(d["device"]))
            for d in release_data["devices"]]),
        ("About Porthole", "about.md"),
        ("Project status", "project-status.md"),
        ("Get started", [
            ("Working guide", "working-guide.md"),
            # First, and deliberately: it is the page a new host needs, and it
            # was the one that did not exist.
            ("Setting up a new host", "new-host.md"),
            ("Running pmbootstrap safely", "sandbox.md"),
            ("Configuration", "config.md"),
            ("Performance", "performance.md"),
            ("Architecture", "architecture.md"),
            ("Contributing", "contributing.md"),
            ("Building and reviewing releases", "releases.md"),
            ("Nura naming and upstream compatibility", "upstream-naming.md"),
        ]),
        ("Developer reference", [
            ("Commands", "cli.md"),
            ("Tools", "tools.md"),
            ("Profile keys", "profile-keys.md"),
        ]),
        ("For agents", [
            ("AGENTS.md", "agents.md"),
            ("The rules, and what they cost", "agents-rules.md"),
        ]),
    ]
    brain_nav = [("Browse by topic", "brain/browse.md")]
    if (src / "brain" / "README.md").is_file():
        brain_nav.append(("How to read this", "brain/README.md"))
    for section in ("laws", "traps", "findings", "playbooks", "workflow", "devices",
                    "memory"):
        if section in sections:
            brain_nav.append((section.title(),
                              sorted(sections[section], key=lambda x: x[1])))
    if brain_nav:
        nav.append(("Knowledge base", brain_nav))

    app = root / "site-app"
    pages = _starlight_content(src, app / "src" / "content" / "docs", nav, app)

    def render():
        o = ctx.out
        o(f"{o.paint(o.sym('✓', 'ok'), 'green')} generated {pages} pages "
          f"into {SITE_SRC}/")
        o(f"  staged Starlight content for {args.org or 'porthole-dev'}/"
          f"{args.repo or 'porthole'} in site-app/")
        o.blank()
        o(o.paint("  Nothing here is hand-maintained: the CLI reference comes "
                  "from the\n  command registry, the tool catalogue from the "
                  "tool headers, the\n  profile keys from the template, and the "
                  "knowledge base from brain/.", "grey"))
        o.blank()
        o.hint("porthole docs serve", "preview at http://127.0.0.1:8000/porthole/")
        o.hint("git push — CI builds it; PUBLISH_DOCS=true enables Pages")

    return ctx.emit({"pages": pages, "src": str(src)}, render)


# The README links by repository path; the site is flat. Rewriting these is
# what lets one README serve both audiences instead of maintaining two.
README_LINK_MAP = {
    "docs/WORKING-GUIDE.md": "working-guide.md",
    "docs/NEW-HOST.md": "new-host.md",
    "docs/SANDBOX.md": "sandbox.md",
    "docs/CONFIG.md": "config.md",
    "docs/PERFORMANCE.md": "performance.md",
    "docs/ARCHITECTURE.md": "architecture.md",
    "docs/CONTRIBUTING.md": "contributing.md",
    "docs/AGENTS-RULES.md": "agents-rules.md",
    "docs/TOOLS.md": "tools.md",
    "docs/RELEASES.md": "releases.md",
    "docs/PROJECT-STATUS.md": "project-status.md",
    "docs/UPSTREAM-NAMING.md": "upstream-naming.md",
    "AGENTS.md": "agents.md",
    "../AGENTS.md": "agents.md",
    "../skills/porthole-bringup/": "https://github.com/porthole-dev/porthole/tree/main/skills/porthole-bringup",
    "../SECURITY.md": "https://github.com/porthole-dev/porthole/blob/main/SECURITY.md",
    "../LICENSE": "https://github.com/porthole-dev/porthole/blob/main/LICENSE",
    "../AI.md": "https://github.com/porthole-dev/porthole/blob/main/AI.md",
    "CONTRIBUTING.md": "contributing.md",
    "../brain/": "brain/README.md",
    "../profiles/": "https://github.com/porthole-dev/porthole/tree/main/profiles",
    "skills/porthole-bringup/": "https://github.com/porthole-dev/porthole/tree/main/skills/porthole-bringup",
    "LICENSE": "https://github.com/porthole-dev/porthole/blob/main/LICENSE",
    "AI.md": "https://github.com/porthole-dev/porthole/blob/main/AI.md",
    "SECURITY.md": "https://github.com/porthole-dev/porthole/blob/main/SECURITY.md",
}


def _readme_as_index(text: str) -> str:
    """The README works as a landing page, minus its table of contents.

    The site has real navigation, so an in-page TOC of anchors that no longer
    all exist is worse than none.
    """
    for old, new in README_LINK_MAP.items():
        text = text.replace(f"]({old})", f"]({new})")
    out, skipping = [], False
    for line in text.splitlines():
        if line.strip() == "## Table of contents":
            skipping = True
            continue
        if skipping:
            if line.startswith("---") or (line.startswith("## ")):
                skipping = False
            else:
                continue
        out.append(line)
    return "\n".join(out)


def _starlight_content(src: pathlib.Path, content: pathlib.Path, nav,
                       app: pathlib.Path) -> int:
    """Copy generated Markdown into Starlight's content collection."""
    if content.exists():
        shutil.rmtree(content)
    content.mkdir(parents=True)
    for page in src.rglob("*.md"):
        rel = page.relative_to(src)
        if rel.as_posix() == "index.md":
            continue  # the editorial splash page is maintained in site-assets
        text = page.read_text()
        # Starlight leaves Markdown links as written; its routes have no .md
        # suffix and live one directory below each source page's directory.
        def route(match):
            target = posixpath.normpath(posixpath.join(
                rel.parent.as_posix(), match.group(1)))
            if not (src / target).is_file():
                return match.group(0)
            current = (rel.parent if rel.name == "index.md" else
                       rel.with_suffix("")).as_posix()
            destination = (posixpath.dirname(target) if target.endswith("/index.md")
                           else target[:-3]).lower()
            href = posixpath.relpath(destination, current) + "/"
            return href + (match.group(2) or "")
        text = re.sub(r"(?<=\]\()([^()\s]+\.md)(#[^()\s]*)?(?=\))",
                      route, text)
        text = re.sub(r"(?m)^([ \t]*)(`{3,}|~{3,})dts\s*$",
                      r"\1\2typescript", text)
        match = re.search(r"(?m)^# (.+?)\s*$", text)
        title = match.group(1).strip() if match else page.stem.replace("-", " ").title()
        if match:
            text = text[:match.start()] + text[match.end():]
        lines, output, i = text.splitlines(), [], 0
        while i < len(lines):
            aside = re.match(r'^(\s*)!!!\s+(\w+)(?:\s+["\'](.+?)["\'])?\s*$', lines[i])
            if not aside:
                output.append(lines[i]); i += 1; continue
            indent, kind, heading = aside.groups()
            kind = {"warning": "caution", "quote": "note", "info": "note"}.get(kind, kind)
            if kind not in ("note", "tip", "caution", "danger"):
                kind = "note"
            output.append(indent + ":::" + kind + ("[" + heading + "]" if heading else ""))
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i].startswith(indent + "    ")):
                line = lines[i]
                output.append(line[len(indent) + 4:] if line.startswith(indent + "    ") else "")
                i += 1
            output.append(indent + ":::")
        frontmatter = "---\ntitle: {}\n---\n\n".format(json.dumps(title, ensure_ascii=False))
        destination = content / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(frontmatter + "\n".join(output).strip() + "\n")
    shutil.copyfile(src.parent / "site-assets" / "home.mdx", content / "index.mdx")
    styles = app / "src" / "styles"
    styles.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src.parent / "site-assets" / "editorial.css",
                    styles / "editorial.css")
    assets = app / "src" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src.parent / "site-assets" / "mark.svg",
                    assets / "porthole.svg")
    public = app / "public"
    public.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src.parent / "site-assets" / "mark.svg",
                    public / "porthole.svg")

    web_releases = src.parent / ".run/public-downloads/web-install.json"
    (public / "install-releases.json").write_text(
        web_releases.read_text() if web_releases.is_file() else "[]\n")

    def items(entries):
        result = []
        for label, target in entries:
            if isinstance(target, list):
                group = {"label": label, "items": items(target)}
                if len(target) > 8:
                    group["collapsed"] = True
                result.append(group)
            elif target == "index.md":
                result.append({"label": label, "link": "/"})
            elif target.endswith("/index.md"):
                result.append({"label": label, "slug":
                               pathlib.PurePosixPath(target).parent.as_posix().lower()})
            else:
                result.append({"label": label, "slug":
                               pathlib.PurePosixPath(target).with_suffix("").as_posix().lower()})
        return result
    navigation = items(nav)
    for group in navigation:
        if group.get("label") == "Downloads":
            group["items"].insert(1, {"label": "Browser installer", "link": "/install/"})
    (app / "sidebar.generated.mjs").write_text(
        "export default " + json.dumps(navigation, ensure_ascii=False) + ";\n")
    return sum(1 for _ in content.rglob("*.md*"))


def cmd_serve(args, ctx) -> int:
    env = child_env()
    env["ASTRO_TELEMETRY_DISABLED"] = "1"
    # Astro can leave a background server holding yesterday's content index.
    # It exits successfully when `dev` sees that server, so rebuild first would
    # make a fresh sidebar point into the stale index (downloads, 2026-09-29).
    subprocess.run(["npm", "run", "dev", "--", "stop"],
                   cwd=ctx.root / "site-app", env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cmd_build(args, ctx)
    return subprocess.run(["npm", "run", "dev", "--", "--host", "127.0.0.1",
                           "--port", "8000", "--force"], cwd=ctx.root / "site-app",
                          env=env).returncode


# --------------------------------------------------------- device documents --
#
# Three surfaces, three jobs, and one question that routes any document:
#
#   Does it generalise past this device?  -> brain/            (scope:, evidence:)
#   Is it derivable from code?            -> generated docs/   (never hand-written)
#   Is it this device, this week?         -> $PORTHOLE_WORKDIR/docs/
#
# The third is where a port sprawls. taimen/docs/ reached 65 flat entries --
# HANDOFF-* x19, FINDING-*, MORNING-REPORT-*, NIGHT-SESSION-*, plus status/,
# campaign/ and BLUEPRINT/ -- while brain/devices/google-taimen/ held one file.
# The knowledge went where it was easiest to drop, not where it belonged.
#
# So device documents get a shape, derived from what those 65 files ACTUALLY
# are rather than from categories invented for the occasion:
#
#   docs/status.md              ONE living document. Overwritten, never appended.
#   docs/log/YYYY-MM-DD-topic.md  everything dated.
#
# Two buckets, not four. Handoff, finding, session and report are all "what
# happened on date X"; that distinction was never load-bearing, and splitting on
# it is how you get four directories holding the same kind of file. `kind:` in
# the frontmatter carries it instead, where it costs nothing.

KINDS = ("handoff", "finding", "session", "status")

LOG_TEMPLATE = """\
---
date: {date}
kind: {kind}
subsystem: {subsystem}
status: open
---

# {title}

## What happened

## Evidence

<!-- Commands run and their output. A claim with no evidence is folklore, and
     folklore is what brain/ exists to replace. -->

## What is still open

<!-- If something here turns out to generalise past this device, promote it:
     `porthole brain new <id> --section traps`. That is the ONLY route from a
     dated note into the second brain, and it forces scope: and evidence:. -->
"""

STATUS_TEMPLATE = """\
---
device: {device}
updated: {date}
---

# {device} — status

The one living document. Overwrite it; do not append. Anything with a date
belongs in `log/` instead.

## Works

## Does not work

## Unknown

<!-- An empty reading means unknown, never "changed". Say which you mean. -->

## Next
"""


def _docs_root(ctx):
    """$PORTHOLE_WORKDIR/docs -- the device repo, never porthole's own tree.

    Device documents must not land in porthole: it is a shareable toolkit, and
    someone cloning it should not receive nineteen handoff notes about a phone
    they do not own.
    """
    workdir = ctx.cfg.get("PORTHOLE_WORKDIR", "")
    if not workdir:
        raise Bail("no working repo for this device", EX_FAIL,
                   "porthole init    finds or creates one, or "
                   "`porthole use <codename> --workdir <path>`")
    root = pathlib.Path(workdir).expanduser()
    if not root.is_dir():
        raise Bail(f"{root} does not exist", EX_FAIL,
                   "porthole use <codename> --workdir <path>   to correct it")
    return root / "docs"


def _today(ctx) -> str:
    import datetime
    return datetime.date.today().isoformat()


def cmd_new(args, ctx) -> int:
    """Scaffold a device document in the right place, with the right header."""
    kind = args.name or "handoff"
    if kind not in KINDS:
        # `log` is the DIRECTORY dated entries land in, and the linter names it.
        # Someone reading that advice reasonably types it as a kind, so accept
        # it rather than correcting them with a message that reads as a
        # contradiction.
        if kind == "log":
            kind = "handoff"
        else:
            raise Bail(f"unknown kind {kind!r}", EX_USAGE,
                       f"kinds: {', '.join(KINDS)} — `log` is the directory "
                       f"dated entries land in, not a kind")
    docs = _docs_root(ctx)
    device = ctx.cfg.get("PORTHOLE_DEVICE", "device")
    date = _today(ctx)

    if kind == "status":
        path = docs / "status.md"
        body = STATUS_TEMPLATE.format(device=device, date=date)
    else:
        topic = args.topic or ""
        if not topic:
            raise Bail(f"name the topic", EX_USAGE,
                       f"porthole docs new {kind} display-first-light")
        slug = re.sub(r"[^a-z0-9-]+", "-", topic.lower()).strip("-")
        path = docs / "log" / f"{date}-{slug}.md"
        body = LOG_TEMPLATE.format(
            date=date, kind=kind, subsystem=args.subsystem or "",
            title=topic)

    if path.exists() and not args.force:
        raise Bail(f"{path} already exists", EX_FAIL,
                   "pass --force to overwrite, or pick another topic")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)

    def render():
        ctx.out(f"{ctx.out.paint(ctx.out.sym('✓', 'ok'), 'green')} {path}")
        if kind != "status":
            ctx.out.hint("if it generalises, promote it: porthole brain new <id>")

    return ctx.emit({"path": str(path), "kind": kind}, render)


FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
LOG_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.md$")


def cmd_lint(args, ctx) -> int:
    """Enforce the taxonomy. Nothing here is a style opinion.

    Each rule exists because breaking it produced a real failure: a document
    nobody could find, a generated page hand-edited and then regenerated over,
    or a device note that should have been a brain trap and was lost instead.
    """
    findings = []
    try:
        docs = _docs_root(ctx)
    except Bail:
        docs = None

    if docs and docs.is_dir():
        for path in sorted(docs.rglob("*.md")):
            rel = path.relative_to(docs)
            parts = rel.parts
            if parts == ("status.md",):
                continue
            if parts[0] != "log":
                findings.append(("layout", str(rel),
                                 "device documents live in status.md or log/"))
                continue
            if len(parts) != 2 or not LOG_NAME.match(parts[-1]):
                findings.append(("name", str(rel),
                                 "log entries are log/YYYY-MM-DD-topic.md"))
                continue
            m = FRONTMATTER.match(path.read_text(errors="replace"))
            if not m:
                findings.append(("frontmatter", str(rel),
                                 "no frontmatter — date/kind/subsystem/status"))
                continue
            keys = {l.split(":", 1)[0].strip()
                    for l in m.group(1).splitlines() if ":" in l}
            missing = {"date", "kind", "status"} - keys
            if missing:
                findings.append(("frontmatter", str(rel),
                                 f"missing {', '.join(sorted(missing))}"))

    # A generated page that someone hand-edited is a page that will be silently
    # reverted by the next `porthole docs build`, taking the edit with it.
    site = pathlib.Path(ctx.root) / "docs" / "site"
    if site.is_dir():
        for path in sorted(site.rglob("*.md")):
            findings.append(("generated", str(path.relative_to(ctx.root)),
                             "hand-written file in a generated directory"))

    def render():
        o = ctx.out
        if not findings:
            o(o.paint(f"  {o.sym('✓', 'ok')} document layout clean", "green"))
            return
        o.heading(f"{len(findings)} finding(s)")
        for kind, where, detail in findings:
            o(f"  {o.paint(kind, 'yellow'):<24s} {where}")
            o(f"      {detail}")
        o.blank()
        o.hint("porthole docs new handoff <topic>", "scaffolds it correctly")

    ctx.emit({"findings": [{"kind": k, "path": p, "detail": d}
                           for k, p, d in findings],
              "clean": not findings}, render)
    return EX_OK if not findings else EX_FAIL


# ------------------------------------------------- the agent surface, generated --

MARKER = "GENERATED: verbs"


def verb_table(root) -> str:
    """The verb inventory, from the live registry.

    AGENTS.md is the front door for an agent that has never seen this project.
    A hand-maintained list of verbs there is wrong the first time one is
    renamed -- which happened, and left twenty-four broken invocations across
    the repo including the one on the newcomer's first screen. So the
    inventory is generated and a test fails when the file disagrees with it.

    Only the inventory. The prose stays hand-written, because the judgement and
    the war stories are what make the file worth reading, and no generator
    produces those.
    """
    import porthole_cli
    specs = porthole_cli.discover(pathlib.Path(root))
    lines = ["| verb | does | json | writes outside its profile |",
             "|---|---|---|---|"]
    for spec in specs:
        flags = {n for names, _ in spec["args"] for n in names
                 if n.startswith("-")}
        lines.append(
            f"| `{spec['verb']}` | {spec['help']} | "
            f"{'yes' if '--json' in flags else 'no'} | "
            f"{'needs --yes' if spec.get('escapes_scope') else 'no'} |")
    return "\n".join(lines)


def render_agents(root) -> str:
    """AGENTS.md with its generated block refreshed."""
    path = pathlib.Path(root) / "AGENTS.md"
    text = path.read_text()
    begin, end = f"<!-- BEGIN {MARKER} -->", f"<!-- END {MARKER} -->"
    if begin not in text or end not in text:
        return text
    head, rest = text.split(begin, 1)
    _, tail = rest.split(end, 1)
    return f"{head}{begin}\n{verb_table(root)}\n{end}{tail}"


ACTIONS = {"build": cmd_build, "serve": cmd_serve, "new": cmd_new,
           "lint": cmd_lint}



def dispatch(args, ctx) -> int:
    action = args.action or "build"
    fn = ACTIONS.get(action)
    if not fn:
        raise Bail(f"unknown action {action!r}", EX_USAGE,
                   f"actions: {', '.join(ACTIONS)}")
    return fn(args, ctx)


SPEC = {
    "verb": "docs",
    "order": 92,
    "group": "meta",
    "help": "generate the documentation site",
    "description": (
        "The site is generated, never hand-maintained: the CLI reference from\n"
        "the command registry, the tool catalogue from the tool headers, the\n"
        "profile keys from the template, the knowledge base from brain/.\n\n"
        "Every page has one source of truth, so a page that disagrees with the\n"
        "code is impossible rather than merely unlikely."),
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION", "choices": list(ACTIONS),
                      "help": "build | serve | new | lint"}),
        (["name"], {"nargs": "?", "metavar": "KIND",
                    "help": "new: handoff | finding | session | status"}),
        (["topic"], {"nargs": "?", "help": "new: what the document is about"}),
        (["--subsystem"], {"help": "new: display, suspend, ..."}),
        (["--force"], {"action": "store_true", "help": "new: overwrite"}),
        (["--org"], {"help": "GitHub org/user for links (default porthole-dev)"}),
        (["--repo"], {"help": "repository name (default porthole)"}),
        (["--catalog"], {"help": "reviewed release manifests and hardware reports directory"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": dispatch,
    "examples": [
        "porthole docs build",
        "porthole docs serve",
        "porthole docs new handoff display-first-light --subsystem display",
        "porthole docs new status",
        "porthole docs lint",
    ],
}
