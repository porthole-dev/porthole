# SPDX-License-Identifier: MIT
"""Local release planning, evidence validation and catalogue generation.

No network, device writes, signing, or publication. CI uses these same checks
before handing candidate artifacts to a separately authorized publisher.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import re
from urllib.parse import urlsplit

import porthole_aports_manifest as aports
from porthole_cli import Bail, EX_FAIL, EX_OK

NAME = re.compile(r"[a-z0-9][a-z0-9._+-]*\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")
COMMIT = re.compile(r"[a-f0-9]{40}\Z")
RESULTS = {"works", "partial", "fails", "untested", "not applicable"}


def digest(path):
    h = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(pathlib.Path(path).read_text())


def write(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def https(value):
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username
            or parts.password or any(c in value for c in "\r\n<>\"() ")):
        raise ValueError("expected a public HTTPS URL")
    return value


def policies(root):
    out = {}
    for path in sorted((pathlib.Path(root) / "profiles").glob("*/release.json")):
        p = read(path)
        if p.get("schema") != 1 or not NAME.fullmatch(path.parent.name):
            raise ValueError("unsupported release policy: " + str(path))
        if not p.get("owner") or not isinstance(p.get("image_enabled"), bool):
            raise ValueError("policy needs owner and explicit image_enabled")
        if "dtbo_sha256" in p and (not isinstance(p["dtbo_sha256"], str)
                                   or not SHA.fullmatch(p["dtbo_sha256"])):
            raise ValueError("invalid DTBO digest")
        if p.get("freshness_days", 0) <= 0 or not p.get("critical_tests"):
            raise ValueError("policy needs freshness and critical tests")
        for combo in p["combinations"]:
            if set(combo) != {"channel", "ui", "init"} or not all(NAME.fullmatch(v) for v in combo.values()):
                raise ValueError("invalid image combination")
        out[path.parent.name] = p
    return out


def _profile_identity(path):
    values = {}
    for key in ("PORTHOLE_DEVICE_NAME", "PORTHOLE_VENDOR"):
        match = re.search(r"(?m)^" + key + r'=([^#\n]+)', path.read_text())
        values[key] = match.group(1).strip().strip('"') if match else ""
    return values


def inventory(root, tree):
    """Export existing manifests against this pmaports checkout, never git ancestry."""
    root, tree = pathlib.Path(root), pathlib.Path(tree)
    entries, inputs = {}, {}
    paths = sorted((root / "profiles").glob("*/aports.conf"))
    shared = root / "profiles/shared-aports.conf"
    if shared.exists():
        paths.append(shared)
    for path in paths:
        man = aports.parse(path.read_text())
        errors = aports.problems(man)
        if errors:
            raise ValueError("; ".join(errors))
        inputs[str(path.relative_to(root))] = digest(path)
        for name, fields in man.items():
            if not NAME.fullmatch(name):
                raise ValueError("invalid aport name: " + name)
            item = entries.setdefault(name, {"name": name, "owners": [], "why": fields["why"]})
            item["owners"].append(path.parent.name if path != shared else "shared")
    missing, packages = [], []
    for name, item in sorted(entries.items()):
        matches = [p.parent for pattern in ("*/%s/APKBUILD", "device/*/%s/APKBUILD", "extra-repos/*/%s/APKBUILD")
                   for p in tree.glob(pattern % name) if "archived" not in p.parts]
        if len(matches) > 1:
            raise ValueError("ambiguous aport: " + name)
        if not matches:
            missing.append(item)
        else:
            item["path"] = str(matches[0].relative_to(tree))
            packages.append(item)
    if not packages:
        raise ValueError("no maintained aports found in checkout")
    return {"schema": 1, "inputs": inputs, "packages": packages, "missing": missing}


def plan(root, tree=None):
    inv = inventory(root, tree) if tree else None
    configured = policies(root)
    profiles = sorted(path.parent.name for path in (pathlib.Path(root) / "profiles").glob("*/device.env")
                      if not path.parent.name.startswith("_"))
    orphaned = set(configured) - set(profiles)
    if orphaned:
        raise ValueError("release policy has no device profile: " + ", ".join(sorted(orphaned)))
    matrix, blocked = [], []
    for device in profiles:
        policy = configured.get(device)
        if policy is None:
            blocked.append({"device": device,
                            "reasons": ["No reviewed release policy exists for this device."]})
            continue
        reasons = list(policy.get("blocked_by", []))
        if not policy["image_enabled"] and not reasons:
            reasons.append("image builds are disabled")
        if inv:
            reasons += ["missing aport: " + p["name"] for p in inv["missing"] if device in p["owners"]]
        if reasons:
            blocked.append({"device": device, "reasons": reasons})
        else:
            matrix += [dict(device=device, **c) for c in policy["combinations"]]
    return {"include": matrix, "blocked": blocked}


def validate_manifest(m, policy=None):
    if m.get("schema") != 1 or m.get("status") not in {"verified", "failed"}:
        raise ValueError("unsupported manifest or status")
    for field in ("device", "channel", "ui", "init", "id"):
        if not NAME.fullmatch(m.get(field, "")):
            raise ValueError("invalid " + field)
    dt.date.fromisoformat(m["date"])
    https(m["build_url"])
    if m["status"] == "failed":
        if not m.get("reason"):
            raise ValueError("failed attempt needs a reason")
        return
    if set(m.get("sources", {})) != {"porthole", "pmaports", "pmbootstrap"}:
        raise ValueError("missing source provenance")
    if not all(COMMIT.fullmatch(s) for s in m["sources"].values()):
        raise ValueError("source revisions must be full commit IDs")
    if not SHA.fullmatch(m.get("builder_sha256", "")):
        raise ValueError("missing builder image digest")
    if not m.get("files") or not m.get("packages") or not m.get("indexes"):
        raise ValueError("empty artifacts or dependency provenance")
    roles = {f.get("role") for f in m["files"]}
    if not {"boot", "rootfs"} <= roles:
        raise ValueError("manifest needs boot and rootfs artifacts")
    if policy and policy.get("dtbo_sha256"):
        dtbo = [f for f in m["files"] if f.get("role") == "dtbo"]
        if len(dtbo) != 1 or dtbo[0].get("sha256") != policy["dtbo_sha256"]:
            raise ValueError("device requires its verified DTBO artifact")
    names = set()
    for f in m["files"]:
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", f["name"])
                or f["name"] in names or not SHA.fullmatch(f["sha256"])
                or not isinstance(f["size"], int) or f["size"] <= 0):
            raise ValueError("invalid or duplicate artifact")
        names.add(f["name"])
        if f.get("url"):
            https(f["url"])
    for item in m["packages"]:
        if not NAME.fullmatch(item["name"]) or not item["version"] or not SHA.fullmatch(item["sha256"]):
            raise ValueError("invalid package provenance")
    for index in m["indexes"]:
        https(index["url"])
        if not SHA.fullmatch(index["sha256"]):
            raise ValueError("invalid index provenance")
    required = {"boot-content", "rootfs-packages", "first-boot", "firmware", "export"}
    if not required <= set(m.get("checks", {})) or any(m["checks"][key] is not True for key in required):
        raise ValueError("required verification did not pass")


def validate_report(report, manifest, policy, today=None):
    validate_manifest(manifest, policy)
    if manifest["status"] != "verified":
        raise ValueError("hardware report requires a verified image")
    images = {f["sha256"] for f in manifest["files"] if f.get("role") == "rootfs"}
    if (report.get("schema") != 1 or report.get("device") != manifest["device"]
            or report.get("image_sha256") not in images):
        raise ValueError("report does not identify this rootfs image")
    if not report.get("kernel") or not report.get("packages_sha256") or not report.get("reviewer"):
        raise ValueError("report needs running kernel/package provenance and reviewer")
    if not SHA.fullmatch(report["packages_sha256"]):
        raise ValueError("invalid installed package inventory hash")
    date = dt.date.fromisoformat(report["date"])
    age = ((today or dt.date.today()) - date).days
    if age < 0:
        raise ValueError("hardware report is in the future")
    for name, test in report["tests"].items():
        if name not in policy["capabilities"] or test.get("result") not in RESULTS:
            raise ValueError("unknown capability/result: " + name)
        if test["result"] != "untested":
            if not test.get("command") or not test.get("control"):
                raise ValueError("executed test needs command and positive control")
            https(test.get("evidence", ""))
    return {"stale": age > policy["freshness_days"], "age_days": age,
            "critical_passed": all(report["tests"].get(t, {}).get("result") == "works"
                                   for t in policy["critical_tests"])}


def catalogue(root, directory, today=None):
    pol = policies(root)
    manifests, reports = [], []
    for path in sorted(pathlib.Path(directory).glob("*.json")):
        data = read(path)
        if "image_sha256" in data:
            reports.append(data)
        else:
            if data["device"] not in pol:
                raise ValueError("manifest names unknown device")
            validate_manifest(data, pol[data["device"]])
            combo = {k: data[k] for k in ("channel", "ui", "init")}
            if combo not in pol[data["device"]]["combinations"]:
                raise ValueError("manifest names an unsupported combination")
            manifests.append(data)
    devices = []
    profiles = sorted((path for path in (pathlib.Path(root) / "profiles").glob("*/device.env")
                       if not path.parent.name.startswith("_")), key=lambda p: p.parent.name)
    for profile in profiles:
        device = profile.parent.name
        policy = pol.get(device)
        if policy is None:
            identity = _profile_identity(profile)
            policy = {"name": identity["PORTHOLE_DEVICE_NAME"] or device,
                      "vendor": identity["PORTHOLE_VENDOR"], "status": "setup only",
                      "owner": "", "summary": "Only a device profile is in place; build and release setup is still needed.",
                      "capabilities": [], "critical_tests": [], "freshness_days": 0,
                      "image_enabled": False, "combinations": [],
                      "blocked_by": []}
        builds = sorted((m for m in manifests if m["device"] == device), key=lambda m: (m["date"], m["id"]), reverse=True)
        tested = []
        for report in (r for r in reports if r.get("device") == device):
            matches = [m for m in builds if any(f.get("role") == "rootfs" and f["sha256"] == report["image_sha256"] for f in m.get("files", []))]
            if not matches:
                raise ValueError("orphan hardware report")
            verdict = validate_report(report, matches[0], policy, today)
            tested.append(dict(report, **verdict))
        tested.sort(key=lambda r: r["date"], reverse=True)
        for build in builds:
            hashes = {f["sha256"] for f in build.get("files", [])
                      if f.get("role") == "rootfs"}
            build["download_ready"] = bool(policy["image_enabled"] and not policy["blocked_by"]) and any(
                r["image_sha256"] in hashes and r["critical_passed"] and not r["stale"]
                for r in tested)
        devices.append({"device": device, "policy": policy, "builds": builds, "reports": tested})
    if any(r.get("device") not in pol for r in reports):
        raise ValueError("report names unknown device")
    return {"schema": 1, "generated": (today or dt.date.today()).isoformat(), "devices": devices}


def markdown_catalogue(data, out):
    """Generate the device directory and a page for each profile."""
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rows = ["# Devices", "",
            "Browse device bring-up status, release availability, and test results.", ""]
    rows += ["| Device | Manufacturer | Port status | Images |", "|---|---|---|---|"]
    for device in data["devices"]:
        policy = device["policy"]
        available = sum(1 for build in device["builds"] if build.get("download_ready"))
        rows.append("| [{}]({}.md) | {} | {} | {} |".format(
            policy["name"], device["device"], policy.get("vendor", "Unknown"),
            policy["status"].capitalize(), str(available) if available else "Not available yet"))
    rows += [""]
    for d in data["devices"]:
        p, builds = d["policy"], d["builds"]
        rows += ["## " + p["name"], "",
                 "`{}` · {}".format(d["device"], p["status"].capitalize()), "",
                 p["summary"], "", "[Device details →]({}.md)".format(d["device"]), ""]
        page = ["# " + p["name"], "",
                "`{}` · {}".format(d["device"], p["status"].capitalize()), "",
                p["summary"], "",
                "Unofficial community work; not affiliated with Nura.", "",
                "## Downloads", ""]
        if not builds:
            page += ["No tested release image yet. [Browse experimental image downloads](../../images/).", ""]
        for m in builds:
            page += ["### {} · {} · {} · {}".format(
                m["channel"], m["ui"], m["init"], m["id"]), "",
                "Built {} · **{}** · [build log]({})".format(
                    m["date"], m["status"], m["build_url"]), ""]
            if m["status"] == "failed":
                page += ["Build check failed: " + m["reason"], ""]
            else:
                if not m["download_ready"]:
                    page += ["Hardware promotion pending: no current report passes every critical test.", ""]
                for f in m["files"]:
                    url = f.get("url") if m["download_ready"] else None
                    label = "[{}]({})".format(f["name"], url) if url else f["name"]
                    page += ["- {} · {} bytes · SHA-256 `{}`{}".format(
                        label, f["size"], f["sha256"],
                        "" if url else " · no eligible download")]
                page += [""]
            page += ["Source revisions: " + ", ".join(
                "{} `{}`".format(name, sha[:12])
                for name, sha in sorted(m.get("sources", {}).items())), ""]
        if not p["capabilities"]:
            page += ["## Device tests", "",
                     "Test tracking is not set up for this profile yet.", ""]
        else:
            page += ["## Device tests", "",
                     "Each result applies to the image hash shown in its row.", "",
                     "| Test | Status | Date | Image SHA-256 | Evidence |",
                     "|---|---|---|---|---|"]
        for cap in p["capabilities"]:
            report = next((r for r in d["reports"] if cap in r["tests"]), None)
            test = report["tests"][cap] if report else {}
            image_hash = report["image_sha256"] if report else "—"
            date = report["date"] + (" · stale" if report["stale"] else "") if report else "—"
            evidence = "[report]({})".format(test["evidence"]) if test.get("evidence") else "—"
            page.append("| {} | {} | {} | `{}` | {} |".format(
                cap, test.get("result", "not tested"), date, image_hash, evidence))
        if p.get("blocked_by"):
            page += ["", "## Before release", ""]
            page += ["- " + reason for reason in p["blocked_by"]]
        (out / (d["device"] + ".md")).write_text("\n".join(page) + "\n")
    (out / "index.md").write_text("\n".join(rows) + "\n")
    write(out / "index.json", data)


def markdown_downloads(data):
    """Separate the download decision from device support and build guidance."""
    rows = ["# Downloads", "",
            "Images are listed only after release checks pass. Choose a device for its test results and release notes.", ""]
    for d in data["devices"]:
        policy, builds = d["policy"], d["builds"]
        rows += ["## " + policy["name"], "",
                 "`{}` · {}".format(d["device"], policy["status"].capitalize()), "",
                 "[Device details →](../devices/{}/)".format(d["device"]), ""]
        if not builds:
            rows += ["No release images yet.", ""]
            continue
        for manifest in builds:
            rows += ["### {} · {} · {}".format(manifest["date"], manifest["channel"], manifest["id"]), "",
                     "Build status: **{}** · [build log]({})".format(manifest["status"], manifest["build_url"]), ""]
            if manifest["status"] == "failed":
                rows += ["No downloads: " + manifest["reason"], ""]
                continue
            if not manifest["download_ready"]:
                rows += ["No downloads: a current hardware report must pass every critical test.", ""]
            published = False
            for artifact in manifest["files"]:
                url = artifact.get("url") if manifest["download_ready"] else None
                label = "[{}]({})".format(artifact["name"], url) if url else artifact["name"]
                published |= bool(url)
                rows += ["- {} · {} bytes · SHA-256 `{}`{}".format(
                    label, artifact["size"], artifact["sha256"],
                    "" if url else " · no eligible download")]
            if not published and manifest["download_ready"]:
                rows += ["The verified build has no public download URLs yet."]
            rows.append("")
    return "\n".join(rows)


def verify_artifacts(manifest, artifacts, policy=None):
    validate_manifest(manifest, policy)
    if manifest["status"] != "verified":
        raise ValueError("failed attempt has no verified artifacts")
    artifacts = pathlib.Path(artifacts)
    for item in manifest["files"]:
        path = artifacts / item["name"]
        if (path.is_symlink() or path.stat().st_size != item["size"]
                or digest(path) != item["sha256"]):
            raise ValueError("artifact content mismatch: " + item["name"])


def dispatch(args, ctx):
    try:
        if args.action == "plan":
            result = plan(ctx.root, args.pmaports)
        elif args.action == "inventory":
            if not args.pmaports:
                raise ValueError("inventory requires --pmaports")
            result = inventory(ctx.root, args.pmaports)
        elif args.action == "catalog":
            result = catalogue(ctx.root, args.catalog or ctx.root / "releases")
            if args.output:
                markdown_catalogue(result, args.output)
        elif args.action == "verify":
            if not args.manifest or not args.artifacts:
                raise ValueError("verify requires --manifest and --artifacts")
            result = read(args.manifest)
            verify_artifacts(result, args.artifacts, policies(ctx.root).get(result.get("device")))
        elif args.action == "rehearse":
            from porthole_release_rehearsal import rehearse
            result = rehearse(ctx.root, args.org or ctx.root / ".run/organization-cleanup",
                              args.pmaports, args.mirror, args.manifest, args.artifacts)
        else:
            if not args.manifest or not args.report:
                raise ValueError("report requires --manifest and --report")
            manifest = read(args.manifest)
            result = validate_report(read(args.report), manifest, policies(ctx.root)[manifest["device"]])
        if args.output and args.action != "catalog":
            write(args.output, result)
        ctx.emit(result, lambda: ctx.out(json.dumps(result, indent=2)))
        return EX_FAIL if args.action == "rehearse" and not result["ready"] else EX_OK
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise Bail(str(exc), EX_FAIL, "see docs/RELEASES.md; no publication was attempted")


SPEC = {
    "verb": "release", "order": 91, "group": "meta",
    "help": "plan images and validate release and hardware evidence locally",
    "description": "No device or external network access. Rehearsal may serve supplied assets on loopback.\n"
                   "Writes only --output when supplied. Never publishes or signs.\n"
                   "Verification checks artifact hashes; hardware claims require a separate report.",
    "args": [
        (["action"], {"choices": ["plan", "inventory", "catalog", "verify", "report", "rehearse"]}),
        (["--org"], {"help": "directory containing local organization repository checkouts"}),
        (["--pmaports"], {"help": "package checkout used for planning and inventory"}),
        (["--mirror"], {"help": "local release asset tree to serve and test over HTTP"}),
        (["--catalog"], {"help": "directory of reviewed manifests/reports"}),
        (["--manifest"], {"help": "release manifest JSON"}),
        (["--artifacts"], {"help": "directory containing the manifest's files"}),
        (["--report"], {"help": "reviewed hardware report JSON"}),
        (["--output"], {"help": "JSON file, or a Markdown directory for catalog"}),
        (["--json"], {"action": "store_true", "help": "machine-readable result"}),
    ],
    "run": dispatch,
    "examples": ["porthole release plan --json", "porthole release catalog --output site-src/devices"],
}
