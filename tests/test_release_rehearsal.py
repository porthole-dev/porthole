#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The download check must ask for APKs named by the signed index."""
import gzip
import io
import os
import pathlib
import subprocess
import tempfile
import sys
import tarfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import porthole_release_rehearsal as rehearsal  # noqa: E402


def member(name, data):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    return gzip.compress(buf.getvalue())


def test_index_names_drive_downloads():
    signed = member(".SIGN.RSA.fixture", b"signature") + member(
        "APKINDEX", b"P:one\nV:1-r0\n\nP:two\nV:2-r1\n\n")
    assert rehearsal.package_names(signed) == ["one-1-r0.apk", "two-2-r1.apk"]
    try:
        rehearsal.package_names(member("APKINDEX", b"P:one\nV:1-r0\n"))
    except ValueError as error:
        assert "signed" in str(error)
    else:
        raise AssertionError("unsigned index accepted")


def test_http_mirror_requires_every_signed_index():
    key = ROOT / "profiles/google-taimen/keys/porthole-dev-packages-20260915.rsa.pub"
    fixture = (ROOT / "tests/fixtures/pkgrepo/APKINDEX.empty.tar.gz").read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        top = pathlib.Path(tmp)
        for prefix in ("", "systemd"):
            for arch in ("aarch64", "x86_64"):
                dest = top / prefix / "main" / arch / "APKINDEX.tar.gz"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(fixture)
        result = rehearsal.mirror_check(top, key, "main", "aarch64")
        assert result["state"] == "blocked" and "0 indexed APKs" in result["evidence"]
        (top / "systemd/main/x86_64/APKINDEX.tar.gz").unlink()
        result = rehearsal.mirror_check(top, key, "main", "aarch64")
        assert result["state"] == "fail" and "host-arch-missing" in result["evidence"]


def test_relative_checkout_runs_publisher_contract():
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        root = pathlib.Path(tmp)
        org = root / "org"
        script = org / "pmaports/.github/scripts/test-publish-repo.py"
        script.parent.mkdir(parents=True)
        script.write_text("print('publisher reached')\n")
        relative = os.path.relpath(org)
        original_plan = rehearsal.release.plan
        rehearsal.release.plan = lambda *_: {"include": [], "blocked": []}
        try:
            result = rehearsal.rehearse(root, relative)
        finally:
            rehearsal.release.plan = original_plan
        phase = next(p for p in result["phases"] if p["phase"] == "package publisher contract")
        assert phase["state"] == "pass" and "publisher reached" in phase["evidence"]


def test_workflow_inventory_only_counts_github_org_checkouts():
    with tempfile.TemporaryDirectory() as tmp:
        local = pathlib.Path(tmp) / "taimen"
        local.mkdir()
        (local / ".git").mkdir()
        org = pathlib.Path(tmp) / "pmaports"
        org.mkdir()
        subprocess.run(["git", "init", "-q", str(org)], check=True)
        subprocess.run(["git", "-C", str(org), "remote", "add", "origin",
                        "https://github.com/porthole-dev/pmaports.git"], check=True)
        assert not rehearsal.is_org_checkout(local)
        assert rehearsal.is_org_checkout(org)


def test_shared_commit_must_be_reachable_from_a_fetched_origin_ref():
    with tempfile.TemporaryDirectory() as tmp:
        repo = pathlib.Path(tmp)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.org"], check=True)
        file = repo / "file"
        file.write_text("published\n")
        subprocess.run(["git", "-C", str(repo), "add", "file"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "published"], check=True)
        published = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", published], check=True)
        assert rehearsal.commit_is_published(repo, published)
        file.write_text("local only\n")
        subprocess.run(["git", "-C", str(repo), "commit", "-qam", "local"], check=True)
        local = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        assert not rehearsal.commit_is_published(repo, local)


if __name__ == "__main__":
    test_index_names_drive_downloads()
    test_http_mirror_requires_every_signed_index()
    test_relative_checkout_runs_publisher_contract()
    test_workflow_inventory_only_counts_github_org_checkouts()
    test_shared_commit_must_be_reachable_from_a_fetched_origin_ref()
    print("release rehearsal: index parsing and signed HTTP mirror controls passed")
