#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Differential mainlining, checked against real silicon.

The fixtures are node headers extracted from the downstream Google Tensor
trees (gs101, in the Pixel 6; gs201, in the Pixel 7). Real data rather than a
synthetic pair, because the property being tested is "does this find the truth
about an actual SoC generation" -- and a hand-made fixture would only prove the
code agrees with whoever wrote the fixture.

The expected numbers here were derived by this tool and then corroborated
independently: the APM block move it reports (0x174xxxxx -> 0x180xxxxx) also
shows up in the downstream clk-gs201.c `clkout_addresses[]` table, which is a
different file written by different people for a different purpose.
"""
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402
sys.path.insert(0, str(ROOT / "lib"))
FIX = ROOT / "tests" / "fixtures" / "dts"

import porthole_dtsdelta as dd  # noqa: E402

OLD, NEW = FIX / "vendor-old.dtsi", FIX / "vendor-new.dtsi"


def test_parse_keys_by_label():
    nodes = dd.parse(OLD)
    assert "pmu_system_controller" in nodes
    assert nodes["pmu_system_controller"].addr == "17460000"


def test_leading_zeros_do_not_split_a_match():
    """`@0810` and `@810` are the same address. Treating them as different
    would report a move that never happened."""
    with tempfile.TemporaryDirectory() as tmp:
        a = pathlib.Path(tmp) / "a.dtsi"
        b = pathlib.Path(tmp) / "b.dtsi"
        a.write_text("/ {\n\tfoo: bar@0000ff00 { };\n};\n")
        b.write_text("/ {\n\tfoo: bar@ff00 { };\n};\n")
        delta = dd.Delta(a, b)
        assert not delta.moved, f"spurious move: {delta.moved}"
        assert len(delta.same) == 1


def test_the_real_generation_delta():
    """gs101 -> gs201, the whole point of the exercise."""
    d = dd.Delta(OLD, NEW).to_dict()
    c = d["counts"]
    assert c["old_nodes"] == 42, c
    assert c["new_nodes"] == 40, c
    assert c["identical"] == 29, c
    assert c["moved"] == 7, c
    assert c["added"] == 4, c
    assert c["removed"] == 6, c
    # Overwhelmingly unchanged is the finding that makes the port tractable.
    assert c["identical"] / c["old_nodes"] > 0.65


def test_moves_group_into_coherent_blocks():
    """Seven scattered addresses would be noise. Two blocks, each a single
    region relocating, is a memory-map decision that can be checked against
    another source."""
    blocks = dd.Delta(OLD, NEW).blocks()
    assert len(blocks) == 2, [b["from"] for b in blocks]
    summary = {(b["from"], b["to"]): len(b["nodes"]) for b in blocks}
    assert summary[("0x17xxxxxx", "0x18xxxxxx")] == 5, summary
    assert summary[("0x111xxxxx", "0x112xxxxx")] == 2, summary


def test_every_move_cites_a_source_line():
    """An address with no citation is an address someone invented. The audit
    trail is the entire reason this is trustworthy without hardware."""
    for block in dd.Delta(OLD, NEW).blocks():
        for node in block["nodes"]:
            assert ":" in node["source"], node
            assert int(node["source"].rsplit(":", 1)[1]) > 0, node


def test_added_and_removed_are_reported_not_swallowed():
    d = dd.Delta(OLD, NEW).to_dict()
    assert "watchdog_cl0" in d["added_nodes"]
    assert "watchdog_cl1" in d["added_nodes"]
    assert "mct" in d["added_nodes"]
    # The removals matter as much: a hand count of this delta missed all six
    # tmuctrl nodes, which is exactly the class of omission this tool exists
    # to stop.
    assert len([n for n in d["removed_nodes"] if n.startswith("tmuctrl")]) == 6


def test_toml_round_trips():
    delta = dd.Delta(OLD, NEW)
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "delta.toml"
        path.write_text(dd.to_toml(delta, "google-gs101", "google-gs201"))
        doc = dd.load_toml(path)
        moves = dd.justified(doc)
    assert moves["pmu_system_controller"] == "18060000", moves
    assert len(moves) == len(delta.moved)


def test_verify_is_clean_when_the_candidate_matches_the_delta():
    delta = dd.Delta(OLD, NEW)
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "delta.toml"
        path.write_text(dd.to_toml(delta, "old", "new"))
        doc = dd.load_toml(path)
        # NEW is by construction the tree the delta describes, so verifying it
        # against OLD must find only what OLD has and NEW dropped.
        findings = dd.verify(NEW, OLD, doc)
    kinds = {f["kind"] for f in findings}
    assert kinds <= {"missing"}, findings
    assert all(f["label"].startswith("tmuctrl") for f in findings), findings


def test_verify_catches_an_invented_address():
    """The failure that matters: a value nobody can account for."""
    delta = dd.Delta(OLD, NEW)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "delta.toml").write_text(dd.to_toml(delta, "old", "new"))
        bad = tmp / "bad.dtsi"
        bad.write_text(NEW.read_text().replace("mct: timer@", "mct: timer@dead"))
        # Move a node to an address the delta does not justify.
        text = OLD.read_text().replace("gic: interrupt-controller@10400000",
                                       "gic: interrupt-controller@1f400000")
        (tmp / "invented.dtsi").write_text(text)
        findings = dd.verify(tmp / "invented.dtsi", OLD,
                             dd.load_toml(tmp / "delta.toml"))
    unjustified = [f for f in findings if f["kind"] == "unjustified"]
    assert any(f["label"] == "gic" for f in unjustified), findings


def test_verify_catches_a_dropped_node():
    """Silent omission is the failure mode of every hand-carried port: it
    surfaces months later as a driver that never probes."""
    with tempfile.TemporaryDirectory() as tmp:
        thin = pathlib.Path(tmp) / "thin.dtsi"
        thin.write_text("/ {\n\tgic: interrupt-controller@10400000 { };\n};\n")
        findings = dd.verify(thin, OLD, {"block": []})
    missing = [f["label"] for f in findings if f["kind"] == "missing"]
    assert "pmu_system_controller" in missing
    assert len(missing) > 30, len(missing)


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
