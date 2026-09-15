#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""A device's prebuilt package repository: probe, signature, config, doctor.

No network. The fetch tests serve fixture trees from a loopback HTTP server,
so the real urllib path runs -- a fake fetcher alone would pass while the one
pmbootstrap-shaped URL builder in the module was wrong.

The signed index fixture is the empty x86_64 index the package CI published,
and the key is the one the google-taimen profile vendors: the positive control
is a real signature by the real key, verified with no openssl.
"""
import functools
import gzip
import http.server
import io
import pathlib
import shutil
import sys
import tarfile
import tempfile
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402
sys.path.insert(0, str(ROOT / "lib"))

import porthole_pkgrepo as pkgrepo  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "pkgrepo"
INDEX = (FIX / "APKINDEX.empty.tar.gz").read_bytes()
KEY = ROOT / "profiles" / "google-taimen" / "keys" / "porthole-dev-packages-20260915.rsa.pub"
URL = "https://example.invalid/releases/download"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _serve(tree: pathlib.Path):
    handler = functools.partial(_Quiet, directory=str(tree))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _tree(arches, systemd_arches=None, index=INDEX) -> pathlib.Path:
    """A release layout: <base>/main/<arch>/ and <base>/systemd/main/<arch>/."""
    top = pathlib.Path(tempfile.mkdtemp())
    for arch in arches:
        (top / "main" / arch).mkdir(parents=True)
        (top / "main" / arch / "APKINDEX.tar.gz").write_bytes(index)
    for arch in systemd_arches or []:
        (top / "systemd" / "main" / arch).mkdir(parents=True)
        (top / "systemd" / "main" / arch / "APKINDEX.tar.gz").write_bytes(index)
    return top


def _probe_tree(tree, key=KEY, systemd=True):
    server, base = _serve(tree)
    try:
        repo = pkgrepo.Repo(base, f"{base}/systemd" if systemd else "", key)
        return pkgrepo.probe(repo, "main", "aarch64", host="x86_64")
    finally:
        server.shutdown()


def _resign(name: str) -> bytes:
    """The fixture index with its signature entry renamed, bytes untouched."""
    _n, _d, sig, signed = pkgrepo.signature(INDEX)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(".SIGN.RSA." + name)
        info.size = len(sig)
        tar.addfile(info, io.BytesIO(sig))
    return gzip.compress(buf.getvalue()) + signed


# ------------------------------------------------------------------ signature --

def test_the_published_index_verifies_with_the_vendored_key():
    """THE POSITIVE CONTROL for every key-mismatch test below."""
    name, digest, sig, signed = pkgrepo.signature(INDEX)
    assert name == KEY.name, name
    assert digest == "sha1"
    assert pkgrepo.rsa_verify(pkgrepo.public_key(KEY.read_text()),
                              digest, sig, signed)


def test_one_changed_byte_does_not_verify():
    name, digest, sig, signed = pkgrepo.signature(INDEX)
    numbers = pkgrepo.public_key(KEY.read_text())
    assert not pkgrepo.rsa_verify(numbers, digest, sig, signed[:-1] + bytes([signed[-1] ^ 1]))


def test_an_unsigned_index_is_named_as_such():
    _n, _d, _s, signed = pkgrepo.signature(INDEX)
    try:
        pkgrepo.signature(signed)
    except ValueError as err:
        assert "unsigned" in str(err), err
    else:
        raise AssertionError("an index with no signature member was accepted")


def test_the_key_parses_as_rsa():
    n, e = pkgrepo.public_key(KEY.read_text())
    assert n.bit_length() >= 2048 and e == 65537
    assert pkgrepo.public_key("-----BEGIN PUBLIC KEY-----\nnotbase64\n") is None


# --------------------------------------------------------------------- probe --

def test_a_complete_repository_probes_ok_over_http():
    verdict, detail = _probe_tree(_tree(["aarch64", "x86_64"],
                                        ["aarch64", "x86_64"]))
    assert verdict == "ok", detail
    assert "4 indexes" in detail and "x86_64" in detail, detail


def test_a_repository_that_answers_404_everywhere_is_called_private():
    verdict, detail = _probe_tree(_tree([]))
    assert verdict == "private", (verdict, detail)
    assert "404" in detail and "token" in detail, detail


def test_a_missing_host_arch_index_is_its_own_verdict():
    """The trap: aarch64 is all that is ever installed, and still pmbootstrap
    aborts on the x86_64 404 when it sets up the native chroot."""
    verdict, detail = _probe_tree(_tree(["aarch64"], ["aarch64"]))
    assert verdict == "host-arch-missing", (verdict, detail)
    assert "x86_64" in detail and "native chroot" in detail, detail


def test_a_missing_systemd_host_index_is_caught_too():
    verdict, _ = _probe_tree(_tree(["aarch64", "x86_64"], ["aarch64"]))
    assert verdict == "host-arch-missing", verdict


def test_no_systemd_url_means_no_systemd_index_is_asked_for():
    verdict, detail = _probe_tree(_tree(["aarch64", "x86_64"]), systemd=False)
    assert verdict == "ok", detail


def test_a_missing_target_index_is_not_reported_as_a_host_arch_problem():
    verdict, _ = _probe_tree(_tree(["x86_64"], ["aarch64", "x86_64"]))
    assert verdict == "missing", verdict


def test_an_index_signed_under_another_name_is_a_key_mismatch():
    tree = _tree(["aarch64", "x86_64"], index=_resign("someone-else.rsa.pub"))
    verdict, detail = _probe_tree(tree, systemd=False)
    assert verdict == "key-mismatch", (verdict, detail)
    assert "someone-else.rsa.pub" in detail, detail


def test_a_different_key_under_the_same_name_is_a_key_mismatch():
    """apk finds the key by name; a same-named impostor must still fail."""
    key = pathlib.Path(tempfile.mkdtemp()) / KEY.name
    shutil.copyfile(FIX / "other.rsa.pub", key)
    verdict, detail = _probe_tree(_tree(["aarch64", "x86_64"]), key=key,
                                  systemd=False)
    assert verdict == "key-mismatch", (verdict, detail)
    assert "does not verify" in detail, detail


def test_an_unreachable_host_is_not_called_private():
    repo = pkgrepo.Repo(URL, "", KEY)
    verdict, detail = pkgrepo.probe(repo, "main", "aarch64", host="x86_64",
                                    fetcher=lambda url: (None, None, "timed out"))
    assert verdict == "unreachable" and "timed out" in detail, detail


def test_the_urls_are_the_ones_pmbootstrap_builds():
    """pmb/helpers/repo.py: os.path.join(mirror, mirrordir), then
    f"{url}/{arch}/APKINDEX.tar.gz"."""
    repo = pkgrepo.Repo(URL, URL + "/systemd", KEY)
    urls = [u for _l, _a, u in pkgrepo.index_urls(repo, "main", ["aarch64"])]
    assert urls == [URL + "/main/aarch64/APKINDEX.tar.gz",
                    URL + "/systemd/main/aarch64/APKINDEX.tar.gz"], urls


def test_a_missing_key_file_never_reaches_the_network():
    def boom(url):
        raise AssertionError("fetched with no key to check against")
    repo = pkgrepo.Repo(URL, "", pathlib.Path("/nonexistent/key.pub"))
    verdict, _ = pkgrepo.probe(repo, "main", "aarch64", fetcher=boom)
    assert verdict == "key-mismatch"


# ------------------------------------------------------------ config and key --

def test_the_taimen_profile_resolves_to_a_key_that_exists():
    import porthole
    cfg = porthole.load_config(ROOT, env={"PORTHOLE_DEVICE": "google-taimen",
                                          "HOME": tempfile.mkdtemp(),
                                          "XDG_CONFIG_HOME": tempfile.mkdtemp()})
    repo = pkgrepo.resolve(cfg, ROOT)
    assert repo and repo.url.startswith("https://"), repo
    assert repo.systemd_url == repo.url + "/systemd", repo
    assert repo.key == KEY and repo.key.is_file(), repo


def test_none_opts_a_machine_out():
    cfg = {"PORTHOLE_PKG_REPO_URL": "none", "PORTHOLE_PKG_REPO_KEY": "k.pub"}
    assert pkgrepo.resolve(cfg, ROOT) is None
    assert pkgrepo.resolve({}, ROOT) is None


def test_an_absolute_key_is_not_rebased_onto_the_profile():
    cfg = {"PORTHOLE_PKG_REPO_URL": URL + "/", "PORTHOLE_PKG_REPO_KEY": str(KEY),
           "PORTHOLE_DEVICE": "elsewhere"}
    repo = pkgrepo.resolve(cfg, ROOT)
    assert repo.key == KEY and repo.url == URL and repo.systemd_url == ""


def test_usable_writes_both_mirrors_and_keeps_everything_else():
    repo = pkgrepo.Repo(URL, URL + "/systemd", KEY)
    rows, notes = pkgrepo.merge_mirrors({"alpine": "http://mine/alpine"}, repo, True)
    assert rows == {"alpine": "http://mine/alpine", "pmaports_custom": URL,
                    "systemd_custom": URL + "/systemd"}, rows
    assert len(notes) == 2, notes


def test_a_displaced_mirror_is_named_not_silently_replaced():
    repo = pkgrepo.Repo(URL, "", KEY)
    rows, notes = pkgrepo.merge_mirrors({"pmaports_custom": "http://old"}, repo, True)
    assert rows["pmaports_custom"] == URL
    assert any("replaced http://old" in n for n in notes), notes


def test_an_unusable_repository_is_removed_if_porthole_wrote_it():
    """Left in place it aborts every pmbootstrap command."""
    repo = pkgrepo.Repo(URL, "", KEY)
    rows, notes = pkgrepo.merge_mirrors({"pmaports_custom": URL}, repo, False)
    assert "pmaports_custom" not in rows, rows
    assert any("removed" in n for n in notes), notes


def test_a_mirror_that_is_not_ours_is_kept_and_named():
    repo = pkgrepo.Repo(URL, "", KEY)
    for r, usable in ((repo, False), (None, False)):
        rows, notes = pkgrepo.merge_mirrors({"pmaports_custom": "http://mine"},
                                            r, usable)
        assert rows == {"pmaports_custom": "http://mine"}, rows
        assert any("kept" in n for n in notes), notes


def test_nothing_to_say_when_nothing_is_configured():
    assert pkgrepo.merge_mirrors({}, None, False) == ({}, [])


def test_the_config_text_carries_the_mirrors():
    import porthole_cmd_sandbox as sb
    text = sb.pmb_config_text("google-taimen", {}, "", {"pmaports_custom": URL})
    assert text.endswith(f"[mirrors]\npmaports_custom = {URL}\n"), text
    parsed = pathlib.Path(tempfile.mkdtemp()) / "c.cfg"
    parsed.write_text(text)
    assert pkgrepo.read_mirrors(parsed) == {"pmaports_custom": URL}


def test_the_key_is_installed_once_under_its_own_name():
    work = pathlib.Path(tempfile.mkdtemp())
    assert pkgrepo.install_key(KEY, work) == "installed"
    assert (work / "config_apk_keys" / KEY.name).read_bytes() == KEY.read_bytes()
    assert pkgrepo.install_key(KEY, work) == "unchanged"
    (work / "config_apk_keys" / KEY.name).write_text("stale")
    assert pkgrepo.install_key(KEY, work) == "replaced"


def _pmaports() -> pathlib.Path:
    """Just enough checkout for find_pmaports and the channel lookup."""
    pm = pathlib.Path(tempfile.mkdtemp())
    (pm / "device").mkdir()
    (pm / "pmaports.cfg").write_text("[pmaports]\nchannel=edge\n")
    (pm / "channels.cfg").write_text(
        "[channels.cfg]\nrecommended=edge\n\n[edge]\nbranch_pmaports=main\n"
        "\n[v26.06]\nbranch_pmaports=v26.06\n")
    return pm


def test_the_branch_comes_from_the_channel_not_the_checkout():
    assert pkgrepo.branch(_pmaports()) == "main"
    assert pkgrepo.branch(None) == ""


# -------------------------------------------------------------------- doctor --

class _Out:
    def __init__(self):
        self.lines = []

    def __call__(self, *parts):
        self.lines.append(" ".join(str(p) for p in parts))

    def paint(self, text, _colour):
        return text

    def warn(self, text):
        self.lines.append("WARN " + text)

    def hint(self, text, note=""):
        self.lines.append(f"HINT {text} {note}")


class _Ctx:
    def __init__(self, cfg):
        self.cfg, self.root, self.out = cfg, ROOT, _Out()


def _cfg(work, **extra):
    cfg = {"PORTHOLE_DEVICE": "google-taimen", "PORTHOLE_ARCH": "aarch64",
           "PORTHOLE_PKG_REPO_URL": URL, "PORTHOLE_PKG_REPO_KEY": str(KEY),
           "PORTHOLE_SANDBOX_PMB_DIR": str(work),
           "PORTHOLE_PMAPORTS": str(_pmaports())}
    cfg.update(extra)
    return cfg


def _doctor(cfg, fetcher, deep=True):
    import porthole_cmd_doctor as doctor
    ch = doctor.Checks()
    doctor.check_pkg_repo(ch, _Ctx(cfg), cfg, deep=deep, fetcher=fetcher)
    return {r["name"]: r for r in ch.rows}


def _not_found(url):
    return 404, None, "HTTP Error 404: Not Found"


def _found(url):
    return 200, INDEX, ""


def test_doctor_fails_a_repository_the_workspace_uses_but_cannot_fetch():
    work = pathlib.Path(tempfile.mkdtemp())
    (work / "pmbootstrap_v3.cfg").write_text(
        f"[pmbootstrap]\n[mirrors]\npmaports_custom = {URL}\n")
    rows = _doctor(_cfg(work), _not_found)
    assert rows["package repository"]["status"] == "fail", rows
    assert rows["package repository"]["fix"], rows


def test_doctor_only_warns_while_nothing_uses_it():
    work = pathlib.Path(tempfile.mkdtemp())
    (work / "pmbootstrap_v3.cfg").write_text("[pmbootstrap]\n[mirrors]\n")
    rows = _doctor(_cfg(work), _not_found)
    assert rows["package repository"]["status"] == "warn", rows
    assert "private" in rows["package repository"]["detail"], rows
    assert rows["workspace: package repository"]["status"] == "skip", rows


def test_doctor_says_ok_and_names_a_workspace_that_does_not_use_it_yet():
    work = pathlib.Path(tempfile.mkdtemp())
    (work / "pmbootstrap_v3.cfg").write_text("[pmbootstrap]\n[mirrors]\n")
    rows = _doctor(_cfg(work), _found)
    assert rows["package repository"]["status"] == "ok", rows
    assert rows["workspace: package repository"]["status"] == "warn", rows
    pkgrepo.install_key(KEY, work)
    (work / "pmbootstrap_v3.cfg").write_text(
        f"[pmbootstrap]\n[mirrors]\npmaports_custom = {URL}\n")
    rows = _doctor(_cfg(work), _found)
    assert rows["workspace: package repository"]["status"] == "ok", rows


def test_doctor_does_not_fetch_without_all():
    def boom(url):
        raise AssertionError("plain `porthole doctor` went to the network")
    work = pathlib.Path(tempfile.mkdtemp())
    rows = _doctor(_cfg(work), boom, deep=False)
    assert rows["package repository"]["status"] == "skip", rows


def test_sandbox_up_leaves_an_unusable_repository_out_and_says_so():
    import porthole_cmd_sandbox as sb
    work = pathlib.Path(tempfile.mkdtemp())
    real = pkgrepo.fetch
    try:
        pkgrepo.fetch = _not_found
        ctx = _Ctx(_cfg(work))
        mirrors = sb._pkg_repo_mirrors(ctx, work, _pmaports())
        assert mirrors == {}, mirrors
        assert not (work / "config_apk_keys").exists()
        assert any(l.startswith("WARN") and "NOT configured" in l
                   for l in ctx.out.lines), ctx.out.lines
        pkgrepo.fetch = _found
        ctx = _Ctx(_cfg(work))
        mirrors = sb._pkg_repo_mirrors(ctx, work, _pmaports())
        assert mirrors == {"pmaports_custom": URL}, mirrors
        assert (work / "config_apk_keys" / KEY.name).is_file()
    finally:
        pkgrepo.fetch = real


def main():
    return _runner.run(globals())


if __name__ == "__main__":
    sys.exit(main())
