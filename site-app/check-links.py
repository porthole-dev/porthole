#!/usr/bin/env python3
"""Check local paths and heading fragments in the built static site."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
import os
import sys


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if fields.get("id"):
            self.ids.add(fields["id"])
        if tag in ("a", "img", "script", "link") and fields.get("rel") != "canonical":
            url = fields.get("href") or fields.get("src")
            if url:
                self.urls.append(url)


def broken(root, base, site="https://porthole-dev.github.io"):
    root = root.resolve()
    pages = {}
    for page in root.rglob("*.html"):
        links = Links()
        links.feed(page.read_text())
        pages[page] = links
    for page, links in pages.items():
        for url in links.urls:
            parts = urlsplit(url)
            if parts.scheme or parts.netloc:
                if "{}://{}".format(parts.scheme, parts.netloc) != site:
                    continue
            if not (parts.path or parts.fragment):
                continue
            path = unquote(parts.path)
            if path.startswith("/"):
                if not path.startswith(base):
                    yield page, url
                    continue
                target = (root / path[len(base):]).resolve()
            else:
                target = (page.parent / path).resolve() if path else page
            if target != root and root not in target.parents:
                yield page, url
                continue
            if target.is_dir():
                target /= "index.html"
            elif not target.is_file() and (target / "index.html").is_file():
                target /= "index.html"
            if not target.is_file():
                yield page, url
            elif parts.fragment and (target not in pages or
                                     unquote(parts.fragment) not in pages[target].ids):
                yield page, url


if __name__ == "__main__":
    root = Path(__file__).resolve().parent / "dist"
    owner, repo = os.environ.get("GITHUB_REPOSITORY", "porthole-dev/porthole").split("/", 1)
    base = "/" if repo == owner + ".github.io" else "/" + repo + "/"
    errors = list(broken(root, base, "https://{}.github.io".format(owner)))
    for page, url in errors[:30]:
        print("{}: broken local link {}".format(page.relative_to(root), url), file=sys.stderr)
    if errors:
        sys.exit("{} broken local links".format(len(errors)))
    print("local links OK")
