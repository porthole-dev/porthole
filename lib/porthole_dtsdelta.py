# SPDX-License-Identifier: MIT
"""Differential mainlining: carry a device tree across a SoC generation.

A SoC nobody has mainlined, whose FAMILY has a mainlined predecessor, is not a
blank page. It is a solved instance of the same problem sitting next to an
unsolved one::

    downstream OLD --(transform T)--> mainline OLD    both sides in hand: T is known
    downstream NEW --(apply T)------> mainline NEW    the wanted output
          '--------(delta D)--------> downstream NEW  what actually moved

`T` -- the distillation someone already performed -- says which nodes are in
scope: a mainline dtsi is far smaller than the vendor one it came from, and
that subset encodes a judgement about what a minimal boot needs. `D` says which
addresses changed. Applying `T` to downstream NEW, and independently computing
`mainline OLD + D`, gives two derivations of the same file; where they disagree
is a finding, never a value to average.

**This is deliberately not a device tree parser.** It reads exactly one textual
form: the `label: node@addr {` declaration. That was enough to recover a real
SoC generation delta, and a parser is a large dependency bought against a need
that has not appeared.

**Know what that does NOT cover.** A `reg = <...>` property is not read, so a
node whose unit address is right and whose `reg` is wrong passes `verify`
clean. `dtc` catches that mismatch (`simple_bus_reg`), which is why compiling
is a separate, non-optional check rather than something this module claims to
subsume. Likewise `verify` does not police nodes ADDED in the new generation:
it proves nothing was dropped and no address was invented, not that nothing was
added.

Nothing here guesses. An address that cannot be justified is reported.
"""
from __future__ import annotations

import pathlib
import re

# `label: name@addr {`  -- the declaration that binds a name to a unit address.
NODE_RE = re.compile(
    r"^[ \t]*(?P<label>[A-Za-z_][\w-]*)\s*:\s*"
    r"(?P<name>[A-Za-z_][\w,.+-]*)@(?P<addr>[0-9a-fA-F]+)\s*\{",
    re.M)

# An anonymous node still tells you an address exists, but has no stable key
# across trees, so it is counted and reported rather than diffed.
ANON_RE = re.compile(
    r"^[ \t]*(?P<name>[A-Za-z_][\w,.+-]*)@(?P<addr>[0-9a-fA-F]+)\s*\{", re.M)


class Node:
    __slots__ = ("label", "name", "addr", "line")

    def __init__(self, label, name, addr, line):
        self.label, self.name = label, name
        self.addr, self.line = addr.lower().lstrip("0") or "0", line

    def __repr__(self):
        return f"<{self.label}:{self.name}@{self.addr}>"


def parse(path) -> dict[str, Node]:
    """Every labelled, addressed node in a DTS/DTSI, keyed by label.

    Keyed by LABEL rather than by path: a label is what survives a vendor
    reorganising its include tree, and it is what the rest of the tree
    references. Two nodes sharing a label cannot both be referenced, so the
    first wins and the duplicate is dropped rather than silently overwriting.
    """
    text = pathlib.Path(path).read_text(errors="replace")
    out: dict[str, Node] = {}
    for m in NODE_RE.finditer(text):
        label = m.group("label")
        if label in out:
            continue
        line = text.count("\n", 0, m.start()) + 1
        out[label] = Node(label, m.group("name"), m.group("addr"), line)
    return out


def anon_count(path) -> int:
    text = pathlib.Path(path).read_text(errors="replace")
    return len(ANON_RE.findall(text)) - len(NODE_RE.findall(text))


class Delta:
    """What moved between two generations of the same vendor tree."""

    def __init__(self, old_path, new_path):
        self.old_path, self.new_path = str(old_path), str(new_path)
        self.old, self.new = parse(old_path), parse(new_path)
        self.same, self.moved, self.added, self.removed = {}, {}, {}, {}
        for label, node in self.new.items():
            prev = self.old.get(label)
            if prev is None:
                self.added[label] = node
            elif prev.addr == node.addr:
                self.same[label] = node
            else:
                self.moved[label] = (prev, node)
        for label, node in self.old.items():
            if label not in self.new:
                self.removed[label] = node

    def blocks(self) -> list[dict]:
        """Group moves by their shared high bits.

        Moves that share a prefix are one region relocating, not seven
        independent facts. That distinction is the whole reason to trust the
        result: five scattered addresses is noise, five addresses that are all
        `0x174xxxxx -> 0x180xxxxx` is a memory map decision you can check.
        """
        groups: dict[tuple, list] = {}
        for label, (old, new) in self.moved.items():
            key = (_prefix(old.addr, new.addr), len(old.addr), len(new.addr))
            groups.setdefault(key, []).append((label, old, new))
        out = []
        for (shared, _, _), members in sorted(
                groups.items(), key=lambda kv: -len(kv[1])):
            members.sort(key=lambda m: m[0])
            # Summarise the group by what its OWN members share, not by what
            # old and new share with each other: "0x174xxxxx -> 0x180xxxxx" is
            # a memory-map decision you can check, and "0x1xxxxxxx ->
            # 0x1xxxxxxx" is nothing at all.
            olds = [m[1].addr for m in members]
            news = [m[2].addr for m in members]
            out.append({
                "shared_prefix_nibbles": shared,
                "from": _mask(olds[0], _common(olds)),
                "to": _mask(news[0], _common(news)),
                "nodes": [
                    {"label": lbl, "from": o.addr, "to": n.addr,
                     "source": f"{pathlib.Path(self.new_path).name}:{n.line}"}
                    for lbl, o, n in members],
            })
        return out

    def to_dict(self) -> dict:
        return {
            "old": self.old_path, "new": self.new_path,
            "counts": {
                "old_nodes": len(self.old), "new_nodes": len(self.new),
                "identical": len(self.same), "moved": len(self.moved),
                "added": len(self.added), "removed": len(self.removed),
            },
            "blocks": self.blocks(),
            "added_nodes": sorted(self.added),
            "removed_nodes": sorted(self.removed),
            "identical_nodes": sorted(self.same),
        }


def _prefix(a: str, b: str) -> int:
    """How many leading nibbles two addresses share, after padding to equal width."""
    a, b = a.rjust(max(len(a), len(b)), "0"), b.rjust(max(len(a), len(b)), "0")
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _common(addrs: list[str]) -> int:
    """Leading nibbles shared by every address in a group."""
    if not addrs:
        return 0
    n = len(addrs[0])
    for other in addrs[1:]:
        n = min(n, _prefix(addrs[0], other))
    return n


def _mask(addr: str, shared: int) -> str:
    """`0x174xxxxx` -- the shared part kept, the rest shown as varying."""
    return "0x" + addr[:shared] + "x" * (len(addr) - shared) if shared < len(addr) \
        else "0x" + addr


# ------------------------------------------------------------------- toml --

def to_toml(delta: Delta, old_soc: str, new_soc: str, origin: str = "") -> str:
    """The audit trail, as TOML.

    Checked in next to the device tree. Every address that differs from the
    mainline sibling cites the downstream file and line that justifies it, so
    `verify` can prove no value was invented.

    `origin` is what makes those citations resolvable: "file:line" identifies
    nothing unless the reader can obtain that exact file. Pass the repository
    and commit the sources came from, or the audit trail is only an assertion
    with line numbers on it.
    """
    d = delta.to_dict()
    lines = [
        "# Generated by `porthole dts port delta`. Checked in as the audit",
        "# trail: every address here cites the downstream line that justifies",
        "# it, so no value in the ported device tree is unaccounted for.",
        "",
        "[meta]",
        f'old_soc = "{old_soc}"',
        f'new_soc = "{new_soc}"',
        f'old_source = "{pathlib.Path(d["old"]).name}"',
        f'new_source = "{pathlib.Path(d["new"]).name}"',
        # Without this the citations below name a file and a line in a tree
        # nobody can identify, which is an audit trail nobody can audit.
        f'origin = "{origin}"' if origin else
        '# origin = ""   # SET THIS: repo and commit the sources came from',
        "",
        "[counts]",
    ]
    for key, value in d["counts"].items():
        lines.append(f"{key} = {value}")
    for block in d["blocks"]:
        lines += ["", "[[block]]",
                  f'from = "{block["from"]}"', f'to = "{block["to"]}"',
                  f'shared_prefix_nibbles = {block["shared_prefix_nibbles"]}']
        for node in block["nodes"]:
            lines += ["", "[[block.node]]",
                      f'label = "{node["label"]}"',
                      f'from = "0x{node["from"]}"', f'to = "0x{node["to"]}"',
                      f'source = "{node["source"]}"']
    if d["added_nodes"]:
        lines += ["", "[added]",
                  "# present in the new generation and absent from the old.",
                  "labels = [" + ", ".join(f'"{n}"' for n in d["added_nodes"])
                  + "]"]
    if d["removed_nodes"]:
        lines += ["", "[removed]",
                  "labels = [" + ", ".join(f'"{n}"' for n in d["removed_nodes"])
                  + "]"]
    return "\n".join(lines) + "\n"


def load_toml(path) -> dict:
    """Read a delta.toml back. Minimal reader -- tomllib is 3.11+.

    Only the shapes to_toml writes are understood, which is the point: this
    reads OUR audit trail, not arbitrary TOML, and a general parser would be a
    dependency bought for nothing.
    """
    try:
        import tomllib
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except ImportError:
        pass
    data: dict = {"meta": {}, "counts": {}, "block": [], "added": {},
                  "removed": {}}
    section = None
    for raw in pathlib.Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[[block]]":
            data["block"].append({"node": []})
            section = data["block"][-1]
            continue
        if line == "[[block.node]]":
            data["block"][-1]["node"].append({})
            section = data["block"][-1]["node"][-1]
            continue
        if line.startswith("[") and line.endswith("]"):
            section = data.setdefault(line.strip("[]"), {})
            continue
        if "=" in line and section is not None:
            key, _, value = line.partition("=")
            value = value.strip()
            if value.startswith('"'):
                value = value.strip('"')
            elif value.startswith("["):
                value = [v.strip().strip('"')
                         for v in value.strip("[]").split(",") if v.strip()]
            else:
                try:
                    value = int(value)
                except ValueError:
                    pass
            section[key.strip()] = value
    return data


def justified(delta_doc: dict) -> dict[str, str]:
    """label -> justified new address, from a loaded delta.toml."""
    out = {}
    for block in delta_doc.get("block", []):
        for node in block.get("node", []):
            if "label" in node and "to" in node:
                out[node["label"]] = str(node["to"]).lower().replace("0x", "")
    return out


def from_addrs(delta_doc: dict) -> dict[str, str]:
    """label -> the address it moved FROM, from a loaded delta.toml.

    Needed because the delta is written in the vendor tree's label space and a
    mainline template is not: mainlining renames nodes. The old address is what
    survives that rename, so it is the key that joins the two.
    """
    out = {}
    for block in delta_doc.get("block", []):
        for node in block.get("node", []):
            if "label" in node and "from" in node:
                out[node["label"]] = str(node["from"]).lower().replace("0x", "")
    return out


# ----------------------------------------------------------------- verify --

def moved_pairs(delta_doc: dict) -> set:
    """{(from_addr, to_addr)} -- the delta's claims, stripped of labels.

    A delta entry really asserts "this ADDRESS moved to that address". The
    label it was written under is the vendor tree's, and mainlining renames
    things (pinctrl_0 -> pinctrl_gpio_alive, udc -> usbdrd31). Verifying by
    label would therefore reject a correct port for using upstream's names,
    which is precisely what a ported tree is supposed to do.
    """
    out = set()
    for block in delta_doc.get("block", []):
        for node in block.get("node", []):
            if "from" in node and "to" in node:
                out.add((str(node["from"]).lower().replace("0x", "").lstrip("0"),
                         str(node["to"]).lower().replace("0x", "").lstrip("0")))
    return out


def verify(candidate, sibling, delta_doc: dict) -> list[dict]:
    """Check a ported device tree against its mainline sibling and the delta.

    Two failures matter, and neither is visible by reading the file:

    - **coverage**: a node the mainline sibling implements that the candidate
      dropped. Silent omission is the failure mode of any hand-carried port,
      and it surfaces as a driver that never probes.
    - **justification**: an address that is neither identical to the sibling's
      nor explained by a delta entry. That is a value someone invented.
    """
    cand, sib = parse(candidate), parse(sibling)
    ok = justified(delta_doc)
    pairs = moved_pairs(delta_doc)
    findings = []

    for label, node in sorted(sib.items()):
        if label not in cand:
            findings.append({
                "kind": "missing", "label": label,
                "detail": f"{sibling.name if hasattr(sibling, 'name') else sibling}"
                          f" has {label}@{node.addr}; the candidate does not",
            })

    for label, node in sorted(cand.items()):
        prev = sib.get(label)
        if prev is None:
            continue                      # new in this generation: fine
        if prev.addr == node.addr:
            continue                      # unchanged: fine
        want = ok.get(label)
        if want is None:
            if (prev.addr, node.addr) in pairs:
                continue          # justified by address; upstream renamed it
            findings.append({
                "kind": "unjustified", "label": label,
                "detail": f"moved 0x{prev.addr} -> 0x{node.addr} with no entry "
                          f"in the delta to justify it",
            })
        elif want != node.addr:
            findings.append({
                "kind": "contradicted", "label": label,
                "detail": f"delta says 0x{want}, the candidate says 0x{node.addr}",
            })
    return findings
