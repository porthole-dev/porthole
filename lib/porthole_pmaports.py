# SPDX-License-Identifier: MIT
"""Read pmaports: the device index, SoC families, channels and UIs.

pmaports is the authoritative description of every device postmarketOS
supports, and it is sitting on disk already. Nobody porting a new device should
be guessing at values that a sibling device with the same SoC has had committed
and working for years.

Parsing all 664 device directories costs ~55ms, so there is no cache here and
no staleness bug to go with it.

Nothing in this module writes. Mutating pmaports is `porthole aports`' job, and
keeping the read path side-effect free means `porthole soc` is always safe to
run against a tree someone else is mid-edit on.
"""
from __future__ import annotations

import json
import os
import pathlib
import re

# `soc-*` packages that group devices by silicon. Some soc-* packages are
# feature bundles rather than SoC families -- soc-qcom-modem is a modem stack
# shared across many Qualcomm SoCs, not a SoC -- and treating those as families
# produces nonsense siblings.
NOT_A_SOC_FAMILY = {
    "soc-qcom", "soc-qcom-modem", "soc-qcom-qbootctl", "soc-sprd-audio",
    "soc-qcom-tools",
}

SOC_RE = re.compile(r"\bsoc-([a-z0-9]+)-([a-z0-9_x]+)\b")
DEVICEINFO_RE = re.compile(r'^\s*deviceinfo_([a-z0-9_]+)\s*=\s*"?([^"#\n]*)"?',
                           re.M)

# Where pmaports keeps devices, best-supported first. The category is a real
# signal: a `main` device has maintainers and CI, a `testing` one may be one
# person's weekend, and `archived` means nobody is looking after it.
CATEGORIES = ("main", "community", "testing", "downstream", "archived")


def find_pmaports_with_source(cfg=None):
    """Locate a pmaports checkout AND say which candidate answered.

    The label is not decoration. Four things can decide where pmaports is, and
    which one won is precisely the question a developer cannot answer by
    reading the documentation -- so the resolution order and the explanation of
    it have to be the same code. `porthole doctor` printing a path next to a
    key derived separately was wrong within an hour of being written.
    """
    candidates = []
    if cfg:
        # A per-device override is collapsed into PORTHOLE_PMAPORTS by the
        # config layer before anything here sees it, so name it when it is what
        # produced the value -- "via PORTHOLE_PMAPORTS" is true but unhelpful
        # to someone looking for the line to edit.
        device = cfg.get("PORTHOLE_DEVICE", "")
        per_device = (f"PORTHOLE_PMAPORTS_{device.upper().replace('-', '_')}"
                      if device else "")
        if cfg.get("PORTHOLE_PMAPORTS"):
            label = ("PORTHOLE_PMAPORTS"
                     if not (per_device and cfg.get(per_device)
                             == cfg["PORTHOLE_PMAPORTS"]) else per_device)
            candidates.append((label, pathlib.Path(cfg["PORTHOLE_PMAPORTS"])))
        if per_device and cfg.get(per_device):
            candidates.append((per_device, pathlib.Path(cfg[per_device])))
        pmb = cfg.get("PORTHOLE_PMB_DIR")
        if pmb:
            candidates.append(("PORTHOLE_PMB_DIR/cache_git",
                               pathlib.Path(pmb) / "cache_git" / "pmaports"))
    candidates += [
        ("PORTHOLE_PMAPORTS in the environment",
         pathlib.Path(os.environ.get("PORTHOLE_PMAPORTS", "/nonexistent"))),
        ("the default pmbootstrap work dir",
         pathlib.Path.home() / ".local/var/pmbootstrap/cache_git/pmaports"),
        ("the legacy cache path",
         pathlib.Path.home() / ".cache/pmbootstrap/pmaports"),
    ]
    for label, path in candidates:
        if (path / "device").is_dir():
            return path, label
    return None, ""


def find_pmaports(cfg=None) -> pathlib.Path | None:
    """Locate a pmaports checkout, preferring what pmbootstrap is configured to use."""
    return find_pmaports_with_source(cfg)[0]


# The URLs pmbootstrap's `get_upstream_remote()` will accept, copied from
# pmb/config/__init__.py's `git_repos["pmaports"]`. It matches with
# `url in clean_url`, so a substring is the right shape here too.
#
# WHY THIS IS DUPLICATED. pmbootstrap raises rather than warns when no remote
# matches, and it raises from inside `parse_channels_cfg` -- which every
# `pmbootstrap install`, `build` and `status` reaches. Knowing the answer
# BEFORE a twenty-minute build starts is the whole value, and importing pmb
# from here is not available: the CLI is stdlib-only by rule, and on the
# workspace tier pmbootstrap is not installed on this host at all.
PMAPORTS_UPSTREAM_URLS = (
    "https://gitlab.postmarketos.org/postmarketOS/pmaports.git",
    "git@gitlab.postmarketos.org:postmarketOS/pmaports.git",
)


def _remote_lines(path, runner=None) -> list:
    if runner is not None:
        return runner(path)
    import subprocess
    try:
        proc = subprocess.run(["git", "remote", "-v"], cwd=str(path),
                              capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    return proc.stdout.splitlines() if proc.returncode == 0 else []


def upstream_remote(path, runner=None) -> str:
    """The remote name pmbootstrap will accept for this checkout, or "".

    Mirrors `remote_to_name_and_clean_url` + `get_upstream_remote`, including
    the https authentication-segment strip GitLab CI needs. `runner` is a seam
    so the matching can be tested without a git repository.
    """
    for line in _remote_lines(path, runner):
        name, _, url = line.partition("\t")
        url = url.split(" ", 1)[0].strip()          # drop the " (fetch)" tail
        if url.startswith("https://"):
            from urllib.parse import urlparse
            parsed = urlparse(url)
            url = f"{parsed.scheme}://{parsed.hostname}{parsed.path}"
        if any(u.lower() in url.lower() for u in PMAPORTS_UPSTREAM_URLS):
            return name.strip()
    return ""


def channels_cfg_override(path, runner=None) -> str:
    """The `channels.cfg` to hand pmbootstrap, or "" when it can find its own.

    THE FORK PROBLEM, and it is the normal state for this tool rather than an
    edge case. pmbootstrap reads channels.cfg from `<upstream>/main` -- not
    from the working tree -- and finds `<upstream>` by matching a remote URL
    against two hardcoded postmarketOS ones. A bring-up fork on GitHub matches
    neither, so `pmbootstrap install` dies with

        pmaports: could not find remote name for any URL '[...]' in git
        repository: /pmb/cache_git/pmaports

    which names no fix, no verb, and not the actual subject -- channels.cfg
    was never mentioned. The data was all present: `origin/main:channels.cfg`
    existed and was readable. pmbootstrap simply refused to name the remote.

    `PMB_CHANNELS_CFG` is pmbootstrap's own documented override for this, so
    the fix is neither a patch nor a write to the developer's repository: point
    it at the checkout's own file. That is also the more honest source -- the
    tree being built is the tree whose channel definitions apply.

    Returned only when the checkout CANNOT satisfy pmbootstrap's own path, so a
    stock clone keeps upstream's behaviour exactly, including its reason for
    preferring main's copy over an old release branch's.
    """
    path = pathlib.Path(path)
    if upstream_remote(path, runner):
        return ""
    cfg = path / "channels.cfg"
    return str(cfg) if cfg.is_file() else ""


def _quoted_body(text: str, var: str) -> str:
    """The body of a `var=...` assignment, taken to its closing quote.

    `subpackages` is normally several lines, so reading only the `=` line sees
    the first entry and nothing else.
    """
    match = re.search(rf'(?m)^[ \t]*{re.escape(var)}=(.*)$', text)
    if not match:
        return ""
    rest = match.group(1)
    if rest[:1] in ('"', "'"):
        end = text.find(rest[0], match.start(1) + 1)
        return text[match.start(1) + 1:end] if end != -1 else rest
    return rest.strip("\"'")


def device_kernels(pmaports, codename: str) -> dict:
    """The kernel flavours `device-<codename>` offers: {name: description}.

    Mirrors pmbootstrap's `pmb.parse._apkbuild.kernels()` exactly -- the
    subpackages named `device-<codename>-kernel-<flavour>`, with any `:func`
    suffix stripped and `$pkgname` expanded, which is how every device package
    in pmaports actually writes them.

    WHY PORTHOLE HAS TO KNOW THIS. pmbootstrap's `kernel` setting defaults to
    `stable`, and `pmbootstrap init` is the only thing that ever sets it -- an
    interactive command the workspace tier exists to avoid needing. A device
    that offers only `mainline`, which is most bring-ups, therefore fails
    `pmbootstrap install` at the point where it has already created the rootfs
    chroot:

        ERROR: Selected kernel (stable) is not valid for device
        google-taimen. Please run 'pmbootstrap init' to select a valid kernel.

    -- advice that cannot be followed in a workspace, about a value pmaports
    already answers. Descriptions are returned as well as names because a
    device with two flavours is a question for a human, and the description is
    what makes it answerable.
    """
    if not codename:
        return {}
    # `device/<category>/device-<codename>`, the layout load_devices walks --
    # NOT `<category>/device-<codename>`, which is where the aport for a
    # non-device package lives and where this looked first time round.
    base = pathlib.Path(pmaports) / "device"
    for category in CATEGORIES:
        path = base / category / f"device-{codename}" / "APKBUILD"
        if path.is_file():
            break
    else:
        return {}
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {}
    pkgname = ""
    name_match = re.search(r'(?m)^[ \t]*pkgname=(.*)$', text)
    if name_match:
        pkgname = name_match.group(1).strip().strip("\"'")
    prefix = f"device-{codename}-kernel-"
    out = {}
    for token in _quoted_body(text, "subpackages").split():
        name = token.split(":", 1)[0]
        if pkgname:
            name = name.replace("${pkgname}", pkgname).replace("$pkgname",
                                                               pkgname)
        if name.startswith(prefix):
            flavour = name[len(prefix):]
            # The description lives in the subpackage function. Best effort --
            # a missing one must not hide a flavour that exists.
            func = token.split(":", 1)[1] if ":" in token else flavour
            desc = re.search(rf'(?m)^{re.escape(func)}\(\)[^\n]*\n(?:.*\n)*?'
                             rf'[ \t]*pkgdesc="([^"]*)"', text)
            out[flavour] = desc.group(1) if desc else ""
    return out


def find_aports_upstream(pmaports) -> pathlib.Path | None:
    """Alpine's aports checkout, which pmbootstrap clones beside pmaports.

    Not a config key of its own: pmbootstrap puts both trees in the same
    cache_git/ directory and nothing downstream would work if they were
    apart, so a knob here would only be a second place to be wrong.

    Worth having at all because the two trees are not interchangeable.
    `pmbootstrap build` reads pmaports and nothing else, so Alpine's twelve
    thousand packages are present, useful, and unbuildable until
    `aportgen --fork-alpine` copies one across. Which tree a name is in IS
    the answer to "why did my build say the package does not exist".
    """
    path = pathlib.Path(pmaports).parent / "aports_upstream"
    return path if (path / "main").is_dir() else None


class Device:
    """One device package, as pmaports describes it."""

    __slots__ = ("path", "codename", "category", "info", "soc", "depends")

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.codename = path.name.replace("device-", "", 1)
        self.category = path.parent.name
        self.info: dict[str, str] = {}
        self.soc = ""
        self.depends: list[str] = []
        self._parse()

    def to_cache(self) -> dict:
        return {"info": self.info, "soc": self.soc, "depends": self.depends}

    @classmethod
    def from_cache(cls, path, row) -> "Device":
        self = cls.__new__(cls)
        self.path = path
        self.codename = path.name.replace("device-", "", 1)
        self.category = path.parent.name
        self.info = row["info"]
        self.soc = row["soc"]
        self.depends = row["depends"]
        return self

    def _parse(self) -> None:
        di = self.path / "deviceinfo"
        if di.is_file():
            try:
                for key, value in DEVICEINFO_RE.findall(
                        di.read_text(errors="replace")):
                    self.info[key] = value.strip()
            except OSError:
                pass
        ak = self.path / "APKBUILD"
        if ak.is_file():
            try:
                text = ak.read_text(errors="replace")
            except OSError:
                text = ""
            # Only the depends= block: a soc-* mentioned in a comment is
            # commentary, not a dependency, and taimen's APKBUILD has several.
            block = re.search(r"^depends=\"(.*?)\"", text, re.S | re.M)
            if block:
                self.depends = block.group(1).split()
                for dep in self.depends:
                    m = SOC_RE.fullmatch(dep)
                    if m and dep not in NOT_A_SOC_FAMILY:
                        self.soc = f"{m.group(1)}-{m.group(2)}"
                        break
        if not self.soc:
            self.soc = self._soc_from_dtb()

    def _soc_from_dtb(self) -> str:
        """Fall back to the DTB path: `qcom/msm8998-google-taimen`.

        Less reliable than the depends= line -- a board file can be named for
        the board rather than the SoC -- so it is only used when there is no
        soc-* dependency to read.
        """
        dtb = self.info.get("dtb", "")
        if "/" not in dtb:
            return ""
        vendor, _, leaf = dtb.partition("/")
        leaf = leaf.split()[0] if leaf else ""
        m = re.match(r"([a-z]+[0-9]+[a-z0-9]*)-", leaf)
        return f"{vendor}-{m.group(1)}" if m else ""

    # -- convenience --

    @property
    def name(self) -> str:
        return self.info.get("name", self.codename)

    @property
    def vendor(self) -> str:
        return self.info.get("manufacturer", "")

    @property
    def arch(self) -> str:
        return self.info.get("arch", "")

    @property
    def year(self) -> str:
        return self.info.get("year", "")

    @property
    def chassis(self) -> str:
        return self.info.get("chassis", "")

    @property
    def maturity(self) -> int:
        """Lower is better supported. Used to rank which sibling to copy."""
        try:
            return CATEGORIES.index(self.category)
        except ValueError:
            return len(CATEGORIES)

    def as_dict(self) -> dict:
        return {
            "codename": self.codename, "name": self.name,
            "vendor": self.vendor, "soc": self.soc, "arch": self.arch,
            "year": self.year, "chassis": self.chassis,
            "category": self.category, "path": str(self.path),
            "dtb": self.info.get("dtb", ""),
            "deviceinfo_keys": len(self.info),
        }


def _index_path(base: pathlib.Path) -> pathlib.Path:
    import hashlib
    root = os.environ.get("XDG_CACHE_HOME") or (pathlib.Path.home() / ".cache")
    tag = hashlib.sha256(str(base).encode()).hexdigest()[:12]
    return pathlib.Path(root) / "porthole" / f"pmaports-{tag}.json"


def _index_key(base: pathlib.Path) -> str:
    """Fingerprint of the category directories.

    One stat per category (five of them) rather than per device (664). A
    package added, removed or renamed changes its category's mtime, which is
    what has to invalidate the index. An edit INSIDE an existing package does
    not -- and does not need to, because nothing cached here comes from a file
    body except deviceinfo, which is re-read on demand by the callers that
    care.
    """
    parts = []
    for name in CATEGORIES:
        d = base / name
        try:
            parts.append(f"{name}:{d.stat().st_mtime_ns}")
        except OSError:
            parts.append(f"{name}:-")
    return "|".join(parts)


def load_devices(pmaports: pathlib.Path) -> list[Device]:
    """Every device package in the tree.

    Walking 664 package directories costs ~245ms, and it was paid on every
    `porthole next` and every `porthole soc` -- to answer a question whose
    answer changes when someone adds a package, which is roughly never during a
    working session.

    So the result is cached, keyed by the category directories' mtimes. A stale
    cache must never change an answer, so any error at all falls through to the
    full walk.
    """
    base = pathlib.Path(pmaports) / "device"
    if not base.is_dir():
        return []

    key = _index_key(base)
    path = _index_path(base)
    try:
        blob = json.loads(path.read_text())
        if blob.get("key") == key:
            return [Device.from_cache(base / rel, row)
                    for rel, row in blob["devices"]]
    except Exception:  # noqa: BLE001 -- a bad cache is not an error, just slow
        pass

    devices = []
    for info in base.rglob("deviceinfo"):
        parent = info.parent
        if not parent.name.startswith("device-"):
            continue
        devices.append(Device(parent))
    devices.sort(key=lambda d: (d.maturity, d.codename))

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "key": key, "base": str(base),
            "devices": [[str(d.path.relative_to(base)), d.to_cache()]
                        for d in devices],
        }))
        # Drop indexes for checkouts that no longer exist. The hash is over the
        # PATH, so a moved or removed pmaports leaves its index behind forever
        # -- 13 orphans of up to 488 KB had accumulated before anyone looked.
        for stale in path.parent.glob("pmaports-*.json"):
            if stale == path:
                continue
            try:
                blob = json.loads(stale.read_text())
                if not pathlib.Path(blob.get("base", "/nonexistent")).is_dir():
                    stale.unlink()
            except Exception:  # noqa: BLE001 -- unreadable is also stale
                stale.unlink(missing_ok=True)
    except OSError:
        pass
    return devices


def soc_families(devices: list[Device]) -> dict[str, list[Device]]:
    out: dict[str, list[Device]] = {}
    for dev in devices:
        if dev.soc:
            out.setdefault(dev.soc, []).append(dev)
    return out


def siblings(devices: list[Device], soc: str, exclude: str = "") -> list[Device]:
    """Devices on the same SoC, best-supported first.

    The ordering is the whole point: when seeding a new port you want the most
    mature sibling's answers, not an arbitrary one's.
    """
    return [d for d in devices
            if d.soc == soc and d.codename != exclude]


# ------------------------------------------------------------------ channels --

def channels(pmaports: pathlib.Path) -> dict[str, dict]:
    """Parse channels.cfg -- the release channels pmOS offers."""
    path = pathlib.Path(pmaports) / "channels.cfg"
    out: dict[str, dict] = {}
    if not path.is_file():
        return out
    section = None
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            if section != "channels.cfg":
                out[section] = {}
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if section == "channels.cfg":
            out.setdefault("_meta", {})[key] = value
        elif section:
            out[section][key] = value
    return out


def user_interfaces(pmaports: pathlib.Path) -> list[dict]:
    """The postmarketos-ui-* packages: every compositor/desktop on offer."""
    base = pathlib.Path(pmaports) / "main"
    if not base.is_dir():
        return []
    out = []
    for path in sorted(base.glob("postmarketos-ui-*")):
        if not path.is_dir():
            continue
        name = path.name.replace("postmarketos-ui-", "", 1)
        desc, arch = "", ""
        ak = path / "APKBUILD"
        if ak.is_file():
            try:
                text = ak.read_text(errors="replace")
            except OSError:
                text = ""
            m = re.search(r'^pkgdesc="([^"]*)"', text, re.M)
            desc = m.group(1) if m else ""
            m = re.search(r'^arch="([^"]*)"', text, re.M)
            arch = m.group(1) if m else ""
        out.append({"name": name, "package": path.name,
                    "description": desc, "arch": arch, "path": str(path)})
    return out
