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


if __name__ == "__main__":
    sys.exit(main())
