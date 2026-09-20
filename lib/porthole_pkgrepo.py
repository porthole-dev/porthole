# SPDX-License-Identifier: MIT
"""A device's prebuilt package repository, as pmbootstrap consumes it.

WHY
    A port that carries forks -- mesa, libcamera, phosh, the device package --
    can publish them once, signed, and let every build machine and every phone
    resolve them instead of rebuilding them. pmbootstrap already knows how:
    `[mirrors] pmaports_custom` and `systemd_custom` put a repository in FRONT
    of postmarketOS's own, and a key in `<work>/config_apk_keys/` is mounted
    into every chroot as /etc/apk/keys. `pmbootstrap install` then writes the
    same mirror into the image's /etc/apk/repositories, so the phone can
    `apk upgrade` from it too.

    Nothing set either of those. A signed repository that no build ever asked
    for is work nobody benefits from, which is how this module came to exist.

THE THREE KEYS (profile level, overridable like any other)
    PORTHOLE_PKG_REPO_URL          -> mirrors.pmaports_custom
    PORTHOLE_PKG_REPO_SYSTEMD_URL  -> mirrors.systemd_custom (empty: none)
    PORTHOLE_PKG_REPO_KEY          the public key; relative to the profile

    `PORTHOLE_PKG_REPO_URL=none` in config.env opts a machine out.

WHY PROBE BEFORE CONFIGURING
    pmbootstrap aborts the whole command -- "getting APKINDEX from binary
    package mirror failed!" -- when any configured mirror answers 404. It asks
    for the index of the HOST arch as well as the target one, the moment it
    initialises the native chroot, even though nothing is ever installed from
    it. A repository that is private (GitHub answers an anonymous download
    with 404, and neither pmbootstrap nor apk can send a token) or that
    publishes aarch64 only therefore turns into every build failing. So the
    workspace configures it only once it has been fetched anonymously, for
    both arches, and its signature checked against the configured key -- and
    says why when it does not.

Stdlib only (lib/ rule): the signature check is a PKCS#1 v1.5 verify done with
pow(), which is ~40 lines and needs no openssl binary -- Fedora's openssl
refuses SHA-1 signatures outright, and apk's are SHA-1.
"""
from __future__ import annotations

import base64
import collections
import configparser
import hashlib
import hmac
import io
import pathlib
import platform
import shutil
import tarfile
import urllib.error
import urllib.request
import zlib

Repo = collections.namedtuple("Repo", "url systemd_url key")

# The two [mirrors] keys porthole owns. Every other mirror setting in a work
# dir's config is somebody else's and is carried over untouched.
OWNED = ("pmaports_custom", "systemd_custom")

# DigestInfo prefixes (RFC 8017 section 9.2, note 1) for the digests apk signs
# with. `.SIGN.RSA.` is SHA-1, `.SIGN.RSA256.` SHA-256, `.SIGN.RSA512.` SHA-512.
_DIGEST_INFO = {
    "sha1": bytes.fromhex("3021300906052b0e03021a05000414"),
    "sha256": bytes.fromhex("3031300d060960864801650304020105000420"),
    "sha512": bytes.fromhex("3051300d060960864801650304020305000440"),
}
_SIGN_PREFIX = ((".SIGN.RSA512.", "sha512"), (".SIGN.RSA256.", "sha256"),
                (".SIGN.RSA.", "sha1"))

# platform.machine() spellings -> Alpine arch names.
_HOST_ARCH = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64",
              "arm64": "aarch64", "armv7l": "armv7", "riscv64": "riscv64"}


def resolve(cfg, root) -> Repo | None:
    """The configured repository, or None when there is none."""
    url = (cfg.get("PORTHOLE_PKG_REPO_URL") or "").strip().rstrip("/")
    if not url or url.lower() == "none":
        return None
    systemd = (cfg.get("PORTHOLE_PKG_REPO_SYSTEMD_URL") or "").strip().rstrip("/")
    key = (cfg.get("PORTHOLE_PKG_REPO_KEY") or "").strip()
    path = None
    if key:
        path = pathlib.Path(key).expanduser()
        if not path.is_absolute():
            path = (pathlib.Path(root) / "profiles"
                    / cfg.get("PORTHOLE_DEVICE", "") / path)
    return Repo(url, systemd if systemd.lower() != "none" else "", path)


def host_arch() -> str:
    machine = platform.machine()
    return _HOST_ARCH.get(machine.lower(), machine)


def branch(pmaports) -> str:
    """The mirror directory pmbootstrap will append: the channel's pmaports
    branch, read the way pmbootstrap reads it (pmaports.cfg names the channel,
    channels.cfg maps it). "" when there is no checkout to ask."""
    if not pmaports:
        return ""
    import porthole_cmd_channel as channel
    import porthole_pmaports as pmap

    try:
        name = channel.channel_of_cfg(
            (pathlib.Path(pmaports) / "pmaports.cfg").read_text())
    except OSError:
        return ""
    return pmap.channels(pmaports).get(name, {}).get("branch_pmaports", "")


# ----------------------------------------------------------- work dir config --

def read_mirrors(cfg_file) -> dict:
    """The [mirrors] section of a pmbootstrap config, {} if it has none."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(pathlib.Path(cfg_file).read_text())
    except (OSError, configparser.Error):
        return {}
    return dict(parser["mirrors"]) if parser.has_section("mirrors") else {}


def wanted(repo: Repo) -> dict:
    out = {"pmaports_custom": repo.url}
    if repo.systemd_url:
        out["systemd_custom"] = repo.systemd_url
    return out


def merge_mirrors(existing: dict, repo: Repo | None,
                  usable: bool) -> tuple[dict, list]:
    """(the [mirrors] to write, what changed as sentences). Pure.

    Never silent. Keys porthole does not own are carried as they are. For the
    two it does: a usable repository is written, and a different value it
    displaces is named. An UNUSABLE one is not written -- and if it was
    written before (the value is exactly this repository's URL, so it is
    porthole's own), it is removed, because leaving it is what makes every
    pmbootstrap command abort. A value that is not this repository's is left
    alone and named, since only its owner knows why it is there.
    """
    rows = dict(existing)
    notes = []
    want = wanted(repo) if repo else {}
    for key in OWNED:
        have = (existing.get(key) or "").strip()
        new = want.get(key, "")
        if repo and usable and new:
            if have and have.lower() != "none" and have != new:
                notes.append(f"mirrors.{key}: replaced {have} with {new}")
            elif have != new:
                notes.append(f"mirrors.{key} = {new}")
            rows[key] = new
        elif new and have == new:
            del rows[key]
            notes.append(f"mirrors.{key}: removed {have} -- not usable now")
        elif have and have.lower() != "none":
            notes.append(f"mirrors.{key} = {have} kept: it is not this "
                         f"device's repository, and not porthole's to remove")
    return rows, notes


def install_key(key: pathlib.Path, work: pathlib.Path) -> str:
    """Put the key where pmbootstrap mounts keys from. Idempotent.

    Returns "installed", "replaced" or "unchanged". The file NAME matters as
    much as the content: apk looks a signature's key up by the name recorded
    in the index (`.SIGN.RSA.<name>`), so it is copied under its own name.
    """
    target = pathlib.Path(work) / "config_apk_keys" / key.name
    data = key.read_bytes()
    if target.is_file() and target.read_bytes() == data:
        return "unchanged"
    verdict = "replaced" if target.exists() else "installed"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(key, target)
    return verdict


# ---------------------------------------------------------------- signatures --

def _tlv(buf: bytes, i: int):
    tag, size = buf[i], buf[i + 1]
    i += 2
    if size & 0x80:
        count = size & 0x7F
        size = int.from_bytes(buf[i:i + count], "big")
        i += count
    return tag, i, i + size


def _rsa_numbers(buf: bytes):
    """(n, e) from the first SEQUENCE of exactly two INTEGERs in a DER blob:
    that is RSAPublicKey on its own (PKCS#1) and inside SubjectPublicKeyInfo's
    BIT STRING (what `openssl rsa -pubout` writes and abuild ships)."""
    i = 0
    while i < len(buf):
        tag, start, end = _tlv(buf, i)
        body = buf[start:end]
        if tag == 0x30:
            t1, s1, e1 = _tlv(body, 0)
            if t1 == 0x02 and e1 < len(body):
                t2, s2, e2 = _tlv(body, e1)
                if t2 == 0x02 and e2 == len(body):
                    return (int.from_bytes(body[s1:e1], "big"),
                            int.from_bytes(body[s2:e2], "big"))
            found = _rsa_numbers(body)
            if found:
                return found
        elif tag == 0x03:
            found = _rsa_numbers(body[1:])   # skip the unused-bits byte
            if found:
                return found
        i = end
    return None


def public_key(pem: str):
    """(n, e) from a PEM RSA public key, or None if it is not one."""
    lines = [l.strip() for l in pem.splitlines()
             if l.strip() and not l.startswith("-----")]
    try:
        return _rsa_numbers(base64.b64decode("".join(lines), validate=True))
    except (ValueError, IndexError):
        return None


def rsa_verify(numbers, digest: str, signature: bytes, data: bytes) -> bool:
    """PKCS#1 v1.5 signature verification."""
    n, e = numbers
    size = (n.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    em = pow(int.from_bytes(signature, "big"), e, n).to_bytes(size, "big")
    t = _DIGEST_INFO[digest] + hashlib.new(digest, data).digest()
    if size < len(t) + 11:
        return False
    expected = b"\x00\x01" + b"\xff" * (size - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(em, expected)


def signature(index: bytes):
    """(key name, digest, signature, signed bytes) of an apk index. Raises
    ValueError when it is not a signed index.

    A signed APKINDEX.tar.gz is two gzip members glued together: the first
    holds one tar entry `.SIGN.RSA.<keyname>`, the second is the index as
    `apk index` wrote it -- and the signature is over those second-member
    bytes exactly as they sit on disk, compressed.
    """
    inflater = zlib.decompressobj(31)
    try:
        first = inflater.decompress(index)
    except zlib.error as err:
        raise ValueError(f"not gzip: {err}") from None
    if not inflater.eof or not inflater.unused_data:
        raise ValueError("one gzip stream only -- the index is unsigned")
    try:
        with tarfile.open(fileobj=io.BytesIO(first)) as tar:
            member = tar.next()
            blob = tar.extractfile(member).read() if member else b""
    except (tarfile.TarError, AttributeError) as err:
        raise ValueError(f"no signature entry: {err}") from None
    for prefix, digest in _SIGN_PREFIX:
        if member.name.startswith(prefix):
            return (member.name[len(prefix):], digest, blob,
                    inflater.unused_data)
    raise ValueError(f"first entry is {member.name!r}, not a signature")


# -------------------------------------------------------------------- probing --

def fetch(url: str, timeout: float = 10.0):
    """(HTTP status or None, body or None, error). Anonymous, exactly like
    pmbootstrap and apk: no token, no cookie, nothing a CI secret could add."""
    request = urllib.request.Request(url, headers={"User-Agent": "porthole"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read(), ""
    except urllib.error.HTTPError as err:
        return err.code, None, str(err)
    except (urllib.error.URLError, OSError, ValueError) as err:
        return None, None, str(getattr(err, "reason", err))


def index_urls(repo: Repo, mirrordir: str, arches) -> list:
    """[(repo label, arch, url)] -- the same URLs pmbootstrap's
    pmb/helpers/repo.py builds: `<mirror>/<mirrordir>/<arch>/APKINDEX.tar.gz`."""
    out = []
    for label, base in (("pmaports", repo.url), ("systemd", repo.systemd_url)):
        if not base:
            continue
        for arch in arches:
            out.append((label, arch,
                        f"{base}/{mirrordir}/{arch}/APKINDEX.tar.gz"))
    return out


def classify(results, key_name: str, key_pem: str, target_arch: str,
             host: str) -> tuple:
    """(verdict, detail) for fetched indexes. Pure: every case a test can reach.

    `results` is [(label, arch, url, status, body, error)]. Verdicts:
    ok, unreachable, private, missing, host-arch-missing, unsigned,
    key-mismatch.
    """
    down = [r for r in results if r[3] is None]
    if down:
        return "unreachable", f"{down[0][2]}: {down[0][5]}"
    absent = [r for r in results if r[3] != 200]
    if absent and len(absent) == len(results):
        return "private", (
            f"every index answers {absent[0][3]} to an anonymous download "
            f"(e.g. {absent[0][2]}) -- the repository is private or the URL "
            f"is wrong. GitHub serves 404, not 403, for a private repository, "
            f"and neither pmbootstrap nor apk can send a token")
    target_gone = [r for r in absent if r[1] == target_arch]
    if target_gone:
        return "missing", f"no {target_arch} index: {target_gone[0][2]} " \
                          f"answers {target_gone[0][3]}"
    if absent:
        return "host-arch-missing", (
            f"no {host} index at {absent[0][2]} ({absent[0][3]}). pmbootstrap "
            f"fetches every mirror's host-arch index when it sets up the "
            f"native chroot and aborts on a 404, although it installs nothing "
            f"from it -- the repository must publish a signed index for "
            f"{host}, even an empty one")
    numbers = public_key(key_pem)
    if not numbers:
        return "key-mismatch", "the configured key is not an RSA public key"
    for _label, _arch, url, _status, body, _err in results:
        try:
            name, digest, sig, signed = signature(body)
        except ValueError as err:
            return "unsigned", f"{url}: {err}"
        if name != key_name:
            return "key-mismatch", (
                f"{url} is signed by {name}, the configured key is "
                f"{key_name} -- apk finds keys by that name and would call "
                f"the index UNTRUSTED")
        if not rsa_verify(numbers, digest, sig, signed):
            return "key-mismatch", (
                f"{url} names {name} but its signature does not verify with "
                f"the configured {key_name} -- a different key with the same "
                f"name, or a corrupted index")
    arches = sorted({r[1] for r in results})
    return "ok", f"{len(results)} indexes ({', '.join(arches)}) signed by {key_name}"


def probe(repo: Repo, mirrordir: str, target_arch: str,
          host: str | None = None, fetcher=None) -> tuple:
    """Fetch and classify, the way pmbootstrap will meet the repository."""
    fetcher = fetcher or fetch
    if not repo.key:
        return "key-mismatch", "PORTHOLE_PKG_REPO_KEY is not set"
    try:
        pem = repo.key.read_text()
    except (OSError, UnicodeError) as err:
        return "key-mismatch", f"cannot read the key {repo.key}: {err}"
    if not mirrordir:
        return "unreachable", ("no pmaports checkout to read the channel's "
                               "branch from, so the index URLs are unknown")
    host = host or host_arch()
    arches = [target_arch] + ([host] if host != target_arch else [])
    results = []
    for label, arch, url in index_urls(repo, mirrordir, arches):
        status, body, error = fetcher(url)
        results.append((label, arch, url, status, body, error))
    return classify(results, repo.key.name, pem, target_arch, host)


# -- does our repo actually publish the channel you are switching to? -------
#
# The mirror URL pmbootstrap builds ends in the pmaports BRANCH, not a channel
# name: `<PORTHOLE_PKG_REPO_URL>/<branch>/<arch>/APKINDEX.tar.gz`. edge's
# branch is `main`, and every release channel's is its own version -- v26.06,
# v25.12 and so on (pmaports/channels.cfg, `branch_pmaports`).
#
# Our package CI publishes one GitHub release per <branch>/<arch>, and as of
# 2026-09-20 that is exactly four: main/aarch64, main/x86_64,
# systemd/main/aarch64 and systemd/main/x86_64. Measured:
#
#     main     -> HTTP 200
#     v26.06   -> HTTP 404
#
# So switching to any stable channel points apk at a 404 for our repo. Every
# fork this port carries -- mesa, phoc, libcamera, the device package itself
# -- silently stops being available, and what installs instead is whatever
# stock provides. That is the same class of failure as the mesa drift, with a
# wider blast radius, and nothing anywhere says it before the switch.
CHANNEL_BRANCH_DEFAULT = "main"


def channel_branch(channel: str, channels: dict | None = None) -> str:
    """The pmaports branch a channel resolves to. Pure.

    Falls back to the channel name, which is what pmbootstrap does for a
    channel whose stanza has no branch_pmaports.
    """
    info = (channels or {}).get(channel) or {}
    return (info.get("branch_pmaports") or channel
            or CHANNEL_BRANCH_DEFAULT).strip()


def index_url(base: str, branch: str, arch: str) -> str:
    """The APKINDEX our CI would have published for this branch and arch."""
    return "{}/{}/{}/APKINDEX.tar.gz".format(
        (base or "").rstrip("/"), branch, arch)


def probe_index(url: str, timeout: float = 10.0) -> int:
    """HTTP status for an APKINDEX, or 0 when the request could not be made.

    0 is NOT a failure verdict: an offline host must not be told its channel
    is unpublished. Empty must mean unknown -- brain/laws.
    """
    import urllib.error
    import urllib.request

    try:
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return int(getattr(answer, "status", 0) or 0)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, OSError, ValueError):
        return 0


def channel_supported(status: int):
    """`(state, why)` for one probe result. Pure."""
    if status == 0:
        return "unknown", ("could not reach the package repository, so "
                           "whether it publishes this channel is unknown")
    if 200 <= status < 400:
        return "published", "the package repository publishes this channel"
    if status == 404:
        return "absent", (
            "the package repository does NOT publish this channel. Every fork "
            "this port carries would fall back to stock, silently, and the "
            "patches in them would stop being installed")
    return "unknown", f"the package repository answered HTTP {status}"
