---
id: anubis-blocks-the-wiki-the-api-does-not
title: The pmOS wiki is behind Anubis, but its MediaWiki API is not -- fetch wikitext, not HTML
scope: generic
subsystem: workflow
severity: trap
confidence: proven
evidence: 2026-08-27: WebFetch on wiki.postmarketos.org returned an Anubis denial page; api.php returned the full wikitext in one call
first-learned: 2026-08-27
---

**Symptom** — fetching anything under `wiki.postmarketos.org/wiki/...` returns a
page whose only content is "Making sure you are not a bot!" / an access-denied
notice mentioning Anubis. A summarising fetcher will report, correctly and
uselessly, that the page contains no technical content.

Anubis is a proof-of-work bot check. It gates the **HTML page views** and
expects a browser to run JavaScript. Driving a real browser to solve it needs
Playwright or a Chrome extension, which a headless session often does not have.

**What to do instead** — ask MediaWiki for the source. The API is not behind
the challenge:

```sh
curl -sS -A 'curl/8.5.0' \
  'https://wiki.postmarketos.org/api.php?action=parse&page=PAGE_TITLE&prop=wikitext&format=json&formatversion=2' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["parse"]["wikitext"])'
```

URL-encode the title (`:` -> `%3A`, `/` -> `%2F`, spaces -> `_`). For a plain
read `prop=wikitext` is best -- it is the source, so tables, code blocks and
templates arrive intact rather than as rendered soup. `prop=text` gives HTML if
that is what you want.

Wikitext is also *better* than the rendered page for our purposes: kernel
configs and devicetree snippets come back exactly as written.

**Why it is worth a note** — the obvious readings of the failure are both
wrong and both expensive. "The page does not exist" sends you looking for
another source; "I need a browser" sends you installing one. The page is
there, and one curl gets it.

Same shape for any Anubis-protected MediaWiki, not just pmOS: the challenge is
on the human-facing path, and `api.php` is a machine-facing one.
