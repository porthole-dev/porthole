#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The sandbox workspace container: image build and lifecycle argv shapes.

Almost every test here calls a pure function that RETURNS a podman command
line, and invokes nothing. That is deliberate: CI has no podman and no device,
and a lifecycle test that needs either would simply be skipped there, which is
the same as not having it (see the Makefile's note on silent skips).

The exception is test_container_state_reports_the_workspace, which shells out
to `podman image exists` and `podman ps`. It asserts only on the SHAPE of what
comes back, so it passes with podman absent -- but it is the one test here
whose result depends on the machine, and it is the one that will flake if
something else is starting or stopping containers while the suite runs.
"""
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402
sys.path.insert(0, str(ROOT / "lib"))

import porthole_cmd_sandbox as sb  # noqa: E402


def test_image_tag_is_versioned_and_local():
    tag = sb._image_tag(ROOT)
    version = (ROOT / "VERSION").read_text().strip()
    assert tag == f"localhost/porthole-sandbox:{version}", tag


def test_containerfile_pins_the_base_tag():
    text = (ROOT / "sandbox" / "Containerfile").read_text()
    assert "alpine:latest" not in text, "a sandbox that changes under you is not a sandbox"
    assert "FROM docker.io/library/alpine:3.24" in text, text[:200]


def test_containerfile_asserts_the_load_bearing_pmbootstrap_flag():
    text = (ROOT / "sandbox" / "Containerfile").read_text()
    assert "--no-image" in text, (
        "the zero-privilege design rests on this flag; the BUILD must fail "
        "when a pmbootstrap without it lands, not a device session")


def test_build_argv_tags_the_image_and_points_at_the_containerfile():
    argv = sb._build_argv(ROOT, force=False)
    assert argv[:2] == ["podman", "build"], argv
    assert "-t" in argv and sb._image_tag(ROOT) in argv, argv
    assert str(ROOT / "sandbox" / "Containerfile") in argv, argv
    assert str(ROOT / "sandbox") == argv[-1], argv


def test_build_argv_force_bypasses_the_layer_cache():
    plain = sb._build_argv(ROOT, force=False)
    forced = sb._build_argv(ROOT, force=True)
    assert "--no-cache" not in plain, plain
    assert "--no-cache" in forced, forced


def test_device_key_is_created_private_and_is_idempotent():
    home = pathlib.Path(tempfile.mkdtemp(prefix="porthole-key-test-"))
    key = sb._ensure_device_key(home)
    assert key.exists(), key
    assert key.with_suffix(".pub").exists(), "public half missing"
    assert oct(key.stat().st_mode & 0o777) == "0o600", oct(key.stat().st_mode)
    first = key.read_bytes()
    again = sb._ensure_device_key(home)
    assert again == key and again.read_bytes() == first, "not idempotent"


def test_mounts_never_expose_the_users_ssh_directory():
    mounts = sb._mounts(ROOT, "/pmb-work", None, pathlib.Path("/k/device_key"),
                        "testdev", [])
    for src, dst, _opts in mounts:
        assert not src.rstrip("/").endswith("/.ssh"), (src, dst)
        assert src != str(pathlib.Path.home()), (src, dst)


def test_mounts_carry_the_device_key_read_only():
    mounts = sb._mounts(ROOT, "/pmb-work", None, pathlib.Path("/k/device_key"),
                        "testdev", [])
    key = [m for m in mounts if m[1] == sb.DEVICE_KEY_IN]
    assert len(key) == 1, mounts
    assert key[0][0] == "/k/device_key", key
    assert key[0][2] == "ro", "the key must be mounted read-only"


def test_mounts_include_the_device_mutex_lock():
    mounts = sb._mounts(ROOT, "/pmb-work", None, pathlib.Path("/k/device_key"),
                        "testdev", [])
    lock = sb._lock_path("testdev")
    assert lock == "/tmp/porthole-testdev.lock", lock
    assert any(src == lock for src, _dst, _o in mounts), (
        "without this a containerised agent and a host agent get DIFFERENT "
        "locks and drive the one phone at the same time")


def test_mounts_include_usb_and_the_workdir():
    mounts = sb._mounts(ROOT, "/pmb-work", None, pathlib.Path("/k/device_key"),
                        "testdev", [])
    dsts = [dst for _src, dst, _o in mounts]
    assert "/dev/bus/usb" in dsts, dsts
    assert "/pmb" in dsts, dsts
    assert "/porthole" in dsts, dsts


def test_lock_path_honours_tk_device_lock_override():
    old = os.environ.pop("TK_DEVICE_LOCK", None)
    try:
        assert sb._lock_path("testdev") == "/tmp/porthole-testdev.lock"
        os.environ["TK_DEVICE_LOCK"] = "/custom/lock"
        assert sb._lock_path("testdev") == "/custom/lock"
    finally:
        if old is None:
            os.environ.pop("TK_DEVICE_LOCK", None)
        else:
            os.environ["TK_DEVICE_LOCK"] = old


def test_ensure_device_key_raises_bail_when_ssh_keygen_missing():
    home = pathlib.Path(tempfile.mkdtemp(prefix="porthole-key-test-"))
    old_path = os.environ.get("PATH")
    os.environ["PATH"] = ""
    try:
        try:
            sb._ensure_device_key(home)
            assert False, "expected Bail when ssh-keygen is missing"
        except sb.Bail as exc:
            assert "ssh-keygen" in str(exc), exc
    finally:
        if old_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = old_path


def _argv_for_test():
    mounts = sb._mounts(ROOT, "/pmb-work", None, pathlib.Path("/k/device_key"),
                        "testdev", [])
    return sb._up_argv(ROOT, "localhost/porthole-sandbox:0.1.0", mounts, "testdev")


def test_up_argv_is_detached_and_named_and_not_ephemeral():
    argv = _argv_for_test()
    assert argv[:3] == ["podman", "run", "-d"], argv
    assert "--rm" not in argv, (
        "the workspace is persistent; --rm is what made the old shell useless")
    assert "--name" in argv and sb.CONTAINER in argv, argv


def test_up_argv_maps_container_root_to_our_uid():
    argv = _argv_for_test()
    assert "--userns=keep-id:uid=0,gid=0" in argv, argv


def test_up_argv_grants_fuse_but_not_host_networking():
    argv = _argv_for_test()
    assert "/dev/fuse" in argv, "fuse2fs is how the image is built without loop"
    assert "--network=host" not in argv, (
        "pasta reaches the device on the default netns; host networking "
        "gives up isolation for nothing")
    assert "--privileged" not in argv, argv


def test_up_argv_renders_every_mount():
    mounts = sb._mounts(ROOT, "/pmb-work", None, pathlib.Path("/k/device_key"),
                        "testdev", [])
    argv = sb._up_argv(ROOT, "img:1", mounts, "testdev")
    for src, dst, opts in mounts:
        assert f"{src}:{dst}:{opts}" in argv, (src, dst, opts)


def test_up_argv_keeps_the_container_alive():
    argv = _argv_for_test()
    assert argv[-1] in ("sleep", "infinity") or "infinity" in argv, argv


def test_mounts_include_the_porthole_user_config_dir_when_it_exists():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="porthole-xdg-test-"))
    (tmp / "porthole").mkdir()
    old = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_CONFIG_HOME"] = str(tmp)
    try:
        mounts = sb._mounts(ROOT, "/pmb-work", None, pathlib.Path("/k/device_key"),
                            "testdev", [])
        match = [m for m in mounts if m[1] == "/run/porthole/config/porthole"]
        assert len(match) == 1, (
            "without this the container resolves a DIFFERENT device and the "
            "lock mount above becomes decorative: " + repr(mounts))
        assert match[0][0] == str(tmp / "porthole"), match
        assert match[0][2] == "ro", (
            "config.env sets FASTBOOT/ADB and the HOST executes those "
            "values; a writable mount is container-to-host code "
            "execution: " + repr(match))
    finally:
        if old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = old


def test_a_long_sudo_timeout_is_reported_even_when_joined_by_commas(tmp=None):
    """sudoers joins Defaults options with commas, and this host really has
    `timestamp_timeout=9999,timestamp_type=global`. Splitting on whitespace
    alone captured "9999,timestamp_type=global", float() raised, and the
    except swallowed it -- so the check for a 167-hour root cache had never
    fired on the machine it was written for. A security check that silently
    does nothing is worse than no check: it reads as a clean bill of health."""
    import re as _re
    for line in ("Defaults:you timestamp_timeout=9999,timestamp_type=global",
                 "Defaults:you timestamp_timeout=9999"):
        value = _re.split(r"[\s,]", line.partition("timestamp_timeout")[2]
                          .lstrip("= "))[0]
        assert value == "9999", line
        assert float(value) > 60
    src = (ROOT / "lib" / "porthole_cmd_sandbox.py").read_text()
    assert 're.split(r"[\\s,]"' in src, "the comma split must stay"


def test_the_workspace_gets_its_own_work_dir_not_the_hosts():
    """A work dir built by the old host-root path is owned by uid 0 outside,
    and `--userns=keep-id:uid=0,gid=0` maps YOUR uid and nothing else -- so
    inside it reads as `nobody` and root-in-there cannot write a byte. It
    cannot be converted either: chown would flatten the uids INSIDE the
    chroots, and the userns has no mapping for them. The only version that
    works is a work dir the container creates itself."""
    host = str(pathlib.Path.home() / ".local/var/pmbootstrap")
    assert str(sb._sandbox_pmb({})) != host
    assert str(sb._sandbox_pmb({"PORTHOLE_SANDBOX_PMB_DIR": "/tmp/ws"})) == "/tmp/ws"


def test_the_generated_pmbootstrap_config_sets_work_and_aports():
    """`aports` defaults to `work / "cache_git" / "pmaports"` evaluated against
    the DEFAULT work dir at class-definition time, so setting `work` alone
    still sends pmbootstrap looking under /root. That surfaces as "pmaports
    dir not found: /root/..." and reads as a missing clone rather than a
    config that half applied."""
    text = sb.pmb_config_text("google-taimen")
    assert "work = /pmb" in text
    assert "aports = /pmb/cache_git/pmaports" in text
    assert "device = google-taimen" in text


def test_the_generated_config_never_carries_a_host_path():
    """The host's own pmbootstrap config names host paths. Carrying one into
    the container is the bug class SANDBOX-PROVISIONING.md §4b is about, so
    only known-safe keys cross and `work`/`aports` are always rewritten."""
    text = sb.pmb_config_text("google-taimen",
                              {"ui": "phosh", "work": "/home/someone/pmb",
                               "aports": "/home/someone/pmb/cache_git/pmaports"})
    assert "ui = phosh" in text
    assert "/home/someone" not in text
    assert "work = /pmb" in text


def test_the_image_installs_every_shim_the_workspace_needs():
    """mknod, chmod, sudo and pmbootstrap are all shimmed because a rootless
    userns cannot do what they assume. Two of them were lost to an editing
    slip once and the failure came back as "Do not run pmbootstrap as root!"
    three commands later, naming none of them -- hence the image-build
    assertion this test guards."""
    text = (ROOT / "sandbox" / "Containerfile").read_text()
    for shim in ("/usr/local/bin/pmbootstrap", "/usr/local/bin/mknod",
                 "/usr/local/bin/chmod", "/usr/local/bin/sudo",
                 "/usr/local/bin/porthole-devnodes",
                 "/opt/pmbootstrap-src/pmbootstrap.py"):
        assert f"test -x \"$f\"" in text or shim in text, shim
    assert "FATAL: $f missing from the image" in text


def test_the_device_nodes_are_bound_recursively():
    """A per-node `mount --bind` gives the right major/minor and reads fine,
    but open(O_CREAT) on it is EACCES -- and O_CREAT is what every shell
    redirection uses, so `> /dev/null` inside the chroot fails and apk dies
    with "can't create /dev/null". --rbind of the whole /dev has none of that;
    a plain --bind loses the per-node submounts and reports 0:0."""
    text = (ROOT / "sandbox" / "Containerfile").read_text()
    assert "mount --rbind /dev" in text
    assert "mount --bind /dev/null" not in text


# The device nodes pmbootstrap requires of every chroot it makes
# (pmb/config/__init__.py: chroot_device_nodes), minus ptmx, which podman's
# own /dev carries as a symlink rather than a node -- probing it would re-bind
# on every single call and warn forever about a /dev that is perfectly fine.
DEV_NODES = ("null", "zero", "full", "random", "urandom", "tty")


def _render_devnodes(into: pathlib.Path) -> pathlib.Path:
    """The porthole-devnodes script, generated by the Containerfile's own
    `printf`. Rendered rather than copied, so the test cannot drift from the
    recipe -- and so a quoting slip in that printf (a message split across two
    args, say) fails here rather than in an image build."""
    import subprocess
    text = (ROOT / "sandbox" / "Containerfile").read_text()
    head = "RUN printf '%s\\n' \\\n      '#!/bin/sh' \\\n      '# Re-give every existing chroot a /dev"
    body = text[text.index(head) + 4:]
    body = body[:body.index("\n > /usr/local/bin/porthole-devnodes")]
    script = into / "porthole-devnodes"
    subprocess.run(["sh", "-c", body + f" > {script}"], check=True)
    script.chmod(0o755)
    subprocess.run(["sh", "-n", str(script)], check=True)
    return script


def _run_devnodes(tmp: pathlib.Path, chroot: pathlib.Path):
    """Run it against a fake /pmb with `mount` stubbed. Returns what it tried.

    The script's paths are absolute, which is right in the image and useless
    here, so the one line naming /pmb is pointed at the fake tree. Everything
    else -- the probe, the loop, the warning -- is the shipped script.
    """
    import subprocess
    script = _render_devnodes(tmp)
    script.write_text(script.read_text().replace("/pmb/chroot_",
                                                 f"{tmp}/chroot_"))
    stub = tmp / "bin"
    stub.mkdir()
    (stub / "mount").write_text(f'#!/bin/sh\necho "$@" >> {tmp}/mounted\n')
    (stub / "mount").chmod(0o755)
    env = dict(os.environ, PATH=f"{stub}:{os.environ['PATH']}",
               HOME=str(tmp), XDG_CONFIG_HOME=str(tmp / "cfg"))
    proc = subprocess.run(["sh", str(script)], env=env, capture_output=True,
                          text=True)
    tried = (tmp / "mounted").read_text() if (tmp / "mounted").exists() else ""
    return tried, proc.stderr


def test_a_half_unmounted_chroot_is_re_armed_rather_than_believed():
    """porthole#33. pmbootstrap unmounts a chroot's /dev by walking the
    per-node submounts one at a time and dies partway at dev/shm; what it got
    through is gone, back to podman's placeholder regular files. Probing only
    dev/null -- which survived -- called that chroot healthy, and nothing
    else ever re-checks. A 5.5 h webkit2gtk-6.0 build then died on its LAST
    step because WebKit could not read /dev/urandom, and the SIGSEGV read as
    a qemu-user bug."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        dev = tmp / "chroot_buildroot_aarch64" / "dev"
        dev.mkdir(parents=True)
        # Exactly the shape measured on 2026-09-01: null, full and random
        # survived the walk; zero, urandom and tty are placeholders again.
        for node in ("null", "full", "random"):
            (dev / node).symlink_to(f"/dev/{node}")
        for node in ("zero", "urandom", "tty"):
            (dev / node).write_text("")
        tried, warned = _run_devnodes(tmp, dev)
    assert "--rbind /dev" in tried, (
        "dev/null was a device and every other node was a FILE; probing the "
        "one that survives is how this went unnoticed for a whole build")
    # ...and it says so, because a stub mount cannot actually fix it. The
    # build that dies eight hours later must not be the first news.
    assert "urandom is not a device node" in warned, warned


def test_a_healthy_chroot_is_left_alone():
    """It runs before every pmbootstrap call now, so a re-bind on each one
    would stack mounts all day."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        dev = tmp / "chroot_native" / "dev"
        dev.mkdir(parents=True)
        for node in DEV_NODES:
            (dev / node).symlink_to(f"/dev/{node}")
        tried, warned = _run_devnodes(tmp, dev)
    assert tried == "", tried
    assert warned == "", warned


def test_status_probes_the_live_namespace_not_the_container_spec():
    """#104: a kernel build died on `Invalid pmaports repository, could not
    find the config: /pmb/cache_git/pmaports/pmaports.cfg` -- the bind was
    gone inside the container -- while `sandbox status` said `pmaports
    mounted` at that same moment. The spec cannot see a mount that went away,
    so status has to ask for the file pmbootstrap itself demands."""
    calls = []

    class Done:
        returncode = 1

    class FakeSubprocess:
        # The module attribute, not subprocess.run itself: patching the real
        # module would leak into everything else this worker runs.
        SubprocessError = sb.subprocess.SubprocessError

        @staticmethod
        def run(argv, **kw):
            calls.append(argv)
            return Done()

    real = sb.subprocess
    sb.subprocess = FakeSubprocess
    try:
        assert sb.aports_readable_in_container() is False
    finally:
        sb.subprocess = real
    assert calls and calls[0][:3] == ["podman", "exec", sb.CONTAINER], calls
    assert calls[0][-1] == sb.APORTS_IN + "/pmaports.cfg", (
        "pmaports.cfg is the marker because it is the file the build fails "
        "on; a check that passes where the build fails is not a check: "
        + repr(calls[0]))


def test_a_probe_that_cannot_run_is_unknown_rather_than_healthy():
    """None, not True. A green tick that means "could not ask" is the exact
    shape of the bug."""
    class FakeSubprocess:
        SubprocessError = sb.subprocess.SubprocessError

        @staticmethod
        def run(argv, **kw):
            raise OSError("podman went away")

    real = sb.subprocess
    sb.subprocess = FakeSubprocess
    try:
        assert sb.aports_readable_in_container() is None
    finally:
        sb.subprocess = real


def test_the_guard_runs_where_every_build_routes_through():
    """One guard, and it has to be the wrapper: a chroot is broken by an
    unmount that happens mid-session, long after `sandbox up` armed it."""
    text = (ROOT / "sandbox" / "Containerfile").read_text()
    wrapper = text[text.index("> /usr/local/bin/pmbootstrap") - 900:
                   text.index("> /usr/local/bin/pmbootstrap")]
    assert "porthole-devnodes" in wrapper, wrapper


def test_the_guard_probes_every_node_pmbootstrap_requires():
    text = (ROOT / "sandbox" / "Containerfile").read_text()
    listed = text[text.index("nodes=\"") + 7:]
    listed = listed[:listed.index("\"")].split()
    assert listed == list(DEV_NODES), listed
    assert "ptmx" not in listed, "podman's /dev/ptmx is a symlink, not a node"


def test_up_argv_sets_xdg_config_home_and_matching_device_lock():
    argv = _argv_for_test()
    assert "-e" in argv and "XDG_CONFIG_HOME=/run/porthole/config" in argv, (
        "without this the in-container load_config never finds the mounted "
        "config, and PORTHOLE_DEVICE resolves differently inside: " + repr(argv))
    lock = sb._lock_path("testdev")
    assert f"TK_DEVICE_LOCK={lock}" in argv, (
        "the container must compute the SAME lock path as the host, or the "
        "bind-mounted lock file protects nothing: " + repr(argv))


def test_up_argv_points_ssh_at_the_mounted_device_key():
    argv = _argv_for_test()
    assert f"PORTHOLE_SSH_KEY={sb.DEVICE_KEY_IN}" in argv, (
        "unset, the device tooling falls back to ~/.ssh/id_ed25519 -- which "
        "the workspace deliberately cannot see: " + repr(argv))
    mounts = sb._mounts(ROOT, "/pmb-work", None, pathlib.Path("/k/device_key"),
                        "testdev", [])
    assert sb.DEVICE_KEY_IN in [dst for _s, dst, _o in mounts], (
        "the value must name a path that is actually mounted")


def test_up_argv_labels_the_container_with_the_lock_it_baked_in():
    argv = _argv_for_test()
    assert f"{sb.LOCK_LABEL}={sb._lock_path('testdev')}" in argv, (
        "TK_DEVICE_LOCK is baked in at up time and cannot follow a later "
        "`porthole use`; the label is how the drift is noticed: " + repr(argv))
    assert argv[argv.index(f"{sb.LOCK_LABEL}={sb._lock_path('testdev')}") - 1] \
        == "--label", argv


def test_lock_drift_only_fires_on_a_real_mismatch():
    assert sb._lock_drift("/tmp/a.lock", "/tmp/a.lock") == ""
    assert sb._lock_drift("", "/tmp/a.lock") == "", (
        "a container from before the label is unknowable, not drift")
    msg = sb._lock_drift("/tmp/old.lock", "/tmp/new.lock")
    assert "/tmp/old.lock" in msg and "/tmp/new.lock" in msg, msg


def test_exec_argv_omits_tty_when_a_command_is_given():
    argv = sb._exec_argv(["pmbootstrap", "status"], tty=False)
    assert "-it" not in argv, (
        "an agent has no TTY; -it here is why the container tier was "
        "unreachable from an agent")
    assert "-t" not in argv, argv
    assert argv[:2] == ["podman", "exec"], argv
    assert argv[-2:] == ["pmbootstrap", "status"], argv


def test_exec_argv_is_interactive_for_a_human_shell():
    argv = sb._exec_argv(None, tty=True)
    assert "-it" in argv, argv
    assert argv[-1] == "/bin/bash", argv


def test_exec_argv_targets_the_named_container():
    argv = sb._exec_argv(["true"], tty=False)
    assert sb.CONTAINER in argv, argv


def test_down_removes_the_container_but_names_no_volume():
    argv = sb._down_argv()
    assert argv[:3] == ["podman", "rm", "-f"], argv
    assert sb.CONTAINER in argv, argv
    assert "-v" not in argv and "--volumes" not in argv, (
        "the mounts are the user's real directories -- never remove them")


def test_down_message_distinguishes_removed_from_never_running():
    assert "removed" in sb._down_message(True), sb._down_message(True)
    assert "untouched" in sb._down_message(True), (
        "say the mounts survived; that is the reassurance `down` owes")
    assert "not running" in sb._down_message(False), sb._down_message(False)
    assert "removed" not in sb._down_message(False), (
        "`podman rm -f` exits 0 either way -- this claimed a removal that "
        "never happened once already")


def test_device_key_path_has_one_definition():
    home = pathlib.Path(tempfile.mkdtemp(prefix="porthole-key-test-"))
    assert sb._ensure_device_key(home) == home / sb.DEVICE_KEY, (
        "the created key and the path status reports must be the same "
        "constant; two copies of a security-relevant path drift")


def test_container_state_reports_the_workspace():
    state = sb._container_state(ROOT)
    for key in ("podman", "image", "image_built", "container_running",
                "device_key", "issues"):
        assert key in state, (key, sorted(state))
    assert state["image"] == sb._image_tag(ROOT), state["image"]


def test_a_failed_inspect_is_not_read_as_an_absent_label():
    """The drift guard must be loud when it cannot answer.

    `podman inspect` exits 125 with EMPTY stdout when its template names a
    field that no longer exists. Treating that as "no label" would disable
    device-lock drift detection for every container, silently -- and a silent
    pass reads exactly like agreement. It has to refuse instead.
    """
    try:
        sb._lock_from_inspect(125, "", "Error: unknown field .Config.Nope")
    except Exception as exc:
        assert "device-lock label" in str(exc), exc
    else:
        assert False, "a failed inspect was accepted as an absent label"


def test_an_absent_label_is_reported_as_absent_not_as_a_lock():
    for stdout in ("", "   ", "<no value>"):
        assert sb._lock_from_inspect(0, stdout, "") == "", repr(stdout)


def test_a_present_label_comes_back_verbatim():
    assert sb._lock_from_inspect(0, "/tmp/porthole-taimen.lock\n", "") == \
        "/tmp/porthole-taimen.lock"


# ---- the demotion, guarded ------------------------------------------------
#
# These exist because a propagation pass across nine files is exactly the kind
# of work that rots silently: the code ships, the docs keep recommending the
# thing it replaced, and an agent follows the docs. Grep, do not remember.

def test_the_bringup_skill_tells_an_agent_the_workspace_exists():
    text = (ROOT / "skills" / "porthole-bringup" / "SKILL.md").read_text()
    assert "porthole sandbox" in text, (
        "the bring-up skill never mentions the workspace, so an agent loading "
        "it would reach for host root instead")
    assert "sandbox shell" in text, "the skill does not say how to run a command"


def test_agent_facing_docs_name_the_workspace_verbs():
    for rel in ("AGENTS.md", "README.md", "skills/porthole-bringup/SKILL.md"):
        text = (ROOT / rel).read_text()
        assert "sandbox up" in text or "sandbox shell" in text, rel


def test_the_broker_is_gone_from_the_code_not_just_the_docs():
    """Deleted, not deprecated. A weaker path that still exists is the one a
    stuck agent reaches for, and this one granted a real sudoers entry while
    its own documentation admitted it could not contain a determined chroot
    payload. The verbs go with the files."""
    assert not (ROOT / "sandbox" / "ph-sudo").exists()
    assert not (ROOT / "sandbox" / "ph-sudo-client").exists()
    assert not (ROOT / "tests" / "test_sandbox.py").exists()
    src = (ROOT / "lib" / "porthole_cmd_sandbox.py").read_text()
    for gone in ("BROKER_DST", "_broker_state", "_policy_state",
                 "def _install", "def _audit", "def _uninstall"):
        assert gone not in src, f"{gone} survives in porthole_cmd_sandbox.py"
    choices = [kw.get("choices") for names, kw in sb.SPEC["args"]
               if names[0] == "action"][0]
    for gone in ("install", "audit", "uninstall"):
        assert gone not in choices, f"`sandbox {gone}` is still offered"


def test_the_verb_help_does_not_advertise_two_equal_tiers():
    assert "Two tiers" not in sb.SPEC["description"], sb.SPEC["description"]
    assert "broker" not in sb.SPEC["description"].lower(), sb.SPEC["description"]


# ---- reported from a real session that could not build in the workspace ----

def test_a_single_string_command_runs_as_a_shell_line():
    """`--command "pmbootstrap status"` arrives as ONE argv element, and
    podman would try to exec a file with that literal name."""
    argv = sb._exec_argv(["pmbootstrap status"], tty=False)
    assert argv[-3:] == ["/bin/bash", "-lc", "pmbootstrap status"], argv


def test_an_argv_command_is_passed_through_untouched():
    argv = sb._exec_argv(["pmbootstrap", "status"], tty=False)
    assert argv[-2:] == ["pmbootstrap", "status"], argv
    assert "-lc" not in argv, argv


def test_a_bare_program_name_is_not_wrapped():
    assert sb._exec_argv(["ls"], tty=False)[-1] == "ls"


def test_the_container_can_find_porthole():
    argv = sb._up_argv(ROOT, "img:1", sb._mounts(
        ROOT, "/pmb-work", None, pathlib.Path("/k/device_key"), "testdev", []),
        "testdev")
    path = [a for a in argv if a.startswith("PATH=")]
    assert path and "/porthole/bin" in path[0], (
        "porthole is not a command inside the workspace: " + str(path))


def test_the_container_is_told_which_device_the_host_is_on():
    """The config mount carries the user-config layer, but the host's active
    device usually comes from the ENVIRONMENT, which stops at the boundary."""
    argv = sb._up_argv(ROOT, "img:1", sb._mounts(
        ROOT, "/pmb-work", None, pathlib.Path("/k/device_key"), "taimen", []),
        "taimen")
    assert "PORTHOLE_DEVICE=taimen" in argv, argv


def test_the_workdir_is_the_mount_not_the_host_path():
    mounts = sb._mounts(ROOT, "/pmb-work", "/host/tree",
                        pathlib.Path("/k/device_key"), "taimen", [])
    argv = sb._up_argv(ROOT, "img:1", mounts, "taimen")
    assert "PORTHOLE_WORKDIR=/work" in argv, (
        "the profile's host path does not exist inside the container: "
        + str([a for a in argv if "WORKDIR" in a]))


def test_the_image_records_which_containerfile_built_it():
    """The tag is keyed on VERSION, so `build` skips an existing tag forever.
    Without a recipe hash a changed Containerfile would silently never reach
    anyone, and the workspace would quietly stay on the old one."""
    argv = sb._build_argv(ROOT, force=False)
    labels = [argv[i + 1] for i, a in enumerate(argv) if a == "--label"]
    assert any(x.startswith(sb.SPEC_LABEL + "=") for x in labels), labels
    sha = sb._containerfile_sha(ROOT)
    assert len(sha) == 16, sha
    assert f"{sb.SPEC_LABEL}={sha}" in labels, labels


def test_the_recipe_hash_changes_when_the_recipe_does(tmp=None):
    import hashlib
    import tempfile
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="porthole-cf-"))
    (scratch / "sandbox").mkdir()
    cf = scratch / "sandbox" / "Containerfile"
    cf.write_text("FROM alpine\n")
    first = sb._containerfile_sha(scratch)
    cf.write_text("FROM alpine\nRUN apk add ccache\n")
    assert sb._containerfile_sha(scratch) != first, "a changed recipe hashed the same"


def test_a_missing_containerfile_hashes_to_nothing_rather_than_raising():
    assert sb._containerfile_sha(pathlib.Path("/nonexistent/porthole")) == ""


def test_the_image_defuses_the_line_that_made_every_kernel_build_uncached():
    """envkernel bakes CCACHE_DISABLE=1 into the make alias every rung compiles
    through, and it is set on the command itself so nothing an outer script
    exports can beat it. The image rewrites it to CCACHE_DIR.

    Asserted here as well as in the image build because the failure is silent:
    without it builds still succeed, just uncached, and the only symptom is a
    cache directory that never grows."""
    text = (ROOT / "sandbox" / "Containerfile").read_text()
    assert "CCACHE_DISABLE=1|CCACHE_DIR=" in text, \
        "the image no longer rewrites envkernel's CCACHE_DISABLE line"
    assert "! grep -q 'CCACHE_DISABLE'" in text, \
        "the rewrite is not asserted, so an upstream rename would pass silently"


def test_the_image_does_not_install_a_compiler_cache_of_its_own():
    """A compile runs in chroot_native, a different rootfs. A ccache installed
    in the image cannot be reached from one, and having it there is what made
    an empty cache directory look like a configuration problem for months."""
    lines = (ROOT / "sandbox" / "Containerfile").read_text().splitlines()
    # Comments stripped: the block above this line says "ccache sccache" while
    # explaining why they are gone, and a grep over the whole file would read
    # the explanation as the thing it explains.
    recipe = [ln for ln in lines if not ln.lstrip().startswith("#")]
    assert not any("apk add" in ln for ln in recipe if "ccache" in ln), recipe
    assert not any(ln.strip().startswith("ccache") for ln in recipe), recipe


def test_the_image_carries_the_helper_every_build_needs():
    """The apk package installs the `pmb` python package and NO helpers/.
    helpers/envkernel.sh exists only in the source repo, and every kernel build
    goes through it -- so the workspace had a working CLI and could not build a
    kernel."""
    text = (ROOT / "sandbox" / "Containerfile").read_text()
    assert "envkernel.sh" in text, "nothing guarantees envkernel is in the image"
    assert "pmbootstrap.git" in text, "the source checkout is not cloned"


def test_the_container_is_pointed_at_its_own_pmbootstrap_source():
    """The user config names a HOST checkout, which does not exist inside."""
    mounts = sb._mounts(ROOT, "/pmb-work", "/host/tree",
                        pathlib.Path("/k/device_key"), "taimen", [])
    argv = sb._up_argv(ROOT, "img:1", mounts, "taimen")
    assert f"PORTHOLE_PMBOOTSTRAP_SRC={sb.PMBOOTSTRAP_SRC_IN}" in argv, argv
    assert sb.PMBOOTSTRAP_SRC_IN.startswith("/opt/"), sb.PMBOOTSTRAP_SRC_IN


def test_the_container_gets_a_fastboot_that_exists_inside_it():
    """The fifth host path in the user config, and the only one that reached a
    device session. config.env names the host's platform-tools fastboot; the
    config mount carries that value in, and in here the path does not exist, so
    `fastboot devices` exits 127 with empty stdout -- indistinguishable from
    "no device in the bootloader". A `fast` build reached "safe to flash",
    parked the phone in the bootloader and then timed out after 181.2s
    claiming it never arrived, with the phone in fastboot the whole time.
    """
    argv = _argv_for_test()
    assert "FASTBOOT=fastboot" in argv, argv


# ------------------------------------------- raw pmbootstrap is redirected --
#
# Agents kept reaching past the wrapper verbs and calling pmbootstrap directly
# through `sandbox shell --command`, which skips the buildroot lock, --lax, the
# log and the progress bar. Both build-destroying collisions on record were
# started that way. A hint printed above four hours of silence does not get
# read, so this is a refusal.

def test_a_raw_pmbootstrap_build_is_redirected_to_the_verb():
    import porthole_cmd_sandbox as sandbox

    assert sandbox.redirect_for(["pmbootstrap build --lax phoc"])[1] == \
        "porthole pkg build <aport>"
    assert sandbox.redirect_for(["pmbootstrap", "build", "phoc"])[0] == "build"


def test_checksum_is_redirected_too_because_it_deletes_running_builds():
    import porthole_cmd_sandbox as sandbox

    assert sandbox.redirect_for(["pmbootstrap checksum phoc"])[0] == "checksum"


def test_a_flag_between_pmbootstrap_and_its_verb_does_not_hide_it():
    import porthole_cmd_sandbox as sandbox

    assert sandbox.redirect_for(["pmbootstrap -y --details build phoc"])[0] == "build"


def test_read_only_pmbootstrap_commands_are_left_alone():
    """Refusing these would make the guard something people route around, and
    a guard that is routinely bypassed protects nothing."""
    import porthole_cmd_sandbox as sandbox

    for command in ("pmbootstrap status", "pmbootstrap log",
                    "pmbootstrap config channel", "pmbootstrap pull"):
        assert sandbox.redirect_for([command]) == (), command


def test_a_command_that_is_not_pmbootstrap_is_left_alone():
    import porthole_cmd_sandbox as sandbox

    assert sandbox.redirect_for(["ls -la /pmb"]) == ()
    assert sandbox.redirect_for(None) == ()


def test_a_build_hidden_behind_a_cd_is_still_caught():
    import porthole_cmd_sandbox as sandbox

    assert sandbox.redirect_for(["cd /porthole && pmbootstrap build phoc"])[0] \
        == "build"


def test_the_workspace_runs_an_init_that_reaps():
    """PID 1 is `sleep infinity`, which reaps nothing. One interrupted webkit
    build left four zombie clang++ processes behind, and a workspace that
    lives for weeks accumulates them."""
    import porthole_cmd_sandbox as sandbox

    argv = sandbox._up_argv("/root", "img", [], "google-taimen")
    assert "--init" in argv, argv


def test_the_image_carries_dtc():
    """`porthole verify` ends INCOMPLETE on every clean checkout because dtc
    is absent from the host AND the image, so the device-tree check -- the
    only one that reads reg properties -- never runs for anybody. A verdict
    that is permanently incomplete trains people to stop reading it."""
    import pathlib

    text = (pathlib.Path(__file__).resolve().parent.parent
            / "sandbox" / "Containerfile").read_text()
    assert "dtc" in text, "dtc is not installed in the sandbox image"


def _dtc_via_container(returncode: int) -> str:
    """`verify._dtc()` with the host dtc removed and the workspace up.

    Both tests below used to run their assertions only when the host had no
    dtc -- a state `porthole doctor` tells people to fix -- so on any machine
    that took that advice they executed nothing and still passed. They guard
    the two most recent fixes on this branch, so they have to reach the
    container path whatever the host has.
    """
    import porthole_cmd_sandbox as sandbox
    import porthole_cmd_verify as verify

    class Probe:
        stdout = "/usr/bin/dtc\n" if returncode == 0 else ""

    Probe.returncode = returncode

    saved = (verify.shutil.which, verify.subprocess.run,
             sandbox._container_running)
    verify.shutil.which = lambda *a, **k: None
    verify.subprocess.run = lambda *a, **k: Probe
    sandbox._container_running = lambda *a, **k: True
    try:
        return verify._dtc()
    finally:
        (verify.shutil.which, verify.subprocess.run,
         sandbox._container_running) = saved


def test_a_container_without_dtc_skips_rather_than_failing():
    """A tooling gap must not read as a check failure. With the container up
    but the image predating the dtc change, verify reported
    `fail device tree compiles: executable dtc not found` -- the same
    confusion `aports lint` was fixed for on this branch."""
    assert _dtc_via_container(1) == "", _dtc_via_container(1)


def test_the_containerised_dtc_can_receive_stdin():
    """`podman exec` drops stdin without -i, and dtc reads its source from
    stdin -- so the fallback compiled nothing and reported
    `<stdin>:0.0 syntax error`, which reads as a broken device tree rather
    than as a command that was never given any input."""
    import porthole_cmd_sandbox as sandbox

    found = _dtc_via_container(0)
    assert found == "podman exec -i {} dtc".format(sandbox.CONTAINER), found


def main():
    return _runner.run(globals())


# ------------------------------------------------- mounts go stale --

def test_a_workspace_started_before_the_working_repo_cannot_build():
    """Mounts are fixed when the container is created. Set a working repo
    afterwards -- the ordinary order on a new host, and what `porthole init`
    now does -- and the running workspace still has no /work, so ph-build.sh
    dies on its own `${PORTHOLE_WORKDIR:?}` naming neither the container nor
    the fix. `sandbox status` said "sandbox is configured" throughout."""
    import porthole_cmd_sandbox as sandbox

    drift = sandbox.workdir_drift({"PORTHOLE_WORKDIR": "/home/x/taimen"},
                                  {"/pmb", "/porthole"})
    assert drift, "a configured repo with no /work mount was reported as fine"
    assert "sandbox down" in drift, drift


def test_a_mounted_working_repo_is_not_drift():
    import porthole_cmd_sandbox as sandbox

    assert sandbox.workdir_drift({"PORTHOLE_WORKDIR": "/home/x/taimen"},
                                 {"/pmb", "/porthole", "/work"}) == ""


def test_no_working_repo_configured_is_the_hosts_problem_not_the_workspaces():
    """`porthole pkg build` needs no working repo, so a workspace without one
    is not broken -- and `porthole build` already names the missing key. A
    second refusal here would block the one tier that still works."""
    import porthole_cmd_sandbox as sandbox

    assert sandbox.workdir_drift({}, {"/pmb"}) == ""
    assert sandbox.workdir_drift({"PORTHOLE_WORKDIR": "  "}, {"/pmb"}) == ""


# ------------------------------------------- pmaports reaches the workspace --

def test_up_finds_pmaports_wherever_the_config_says_it_is():
    """`sandbox up` looked ONLY in `$PORTHOLE_PMB_DIR/cache_git/pmaports`, so
    a host whose pmaports is named by PORTHOLE_PMAPORTS or the per-device key
    was told "the workspace has nothing to build from, run `pmbootstrap init`
    on the host" -- false, and the opposite of what the workspace tier exists
    to avoid. `porthole init` had already asked for that checkout and written
    the key; `up` did not read it, then brought a workspace up with no
    pmaports mounted at all."""
    import porthole_pmaports as pmap

    with tempfile.TemporaryDirectory() as tmp:
        fake = pathlib.Path(tmp) / "mine" / "pmaports"
        (fake / "device").mkdir(parents=True)
        cfg = {"PORTHOLE_DEVICE": "google-taimen",
               "PORTHOLE_PMAPORTS_GOOGLE_TAIMEN": str(fake),
               "PORTHOLE_PMAPORTS": str(fake),
               "PORTHOLE_PMB_DIR": str(pathlib.Path(tmp) / "nowhere")}
        found, via = pmap.find_pmaports_with_source(cfg)
        assert found == fake, (found, via)
        assert "PMAPORTS" in via, via


def test_pmaports_is_mounted_where_pmbootstrap_derives_it():
    """The container's own pmbootstrap config points `aports` here, so the
    mount and the config have to name the same path -- and ph-build.sh's
    `_PH_APORTS` fallback finds it there too, with no configuration on either
    side."""
    import porthole_cmd_sandbox as sandbox

    mounts = sandbox._mounts("/repo", "/pmb-host", "/work-host", "/key",
                             "google-taimen", None, aports="/host/pmaports")
    dests = {dst: src for src, dst, _o in mounts}
    assert dests.get(sandbox.APORTS_IN) == "/host/pmaports", mounts
    assert sandbox.APORTS_IN in sandbox.pmb_config_text("google-taimen")


def test_a_checkout_already_inside_the_work_dir_is_not_bound_over_itself():
    """/pmb already exposes it, so a second bind is redundant at best and a
    nested mount podman has to unpick at worst."""
    import porthole_cmd_sandbox as sandbox

    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp) / "porthole-sandbox"
        inside = work / "cache_git" / "pmaports"
        inside.mkdir(parents=True)
        assert sandbox._inside(inside, work)
        assert not sandbox._inside(pathlib.Path(tmp) / "elsewhere", work)


def test_a_workspace_that_cannot_see_pmaports_says_so():
    """It was up and useless, and every row said it was fine."""
    import porthole_cmd_sandbox as sandbox

    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"PORTHOLE_SANDBOX_PMB_DIR": tmp}
        # (None, None) = the host has no checkout either. Injected, because
        # otherwise this reads the DEVELOPER's real pmaports and takes the
        # other branch -- which is exactly what it did, so this assertion only
        # held on a machine with no checkout at all, i.e. CI.
        gap = sandbox.aports_gap(cfg, {"/pmb", "/porthole"}, (None, None))
        assert gap, "a workspace with no pmaports at all was reported as fine"
        assert "porthole init" in gap, gap

        # Configured on the host but not mounted: a DIFFERENT problem with a
        # different fix, and telling someone to go and find a checkout they
        # have already configured is how "one command sets this host up" stops
        # being believed.
        checkout = pathlib.Path(tmp) / "elsewhere" / "pmaports"
        (checkout / "device").mkdir(parents=True)
        cfg["PORTHOLE_PMAPORTS"] = str(checkout)
        gap = sandbox.aports_gap(cfg, {"/pmb", "/porthole"},
                                 (checkout, "PORTHOLE_PMAPORTS"))
        assert "sandbox down" in gap, gap
        assert str(checkout) in gap, gap


def test_a_mounted_or_locally_cloned_pmaports_is_not_a_gap():
    """Two ways it can be there and only one of them is a mount: a clone made
    inside the work dir arrives through /pmb. Checking the mount list alone
    would call a working workspace broken."""
    import porthole_cmd_sandbox as sandbox

    with tempfile.TemporaryDirectory() as tmp:
        cfg = {"PORTHOLE_SANDBOX_PMB_DIR": tmp}
        assert sandbox.aports_gap(cfg, {sandbox.APORTS_IN}) == ""

        own = pathlib.Path(tmp) / "cache_git" / "pmaports" / "device"
        own.mkdir(parents=True)
        assert sandbox.aports_gap(cfg, {"/pmb"}) == ""


# ------------------------------------------- the kernel flavour --

def test_the_workspace_config_names_a_kernel_the_device_actually_offers():
    """pmbootstrap defaults `kernel` to `stable` and only `pmbootstrap init`
    -- interactive, and the thing this tier exists to avoid -- ever changes
    it. So `pmbootstrap install` died after building the rootfs chroot with
    "Selected kernel (stable) is not valid for device google-taimen. Please
    run 'pmbootstrap init'", advice that cannot be followed in a workspace
    about a value pmaports already answers."""
    import porthole_cmd_sandbox as sandbox

    text = sandbox.pmb_config_text("google-taimen", {}, "mainline")
    assert "kernel = mainline" in text, text
    # Empty leaves pmbootstrap its own default rather than writing a blank.
    assert "kernel" not in sandbox.pmb_config_text("google-taimen", {}, "")


def test_an_explicit_kernel_beats_the_host_config_which_beats_the_derived_one():
    """Each step earns its place: PORTHOLE_PMB_KERNEL is the developer's word,
    the host's own pmbootstrap config is what they chose for host builds and
    the workspace must not silently differ from it, and the derived answer is
    a fact about the device package."""
    import porthole_cmd_sandbox as sandbox

    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp) / "device" / "testing" / "device-acme-x"
        d.mkdir(parents=True)
        (d / "APKBUILD").write_text(
            'pkgname=device-acme-x\nsubpackages="\n\t$pkgname-kernel-mainline:k\n"\n')

        # An explicit value beats a host config that says otherwise, so the
        # host is given one here rather than left to whatever this machine has.
        got, why = sandbox.resolve_pmb_kernel(
            {"PORTHOLE_PMB_KERNEL": "downstream"}, tmp, "acme-x",
            host_cfg={"kernel": "stable"})
        assert got == "downstream" and "PORTHOLE_PMB_KERNEL" in why, (got, why)

        # The middle step, asserted rather than assumed.
        got, why = sandbox.resolve_pmb_kernel({}, tmp, "acme-x",
                                              host_cfg={"kernel": "stable"})
        assert got == "stable" and "host" in why, (got, why)

        # And with nothing above it, the derived answer. host_cfg={} is the
        # point: without it this read the developer's own pmbootstrap config
        # and returned that, so the derived branch was never exercised.
        got, why = sandbox.resolve_pmb_kernel({}, tmp, "acme-x", host_cfg={})
        assert got == "mainline", (got, why)
        assert "only one" in why, why


def test_two_flavours_are_not_guessed_between():
    """Choosing changes which kernel lands on the phone. That is not a guess
    to make for someone -- the same bar the tree autoselection holds."""
    import porthole_cmd_sandbox as sandbox

    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp) / "device" / "testing" / "device-acme-y"
        d.mkdir(parents=True)
        (d / "APKBUILD").write_text(
            'pkgname=device-acme-y\nsubpackages="\n'
            '\t$pkgname-kernel-mainline:a\n\t$pkgname-kernel-downstream:b\n"\n')
        # host_cfg={} or this never reaches the branch under test. It did not:
        # on any machine with a kernel configured it returned that instead, so
        # the one check standing between porthole and guessing which kernel
        # lands on a phone asserted nothing.
        got, _why = sandbox.resolve_pmb_kernel({}, tmp, "acme-y", host_cfg={})
        assert got == "", got
        # ...and the preview says so before a build spends twenty minutes
        # reaching pmbootstrap's own refusal.
        problem = sandbox.pmb_kernel_problem("", tmp, "acme-y")
        assert "stable" in problem and "PORTHOLE_PMB_KERNEL" in problem, problem


def test_a_kernel_the_device_does_not_offer_is_caught_before_the_build():
    import porthole_cmd_sandbox as sandbox

    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp) / "device" / "testing" / "device-acme-z"
        d.mkdir(parents=True)
        (d / "APKBUILD").write_text(
            'pkgname=device-acme-z\nsubpackages="\n\t$pkgname-kernel-mainline:k\n"\n')
        problem = sandbox.pmb_kernel_problem("stable", tmp, "acme-z")
        assert "mainline" in problem, problem
        assert sandbox.pmb_kernel_problem("mainline", tmp, "acme-z") == ""
        # A device whose kernel is hardcoded in depends never consults the
        # setting, so it cannot be wrong.
        assert sandbox.pmb_kernel_problem("stable", tmp, "acme-absent") == ""
        assert sandbox.pmb_kernel_problem("none", tmp, "acme-z") == ""


def test_the_channels_override_reaches_the_container_as_a_container_path():
    """Decided on the host, where git is reliable and the checkout really is;
    sent as the path INSIDE, because the host's is not resolvable in there.
    The same translation PORTHOLE_KERNEL_TREE gets."""
    import porthole_cmd_sandbox as sandbox

    argv = sandbox._up_argv("/repo", "img", [], "google-taimen",
                            "/home/x/pmaports/channels.cfg")
    joined = " ".join(argv)
    assert f"PMB_CHANNELS_CFG={sandbox.APORTS_IN}/channels.cfg" in joined, joined
    assert "/home/x/pmaports/channels.cfg" not in joined, (
        "a host path crossed the boundary")
    # A stock clone gets nothing, so pmbootstrap keeps its own behaviour.
    assert "PMB_CHANNELS_CFG" not in " ".join(
        sandbox._up_argv("/repo", "img", [], "google-taimen", ""))


def test_the_ceiling_is_raised_once_and_never_lowered():
    """5 GB is ccache's default, not a decision. But a ceiling somebody
    deliberately raised to 60G must survive `sandbox up` -- a setting that
    silently reverts is worse than no setting."""
    import porthole_cmd_build as build

    GB = 2 ** 30
    calls = []

    class FakeCtx:
        pass

    def fake_stats(_ctx):
        return [("aarch64", {"used": 1 * GB, "max": 5 * GB}),
                ("x86_64", {"used": 1 * GB, "max": 60 * GB})]

    def fake_set(_ctx, arch, size):
        calls.append((arch, size))

    raised = build.ensure_ccache_ceiling(
        FakeCtx(), 25 * GB, stats=fake_stats, apply=fake_set)
    assert raised == ["aarch64"], raised
    assert calls == [("aarch64", 25 * GB)], calls


def test_the_ceiling_it_asks_for_is_the_ceiling_it_reads_back():
    """ccache counts in GB, not GiB: `-M 25G` stores 25_000_000_000 bytes and
    `-s` reports 25.0, which is what parse_ccache_stats returns.

    Written as `25 * 2**30` the target was 26.84e9 -- 7% above anything ccache
    would ever report -- so every ceiling looked short, `sandbox up` raised it
    again, and printed `ceiling raised to 25G` on every run forever. Measured
    on the reference host before the fix: ratio 0.931, both arches, every run.
    The mirror-image bug is in the writer, which divided by 2**30 and would
    have asked for `23G` while its caller believed it had said 25."""
    import porthole_cmd_build as build

    want = build.CCACHE_DEFAULT_MAX
    assert build.ccache_max_arg(want) == "25G", build.ccache_max_arg(want)

    # What ccache holds and reports after exactly one raise to `want`.
    stored = float(build.ccache_max_arg(want).rstrip("G")) * 10 ** 9
    calls = []
    raised = build.ensure_ccache_ceiling(
        None, want,
        stats=lambda _c: [("aarch64", {"used": 1e9, "max": stored})],
        apply=lambda _c, arch, size: calls.append((arch, size)))
    assert raised == [], "a second `sandbox up` raised it again: {}".format(raised)
    assert calls == [], calls


# ------------------------------------------------------- gc: what is stale --

def _repo(names):
    d = pathlib.Path(tempfile.mkdtemp(prefix="porthole-gc-"))
    for n in names:
        (d / n).write_bytes(b"x")
    return d


def test_gc_keeps_the_newest_builds_and_offers_the_rest():
    d = _repo(["foo-1-r1.apk", "foo-1-r2.apk", "foo-1-r3.apk", "foo-1-r4.apk"])
    got = sorted(f.name for f in sb.superseded_apks(d, 2))
    assert got == ["foo-1-r1.apk", "foo-1-r2.apk"], got


def test_gc_never_offers_the_newest_build():
    """The one failure that must not happen. A wrong version sort here deletes
    the build you just made and reports success, and you find out at the next
    install -- which is exactly the silent-wrong-answer shape `pkg drift`
    exists to catch, so it must not be introduced by the thing cleaning up
    after it."""
    for names, newest in (
        (["foo-1-r9.apk", "foo-1-r10.apk"], "foo-1-r10.apk"),
        (["m-26.1.6-r14.apk", "m-26.2.2-r0.apk"], "m-26.2.2-r0.apk"),
        (["l-99990.7.2-r3.apk", "l-99990.7.2-r18.apk"], "l-99990.7.2-r18.apk"),
        (["d-1-r34.apk", "d-1-r41.apk", "d-1-r42.apk"], "d-1-r42.apk"),
        (["w-2.48.1-r4.apk", "w-2.52.6-r64.apk"], "w-2.52.6-r64.apk"),
    ):
        d = _repo(names)
        offered = {f.name for f in sb.superseded_apks(d, 1)}
        assert newest not in offered, (newest, offered)
        assert len(offered) == len(names) - 1, (names, offered)


def test_gc_orders_r10_after_r9_rather_than_as_text():
    """`-r10` sorts before `-r9` as a string. pkgrel is a number and is read as
    one; getting this wrong deletes the newest build of anything past r9, which
    on this port is every package that matters."""
    d = _repo(["foo-1-r9.apk", "foo-1-r10.apk"])
    assert [f.name for f in sb.superseded_apks(d, 1)] == ["foo-1-r9.apk"]


def test_gc_does_not_trip_over_a_non_numeric_version():
    """Comparing an int against a str raises TypeError in py3, and it would do
    so only on the one oddly-versioned package rather than in any test -- so
    here is that test. Each version part carries its kind in the sort key."""
    d = _repo(["x-1.2.3-r1.apk", "x-1.2.3-r2.apk", "x-alpha-r1.apk",
               "x-2020beta1-r1.apk"])
    got = sb.superseded_apks(d, 1)          # must not raise
    assert len(got) == 3, [f.name for f in got]


def test_gc_treats_each_package_separately():
    d = _repo(["foo-1-r1.apk", "foo-1-r2.apk", "bar-1-r1.apk"])
    got = sorted(f.name for f in sb.superseded_apks(d, 1))
    assert got == ["foo-1-r1.apk"], got


def test_gc_keeps_at_least_one_even_if_asked_for_none():
    """`--keep 0` would empty the repo, including the build the device is
    running. Clamped, because a flag that can delete everything is a flag
    someone types by accident."""
    d = _repo(["foo-1-r1.apk", "foo-1-r2.apk"])
    assert [f.name for f in sb.superseded_apks(d, 0)] == ["foo-1-r1.apk"]


def test_gc_ignores_files_that_are_not_apks():
    d = _repo(["foo-1-r1.apk", "foo-1-r2.apk"])
    (d / "APKINDEX.tar.gz").write_bytes(b"x")
    (d / "notes.txt").write_bytes(b"x")
    got = [f.name for f in sb.superseded_apks(d, 1)]
    assert got == ["foo-1-r1.apk"], got



if __name__ == "__main__":
    sys.exit(main())
