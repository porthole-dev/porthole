---
id: ci-floor-copies-release-cache
title: Python floor test fills disk after an image build
scope: generic
subsystem: workflow
severity: trap
confidence: proven
evidence: 2026-09-29 make ci's Python 3.8 floor copied the whole checkout with `cp -r /src /w`, including the 4.6 GB image and package caches under .run. Host free space fell from 30 GiB to 17 GiB while the copy was still running. Stopping the container restored 30 GiB. The floor target now excludes .run and generated site output; `make floor` passed with disk usage stable.
first-learned: 2026-09-29
---

**Symptom** — `make ci` reaches the Python floor job and stops printing while
free disk space falls quickly after a release image build.

**Cause** — The job copied the whole working tree into its container. That
included ignored image and package caches in `.run`, even though the tests
only need source files.

**What to do** — Exclude `.run` and generated site output when copying source
for the floor test. Watch disk usage on a first run after building an image;
the copy should finish without duplicating the build workspace.
