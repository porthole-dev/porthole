#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Config resolution: precedence, legacy aliases, profile loading.

Runs with no device attached and no third-party packages. Plain asserts --
the point is that a `python3 tests/test_config.py` in CI or in a shell tells
you in one line whether the compatibility contract still holds.

The legacy-alias tests are the load-bearing ones. Every command line in the
taimen docs sets PHONE= or TK_HOST= directly; if those stop overriding, a
year of documented invocations silently start talking to the wrong device.
"""
import os
import sys
import tempfile
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402
import porthole  # noqa: E402


def sandbox(profile_env="", user_env=None, root_env=None, device="testdev"):
    """Build a throwaway PORTHOLE_ROOT + XDG_CONFIG_HOME and return both paths."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="porthole-test-"))
    prof = tmp / "profiles" / device
    prof.mkdir(parents=True)
    (prof / "device.env").write_text(profile_env)
    if root_env is not None:
        (tmp / ".env").write_text(root_env)
    xdg = tmp / "xdg"
    if user_env is not None:
        (xdg / "porthole").mkdir(parents=True)
        (xdg / "porthole" / "config.env").write_text(user_env)
    return tmp, xdg


def load(tmp, xdg, **env):
    base = {"PORTHOLE_ROOT": str(tmp), "XDG_CONFIG_HOME": str(xdg)}
    base.update(env)
    return porthole.load_config(env=base)


# ------------------------------------------------------------------ parsing --

def test_parses_key_value_with_comments_and_quotes():
    tmp, xdg = sandbox(profile_env=(
        "# a comment\n"
        "\n"
        "PORTHOLE_SOC=msm8998\n"
        'PORTHOLE_DEVICE_NAME="Google Pixel 2 XL"\n'
        "PORTHOLE_ARCH = aarch64 \n"          # tolerate spaces around =
        "export PORTHOLE_DTB=qcom/x.dtb\n"    # tolerate a leading `export`
        "PORTHOLE_TRAILING=1 # inline comment\n"
    ))
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev")
    assert cfg["PORTHOLE_SOC"] == "msm8998", cfg["PORTHOLE_SOC"]
    assert cfg["PORTHOLE_DEVICE_NAME"] == "Google Pixel 2 XL"
    assert cfg["PORTHOLE_ARCH"] == "aarch64"
    assert cfg["PORTHOLE_DTB"] == "qcom/x.dtb"
    assert cfg["PORTHOLE_TRAILING"] == "1"


def test_a_quoted_value_with_a_trailing_comment_loses_its_quotes():
    """profiles/ documents almost every key, so `KEY="a"  # why` is the common
    shape. Keeping the quotes makes every comparison against the value fail
    silently -- it is what let a forbidden-slot guard pass `"a" != a`."""
    tmp, xdg = sandbox(profile_env='PORTHOLE_SLOT_FORBIDDEN="a"   # no image\n')
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev")
    assert cfg["PORTHOLE_SLOT_FORBIDDEN"] == "a", repr(cfg["PORTHOLE_SLOT_FORBIDDEN"])


def test_profile_beats_the_built_in_defaults():
    """Defaults are the LOWEST layer. A profile saying the device has A/B slots
    must win over the conservative default of 0."""
    tmp, xdg = sandbox(profile_env="PORTHOLE_HAS_AB_SLOTS=1\nPORTHOLE_SSH_PORT=2222\n")
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev")
    assert cfg["PORTHOLE_HAS_AB_SLOTS"] == "1"
    assert cfg["PORTHOLE_SSH_PORT"] == "2222"


def test_a_value_containing_a_hash_survives_when_quoted():
    # A password or a cmdline fragment can legitimately contain '#'.
    tmp, xdg = sandbox(profile_env='PORTHOLE_CMDLINE="loglevel=5 x#y"\n')
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev")
    assert cfg["PORTHOLE_CMDLINE"] == "loglevel=5 x#y", cfg["PORTHOLE_CMDLINE"]


# --------------------------------------------------------------- precedence --

def test_five_layer_precedence():
    """defaults < profile < user config.env < root .env < process env."""
    tmp, xdg = sandbox(
        profile_env="LAYER=profile\nONLY_PROFILE=p\n",
        user_env="LAYER=user\nONLY_USER=u\n",
        root_env="LAYER=root\nONLY_ROOT=r\n",
    )
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev")
    assert cfg["LAYER"] == "root", cfg["LAYER"]
    assert cfg["ONLY_PROFILE"] == "p"
    assert cfg["ONLY_USER"] == "u"
    assert cfg["ONLY_ROOT"] == "r"

    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev", LAYER="env")
    assert cfg["LAYER"] == "env", "process env must beat every file"


def test_source_reports_which_layer_won():
    tmp, xdg = sandbox(profile_env="LAYER=profile\n", user_env="LAYER=user\n")
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev")
    assert cfg.source("LAYER") == "user-config", cfg.source("LAYER")
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev", LAYER="env")
    assert cfg.source("LAYER") == "environment", cfg.source("LAYER")
    assert cfg.source("PORTHOLE_SSH_PORT") == "default"


def test_defaults_are_present_without_any_file():
    tmp, xdg = sandbox()
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev")
    assert cfg["PORTHOLE_SSH_PORT"] == "22"
    assert cfg["PORTHOLE_POLL"] == "0.5"
    assert cfg["FASTBOOT"] == "fastboot"


# ------------------------------------------------------------------ profile --

def test_missing_profile_is_an_error_not_a_silent_empty():
    """A typo'd device name must not resolve to a config with no device facts:
    that is how you flash the wrong DTB."""
    tmp, xdg = sandbox()
    try:
        load(tmp, xdg, PORTHOLE_DEVICE="nosuchdevice")
    except porthole.ProfileNotFound as exc:
        assert "nosuchdevice" in str(exc)
    else:
        assert False, "a missing profile must raise, not resolve empty"


def test_no_device_selected_is_tolerated():
    """`porthole devices` and `doctor` must work before a device is chosen."""
    tmp, xdg = sandbox()
    cfg = load(tmp, xdg)
    assert cfg["PORTHOLE_DEVICE"] == ""
    assert cfg["PORTHOLE_SSH_PORT"] == "22"


def test_profiles_dir_lists_devices():
    tmp, xdg = sandbox(device="google-taimen")
    (tmp / "profiles" / "_template").mkdir(parents=True, exist_ok=True)
    (tmp / "profiles" / "_template" / "device.env").write_text("")
    names = porthole.list_profiles(tmp)
    assert names == ["google-taimen"], names   # _template is not a device


def test_a_directory_without_a_device_env_is_not_a_device():
    """`new-device` makes the directory before it writes device.env, and for
    that instant a bare directory was reported as a known device -- so
    `aports worktree -d zzz-ruletest2` answered "no profile ... device.env
    does not exist. Known devices: ..., zzz-ruletest2", naming it missing
    and known in one sentence. It also made the parallel suites flaky, since
    test_cli_rules.py scaffolds such profiles while test_isolation.py is
    iterating them."""
    tmp, _ = sandbox(device="google-taimen")
    (tmp / "profiles" / "zzz-half-written").mkdir(parents=True)
    assert porthole.list_profiles(tmp) == ["google-taimen"]


# ------------------------------------------------------- legacy alias table --
# One test per row of spec section 4.3. These are the never-break-taimen tests.

def test_PHONE_is_used_verbatim_when_set():
    tmp, xdg = sandbox(profile_env="")
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev",
               PORTHOLE_USER="alice", PORTHOLE_HOST="10.0.0.5",
               PHONE="olduser@172.16.42.1")
    assert porthole.resolve_phone(cfg) == "olduser@172.16.42.1"


def test_PHONE_is_composed_when_unset():
    tmp, xdg = sandbox()
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev",
               PORTHOLE_USER="alice", PORTHOLE_HOST="10.0.0.5")
    assert porthole.resolve_phone(cfg) == "alice@10.0.0.5"


def test_HOST_and_TK_HOST_override_PORTHOLE_HOST():
    tmp, xdg = sandbox()
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev",
               PORTHOLE_HOST="10.0.0.5", HOST="172.16.42.1")
    assert porthole.resolve_host(cfg) == "172.16.42.1"

    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev",
               PORTHOLE_HOST="10.0.0.5", TK_HOST="172.16.42.9")
    assert porthole.resolve_host(cfg) == "172.16.42.9"


def test_HOST_beats_TK_HOST_when_both_set():
    tmp, xdg = sandbox()
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev",
               HOST="1.1.1.1", TK_HOST="2.2.2.2")
    assert porthole.resolve_host(cfg) == "1.1.1.1"


def test_host_falls_back_to_the_host_part_of_PHONE():
    """ph-stream.sh does `HOST=${PHONE#*@}`. Someone who sets only PHONE must
    still get a usable HOST for the ping probes."""
    tmp, xdg = sandbox()
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev", PHONE="bob@192.168.7.7")
    assert porthole.resolve_host(cfg) == "192.168.7.7"


def test_legacy_scalars_pass_through_untouched():
    tmp, xdg = sandbox(profile_env="PORTHOLE_POLL=9\n")
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev",
               FASTBOOT="/opt/fastboot", TK_POLL="0.1", TK_FORCE="1",
               TK_AGENT="claude", TK_DEVICE_LOCK="/tmp/x.lock",
               TK_DEVICE_TIMEOUT="30", TK_DEVICE_MAX="60",
               TK_DEVICE_STATE="BOOTED")
    assert cfg["FASTBOOT"] == "/opt/fastboot"
    assert porthole.legacy(cfg, "TK_POLL", "PORTHOLE_POLL") == "0.1"
    for k, v in [("TK_FORCE", "1"), ("TK_AGENT", "claude"),
                 ("TK_DEVICE_LOCK", "/tmp/x.lock"), ("TK_DEVICE_TIMEOUT", "30"),
                 ("TK_DEVICE_MAX", "60"), ("TK_DEVICE_STATE", "BOOTED")]:
        assert cfg[k] == v, f"{k} was mangled: {cfg[k]!r}"


def test_TK_AGENT_falls_back_to_PORTHOLE_AGENT():
    tmp, xdg = sandbox(user_env="PORTHOLE_AGENT=agentname\n")
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev")
    assert porthole.legacy(cfg, "TK_AGENT", "PORTHOLE_AGENT") == "agentname"


def test_ssh_opts_include_the_boot_survivable_flags():
    """Host keys change on essentially every boot, so these are mandatory,
    not laziness. Losing them makes every tool prompt and hang."""
    tmp, xdg = sandbox()
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev")
    opts = " ".join(porthole.ssh_opts(cfg))
    for flag in ("StrictHostKeyChecking=no", "UserKnownHostsFile=/dev/null",
                 "BatchMode=yes", "ConnectTimeout="):
        assert flag in opts, f"{flag} missing from {opts}"


# ------------------------------------------------------------------- runner --

# ------------------------------------------------------- environment drift --
#
# A stale `export PORTHOLE_KERNEL_PKG=...-6.18` outranked a profile committed
# at 7.2, and the build succeeded while producing the retired kernel. Observed
# twice, once poisoning a rootfs chroot. The provenance to catch it was always
# there; nothing acted on it.

def test_the_environment_beating_the_profile_is_blocking_drift():
    tmp, xdg = sandbox(profile_env='PORTHOLE_KERNEL_PKG="linux-7.2"\n',
                       device="testdev")
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev",
               PORTHOLE_KERNEL_PKG="linux-6.18")
    drifts = porthole.drift(cfg)
    hit = [d for d in drifts if d["key"] == "PORTHOLE_KERNEL_PKG"]
    assert hit, drifts
    assert hit[0]["winning"] == "linux-6.18", hit
    assert hit[0]["committed"] == "linux-7.2", hit
    assert hit[0]["committed_layer"] == "profile", hit
    assert hit[0]["blocking"] is True, "a wrong kernel must refuse, not warn"


def test_an_environment_that_agrees_is_not_drift():
    tmp, xdg = sandbox(profile_env='PORTHOLE_KERNEL_PKG="linux-7.2"\n')
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev",
               PORTHOLE_KERNEL_PKG="linux-7.2")
    assert not [d for d in porthole.drift(cfg)
                if d["key"] == "PORTHOLE_KERNEL_PKG"]


def test_a_key_the_profile_never_sets_is_not_drift():
    """Otherwise every unset key becomes a refusal the moment anyone exports
    it, which is how a guard gets worked around instead of obeyed."""
    tmp, xdg = sandbox(profile_env="")
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev",
               PORTHOLE_KERNEL_PKG="linux-6.18")
    assert not [d for d in porthole.drift(cfg)
                if d["key"] == "PORTHOLE_KERNEL_PKG"]


def test_the_device_only_advises():
    """Selecting a device from the environment is a documented workflow, and on
    the reference host the environment is the CORRECT one while the stored
    value is stale. Refusing there would block the normal setup."""
    tmp, xdg = sandbox(profile_env="", user_env='PORTHOLE_DEVICE="testdev"\n')
    (tmp / "profiles" / "other").mkdir(parents=True, exist_ok=True)
    (tmp / "profiles" / "other" / "device.env").write_text("")
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="other")
    hit = [d for d in porthole.drift(cfg) if d["key"] == "PORTHOLE_DEVICE"]
    assert hit, porthole.drift(cfg)
    assert hit[0]["blocking"] is False, "the device must not refuse a build"


def test_the_device_flag_is_not_mistaken_for_a_stale_export():
    """`porthole -d CODENAME` reaches load_config as an environment value, so
    without a marker the guard refuses a documented flag."""
    tmp, xdg = sandbox(profile_env="", user_env='PORTHOLE_DEVICE="testdev"\n')
    (tmp / "profiles" / "other").mkdir(parents=True, exist_ok=True)
    (tmp / "profiles" / "other" / "device.env").write_text("")
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="other",
               **{porthole.DELIBERATE_DEVICE: "1"})
    assert not [d for d in porthole.drift(cfg)
                if d["key"] == "PORTHOLE_DEVICE"]


def test_the_refusal_names_both_values_and_where_each_came_from():
    tmp, xdg = sandbox(profile_env='PORTHOLE_DEFCONFIG="right_defconfig"\n')
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev",
               PORTHOLE_DEFCONFIG="wrong_defconfig")
    text = "\n".join(porthole.drift_lines(porthole.drift(cfg)))
    for needed in ("PORTHOLE_DEFCONFIG", "wrong_defconfig", "right_defconfig",
                   "environment", "profile"):
        assert needed in text, (needed, text)


def test_config_remembers_what_each_layer_displaced():
    tmp, xdg = sandbox(profile_env='PORTHOLE_DEFCONFIG="from_profile"\n')
    cfg = load(tmp, xdg, PORTHOLE_DEVICE="testdev",
               PORTHOLE_DEFCONFIG="from_env")
    shadowed = dict((layer, value)
                    for layer, value in cfg.shadowed("PORTHOLE_DEFCONFIG"))
    assert shadowed.get("profile") == "from_profile", cfg.shadowed(
        "PORTHOLE_DEFCONFIG")


def main():
    return _runner.run(globals())


def test_the_cross_file_knobs_answer_to_both_names():
    """Old name wins -- lib/porthole.py::legacy's docstring is the contract:
    "That is the whole never-break-taimen contract in one function." A porter
    with TK_PMOS_PASSWORD exported in a shell they have had open for a week
    must not have a build stop dead because this repo renamed something.

    TK_SSH_OPTS is deliberately absent. It is not a knob: lib/porthole.sh
    BUILDS it from PORTHOLE_CONNECT_TIMEOUT, PORTHOLE_SSH_PORT and
    PORTHOLE_SSH_KEY, so a PORTHOLE_SSH_OPTS input would be a second authority
    over the same array rather than a twin of it.
    Asserted against the CALL SITES, not against `legacy()` itself. `legacy`
    is pure and has always been correct for any pair of names, so a test that
    only exercises it passes whether or not a single caller was ever moved
    onto it -- which is the whole shape this repo has been bitten by.
    """
    import os

    import porthole_cmd_build as build
    import porthole_cmd_sandbox as sandbox

    # The rootfs password, read by `porthole build` on the install rungs.
    real = dict(os.environ)
    try:
        os.environ.pop("TK_PMOS_PASSWORD", None)
        os.environ.pop("PORTHOLE_PMOS_PASSWORD", None)
        assert build.rootfs_password({"PORTHOLE_PMOS_PASSWORD": "new"}) == "new"
        assert build.rootfs_password({"TK_PMOS_PASSWORD": "old"}) == "old"
        assert build.rootfs_password({"TK_PMOS_PASSWORD": "old",
                                      "PORTHOLE_PMOS_PASSWORD": "new"}) == "old", (
            "TK_PMOS_PASSWORD must beat its twin: a porter's exported shell wins")
        # ...and the environment is still read when the config says nothing.
        os.environ["PORTHOLE_PMOS_PASSWORD"] = "from-env"
        assert build.rootfs_password({}) == "from-env"
    finally:
        os.environ.clear()
        os.environ.update(real)

    # The device mutex path, which the container is launched with.
    real = dict(os.environ)
    try:
        os.environ.pop("TK_DEVICE_LOCK", None)
        os.environ["PORTHOLE_DEVICE_LOCK"] = "/tmp/new.lock"
        assert sandbox._lock_path("taimen") == "/tmp/new.lock"
        os.environ["TK_DEVICE_LOCK"] = "/tmp/old.lock"
        assert sandbox._lock_path("taimen") == "/tmp/old.lock", (
            "TK_DEVICE_LOCK must beat its twin, or one physical phone gets "
            "two locks")
    finally:
        os.environ.clear()
        os.environ.update(real)


BLOCKS = """\
# a comment
mesa
  upstream: main/mesa
  tier:     required
  bogus:    ignored

libcamera
  upstream: main/libcamera
"""


def test_parse_blocks_reads_allowed_fields_in_file_order():
    got = porthole.parse_blocks(BLOCKS, ("upstream", "tier"))
    assert list(got) == ["mesa", "libcamera"]
    assert got["mesa"]["upstream"] == "main/mesa"
    assert got["mesa"]["tier"] == "required"


def test_parse_blocks_drops_unknown_fields_rather_than_failing():
    got = porthole.parse_blocks(BLOCKS, ("upstream", "tier"))
    assert "bogus" not in got["mesa"], got["mesa"]


def test_parse_blocks_keeps_a_block_with_only_one_field():
    got = porthole.parse_blocks(BLOCKS, ("upstream", "tier"))
    assert got["libcamera"] == {"upstream": "main/libcamera"}


if __name__ == "__main__":
    sys.exit(main())
