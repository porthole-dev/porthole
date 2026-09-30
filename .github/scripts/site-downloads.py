#!/usr/bin/env python3
"""Refresh website downloads from public releases; verify APK indexes first."""
import datetime
import hashlib
import io
import json
import pathlib
import re
import sys
import tarfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))
from porthole_pkgrepo import public_key, rsa_verify, signature

OUT = ROOT / ".run/public-downloads"
OUT.mkdir(parents=True, exist_ok=True)
KEY = ROOT / "profiles/google-taimen/keys/porthole-dev-packages-20260915.rsa.pub"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Porthole-release-catalog", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read()


def releases(repo):
    return json.loads(fetch("https://api.github.com/repos/porthole-dev/" + repo + "/releases?per_page=100"))


def size(n):
    return "{:.1f} {}".format(n / (1024 if n < 1024 * 1024 else 1024 * 1024), "KiB" if n < 1024 * 1024 else "MiB")


rows = ["# Signed APK packages", "", "Browse the packages currently advertised by our signed repositories. These are individual packages; use the device downloads page for complete images.", "", "Updated " + datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "", "## Install and verify", "", "Install the repository key and configure the mirror as described in the [package setup guide](https://github.com/porthole-dev/pmos-packages#use-it-with-pmbootstrap). APK checks the signed index and package checksums during installation.", "", "[Download the signing key](https://raw.githubusercontent.com/porthole-dev/pmos-packages/main/keys/porthole-dev-packages-20260915.rsa.pub)", ""]
for release in sorted(releases("pmos-packages"), key=lambda r: r["tag_name"]):
    tag = release["tag_name"]
    if not re.fullmatch(r"(?:systemd/)?[a-z0-9._-]+/[a-z0-9_]+", tag):
        continue
    assets = {a["name"]: a for a in release["assets"]}
    index = assets["APKINDEX.tar.gz"]
    body = fetch(index["browser_download_url"])
    keyname, algorithm, sig, signed = signature(body)
    if keyname != KEY.name or not rsa_verify(public_key(KEY.read_text()), algorithm, sig, signed):
        raise SystemExit("Invalid repository signature: " + tag)
    with tarfile.open(fileobj=io.BytesIO(signed), mode="r:gz") as archive:
        records = archive.extractfile("APKINDEX").read().decode().strip().split("\n\n")
    rows += ["## " + tag, "", "**Signature verified** · [Signed index]({}) · SHA-256 `{}`".format(index["browser_download_url"], hashlib.sha256(body).hexdigest()), "", "| Package | Version | Download | SHA-256 |", "|---|---|---|---|"]
    count = 0
    for record in records:
        fields = dict(line.split(":", 1) for line in record.splitlines() if ":" in line)
        if not fields.get("P"):
            continue
        name = fields["P"] + "-" + fields["V"] + ".apk"
        asset = assets[name]
        rows.append("| {} | {} | [APK · {}]({}) | {} |".format(fields["P"], fields["V"], size(asset["size"]), asset["browser_download_url"], "`" + asset["digest"].split(":", 1)[1] + "`" if asset.get("digest", "").startswith("sha256:") else "Covered by signed index"))
        count += 1
    if not count:
        rows += ["", "Empty signed host index. Host build tools are compiled locally."]
    rows += [""]
(OUT / "packages.md").write_text("\n".join(rows) + "\n")

rows = ["# Device downloads", "", "Complete device images, required boot files, and installation instructions. Experimental candidates have passed build checks; hardware support is tracked separately on the device page.", "", "[Device directory and test results](../devices/)", ""]
images = [r for r in releases("pmaports") if "-candidate-" in r["tag_name"] and not r["draft"]]
if not images:
    rows += ["## Image availability", "", "The complete-image pipeline is being validated. No candidate is available yet. [Follow the image build](../pipelines/).", ""]
counts = {}
for release in images:
    assets = {a["name"]: a for a in release["assets"]}
    device = json.loads(fetch(assets["device.json"]["browser_download_url"]))
    codename = device["device"]
    if not re.fullmatch(r"[a-z0-9-]+", codename):
        raise SystemExit("invalid release device codename")
    counts[codename] = counts.get(codename, 0) + 1
    if counts[codename] > 5:
        continue
    image_name = codename + ".img.gz"
    required = {"boot.img", image_name, "SHA256SUMS", "INSTALL.md", "device.json"}
    if device.get("dtbo_sha256"):
        required.add("dtbo.img")
    if not required <= assets.keys():
        raise SystemExit("Incomplete image release: " + release["tag_name"])
    sums = fetch(assets["SHA256SUMS"]["browser_download_url"]).decode()
    hashes = dict((line.split()[1][2:] if line.split()[1].startswith("./") else line.split()[1], line.split()[0]) for line in sums.splitlines())
    rows += ["## " + release.get("name", device["name"]), "", "**Experimental** · Published " + release["published_at"][:10], "", "| File | Download | SHA-256 |", "|---|---|---|"]
    for name in [image_name, "boot.img"] + (["dtbo.img"] if "dtbo.img" in required else []) + ["SHA256SUMS", "INSTALL.md"]:
        asset = assets[name]
        if name in hashes and asset.get("digest") and asset["digest"] != "sha256:" + hashes[name]:
            raise SystemExit("Release asset checksum mismatch: " + name)
        rows.append("| {} | [{}]({}) | `{}` |".format(name, size(asset["size"]), asset["browser_download_url"], hashes.get(name, "See checksum file")))
    rows += ["", "### Installation", "", fetch(assets["INSTALL.md"]["browser_download_url"]).decode().split("\n", 1)[1].replace("## ", "#### "), "", "Verify build provenance with `gh attestation verify <downloaded-file> -R porthole-dev/pmaports`.", ""]
(OUT / "images.md").write_text("\n".join(rows) + "\n")
print("Updated device downloads and verified APK package catalog")
