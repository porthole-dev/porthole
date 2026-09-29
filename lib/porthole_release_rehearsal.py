# SPDX-License-Identifier: MIT
"""Read-only local rehearsal of the organization release chain.

Each result says what actually ran. GitHub uploads and hardware cannot be
emulated by a green local command, so missing evidence stays blocked.
"""
import functools
import http.server
import pathlib
import re
import subprocess
import tarfile
import threading
import zlib
import io

import porthole_cmd_release as release
import porthole_pkgrepo as pkgrepo
from porthole_cli import child_env


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def row(phase, state, evidence):
    return {"phase": phase, "state": state, "evidence": evidence}


def is_org_checkout(repo):
    """Only call a checkout an organization repo when its origin says so."""
    result = subprocess.run(["git", "remote", "get-url", "--all", "origin"],
                            cwd=str(repo), capture_output=True, text=True)
    if result.returncode:
        return False
    return any(re.search(r"(?:github\.com[:/]porthole-dev/)[^/]+(?:\.git)?$", url.strip())
               for url in result.stdout.splitlines())


def commit_is_published(repo, commit):
    result = subprocess.run(
        ["git", "for-each-ref", "--contains=" + commit, "--format=%(refname)",
         "refs/remotes/origin"],
        cwd=str(repo), capture_output=True, text=True)
    return result.returncode == 0 and bool(result.stdout.strip())


def shared_pin_check(root, org):
    """Check pinned shared actions against the local tree and fetched refs."""
    shared = org / "community"
    references, missing, unpublished = 0, [], []
    repositories = (sorted(org.iterdir()) if org.is_dir() else []) + [root]
    for repo in repositories:
        if repo != root and (not (repo / ".git").exists() or not is_org_checkout(repo)):
            continue
        for workflow in sorted((repo / ".github").rglob("*.yml")):
            for path, commit in re.findall(
                    r"uses:\s+porthole-dev/\.github/(actions/[^@\s]+|\.github/workflows/[^@\s]+)@([a-f0-9]{40})",
                    workflow.read_text()):
                references += 1
                exists = subprocess.run(
                    ["git", "cat-file", "-e", commit + ":" + path],
                    cwd=str(shared), capture_output=True).returncode == 0
                if not exists:
                    missing.append(workflow.name + " -> " + commit[:12] + ":" + path)
                elif not commit_is_published(shared, commit):
                    unpublished.append(workflow.name + " -> " + commit[:12])
    if missing:
        detail = "missing local target: " + ", ".join(sorted(set(missing)))
        return row("shared action pins", "fail", detail)
    if unpublished:
        return row("shared action pins", "blocked",
                   "pin exists locally but is not reachable from fetched origin refs: " +
                   ", ".join(sorted(set(unpublished))))
    return row("shared action pins", "pass",
               "{} shared pins resolve in the local checkout and fetched origin refs".format(references))


def package_names(index):
    """Names actually advertised in a signed APKINDEX, not files nearby."""
    first = zlib.decompressobj(31)
    first.decompress(index)
    if not first.eof or not first.unused_data:
        raise ValueError("APKINDEX is not a signed two-member gzip stream")
    with tarfile.open(fileobj=io.BytesIO(first.unused_data), mode="r:gz") as archive:
        content = archive.extractfile("APKINDEX").read().decode()
    names = []
    for record in content.strip().split("\n\n"):
        fields = dict(line.split(":", 1) for line in record.splitlines() if ":" in line)
        if "P" in fields and "V" in fields:
            name = fields["P"] + "-" + fields["V"] + ".apk"
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*\.apk", name):
                raise ValueError("invalid APK filename in signed index")
            names.append(name)
    return names


def mirror_check(mirror, key, branch, target_arch):
    """Serve the supplied release tree and fetch each indexed APK anonymously."""
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(mirror)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = "http://127.0.0.1:{}".format(server.server_address[1])
        repo = pkgrepo.Repo(base, base + "/systemd", key)
        verdict, detail = pkgrepo.probe(repo, branch, target_arch)
        if verdict != "ok":
            return row("signed APK mirror", "fail", verdict + ": " + detail)
        count = 0
        urls = pkgrepo.index_urls(repo, branch, sorted(set((target_arch, pkgrepo.host_arch()))))
        for _label, _arch, url in urls:
            status, body, error = pkgrepo.fetch(url)
            if status != 200:
                return row("APK downloads", "fail", error or url)
            try:
                names = package_names(body)
            except (ValueError, OSError, KeyError, tarfile.TarError, UnicodeError) as exc:
                return row("APK downloads", "fail", "{}: {}".format(url, exc))
            for name in names:
                apk_url = url.rsplit("/", 1)[0] + "/" + name
                apk_status, apk_body, apk_error = pkgrepo.fetch(apk_url)
                if apk_status != 200 or not apk_body:
                    return row("APK downloads", "fail", apk_error or apk_url)
                count += 1
        return row("signed APK mirror and downloads", "pass" if count else "blocked",
                   "verified {} signed indexes and fetched {} indexed APKs over HTTP".format(len(urls), count))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def rehearse(root, org, pmaports=None, mirror=None, manifest=None, artifacts=None, cfg=None):
    root, org = pathlib.Path(root).resolve(), pathlib.Path(org).resolve()
    out = []
    apps = ("obscura", "tap", "phosh-nfc-quick-setting")
    shared = org / "community/.github/workflows/app-release.yml"
    out.append(row("shared app release workflow", "pass" if shared.is_file() else "blocked",
                   str(shared) if shared.is_file() else "shared workflow checkout unavailable"))
    for app in apps:
        checkout = org / app
        workflow = checkout / ".github/workflows/release.yml"
        if not workflow.is_file():
            out.append(row(app + " release", "blocked", "workflow checkout unavailable"))
            continue
        content = workflow.read_text()
        aport = re.search(r"(?m)^\s+aport:\s+(\S+)", content)
        pin = re.search(r"app-release\.yml@([a-f0-9]{40})", content)
        tag = subprocess.run(["git", "tag", "--list", "v*"], cwd=str(checkout),
                             capture_output=True, text=True)
        tags = tag.stdout.splitlines() if tag.returncode == 0 else []
        if not aport or not pin or "dry-run:" not in content:
            out.append(row(app + " release", "fail", "caller contract incomplete"))
        elif shared.is_file() and subprocess.run(
                ["git", "show", pin.group(1) + ":.github/workflows/app-release.yml"],
                cwd=str(org / "community"), capture_output=True).stdout != shared.read_bytes():
            out.append(row(app + " release", "blocked",
                           "pinned shared workflow commit unavailable or differs from local checkout"))
        elif not tags:
            out.append(row(app + " release", "blocked",
                           "caller {} present; no local version tag/archive to rehearse".format(aport.group(1))))
        else:
            out.append(row(app + " release", "blocked",
                           "tag {} exists; GitHub Codeload archive, release upload and aport PR untested".format(tags[-1])))
    repositories = (sorted(org.iterdir()) if org.is_dir() else []) + [root]
    for repo in repositories:
        if not (repo / ".git").exists() and repo != root:
            continue
        if repo != root and not is_org_checkout(repo):
            continue
        workflows = sorted((repo / ".github/workflows").glob("*.yml"))
        if not workflows:
            out.append(row(repo.name + " workflows", "missing", "no workflow in local checkout"))
        for workflow in workflows:
            if workflow.name == "release.yml" and repo.name in apps:
                continue
            if workflow == shared:
                continue
            if repo.name == "pmaports" and workflow.name == "build.yml":
                detail = "package build and GitHub artifact upload need a running aarch64 builder"
            elif repo.name == "pmaports" and "chromium" in workflow.name:
                detail = "Chromium stage/build artifacts and publication need a running aarch64 builder"
            else:
                detail = "workflow present; its GitHub runner and remote effects were not exercised"
            out.append(row(repo.name + "/" + workflow.name, "listed", detail))
    out.append(shared_pin_check(root, org))
    tree = pathlib.Path(pmaports).resolve() if pmaports else org / "pmaports"
    publisher = tree / ".github/scripts/test-publish-repo.py"
    if publisher.is_file():
        result = subprocess.run(["python3", str(publisher)], cwd=str(tree),
                                capture_output=True, text=True, timeout=60)
        out.append(row("package publisher contract", "pass" if result.returncode == 0 else "fail",
                       (result.stdout + result.stderr).strip()[-800:]))
    else:
        out.append(row("package publisher contract", "blocked", "pmaports test unavailable"))
    if mirror:
        repo = pkgrepo.resolve(cfg or {}, root)
        target_arch = (cfg or {}).get("PORTHOLE_ARCH", "")
        branch = pkgrepo.branch(tree)
        if not repo or not repo.key or not target_arch:
            out.append(row("signed APK mirror", "blocked",
                           "selected device needs a repository key and target architecture"))
        elif not branch:
            out.append(row("signed APK mirror", "blocked", "pmaports branch unavailable"))
        else:
            out.append(mirror_check(pathlib.Path(mirror), repo.key, branch, target_arch))
    else:
        out.append(row("signed APK mirror and downloads", "blocked",
                       "provide --mirror with real published release assets"))
    out.append(row("APK dependency resolution and installation", "blocked",
                   "requires a running porthole sandbox and opt-in repository E2E test"))
    site = root / "site-app/node_modules/.bin/astro"
    if site.is_file():
        steps = ([str(root / "bin/porthole"), "docs", "build"],
                 ["npm", "run", "build", "--prefix", "site-app"],
                 ["python3", "site-app/check-links.py"])
        for command in steps:
            env = child_env()
            env["ASTRO_TELEMETRY_DISABLED"] = "1"
            result = subprocess.run(command, cwd=str(root), capture_output=True,
                                    text=True, timeout=180, env=env)
            if result.returncode:
                out.append(row("website build and local links", "fail",
                               "{}: {}".format(" ".join(command), (result.stdout + result.stderr)[-800:])))
                break
        else:
            out.append(row("website build and local links", "pass",
                           "generated, built and checked local links using Docs workflow commands"))
    else:
        out.append(row("website build and local links", "blocked", "run npm ci --prefix site-app first"))
    if manifest and artifacts:
        try:
            candidate = release.read(manifest)
            release.verify_artifacts(candidate, artifacts,
                                     release.policies(root).get(candidate.get("device")))
            out.append(row("image artifact bytes", "pass", "manifest hashes match all local artifacts"))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            out.append(row("image artifact bytes", "fail", str(exc)))
    else:
        out.append(row("image artifact bytes", "blocked", "provide --manifest and --artifacts"))
    out.append(row("image assembly and boot", "blocked",
                   "requires a recorded candidate build and device boot evidence"))
    out.append(row("site image download URLs", "blocked",
                   "no promoted image URL was fetched from the public site"))
    planned = release.plan(root, tree if tree.is_dir() else None)
    out.append(row("image release policy", "pass" if planned["include"] else "blocked",
                   planned["include"] if planned["include"] else planned["blocked"]))
    out.append(row("GitHub uploads, Pages, attestation and hardware", "blocked",
                   "requires real service responses and a tested candidate image"))
    return {"schema": 1, "phases": out,
            "ready": bool(out) and all(item["state"] == "pass" for item in out)}
