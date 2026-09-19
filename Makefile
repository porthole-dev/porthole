# porthole -- development entry points.
#
# Everything here runs without a device attached and without installing
# anything beyond what `porthole doctor` calls required. That is deliberate:
# a contributor should be able to check their work on a laptop with no phone.
#
# One rule keeps this file honest: .github/workflows/ci.yml runs NOTHING but
# these targets. Every job there is `make <target>`, every target it names is
# reached by `make ci`, and tests/test_tools.py fails if that stops being true.
# Three hand-kept copies of the step list -- the workflow, this file, and
# tests/ci-local.sh -- is how "make check passed" and "CI is red" kept being
# true at the same time.

SHELL := /bin/bash
PY    := python3
# The oldest interpreter bin/porthole accepts, and what CI's
# matrix floor is. Keep the three in step.
PY_FLOOR := 3.8
# How many suites run at once. 8 measured best on a 16-thread/8-core box: the
# two big suites are already internally parallel (tests/_runner.py), so a
# higher number only contends with them -- -P 8 and -P 16 both land at ~13.9s.
TEST_JOBS ?= 8
TOOLS := $(shell find tools profiles/*/tools -type f \( -name '*.sh' -o -name '*.py' \) \
                  -not -type l 2>/dev/null)

.PHONY: help test smoke lint floor check ci trailers issue-trailers distros fmt tools-doc tools-audit brain-index clean install-completion docs docs-serve rules

help:            ## show this help
	@grep -hE '^[a-z-]+:.*?##' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

test:            ## CI job "tests": suites, brain lint, shell lib, device mutex
	@# Globbed, not listed. CI globs; a hand-kept list here silently diverged
	@# and eight suites ran in CI but never locally.
	@fail=0; \
	PY="$(PY)" TEST_JOBS=$(TEST_JOBS) bash tests/run-suites.sh || fail=1; \
	printf '%-28s ' 'brain lint'; \
	out=$$(./bin/porthole brain lint 2>&1) && echo "ok" \
	  || { echo FAIL; echo "$$out" | sed 's/^/    /'; fail=1; }; \
	printf '%-28s ' test_shell_lib.sh; bash tests/test_shell_lib.sh || fail=1; \
	printf '%-28s ' test_ph_build.sh; bash tests/test_ph_build.sh || fail=1; \
	printf '%-28s ' test_pkgcheck_owned.sh; sh tests/test_pkgcheck_owned.sh || fail=1; \
	printf '%-28s ' ph-device-test.sh; bash tools/ph-device-test.sh >/dev/null \
	  && echo "ok" || { echo FAIL; fail=1; }; \
	exit $$fail

smoke:           ## CI job "smoke": fresh clone, bare PATH, empty HOME
	@bash tests/ci-local.sh

SHELLCHECK_ARGS := -S warning -e SC1091,SC2086,SC2181
SHELLCHECK_IMAGE := docker.io/koalaman/shellcheck-alpine:stable
# -slim has no git, and test_build_flash.py shells out to it for series attribution.
FLOOR_IMAGE := docker.io/library/python:$(PY_FLOOR)
PODMAN := podman run --rm --userns=keep-id:uid=0,gid=0 --security-opt label=disable

lint:            ## CI job "lint": shellcheck + python syntax (falls back to a container)
	@# Never silently skip: a linter that prints "skipping" and exits 0 reads
	@# exactly like a clean run, which is how 28 findings once reached CI.
	@if command -v shellcheck >/dev/null; then \
	  shellcheck $(SHELLCHECK_ARGS) lib/porthole.sh $(filter %.sh,$(TOOLS)) \
	    && echo "shellcheck: ok"; \
	elif command -v podman >/dev/null; then \
	  echo "shellcheck not installed -- running it in a container"; \
	  $(PODMAN) -v "$(CURDIR):/src:ro" -w /src $(SHELLCHECK_IMAGE) \
	    shellcheck $(SHELLCHECK_ARGS) lib/porthole.sh $(filter %.sh,$(TOOLS)) \
	    && echo "shellcheck: ok"; \
	else \
	  echo "ERROR: neither shellcheck nor podman is available."; \
	  echo "       Install one, or run: make lint SHELLCHECK_SKIP=1 (and know CI will not)"; \
	  [ -n "$(SHELLCHECK_SKIP)" ]; \
	fi
	@$(PY) -m compileall -q lib bin tools tests >/dev/null && echo "python: ok ($(shell $(PY) -c 'import sys;print(".".join(map(str,sys.version_info[:2])))'))"
	@bash -n lib/porthole.sh && echo "shell lib: ok"

floor:           ## the suite on the declared python floor, in a container (needs podman)
	@# Compiling with the DEVELOPER's interpreter is not enough, and this is not
	@# hypothetical: a multi-line expression inside an f-string is PEP 701, legal
	@# on 3.12+ and a syntax error below it. It compiled locally on 3.14 and
	@# broke every CI job. The floor is what bin/porthole declares, so the floor
	@# is what must be checked.
	@# `rm -rf /w/.git`, and it is load-bearing. In a LINKED WORKTREE `.git` is
	@# a FILE holding `gitdir: <path on the host>`, and that path does not exist
	@# inside the container -- so every git call with a cwd under /w exits 128
	@# with "not a git repository". `_merge_one` reads >=128 as "the merge could
	@# not run" and correctly returns a conflict, so six test_pkg_rebase cases
	@# and one fidelity case failed for a reason that had nothing to do with the
	@# code under test. Measured 2026-09-18: 10 failures from a worktree, 3 from
	@# a true clone of the same commit.
	@#
	@# Dropping it makes the run identical either way, which is the property
	@# this target actually needs. Nothing under tests/ requires the repo's own
	@# history: the one reader (test_brain.py) already handles "git absent, or
	@# not a clone" and only uses it to enrich a failure message.
	@if command -v podman >/dev/null; then \
	  $(PODMAN) -v "$(CURDIR):/src:ro" -w /tmp $(FLOOR_IMAGE) \
	    sh -c 'cp -r /src /w && rm -rf /w/.git && cd /w || exit 1; \
	      fail=0; \
	      python -m compileall -q lib bin tools tests || fail=1; \
	      for t in tests/test_*.py; do \
	        printf "%-26s " "$$(basename $$t)"; \
	        out=$$(python "$$t" 2>&1) && echo "$$out" | tail -1 \
	          || { echo FAIL; echo "$$out" | sed "s/^/    /"; fail=1; }; \
	      done; exit $$fail'; \
	else \
	  echo "WARNING: no podman -- cannot check the python $(PY_FLOOR) floor."; \
	  echo "         Your interpreter is newer and accepts syntax CI will reject."; \
	fi

distros:         ## does doctor's install advice actually work, per distro? (needs podman)
	@# Not in `make check`: it pulls four images. It exists because the hint
	@# table was wrong on the reference host -- Silverblue reports ID=fedora
	@# and has no dnf -- and nothing noticed, because nothing ran it.
	@bash tests/distro-matrix.sh

check: lint test ## lint then test, on YOUR interpreter -- the everyday one

ci: check smoke floor trailers ## every job CI runs, plus the python floor
	@echo
	@echo "== green here means green on GitHub: the jobs run these same targets =="

trailers:        ## CI job "Commit check": banned lines in the log and PR body
	@# The pull request body and range are only reachable when the workflow
	@# exports PR_BODY, PR_BASE and PR_HEAD; locally this is the commit-log half.
	@# The DCO half runs on a pull request from a FORK only -- our own branches
	@# are certified by a Code-Owner review and the merge. `--dco RANGE` still
	@# checks a range by hand, which is what a series bound upstream wants:
	@#   python3 lib/porthole_trailers.py --dco origin/main..HEAD
	@# AGENTS.md section 5.
	@$(PY) lib/porthole_trailers.py --ci && echo "trailers                     ok"

issue-trailers:  ## CI job "Commit check" (issues): strip banned lines from issue $$ISSUE's body
	@# The third publishing surface, and the one issue #54 went out through.
	@# An issue body is stripped rather than rejected: a red run on an issue
	@# event appears nowhere anybody is looking. Only banned lines go
	@# (lib/porthole_trailers.py); Assisted-by and Signed-off-by stay.
	@body=$$(mktemp) && trap 'rm -f "$$body"' EXIT && \
	printf '%s' "$$ISSUE_BODY" > "$$body" && \
	if $(PY) lib/porthole_trailers.py --scan "$$body"; then \
	  echo "issue trailers               ok"; \
	else \
	  $(PY) lib/porthole_trailers.py --strip "$$body" && \
	  gh issue edit "$$ISSUE" --body-file "$$body" >/dev/null && \
	  echo "issue trailers               stripped a banned line from #$$ISSUE"; fi

brain-index:     ## regenerate brain/INDEX.md
	@./bin/porthole brain reindex

docs:            ## generate the documentation site sources
	@./bin/porthole docs build

docs-serve:      ## preview the docs at http://127.0.0.1:8000
	@./bin/porthole docs serve

rules:           ## regenerate the rule blocks in AGENTS.md and the skill
	@# tests/test_rules.py fails when these are stale, the same way
	@# tools-doc's output is checked. Edit lib/porthole_rules.py, run this.
	@$(PY) lib/porthole_rules.py --write

tools-doc:       ## regenerate docs/TOOLS.md from the tool headers
	@$(PY) tools/gen-tools-doc.py > docs/TOOLS.md && echo "wrote docs/TOOLS.md"

tools-audit:     ## score every tool against the contract, worst first
	@./bin/porthole tools audit

install-completion: ## install shell completion for the current shell
	@shell=$$(basename "$$SHELL"); \
	case $$shell in \
	  bash) d=$$HOME/.local/share/bash-completion/completions; mkdir -p $$d; \
	        ./bin/porthole completion bash > $$d/porthole; echo "installed -> $$d/porthole" ;; \
	  zsh)  d=$$HOME/.zsh/completions; mkdir -p $$d; \
	        ./bin/porthole completion zsh > $$d/_porthole; \
	        echo "installed -> $$d/_porthole (ensure it is on your fpath)" ;; \
	  fish) d=$$HOME/.config/fish/completions; mkdir -p $$d; \
	        ./bin/porthole completion fish > $$d/porthole.fish; echo "installed -> $$d/porthole.fish" ;; \
	  *)    echo "unknown shell $$shell; run: porthole completion <bash|zsh|fish>" ;; \
	esac

clean:           ## remove runtime and build scratch
	@rm -rf .run __pycache__ lib/__pycache__ tools/__pycache__ tests/__pycache__
	@find . -name '*.pyc' -delete
	@echo "cleaned"
