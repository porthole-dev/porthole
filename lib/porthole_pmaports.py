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


def find_pmaports(cfg=None) -> pathlib.Path | None:
    """Locate a pmaports checkout, preferring what pmbootstrap is configured to use."""
    candidates = []
    if cfg:
        if cfg.get("PORTHOLE_PMAPORTS"):
            candidates.append(pathlib.Path(cfg["PORTHOLE_PMAPORTS"]))
        pmb = cfg.get("PORTHOLE_PMB_DIR")
        if pmb:
            candidates.append(pathlib.Path(pmb) / "cache_git" / "pmaports")
    candidates += [
        pathlib.Path(os.environ.get("PORTHOLE_PMAPORTS", "/nonexistent")),
        pathlib.Path.home() / ".local/var/pmbootstrap/cache_git/pmaports",
        pathlib.Path.home() / ".cache/pmbootstrap/pmaports",
    ]
    for path in candidates:
        if (path / "device").is_dir():
            return path
    return None


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


def load_devices(pmaports: pathlib.Path) -> list[Device]:
    """Every device package in the tree. ~55ms for 664 of them."""
    base = pathlib.Path(pmaports) / "device"
    if not base.is_dir():
        return []
    devices = []
    for info in base.rglob("deviceinfo"):
        parent = info.parent
        if not parent.name.startswith("device-"):
            continue
        devices.append(Device(parent))
    devices.sort(key=lambda d: (d.maturity, d.codename))
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
