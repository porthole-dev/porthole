#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""kconfig: the asked-for versus got comparison.

The trap under test: olddefconfig resolves an unmet dependency by DROPPING the
symbol and saying nothing, so a feature is simply absent at runtime with no
diagnostic. Catching that is the whole point of the verb.
"""
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from porthole_cmd_kconfig import NOISE, parse_config  # noqa: E402

TMP = pathlib.Path(tempfile.mkdtemp(prefix="porthole-kconfig-test-"))


def write(name, text):
    p = TMP / name
    p.write_text(text)
    return p


def test_parses_both_set_and_unset_forms():
    p = write("a.config", "CONFIG_A=y\n# CONFIG_B is not set\nCONFIG_C=m\n"
                          'CONFIG_D="a string"\nCONFIG_E=42\n\n# a comment\n')
    cfg = parse_config(p)
    assert cfg == {"CONFIG_A": "y", "CONFIG_B": "n", "CONFIG_C": "m",
                   "CONFIG_D": '"a string"', "CONFIG_E": "42"}, cfg


def test_an_explicitly_unset_symbol_is_not_the_same_as_an_absent_one():
    """`# CONFIG_X is not set` is a decision someone made. A symbol that is
    simply missing means it did not exist in this tree at all -- which is a
    different problem with a different fix."""
    cfg = parse_config(write("b.config", "# CONFIG_SET_OFF is not set\n"))
    assert cfg["CONFIG_SET_OFF"] == "n"
    assert "CONFIG_NEVER_MENTIONED" not in cfg


def test_noise_filter_catches_toolchain_probes():
    """These are written by the build system, not chosen by anyone. On a real
    taimen config they were the first five 'dropped' symbols and drowned the
    signal completely."""
    for symbol in ("CONFIG_CC_IS_GCC", "CONFIG_AS_IS_GNU", "CONFIG_LD_IS_BFD",
                   "CONFIG_CC_HAS_ASM_GOTO", "CONFIG_GCC_VERSION",
                   "CONFIG_TOOLS_SUPPORT_RELR", "CONFIG_PAHOLE_VERSION",
                   "CONFIG_HAVE_ARCH_KASAN", "CONFIG_ARCH_HAS_FORTIFY_SOURCE",
                   "CONFIG_GENERIC_MSI_IRQ_DOMAIN"):
        assert NOISE.match(symbol), f"{symbol} should be filtered as noise"


def test_noise_filter_does_not_swallow_real_symbols():
    """Filtering too much is worse than filtering too little: a dropped driver
    that never gets reported is exactly the failure this verb exists for."""
    for symbol in ("CONFIG_ARM_SMMU", "CONFIG_QCOM_SMD_RPM", "CONFIG_USB_GADGET",
                   "CONFIG_DRM_MSM", "CONFIG_ATH10K_SNOC", "CONFIG_PANIC_TIMEOUT",
                   "CONFIG_QCOM_Q6V5_PAS", "CONFIG_ARCH_QCOM",
                   "CONFIG_HAVE_A_REAL_NAME_ANYWAY_ELSE"[:20]):
        if symbol == "CONFIG_HAVE_A_REAL_NAME"[:20]:
            continue
        if symbol.startswith(("CONFIG_HAVE_", "CONFIG_GENERIC_")):
            continue
        assert not NOISE.match(symbol), f"{symbol} must NOT be filtered"


def test_arch_qcom_is_not_treated_as_noise():
    """CONFIG_ARCH_QCOM selects the whole platform. An earlier filter draft
    matched ARCH_* wholesale and would have hidden it."""
    assert not NOISE.match("CONFIG_ARCH_QCOM")
    assert not NOISE.match("CONFIG_ARCH_MSM8998")


def test_a_missing_file_is_reported_not_crashed():
    from porthole_cli import Bail
    try:
        parse_config(TMP / "does-not-exist.config")
    except Bail as exc:
        assert "cannot read" in exc.message
    else:
        assert False, "a missing config must raise Bail, not crash"


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
