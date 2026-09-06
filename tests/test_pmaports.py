#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""pmaports reading: the device index, SoC families, channels, UIs.

Runs against a synthetic pmaports tree so it needs no checkout and cannot be
broken by whatever branch someone left the real one on.
"""
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import porthole_pmaports as pmap  # noqa: E402
from porthole_cmd_ui import arch_allows  # noqa: E402

TMP = pathlib.Path(tempfile.mkdtemp(prefix="porthole-pmaports-test-"))


def device(category, codename, *, soc_dep=None, dtb="", **info):
    d = TMP / "device" / category / f"device-{codename}"
    d.mkdir(parents=True, exist_ok=True)
    lines = [f'deviceinfo_codename="{codename}"']
    if dtb:
        lines.append(f'deviceinfo_dtb="{dtb}"')
    for k, v in info.items():
        lines.append(f'deviceinfo_{k}="{v}"')
    (d / "deviceinfo").write_text("\n".join(lines) + "\n")
    deps = f'depends="\n\t{soc_dep}\n\tdevicepkg-dev\n"' if soc_dep else 'depends=""'
    # A soc-* named in a COMMENT must not be read as a dependency; taimen's real
    # APKBUILD mentions three of them in prose above the depends block.
    (d / "APKBUILD").write_text(
        f"# see soc-qcom-decoy for why this is not the SoC\n"
        f"pkgname=device-{codename}\n{deps}\n")
    return d


# A realistic little tree.
device("testing", "google-taimen", soc_dep="soc-qcom-msm8998",
       dtb="qcom/msm8998-google-taimen", name="Google Pixel 2 XL",
       manufacturer="Google", arch="aarch64", year="2017",
       flash_offset_base="0x00000000", flash_pagesize="4096")
device("archived", "oneplus-cheeseburger", soc_dep="soc-qcom-msm8998",
       dtb="qcom/msm8998-oneplus-cheeseburger", name="OnePlus 5",
       arch="aarch64", year="2017", flash_offset_base="0x00000000",
       flash_pagesize="4096", header_version="1")
device("community", "oneplus-enchilada", soc_dep="soc-qcom-sdm845",
       dtb="qcom/sdm845-oneplus-enchilada", name="OnePlus 6", arch="aarch64")
# No soc-* dependency: the DTB path is the only signal.
device("testing", "nosoc-device", dtb="mediatek/mt6735-nosoc-device",
       name="No SoC Dep", arch="aarch64")
# A modem package is not a SoC family.
device("testing", "modem-only", soc_dep="soc-qcom-modem",
       dtb="qcom/msm8916-modem-only", name="Modem Only")

(TMP / "channels.cfg").write_text(
    "[channels.cfg]\nrecommended=edge\n\n"
    "[edge]\ndescription=Rolling release\nbranch_pmaports=main\n"
    "branch_aports=master\nmirrordir_alpine=edge\n\n"
    "[v26.06]\ndescription=Latest release\nbranch_pmaports=v26.06\n")

for ui, arch, desc in [("phosh", "noarch !armhf", "(Wayland) Mobile UI"),
                       ("plasma-mobile", "noarch !armhf !x86", "(Wayland) Plasma"),
                       ("cage", "all", "(Wayland) Kiosk WM")]:
    d = TMP / "main" / f"postmarketos-ui-{ui}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "APKBUILD").write_text(f'pkgname=postmarketos-ui-{ui}\n'
                                f'pkgdesc="{desc}"\narch="{arch}"\n')

DEVICES = pmap.load_devices(TMP)


# ------------------------------------------------------------------ devices --

def test_finds_every_device():
    assert len(DEVICES) == 5, [d.codename for d in DEVICES]


def test_soc_comes_from_the_depends_block_not_a_comment():
    """Every APKBUILD in the fixture mentions `soc-qcom-decoy` in a comment.
    Reading the whole file instead of the depends block picks up the decoy."""
    taimen = next(d for d in DEVICES if d.codename == "google-taimen")
    assert taimen.soc == "qcom-msm8998", taimen.soc


def test_a_modem_package_is_not_a_soc_family():
    """soc-qcom-modem is a shared modem stack, not silicon. Treating it as a
    family groups unrelated devices as siblings."""
    dev = next(d for d in DEVICES if d.codename == "modem-only")
    assert dev.soc != "qcom-modem", "soc-qcom-modem must not be a family"
    assert dev.soc == "qcom-msm8916", f"should fall back to the DTB, got {dev.soc}"


def test_soc_falls_back_to_the_dtb_path():
    dev = next(d for d in DEVICES if d.codename == "nosoc-device")
    assert dev.soc == "mediatek-mt6735", dev.soc


def test_siblings_are_ranked_by_support_maturity():
    """When seeding a port you want the most mature sibling's answers."""
    sibs = pmap.siblings(DEVICES, "qcom-msm8998", exclude="google-taimen")
    assert [d.codename for d in sibs] == ["oneplus-cheeseburger"]


def test_maturity_orders_main_before_archived():
    order = [d.maturity for d in DEVICES]
    assert order == sorted(order), "load_devices must sort by maturity"


def test_soc_families_excludes_devices_with_no_soc():
    fams = pmap.soc_families(DEVICES)
    assert "qcom-msm8998" in fams and len(fams["qcom-msm8998"]) == 2
    assert "qcom-modem" not in fams


def test_deviceinfo_values_are_unquoted():
    dev = next(d for d in DEVICES if d.codename == "oneplus-cheeseburger")
    assert dev.info["flash_offset_base"] == "0x00000000"
    assert dev.info["header_version"] == "1"
    assert dev.name == "OnePlus 5"


# ----------------------------------------------------------------- channels --

def test_channels_parse():
    chans = pmap.channels(TMP)
    assert chans["_meta"]["recommended"] == "edge"
    assert chans["edge"]["branch_pmaports"] == "main"
    assert chans["v26.06"]["branch_pmaports"] == "v26.06"
    assert "channels.cfg" not in chans, "the header section is not a channel"


# ----------------------------------------------------------------------- ui --

def test_user_interfaces_parse():
    uis = {u["name"]: u for u in pmap.user_interfaces(TMP)}
    assert set(uis) == {"phosh", "plasma-mobile", "cage"}
    assert uis["phosh"]["description"] == "(Wayland) Mobile UI"


def test_alpine_arch_exclusion_syntax():
    """`arch="noarch !armhf"` means everything EXCEPT armhf. A substring match
    reports phosh as unavailable on aarch64 -- which is what most pmOS phones
    actually run, so the bug is both wrong and obviously wrong."""
    assert arch_allows("noarch !armhf", "aarch64")
    assert arch_allows("noarch !armhf", "x86_64")
    assert not arch_allows("noarch !armhf", "armhf")
    assert not arch_allows("noarch !armhf !x86", "x86")
    assert arch_allows("all", "riscv64")
    assert arch_allows("aarch64 x86_64", "aarch64")
    assert not arch_allows("aarch64 x86_64", "armv7")
    assert arch_allows("", "aarch64"), "an empty spec constrains nothing"
    assert arch_allows("noarch !armhf # a trailing comment", "aarch64")


# ------------------------------------------------------------------ finding --

def test_find_pmaports_prefers_the_configured_path():
    found = pmap.find_pmaports({"PORTHOLE_PMAPORTS": str(TMP)})
    assert found == TMP


def test_find_pmaports_returns_none_when_absent():
    assert pmap.find_pmaports({"PORTHOLE_PMAPORTS": "/nonexistent-xyz"}) != \
        pathlib.Path("/nonexistent-xyz")


# --------------------------------------------------------------------- dts --

def test_dts_include_following_finds_nodes_in_a_family_dtsi():
    """Taimen configures &ufshc and eleven others in the board-family dtsi it
    shares with walleye. Reading only the .dts reports all of them as
    unconfigured and sends someone to redo work done years ago."""
    from porthole_cmd_dts import nodes_including

    d = TMP / "dts"
    d.mkdir(exist_ok=True)
    (d / "soc.dtsi").write_text("&ufshc { status = \"okay\"; };\n"
                                "&usb3 { status = \"okay\"; };\n")
    (d / "family.dtsi").write_text('#include "soc.dtsi"\n'
                                   "&blsp1_uart3 { status = \"okay\"; };\n")
    (d / "device.dts").write_text('#include "family.dtsi"\n'
                                  "&tlmm { foo; };\n")

    shallow = set(__import__("re").findall(r"^\s*&([a-zA-Z_]\w*)\s*\{",
                                           (d / "device.dts").read_text(),
                                           __import__("re").M))
    assert shallow == {"tlmm"}, "sanity: the .dts alone names one node"

    nodes, files = nodes_including(d / "device.dts")
    assert nodes == {"tlmm", "blsp1_uart3", "ufshc", "usb3"}, nodes
    assert len(files) == 3, [f.name for f in files]


def test_dts_include_following_survives_a_cycle():
    """A dtsi that includes something which includes it back must not hang."""
    from porthole_cmd_dts import nodes_including
    d = TMP / "dts-cycle"
    d.mkdir(exist_ok=True)
    (d / "a.dtsi").write_text('#include "b.dtsi"\n&nodea { };\n')
    (d / "b.dtsi").write_text('#include "a.dtsi"\n&nodeb { };\n')
    nodes, _ = nodes_including(d / "a.dtsi")
    assert nodes == {"nodea", "nodeb"}, nodes


def test_dts_include_following_ignores_missing_headers():
    """`#include <dt-bindings/...>` and absent files must not raise."""
    from porthole_cmd_dts import nodes_including
    d = TMP / "dts-missing"
    d.mkdir(exist_ok=True)
    (d / "x.dts").write_text('#include <dt-bindings/gpio/gpio.h>\n'
                             '#include "nonexistent.dtsi"\n&only { };\n')
    nodes, files = nodes_including(d / "x.dts")
    assert nodes == {"only"}, nodes
    assert len(files) == 1


# ------------------------------------------------------------------ serial --

def test_serial_ignores_phantom_8250_ports():
    """Linux registers a fixed set of ttyS nodes whether the hardware exists or
    not, so a plain glob reports 32 serial ports on a laptop with none. `type`
    is the discriminator: 0 is PORT_UNKNOWN."""
    import porthole_cmd_serial as ser
    sysfs = TMP / "sys-tty"
    (sysfs / "ttyS0" / "device").mkdir(parents=True, exist_ok=True)
    (sysfs / "ttyS0" / "type").write_text("0\n")           # placeholder
    (sysfs / "ttyS1" / "device").mkdir(parents=True, exist_ok=True)
    (sysfs / "ttyS1" / "type").write_text("4\n")           # a real 16550A

    real = pathlib.Path
    try:
        # Point the checker at the fixture without touching the real /sys.
        orig = ser.pathlib.Path
        class FakePath(type(real("/"))):
            pass
        ser.pathlib = __import__("types").SimpleNamespace(
            Path=lambda p="": orig(str(p).replace("/sys/class/tty", str(sysfs))))
        assert not ser._is_real_uart("ttyS0"), "PORT_UNKNOWN must be filtered"
        assert ser._is_real_uart("ttyS1"), "a real UART must be kept"
    finally:
        ser.pathlib = __import__("pathlib")


def test_serial_baud_table_covers_the_usual_rates():
    import porthole_cmd_serial as ser
    for rate in (9600, 115200, 921600):
        assert ser.BAUDS.get(rate), f"{rate} missing from the baud table"


# -------------------------------------------------------------------- docs --

def test_docs_nav_quotes_titles_containing_colons():
    """Note titles routinely contain a colon -- "Playbook: first boot" -- and an
    unquoted colon-space is a YAML mapping. The file then fails to parse with an
    error pointing at a line that looks perfectly fine."""
    from porthole_cmd_docs import _nav, _yaml_key
    assert _yaml_key("Playbook: first boot") == '"Playbook: first boot"'
    assert _yaml_key('a "quoted" title') == '"a \\"quoted\\" title"'
    out = _nav([("Playbook: first boot", "a.md")])
    assert out.strip() == '- "Playbook: first boot": a.md', out


def test_docs_resolves_wikilinks_across_sections():
    """A law is linked from a workflow note. Resolving relative to the LINKING
    file points at a sibling that does not exist, and mkdocs --strict fails."""
    from porthole_cmd_docs import _resolve_links
    index = {
        "the-lock-says-who-not-what": pathlib.PurePath("laws/the-lock-says-who-not-what.md"),
        "frozen-is-not-hung": pathlib.PurePath("traps/frozen-is-not-hung.md"),
    }
    here = pathlib.PurePath("workflow/agent-protocol.md")
    out = _resolve_links("see [[the-lock-says-who-not-what]] and "
                         "[[frozen-is-not-hung]]", here, index)
    assert "(../laws/the-lock-says-who-not-what.md)" in out, out
    assert "(../traps/frozen-is-not-hung.md)" in out, out


def test_docs_renders_an_unwritten_wikilink_as_plain_text():
    """brain/README.md says to link liberally: a [[link]] to a note nobody has
    written yet is a marker, not an error. It must not become a broken link
    that fails a strict docs build."""
    from porthole_cmd_docs import _resolve_links
    out = _resolve_links("see [[not-written-yet]]",
                         pathlib.PurePath("laws/x.md"), {})
    assert out == "see `not-written-yet`", out


def test_docs_rewrites_readme_repo_links_for_the_flat_site():
    from porthole_cmd_docs import _readme_as_index
    out = _readme_as_index("see [config](docs/CONFIG.md) and [agents](AGENTS.md)")
    assert "(config.md)" in out and "(agents.md)" in out, out


def test_docs_index_drops_the_readme_table_of_contents():
    """The site has real navigation; an in-page TOC of anchors that no longer
    all exist is worse than none."""
    from porthole_cmd_docs import _readme_as_index
    out = _readme_as_index("# t\n\n## Table of contents\n\n- [a](#a)\n- [b](#b)\n\n"
                           "## Real section\n\nbody\n")
    assert "Table of contents" not in out
    assert "Real section" in out and "body" in out


def test_series_problems_reports_a_malformed_patch():
    import tempfile
    import porthole_cmd_aports as aports

    pkg = pathlib.Path(tempfile.mkdtemp(prefix="porthole-series-"))
    (pkg / "APKBUILD").write_text(
        'pkgname=linux-test\nsource="\n\tlinux-1.0.tar.xz\n\t0001-a.patch\n"\n')
    # Header promises two insertions; the body has one.
    (pkg / "0001-a.patch").write_text(
        "--- a/f.c\n+++ b/f.c\n@@ -1,2 +1,3 @@\n ctx\n-gone\n+new\n")

    kinds = [kind for kind, _ in aports._series_problems(pkg)]
    assert "malformed" in kinds, aports._series_problems(pkg)


def test_series_problems_still_reports_orphans():
    # The pre-existing kinds must survive the refactor.
    import tempfile
    import porthole_cmd_aports as aports

    pkg = pathlib.Path(tempfile.mkdtemp(prefix="porthole-series-"))
    (pkg / "APKBUILD").write_text('pkgname=linux-test\nsource="\n\tlinux.tar.xz\n"\n')
    (pkg / "0001-orphan.patch").write_text(
        "--- a/f.c\n+++ b/f.c\n@@ -1,1 +1,1 @@\n-a\n+b\n")

    kinds = [kind for kind, _ in aports._series_problems(pkg)]
    assert "orphan" in kinds, aports._series_problems(pkg)


def test_lint_reports_a_bad_series_even_when_apkbuild_lint_is_gone():
    """The local check runs whether or not pmbootstrap can lint.

    `aports lint` could previously only decline. A verb whose only outcome is
    "I cannot" is one nobody runs, and the series defect it would have caught
    cost the taimen port a subsystem.
    """
    import porthole_cmd_aports as aports

    problems = [("malformed", "0199-x.patch: header says -7/+9, body has -7/+8")]
    assert aports.lint_verdict(problems, apkbuild_lint_rc=None) == 1


def test_lint_missing_apkbuild_lint_alone_is_not_a_finding():
    # 69, never 1: the tool did not run, so the answer is not "no".
    import porthole_cmd_aports as aports

    assert aports.lint_verdict([], apkbuild_lint_rc=None) == 69


def test_lint_a_stripped_patch_alone_does_not_fail_the_verb():
    # 16 real patches carry this and build. Reported, not fatal.
    import porthole_cmd_aports as aports

    problems = [("stripped", "1000-drm.patch: line 12: ... leading space")]
    assert aports.lint_verdict(problems, apkbuild_lint_rc=0) == 0


def test_lint_unavailable_hint_does_not_claim_clean_over_warnings():
    # apkbuild-lint absent AND a non-fatal warning printed above: the hint
    # must not say "clean" -- that would contradict the yellow line the user
    # just read two lines up in the same invocation.
    import porthole_cmd_aports as aports

    problems = [("stripped", "1000-drm.patch: line 12: ... leading space")]
    hint = aports._lint_unavailable_hint(problems)
    assert "clean" not in hint, hint
    assert "non-fatal" in hint, hint


def test_dropped_patches_names_what_the_rewrite_would_delete():
    """The 19 venus patches, in the shape they would have vanished in."""
    import porthole_cmd_aports as aports

    listed = ["0001-a.patch", "0181-venus-a.patch", "0199-venus-s.patch"]
    new = ["0001-a.patch"]
    gone = aports.dropped_patches(listed, new)
    assert gone == ["0181-venus-a.patch", "0199-venus-s.patch"], gone


def test_dropped_patches_is_empty_when_the_series_is_reproduced():
    import porthole_cmd_aports as aports

    same = ["0001-a.patch", "0002-b.patch"]
    assert aports.dropped_patches(same, list(same)) == []


def test_append_numbers_from_one_past_the_highest():
    import porthole_cmd_aports as aports

    existing = ["0001-a.patch", "0199-venus.patch"]
    assert aports.next_patch_number(existing) == 200


def test_append_numbers_from_one_when_there_is_no_series():
    import porthole_cmd_aports as aports

    assert aports.next_patch_number([]) == 1


def test_patches_checks_dropped_patches_before_it_unlinks_anything():
    """The order is load-bearing, not tidiness.

    The guard must see the old series before any file backing it is deleted,
    or it reports on files it has already destroyed -- the exact silent-loss
    bug this task exists to prevent. Asserted via AST on the actual call
    nodes so a future edit that reorders the two statements fails this test
    even though every other assertion in this file still passes.
    """
    import ast
    import inspect
    import textwrap
    import porthole_cmd_aports as aports

    source = inspect.getsource(aports.cmd_patches)
    tree = ast.parse(textwrap.dedent(source))

    guard_lines = []
    unlink_lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "dropped_patches":
            guard_lines.append(node.lineno)
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "unlink":
            unlink_lines.append(node.lineno)

    # ast.walk is breadth-first, not source order, so the last node visited
    # is not the last in the file. min() asks the right question: does the
    # first dropped_patches() call happen before the first unlink() call?
    guard_lineno = min(guard_lines) if guard_lines else None
    unlink_lineno = min(unlink_lines) if unlink_lines else None

    assert guard_lineno is not None, "cmd_patches must call dropped_patches()"
    assert unlink_lineno is not None, "cmd_patches must call .unlink() on stale patches"
    assert guard_lineno < unlink_lineno, (
        f"dropped_patches() at line {guard_lineno} must run before "
        f"unlink() at line {unlink_lineno} -- otherwise the guard reports "
        f"on patches it has already deleted")


# ------------------------------------------- where pmbootstrap actually runs --
#
# `porthole aports checksum <pkg>` shelled out to the HOST pmbootstrap, which
# wants a sudo the rootless setup deliberately does not have, while the same
# call inside the workspace succeeded on the identical APKBUILD. `aports build`
# had already been moved off that path; checksum, lint, bump and the checksum
# inside `aports patches` had not (#25). The routing is one pure function so
# every one of them is decided in the same place.

def test_a_usable_workspace_runs_pmbootstrap_inside_it():
    import porthole_cmd_aports as aports
    import porthole_cmd_sandbox as sandbox

    argv = aports._pmb_argv(["pmbootstrap", "-y", "checksum", "phoc"],
                            usable=True, same_tree=True)
    assert argv[:2] == ["podman", "exec"], argv
    assert sandbox.CONTAINER in argv, argv
    assert "pmbootstrap -y checksum phoc" in " ".join(argv), argv


def test_no_workspace_still_runs_on_the_host():
    # The host path is the fallback, not a casualty: a machine with working
    # sudo and no container must keep working exactly as before.
    import porthole_cmd_aports as aports

    cmd = ["pmbootstrap", "-y", "checksum", "phoc"]
    assert aports._pmb_argv(cmd, usable=False, same_tree=True) == cmd


def test_a_workspace_that_mounts_another_pmaports_is_not_used():
    """The guard that matters. The container sees /pmb/cache_git/pmaports and
    nothing else, so routing a checksum there when the host is pointed at a
    per-device worktree would checksum a DIFFERENT tree from the one just
    edited -- and report success."""
    import porthole_cmd_aports as aports

    cmd = ["pmbootstrap", "-y", "checksum", "phoc"]
    assert aports._pmb_argv(cmd, usable=True, same_tree=False) == cmd


def test_the_routed_command_survives_a_space():
    # It crosses as a shell line, so an unquoted argument would arrive as two.
    import porthole_cmd_aports as aports

    argv = aports._pmb_argv(["pmbootstrap", "-y", "checksum", "a b"],
                            usable=True, same_tree=True)
    assert "'a b'" in argv[-1], argv


def test_the_mounted_pmaports_is_the_one_sandbox_up_mounts():
    """Read from the same config key `porthole sandbox up` derives it from, so
    the two cannot drift into disagreeing about which tree is inside."""
    import porthole_cmd_aports as aports

    got = aports._mounted_pmaports({"PORTHOLE_PMB_DIR": "/w/pmb"})
    assert str(got) == "/w/pmb/cache_git/pmaports", got


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


# ------------------------------------------------- the fork problem --

def _remotes(*lines):
    return lambda _path: list(lines)


def test_a_postmarketos_remote_is_recognised_under_any_name():
    """pmbootstrap matches by URL, not by the name `origin` -- and so must
    this, or a clone whose upstream is called `upstream` would be treated as a
    fork and quietly given an override it does not need."""
    assert pmap.upstream_remote("/x", _remotes(
        "origin\thttps://gitlab.postmarketos.org/postmarketOS/pmaports.git (fetch)"
    )) == "origin"
    assert pmap.upstream_remote("/x", _remotes(
        "mine\tgit@github.com:someone/pmaports.git (fetch)",
        "upstream\tgit@gitlab.postmarketos.org:postmarketOS/pmaports.git (push)",
    )) == "upstream"


def test_a_gitlab_ci_authentication_segment_does_not_hide_the_upstream():
    """The same strip `remote_to_name_and_clean_url` does. Without it a CI
    checkout looks like a fork."""
    assert pmap.upstream_remote("/x", _remotes(
        "origin\thttps://gitlab-ci-token:xyz@gitlab.postmarketos.org/"
        "postmarketOS/pmaports.git (fetch)"
    )) == "origin"


def test_a_fork_has_no_upstream_remote():
    assert pmap.upstream_remote("/x", _remotes(
        "origin\tgit@github.com:porthole-dev/pmaports.git (fetch)",
        "origin\tgit@github.com:porthole-dev/pmaports.git (push)",
    )) == ""
    assert pmap.upstream_remote("/x", _remotes()) == ""


def test_a_fork_gets_its_own_channels_cfg_and_a_stock_clone_does_not():
    """pmbootstrap reads channels.cfg from `<upstream>/main` and finds the
    upstream by URL, so a bring-up fork -- the normal state for this tool --
    dies inside `pmbootstrap install` with a message about a remote, never
    mentioning channels.cfg. PMB_CHANNELS_CFG is pmbootstrap's own documented
    override, so no patch and no write to anybody's repository.

    Returned ONLY for a checkout that cannot satisfy pmbootstrap's own path:
    upstream prefers main's copy over an old release branch's for a reason,
    and a stock clone must keep that behaviour."""
    tree = TMP / "forked"
    tree.mkdir(parents=True, exist_ok=True)
    (tree / "channels.cfg").write_text("[channels.cfg]\nrecommended=edge\n")

    fork = _remotes("origin\tgit@github.com:porthole-dev/pmaports.git (fetch)")
    assert pmap.channels_cfg_override(tree, fork) == str(tree / "channels.cfg")

    stock = _remotes(
        "origin\thttps://gitlab.postmarketos.org/postmarketOS/pmaports.git (fetch)")
    assert pmap.channels_cfg_override(tree, stock) == ""


def test_no_channels_cfg_means_no_override_to_offer():
    """A fork with no channels.cfg cannot be helped this way, and pointing at
    a file that is not there would turn one clear error into two."""
    bare = TMP / "bare"
    bare.mkdir(parents=True, exist_ok=True)
    fork = _remotes("origin\tgit@github.com:x/pmaports.git (fetch)")
    assert pmap.channels_cfg_override(bare, fork) == ""


# ------------------------------------------------ the kernel flavour --

def _device_apkbuild(codename, subpackages, extra=""):
    d = TMP / "device" / "testing" / f"device-{codename}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "APKBUILD").write_text(
        f'pkgname=device-{codename}\n'
        f'subpackages="\n{subpackages}\n"\n{extra}')
    return d


def test_the_kernel_flavours_come_from_the_device_packages_subpackages():
    """pmbootstrap defaults `kernel` to `stable` and only `pmbootstrap init`
    ever changes it -- an interactive command the workspace exists to avoid.
    A device offering only `mainline`, which is most bring-ups, therefore dies
    inside `pmbootstrap install` after the rootfs chroot is built, telling you
    to run the command you cannot run."""
    _device_apkbuild("acme-one", "\t$pkgname-kernel-mainline:kernel_mainline",
                     extra='kernel_mainline() {\n\tpkgdesc="Close to mainline"\n}\n')
    got = pmap.device_kernels(TMP, "acme-one")
    assert got == {"mainline": "Close to mainline"}, got


def test_several_flavours_are_all_reported():
    """A device with two is a question for a human, and the description is
    what makes it answerable -- so both are returned, not just the names."""
    _device_apkbuild(
        "acme-two",
        "\t$pkgname-kernel-mainline:kernel_mainline\n"
        "\t$pkgname-kernel-downstream:kernel_downstream",
        extra=('kernel_mainline() {\n\tpkgdesc="Mainline"\n}\n'
               'kernel_downstream() {\n\tpkgdesc="Vendor 4.9"\n}\n'))
    got = pmap.device_kernels(TMP, "acme-two")
    assert set(got) == {"mainline", "downstream"}, got
    assert got["downstream"] == "Vendor 4.9", got


def test_a_device_with_a_hardcoded_kernel_offers_no_flavours():
    """pmbootstrap's `kernels()` returns None here and never consults the
    setting, so reporting a flavour would invent a choice that does not
    exist."""
    _device_apkbuild("acme-none", "\t$pkgname-nonfree-firmware:nonfree_firmware")
    assert pmap.device_kernels(TMP, "acme-none") == {}
    assert pmap.device_kernels(TMP, "acme-missing") == {}
    assert pmap.device_kernels(TMP, "") == {}


if __name__ == "__main__":
    sys.exit(main())
