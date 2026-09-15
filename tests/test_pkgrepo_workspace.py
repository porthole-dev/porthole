#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""OPT-IN: pmbootstrap in the workspace really resolves the package repository.

test_pkgrepo.py proves porthole writes the config and checks the signature.
This proves the claim that matters -- a published fork is NOT rebuilt, and apk
installs the mirror's build -- by running the real pmbootstrap in the running
porthole-sandbox against a local HTTP copy of the release trees.

Skipped unless both hold:
  PORTHOLE_PKGREPO_E2E_MIRROR  container path of a copy of the releases, laid
                               out <dir>/main/<arch>/ and <dir>/systemd/main/<arch>/
                               (both arches, or pmbootstrap aborts -- the trap
                               this whole feature is careful about)
  porthole-sandbox             running

Optional:
  PORTHOLE_PKGREPO_E2E_WORK    work dir inside /pmb (default /pmb/pkgrepo-e2e).
                               Kept between runs so the chroots stay warm; the
                               first run bootstraps them from the network.
  PORTHOLE_PKGREPO_E2E_PORT    loopback port for the mirror (default 8124)
  PORTHOLE_PKGREPO_E2E_PKGS    published aports to check, default
                               "mesa libcamera phosh"
  PORTHOLE_PKGREPO_E2E_POLICY  subpackages whose apk policy must name the
                               mirror, default "mesa-dri-gallium libcamera libphosh"

Why a copy and not github.com: a private repository answers 404 to anonymous
downloads and pmbootstrap cannot send a token. Pointed at the public URL after
the repository is published, the same test is the live check.
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402
sys.path.insert(0, str(ROOT / "lib"))

import porthole_cmd_sandbox as sb  # noqa: E402
import porthole_pkgrepo as pkgrepo  # noqa: E402

KEY = ROOT / "profiles" / "google-taimen" / "keys" / "porthole-dev-packages-20260915.rsa.pub"


class Skip(Exception):
    """The suite's skip signal; _runner understands it."""


def _exec(script: str, timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(["podman", "exec", "-i", sb.CONTAINER, "sh", "-s"],
                          input=script, capture_output=True, text=True,
                          timeout=timeout)


def _require():
    mirror = os.environ.get("PORTHOLE_PKGREPO_E2E_MIRROR", "")
    if not mirror:
        raise Skip("opt-in: set PORTHOLE_PKGREPO_E2E_MIRROR to a container "
                   "path holding a copy of the release trees")
    if not shutil.which("podman") or subprocess.run(
            ["podman", "exec", sb.CONTAINER, "true"],
            capture_output=True).returncode != 0:
        raise Skip("podman is not installed, or porthole-sandbox is not running")
    return mirror


def test_a_published_fork_resolves_from_the_mirror_and_is_not_rebuilt():
    mirror = _require()
    work = os.environ.get("PORTHOLE_PKGREPO_E2E_WORK", "/pmb/pkgrepo-e2e")
    if not work.startswith("/pmb/") or work.rstrip("/") == "/pmb":
        raise AssertionError(f"{work}: must be a subdirectory of /pmb, never "
                             f"the workspace's own work dir")
    port = os.environ.get("PORTHOLE_PKGREPO_E2E_PORT", "8124")
    pkgs = os.environ.get("PORTHOLE_PKGREPO_E2E_PKGS", "mesa libcamera phosh").split()
    subpkgs = os.environ.get("PORTHOLE_PKGREPO_E2E_POLICY",
                             "mesa-dri-gallium libcamera libphosh").split()
    base = f"http://127.0.0.1:{port}"

    # Written from the HOST through porthole's own functions -- the ones
    # `sandbox up` uses -- into the host side of the /pmb mount.
    host_top = sb._sandbox_pmb({}) / work[len("/pmb/"):]
    (host_top / "work").mkdir(parents=True, exist_ok=True)
    repo = pkgrepo.Repo(base, f"{base}/systemd", KEY)
    mirrors, _notes = pkgrepo.merge_mirrors({}, repo, True)
    text = sb.pmb_config_text(
        "google-taimen", {"ui": "phosh", "service_manager": "systemd",
                          "user": "user"}, "mainline", mirrors)
    (host_top / sb.PMB_CFG_NAME).write_text(
        text.replace("work = /pmb\n", f"work = {work}/work\n"))
    assert pkgrepo.install_key(KEY, host_top / "work") in (
        "installed", "unchanged")
    stamp = host_top / "work" / "version"
    if not stamp.exists():
        stamp.write_text(_exec("python3 -c 'import pmb.config; "
                               "print(pmb.config.work_version)'").stdout.strip())

    pmb = f"/usr/bin/pmbootstrap --as-root --config {work}/{sb.PMB_CFG_NAME} -y"
    log = f"{work}/work/log.txt"
    script = f"""
set -u
cd /
setsid python3 -m http.server {port} --bind 127.0.0.1 --directory {mirror} \\
    >{work}/http.log 2>&1 &
server=$!
trap 'kill $server 2>/dev/null' EXIT
i=0
until wget -q -O /dev/null {base}/main/aarch64/APKINDEX.tar.gz; do
    i=$((i+1)); [ $i -gt 50 ] && {{ echo "MIRROR DID NOT ANSWER"; exit 3; }}
    sleep 0.1
done
start=$(wc -l < {log} 2>/dev/null || echo 0)
{pmb} build --lax --arch aarch64 {' '.join(pkgs)}; echo "BUILD_RC=$?"
echo "=== BUILD LOG"
tail -n +$((start+1)) {log} | grep -E "Build is necessary|Building [0-9]+ packages|Generating dependency tree|ERROR"
echo "=== LOCAL BUILDS"
for p in {' '.join(pkgs)}; do ls {work}/work/packages/*/aarch64/"$p"-[0-9]*.apk 2>/dev/null; done
echo "=== POLICY"
{pmb} chroot -b aarch64 -- apk policy {' '.join(subpkgs)}; echo "POLICY_RC=$?"
# A rootless workspace cannot umount a chroot's propagated /dev sub-mounts
# by path; lazily detach what this run left so nothing outlives it.
awk '$2 ~ "^{work}/" {{print $2}}' /proc/mounts | sort -r | while read -r m; do
    umount -l "$m" 2>/dev/null
done
"""
    done = _exec(script)
    out = done.stdout + done.stderr
    print(out)
    assert "BUILD_RC=0" in out, f"pmbootstrap build failed:\n{out[-3000:]}"

    # "Up to date" is only the mirror's doing if nothing was built locally in
    # this work dir: a local build satisfies pmbootstrap exactly the same way.
    local = out.split("=== LOCAL BUILDS", 1)[1].split("=== POLICY", 1)[0]
    assert not local.strip(), (
        f"{work} holds local builds, so 'up to date' proves nothing about the "
        f"mirror -- use a fresh PORTHOLE_PKGREPO_E2E_WORK:\n{local}")

    # PROVE IT RAN: the dependency tree of each package was generated, so
    # "no build necessary" is a verdict pmbootstrap reached, not a step that
    # never happened.
    for pkg in pkgs:
        assert f"Package '{pkg}' is up to date" in out, (
            f"pmbootstrap did not call {pkg} up to date:\n{out[-3000:]}")
        assert re.search(rf"aarch64/{re.escape(pkg)}: Generating dependency tree", out), (
            f"pmbootstrap never evaluated {pkg}:\n{out[-3000:]}")
        rebuilt = re.search(rf"Build is necessary for package '{re.escape(pkg)}'.*", out)
        assert not rebuilt, (
            f"{pkg} would be REBUILT although the mirror publishes it: "
            f"{rebuilt.group(0)} -- an aport newer than the published build "
            f"reads the same way")

    assert "POLICY_RC=0" in out, f"apk policy failed:\n{out[-3000:]}"
    policy = out.split("=== POLICY", 1)[1]
    for sub in subpkgs:
        block = re.search(rf"^{re.escape(sub)} policy:\n((?:  .*\n)+)", policy, re.M)
        assert block, f"no apk policy for {sub}:\n{policy}"
        # apk policy lists versions lowest first, and with no pinned tag apk
        # installs the highest: the LAST entry, whose repositories are the
        # indented lines under it.
        picks = re.findall(r"  (\S+):\n((?:    .*\n)+)", block.group(1))
        assert picks and base in picks[-1][1], (
            f"{sub}: the version apk picks ({picks[-1][0] if picks else '?'}) "
            f"does not come from the mirror {base}:\n{block.group(0)}")


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
