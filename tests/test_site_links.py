#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check generated-site paths and fragments, including same-page anchors."""
import importlib.util
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("site_links", ROOT / "site-app/check-links.py")
links = importlib.util.module_from_spec(spec)
spec.loader.exec_module(links)


def test_paths_and_fragments():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "config").mkdir()
        (root / "config/index.html").write_text('<h2 id="legacy-names">Legacy names</h2>')
        page = root / "index.html"
        page.write_text('<h2 id="here">Here</h2>'
                        '<a href="#here">same page</a>'
                        '<a href="config/#legacy-names">other page</a>')
        assert not list(links.broken(root, "/porthole/"))
        page.write_text(page.read_text() + '<a href="#absent">broken fragment</a>')
        assert [u for _, u in links.broken(root, "/porthole/")] == ["#absent"]
        page.write_text('<a href="https://porthole-dev.github.io/porthole/config/#legacy-names">absolute</a>')
        assert not list(links.broken(root, "/porthole/"))
        page.write_text('<a href="https://porthole-dev.github.io/porthole/config/#absent">broken</a>')
        assert [u for _, u in links.broken(root, "/porthole/")] == [
            "https://porthole-dev.github.io/porthole/config/#absent"
        ]


if __name__ == "__main__":
    test_paths_and_fragments()
    print("site links: paths and fragments passed")
