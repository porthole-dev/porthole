# SPDX-License-Identifier: MIT
"""`porthole dts` -- write a device tree without starting from a blank file.

A mainline device tree is layered, and knowing that is most of the work:

    <soc>.dtsi              the SoC. Someone else wrote it. Do not touch it.
      <soc>-<family>.dtsi   optional: a board family shared by sibling phones
        <soc>-<vendor>-<codename>.dts   YOUR device. Often only 200-700 lines.

That last number surprises people. Taimen's own DTS is short because
`msm8998-google-wahoo.dtsi` carries everything it shares with walleye, which in
turn includes `msm8998.dtsi`. Your job is the board: which regulators feed
what, which GPIOs are the buttons, which I2C bus the touchscreen sits on. The
SoC's 3000 lines are already written.

This verb finds the sources worth reading, scaffolds the file, tells you which
nodes a working sibling defines that you have not, and validates what you wrote.
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

from porthole_cli import Bail, EX_FAIL, EX_OK, EX_USAGE
import porthole_pmaports as pmap

NODE_RE = re.compile(r"^\s*&([a-zA-Z_][a-zA-Z0-9_]*)\s*\{", re.M)
INCLUDE_RE = re.compile(r'^\s*#include\s+"([^"]+)"', re.M)
LABEL_RE = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*[a-zA-Z_@]", re.M)
COMPAT_RE = re.compile(r'compatible\s*=\s*"([^"]+)"')

SKELETON = '''// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) {year}, {author}
 */

/dts-v1/;

{includes}
/ {{
	model = "{model}";
	compatible = "{vendor},{codename}", "{soc_vendor},{soc}";

	chassis-type = "handset";

	aliases {{
		serial0 = &blsp1_uart3;
	}};

	chosen {{
		stdout-path = "serial0:115200n8";
	}};

	/*
	 * TODO: memory. Take the base and size from the downstream DTS or from
	 * `fastboot getvar all`. Getting it wrong produces a device that resets
	 * before any console output exists to tell you why.
	 */
	// reserved-memory {{ ... }};

	/*
	 * TODO: gpio-keys. Note that on many phones volume-down is NOT a GPIO --
	 * it is wired to the PMIC RESIN pin, which is also why Power+VolDown is
	 * the hardware reset combo. Check before you spend a day on a key that
	 * was never on the SoC.
	 */
	// gpio-keys {{ ... }};
}};

/*
 * Board wiring below. Everything here is a property of THIS phone; the SoC
 * nodes it references are already defined in the .dtsi above.
 *
 * Order of attack -- see `porthole brain 25-device-tree`:
 *   1. uart / console      you cannot debug what cannot talk
 *   2. regulators          almost everything else depends on them
 *   3. storage (ufs/sdhci) no rootfs without it
 *   4. usb                 the network and the shell
 *   5. everything else
 */
'''


def _tree(ctx) -> pathlib.Path:
    """The kernel source tree."""
    for key in ("PORTHOLE_KERNEL_TREE",):
        if ctx.cfg.get(key):
            path = pathlib.Path(ctx.cfg[key]).expanduser()
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


def _dts_dir(ctx, tree: pathlib.Path) -> pathlib.Path:
    arch = ctx.cfg.get("PORTHOLE_ARCH_DIR") or "arm64"
    dtb = ctx.cfg.get("PORTHOLE_DTB", "")
    vendor = dtb.split("/")[0] if "/" in dtb else "qcom"
    return tree / "arch" / arch / "boot" / "dts" / vendor


def _my_dts(ctx, tree) -> pathlib.Path:
    dtb = ctx.cfg.get("PORTHOLE_DTB_FILE") or ""
    stem = dtb[:-4] if dtb.endswith(".dtb") else dtb
    if not stem:
        soc = ctx.cfg.get("PORTHOLE_SOC", "soc")
        stem = f"{soc}-{ctx.cfg.get('PORTHOLE_CODENAME', 'device')}"
    return _dts_dir(ctx, tree) / f"{stem}.dts"


# ----------------------------------------------------------------- sources --

def cmd_sources(args, ctx) -> int:
    """Where the answers live. Reading beats guessing, every time."""
    cfg = ctx.cfg
    soc = cfg.get("PORTHOLE_SOC", "<soc>")
    codename = cfg.get("PORTHOLE_CODENAME", "<codename>")
    vendor = (cfg.get("PORTHOLE_VENDOR") or "<vendor>").lower()

    sources = [
        ("downstream kernel DTS", "the vendor's own device tree",
         f"The single most valuable artefact. Android vendors ship kernel "
         f"source: look for `{soc}-{codename}.dts` or a board file in their "
         f"release. It is downstream-shaped and cannot be used directly, but "
         f"it holds every regulator, GPIO and I2C address as measured facts.",
         "github.com search: <vendor> kernel <codename>"),
        ("the device's own dtbo/DTB", "what the bootloader currently applies",
         "Pull the dtb/dtbo partition off the device and decompile it: "
         "`tools/dtbo-split.py` then `dtc -I dtb -O dts`. This is what the "
         "hardware is running right now, which makes it authoritative about "
         "the board even when the source is unavailable.",
         "tools/dtbo-split.py, tools/fdtdump.py"),
        ("/proc/device-tree on stock", "the live tree",
         "If the device still boots its stock OS, the running device tree is "
         "readable at /proc/device-tree. Same authority as the dtbo, easier to "
         "get at.", "adb shell / stock recovery"),
        ("a mainline sibling", "someone else's finished work",
         "A device on the same SoC that already boots mainline. Its DTS shows "
         "you the mainline node names and bindings the SoC's dtsi expects, "
         "which downstream sources will not.",
         "porthole soc"),
        ("the SoC dtsi itself", "what is already written for you",
         f"`{soc}.dtsi` in the kernel tree defines every SoC-internal node with "
         f"a label. Your .dts references those labels; it does not redefine "
         f"them. Read it before writing anything.",
         "porthole dts labels"),
        ("Documentation/devicetree/bindings/", "what each property means",
         "The binding for a compatible string tells you which properties are "
         "required and what they mean. A DTS that compiles but has the wrong "
         "properties fails at probe with no useful message.",
         "in the kernel tree"),
    ]
    payload = [{"source": s, "what": w, "why": why, "where": where}
               for s, w, why, where in sources]

    def render():
        o = ctx.out
        o.heading("where a device tree comes from")
        o.blank()
        for i, (name, what, why, where) in enumerate(sources, 1):
            o(f"  {o.paint(f'{i}. {name}', 'bold')} — {what}")
            for line in _wrap(why, 68):
                o(f"     {line}")
            o(f"     {o.paint(where, 'cyan')}")
            o.blank()
        o(o.paint("  Nothing here is optional reading. A device tree is a "
                  "description of\n  hardware you did not design; every value "
                  "in it is a fact to be found,\n  not a value to be chosen.",
                  "grey"))

    return ctx.emit(payload, render)


def _wrap(text: str, width: int) -> list[str]:
    import textwrap
    return textwrap.wrap(text, width)


# --------------------------------------------------------------- new / init --

def cmd_new(args, ctx) -> int:
    tree = _tree(ctx)
    target = _my_dts(ctx, tree)
    if target.exists() and not args.force:
        raise Bail(f"{target} already exists", EX_FAIL,
                   "--force to overwrite, or `porthole dts check` to see what "
                   "is left in it")

    cfg = ctx.cfg
    soc = cfg.get("PORTHOLE_SOC", "soc")
    dtb = cfg.get("PORTHOLE_DTB", "")
    soc_vendor = dtb.split("/")[0] if "/" in dtb else "qcom"

    # Prefer a family dtsi if one exists: sibling phones usually share a board
    # family, and inheriting it is why a device DTS can be 200 lines.
    dts_dir = _dts_dir(ctx, tree)
    includes = []
    family = args.include
    if not family:
        candidates = sorted(dts_dir.glob(f"{soc}-*.dtsi")) if dts_dir.is_dir() else []
        # A family dtsi shares the vendor prefix; the bare SoC dtsi does not.
        vendor = (cfg.get("PORTHOLE_VENDOR") or "").lower()
        fam = [c for c in candidates if vendor and c.name.startswith(f"{soc}-{vendor}-")]
        family = fam[0].name if fam else (f"{soc}.dtsi" if (dts_dir / f"{soc}.dtsi").is_file() else "")
    if family:
        includes.append(f'#include "{family}"')
    else:
        includes.append(f'// TODO: #include "{soc}.dtsi"  -- the SoC dtsi was '
                        f'not found in {dts_dir}')

    body = SKELETON.format(
        year=args.year or "2026",
        author=args.author or _git_author() or "Your Name <you@example.com>",
        includes="\n".join(includes) + "\n",
        model=cfg.get("PORTHOLE_DEVICE_NAME") or cfg.get("PORTHOLE_CODENAME", ""),
        vendor=(cfg.get("PORTHOLE_VENDOR") or "vendor").lower(),
        codename=(cfg.get("PORTHOLE_CODENAME") or "device").split("-")[-1],
        soc_vendor=soc_vendor, soc=soc)

    if args.dry_run:
        print(body)
        return EX_OK

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)

    ctx.out(f"{ctx.out.paint(ctx.out.sym('✓', 'ok'), 'green')} wrote {target}")
    ctx.out.blank()
    if family:
        ctx.out(f"  includes {ctx.out.paint(family, 'bold')} — read it before "
                f"you add anything;\n  it may already define what you were "
                f"about to write.")
    ctx.out.blank()
    ctx.out.heading("next")
    ctx.out.hint("porthole dts sources     where the real values come from")
    ctx.out.hint("porthole dts labels      what the SoC dtsi already defines")
    ctx.out.hint("porthole dts compare <sibling>   what they define that you do not")
    ctx.out.hint("porthole dts check       does it compile")
    ctx.out.blank()
    ctx.out("Also add it to the Makefile in that directory, or it is never built.")
    return EX_OK


def _git_author() -> str:
    try:
        name = subprocess.run(["git", "config", "user.name"], capture_output=True,
                              text=True, timeout=5).stdout.strip()
        mail = subprocess.run(["git", "config", "user.email"], capture_output=True,
                              text=True, timeout=5).stdout.strip()
        return f"{name} <{mail}>" if name and mail else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


# ------------------------------------------------------------------ labels --

def cmd_labels(args, ctx) -> int:
    """Every label the SoC dtsi defines: the vocabulary your .dts may use."""
    tree = _tree(ctx)
    soc = ctx.cfg.get("PORTHOLE_SOC", "")
    dts_dir = _dts_dir(ctx, tree)
    path = pathlib.Path(args.file) if args.file else dts_dir / f"{soc}.dtsi"
    if not path.is_file():
        raise Bail(f"{path} not found", EX_FAIL,
                   "pass --file, or check PORTHOLE_SOC and the kernel tree")
    labels = sorted(set(LABEL_RE.findall(path.read_text(errors="replace"))))
    payload = {"file": str(path), "labels": labels}

    def render():
        ctx.out.heading(f"{path.name} defines {len(labels)} labels")
        ctx.out.blank()
        cols, width = 4, max((len(l) for l in labels), default=10) + 2
        for i in range(0, len(labels), cols):
            ctx.out("  " + "".join(f"{l:<{width}}" for l in labels[i:i + cols]))
        ctx.out.blank()
        ctx.out(ctx.out.paint(
            "  Your .dts references these with `&label { ... };` to add board\n"
            "  properties. It does not redefine them. If what you want is not\n"
            "  here, it is not an SoC node and belongs in the root node.", "grey"))

    return ctx.emit(payload, render)


# ----------------------------------------------------------------- compare --

def nodes_including(path: pathlib.Path, seen=None) -> tuple[set, list]:
    """Nodes configured by a DTS *and everything it includes*.

    Following includes is not a nicety. Taimen configures &ufshc, &usb3 and ten
    others in `msm8998-google-wahoo.dtsi`, the board family it shares with
    walleye. Reading only the .dts reports all twelve as unconfigured and sends
    someone off to redo work that has been done for years -- a confident wrong
    answer, which is worse than no answer.
    """
    seen = seen if seen is not None else set()
    path = path.resolve()
    if path in seen or not path.is_file():
        return set(), []
    seen.add(path)
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return set(), []
    nodes = set(NODE_RE.findall(text))
    files = [path]
    for inc in INCLUDE_RE.findall(text):
        # Relative to this file first, which is how the kernel resolves a
        # quoted include; dt-bindings headers live elsewhere and are skipped.
        candidate = path.parent / inc
        if not candidate.is_file():
            continue
        sub_nodes, sub_files = nodes_including(candidate, seen)
        nodes |= sub_nodes
        files += sub_files
    return nodes, files


def cmd_compare(args, ctx) -> int:
    """Which nodes does a working sibling touch that I have not?

    This is the "what is left to do" list, derived from a device that boots
    rather than from imagination.
    """
    tree = _tree(ctx)
    dts_dir = _dts_dir(ctx, tree)
    mine = _my_dts(ctx, tree)
    if not mine.is_file():
        raise Bail(f"{mine} does not exist yet", EX_FAIL, "porthole dts new")

    ref = pathlib.Path(args.reference)
    if not ref.is_file():
        matches = sorted(dts_dir.glob(f"*{args.reference}*.dts"))
        if not matches:
            raise Bail(f"no DTS matching {args.reference!r} in {dts_dir}",
                       EX_FAIL, "`porthole soc` lists sibling codenames")
        ref = matches[0]

    mine_nodes, mine_files = nodes_including(mine)
    ref_nodes, ref_files = nodes_including(ref)

    missing = sorted(ref_nodes - mine_nodes)
    extra = sorted(mine_nodes - ref_nodes)
    payload = {"mine": str(mine), "reference": str(ref),
               "mine_files": [str(f) for f in mine_files],
               "reference_files": [str(f) for f in ref_files],
               "missing": missing, "extra": extra,
               "shared": sorted(mine_nodes & ref_nodes)}

    def render():
        o = ctx.out
        o.heading(f"{mine.name}  vs  {ref.name}")
        if len(mine_files) > 1 or len(ref_files) > 1:
            o(o.paint(f"  following includes: "
                      f"{len(mine_files)} file(s) vs {len(ref_files)}", "grey"))
        o.blank()
        if missing:
            o.heading(f"they configure, you do not ({len(missing)})")
            for node in missing:
                o(f"  {o.paint(o.sym('•', '-'), 'yellow')} &{node}")
            o.blank()
            o(o.paint("  Not a to-do list to work through blindly: some of "
                      "these are\n  board differences and genuinely do not "
                      "apply. It is a list of\n  questions worth answering.",
                      "grey"))
        else:
            o("  nothing they configure that you do not.")
        if extra:
            o.blank()
            o.heading(f"you configure, they do not ({len(extra)})")
            for node in extra:
                o(f"  {o.paint(o.sym('•', '-'), 'cyan')} &{node}")

    return ctx.emit(payload, render)


# ------------------------------------------------------------------- check --

def cmd_check(args, ctx) -> int:
    """Compile the DTS. A tree that does not compile cannot be debugged."""
    tree = _tree(ctx)
    target = pathlib.Path(args.file) if args.file else _my_dts(ctx, tree)
    if not target.is_file():
        raise Bail(f"{target} not found", EX_FAIL, "porthole dts new")

    dtc = shutil.which("dtc") or str(tree / "scripts/dtc/dtc")
    if not pathlib.Path(dtc).is_file() and not shutil.which("dtc"):
        raise Bail("no dtc found", EX_FAIL,
                   "build the kernel once (it builds scripts/dtc/dtc), or "
                   "install the device-tree-compiler package")

    # A .dts uses cpp includes, so it must be preprocessed before dtc sees it.
    arch = ctx.cfg.get("PORTHOLE_ARCH_DIR") or "arm64"
    includes = [f"-I{tree}/include", f"-I{tree}/arch/{arch}/boot/dts",
                f"-I{target.parent}"]
    cpp = subprocess.run(
        ["cpp", "-nostdinc", *includes, "-undef", "-D__DTS__", "-x",
         "assembler-with-cpp", str(target)],
        capture_output=True, text=True)
    if cpp.returncode != 0:
        ctx.out.error("preprocessing failed:")
        print(cpp.stderr.strip()[:2000])
        return EX_FAIL

    proc = subprocess.run([dtc, "-I", "dts", "-O", "dtb", "-o", os.devnull,
                           "-@", "-"],
                          input=cpp.stdout, capture_output=True, text=True)
    warnings = [l for l in proc.stderr.splitlines() if l.strip()]

    def render():
        if proc.returncode == 0 and not warnings:
            ctx.out(ctx.out.paint(f"  {target.name}: compiles clean", "green"))
            return
        if proc.returncode == 0:
            ctx.out(ctx.out.paint(f"  {target.name}: compiles, "
                                  f"{len(warnings)} warning(s)", "yellow"))
        else:
            ctx.out(ctx.out.paint(f"  {target.name}: does NOT compile", "red"))
        for line in warnings[:40]:
            ctx.out(f"    {line}")

    ctx.emit({"file": str(target), "ok": proc.returncode == 0,
              "warnings": warnings}, render)
    return EX_OK if proc.returncode == 0 else EX_FAIL



# ------------------------------------------------------------------- port --

def cmd_port(args, ctx) -> int:
    """Differential mainlining -- carry a device tree across a SoC generation.

    Three sub-actions, because they are three separate decisions and running
    them as one would hide which step went wrong:

      delta   what moved between two vendor trees        (produces delta.toml)
      apply   rewrite a mainline template with those moves
      verify  prove nothing was dropped or invented      (this is what CI runs)

    See lib/porthole_dtsdelta.py for why this is textual and not a parser.
    """
    import porthole_dtsdelta as dd

    sub = args.reference or "delta"
    if sub not in ("delta", "apply", "verify"):
        raise Bail(f"unknown port step {sub!r}", EX_USAGE,
                   "porthole dts port delta | apply | verify")

    if sub == "delta":
        if not (args.from_dts and args.to_dts):
            raise Bail("port delta needs two vendor trees", EX_USAGE,
                       "porthole dts port delta --from gs101.dtsi --to gs201.dtsi")
        delta = dd.Delta(args.from_dts, args.to_dts)
        doc = delta.to_dict()
        if args.out:
            body = dd.to_toml(delta, args.old_soc or "old",
                              args.new_soc or "new", args.origin or "")
            pathlib.Path(args.out).write_text(body)

        def render():
            o = ctx.out
            c = doc["counts"]
            o.heading(f"{pathlib.Path(delta.old_path).name} -> "
                      f"{pathlib.Path(delta.new_path).name}")
            o.kv("nodes", f"{c['old_nodes']} -> {c['new_nodes']}", 12)
            o.kv("identical", str(c["identical"]), 12,
                 "same label, same address")
            o.kv("moved", str(c["moved"]), 12)
            o.kv("added", str(c["added"]), 12)
            o.kv("removed", str(c["removed"]), 12)
            for block in doc["blocks"]:
                o.blank()
                o.heading(f"  {block['from']} -> {block['to']}   "
                          f"({len(block['nodes'])} nodes)")
                for node in block["nodes"]:
                    o(f"    {node['label']:<24s} 0x{node['from']} -> "
                      f"0x{node['to']}   {o.paint(node['source'], 'grey')}")
            if doc["added_nodes"]:
                o.blank()
                o.heading("  new in this generation")
                o(f"    {', '.join(doc['added_nodes'])}")
            if doc["removed_nodes"]:
                o.blank()
                o.heading("  gone in this generation")
                o(f"    {', '.join(doc['removed_nodes'])}")
            o.blank()
            if args.out:
                o(f"  wrote {args.out}")
            else:
                o.hint("--out delta.toml   to check the audit trail in")

        return ctx.emit(doc, render)

    if sub == "apply":
        for need, flag in ((args.template, "--template"),
                           (args.delta, "--delta"), (args.out, "--out")):
            if not need:
                raise Bail(f"port apply needs {flag}", EX_USAGE)
        doc = dd.load_toml(args.delta)
        moves = dd.justified(doc)
        from_addr = dd.from_addrs(doc)
        text = pathlib.Path(args.template).read_text()
        template_nodes = dd.parse(args.template)
        by_addr = {n.addr: lbl for lbl, n in template_nodes.items()}
        applied, missed, renamed = [], [], []
        for label, want in sorted(moves.items()):
            node = template_nodes.get(label)
            if node is None:
                # The delta is in DOWNSTREAM label space; the template is in
                # MAINLINE label space, and mainlining renames things --
                # pinctrl_0 became pinctrl_gpio_alive, udc became usbdrd31.
                # The old ADDRESS survives the rename, so it is the join key.
                # Matching on it turns a whole class of "the template has no
                # such node" into an automatic, checkable correspondence.
                old_addr = from_addr.get(label, "")
                target = by_addr.get(old_addr)
                if not target:
                    missed.append(label)
                    continue
                renamed.append((label, target))
                node = template_nodes[target]
            old = node.addr
            # Rewrite the node header AND any reg property carrying the old
            # address. Both, because a header that moved with a reg that did
            # not is a device tree that compiles and does not work.
            text = re.sub(rf"(@){old}\b", rf"\g<1>{want}", text)
            text = re.sub(rf"(0x0*){old}\b", rf"\g<1>{want}", text)
            applied.append(label)
        pathlib.Path(args.out).write_text(text)

        def render():
            o = ctx.out
            o(f"  {len(applied)} address(es) rewritten into {args.out}")
            for label in applied:
                o(f"    {label} -> 0x{moves[label]}")
            for src, dst in renamed:
                o(o.paint(f"    {src} -> {dst} (matched by address; mainlining "
                          f"renamed it)", "grey"))
            for label in missed:
                o.warn(f"delta names {label}; the template has no node at that "
                       f"address either — mainline may simply not implement it")
            o.blank()
            o.hint(f"porthole dts port verify --file {args.out} "
                   f"--against <mainline sibling> --delta {args.delta}")

        return ctx.emit({"out": args.out, "applied": applied,
                         "renamed": [{"downstream": s, "mainline": d}
                                     for s, d in renamed],
                         "not_in_template": missed}, render)

    # verify
    if not (args.file and args.against and args.delta):
        raise Bail("port verify needs a candidate, --against and --delta",
                   EX_USAGE,
                   "porthole dts port verify --file gs201.dtsi "
                   "--against gs101.dtsi --delta delta.toml")
    findings = dd.verify(pathlib.Path(args.file), pathlib.Path(args.against),
                         dd.load_toml(args.delta))

    def render():
        o = ctx.out
        if not findings:
            o(o.paint(f"  {o.sym('✓', 'ok')} every node in "
                      f"{pathlib.Path(args.against).name} has a counterpart, "
                      f"and every moved address is justified", "green"))
            return
        o.heading(f"{len(findings)} finding(s)")
        for f in findings:
            colour = {"missing": "red", "unjustified": "yellow",
                      "contradicted": "red"}[f["kind"]]
            o(f"  {o.paint(f['kind'], colour)}  {f['label']}")
            o(f"      {f['detail']}")

    ctx.emit({"findings": findings, "clean": not findings}, render)
    return EX_OK if not findings else EX_FAIL


ACTIONS = {"sources": cmd_sources, "new": cmd_new, "labels": cmd_labels,
           "compare": cmd_compare, "check": cmd_check, "port": cmd_port}


def dispatch(args, ctx) -> int:
    action = args.action or "sources"
    fn = ACTIONS.get(action)
    if not fn:
        raise Bail(f"unknown action {action!r}", EX_USAGE,
                   f"actions: {', '.join(ACTIONS)}")
    if action == "port":
        return cmd_port(args, ctx)
    if action == "compare" and not args.reference:
        raise Bail("name a sibling to compare against", EX_USAGE,
                   "porthole dts compare xiaomi-sagit")
    return fn(args, ctx)


SPEC = {
    "verb": "dts",
    "order": 28,
    "group": "sources",
    "help": "write and check a device tree without starting from blank",
    "description": (
        "A mainline device tree is layered: the SoC dtsi is already written,\n"
        "a board-family dtsi may cover most of the rest, and your .dts is\n"
        "often only a few hundred lines describing the board.\n\n"
        "This finds the sources worth reading, scaffolds the file, lists the\n"
        "labels the SoC already defines, shows what a working sibling\n"
        "configures that you have not, and compiles what you wrote."),
    "args": [
        (["action"], {"nargs": "?", "metavar": "ACTION", "choices": list(ACTIONS),
                      "help": "sources | new | labels | compare | check | port"}),
        (["reference"], {"nargs": "?", "metavar": "REF",
                         "help": "compare: sibling DTS or codename. "
                                 "port: delta | apply | verify"}),
        (["--from"], {"dest": "from_dts", "metavar": "DTS",
                      "help": "port delta: the OLD vendor tree"}),
        (["--to"], {"dest": "to_dts", "metavar": "DTS",
                    "help": "port delta: the NEW vendor tree"}),
        (["--origin"], {"metavar": "REPO@SHA",
                        "help": "port delta: where the vendor sources came "
                                "from — without it the citations resolve to "
                                "nothing"}),
        (["--old-soc"], {"metavar": "SOC", "help": "port delta: label for the old SoC"}),
        (["--new-soc"], {"metavar": "SOC", "help": "port delta: label for the new SoC"}),
        (["--template"], {"metavar": "DTS",
                          "help": "port apply: the mainline sibling to rewrite"}),
        (["--delta"], {"metavar": "TOML", "help": "port apply/verify: the audit trail"}),
        (["--against"], {"metavar": "DTS",
                         "help": "port verify: the mainline sibling to cover"}),
        (["--out"], {"metavar": "PATH", "help": "where to write the result"}),
        (["--file"], {"metavar": "PATH", "help": "operate on this file"}),
        (["--include"], {"metavar": "DTSI", "help": "new: dtsi to include"}),
        (["--author"], {"help": "new: copyright line"}),
        (["--year"], {"help": "new: copyright year"}),
        (["--force"], {"action": "store_true", "help": "new: overwrite"}),
        (["--dry-run"], {"action": "store_true", "help": "new: print, do not write"}),
        (["--json"], {"action": "store_true", "help": "machine-readable"}),
    ],
    "run": dispatch,
    "examples": [
        "porthole dts sources",
        "porthole dts new --dry-run",
        "porthole dts labels",
        "porthole dts compare xiaomi-sagit",
        "porthole dts check",
        "porthole dts port delta --from gs101.dtsi --to gs201.dtsi --out delta.toml",
        "porthole dts port apply --template gs101.dtsi --delta delta.toml --out gs201.dtsi",
        "porthole dts port verify --file gs201.dtsi --against gs101.dtsi --delta delta.toml",
    ],
}
