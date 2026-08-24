# porthole -- development entry points.
#
# Everything here runs without a device attached and without installing
# anything beyond what `porthole doctor` calls required. That is deliberate:
# a contributor should be able to check their work on a laptop with no phone.

SHELL := /bin/bash
PY    := python3
# The oldest interpreter bin/porthole accepts, and what CI's
# matrix floor is. Keep the three in step.
PY_FLOOR := 3.8
TOOLS := $(shell find tools profiles/*/tools -type f \( -name '*.sh' -o -name '*.py' \) \
                  -not -type l 2>/dev/null)

.PHONY: help test lint check ci fmt tools-doc brain-index clean install-completion docs docs-serve

help:            ## show this help
	@grep -hE '^[a-z-]+:.*?##' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

test:            ## run every test (no device needed)
	@# Globbed, not listed. CI globs; a hand-kept list here silently diverged
	@# and eight suites ran in CI but never locally.
	@fail=0; \
	for t in tests/test_*.py; do \
	  printf '%-28s ' "$$(basename $$t)"; \
	  $(PY) $$t || fail=1; \
	done; \
	printf '%-28s ' test_shell_lib.sh; bash tests/test_shell_lib.sh || fail=1; \
	printf '%-28s ' tk-device-test.sh; bash tools/tk-device-test.sh >/dev/null \
	  && echo "ok" || { echo FAIL; fail=1; }; \
	exit $$fail

SHELLCHECK_ARGS := -S warning -e SC1091,SC2086,SC2181
SHELLCHECK_IMAGE := docker.io/koalaman/shellcheck-alpine:stable

lint:            ## shellcheck + python syntax (falls back to a container)
	@# Never silently skip: a linter that prints "skipping" and exits 0 reads
	@# exactly like a clean run, which is how 28 findings once reached CI.
	@if command -v shellcheck >/dev/null; then \
	  shellcheck $(SHELLCHECK_ARGS) lib/porthole.sh $(filter %.sh,$(TOOLS)) \
	    && echo "shellcheck: ok"; \
	elif command -v podman >/dev/null; then \
	  echo "shellcheck not installed -- running it in a container"; \
	  podman run --rm --userns=keep-id:uid=0,gid=0 --security-opt label=disable \
	    -v "$(CURDIR):/src:ro" -w /src $(SHELLCHECK_IMAGE) \
	    shellcheck $(SHELLCHECK_ARGS) lib/porthole.sh $(filter %.sh,$(TOOLS)) \
	    && echo "shellcheck: ok"; \
	else \
	  echo "ERROR: neither shellcheck nor podman is available."; \
	  echo "       Install one, or run: make lint SHELLCHECK_SKIP=1 (and know CI will not)"; \
	  [ -n "$(SHELLCHECK_SKIP)" ]; \
	fi
	@$(PY) -m compileall -q lib bin tools tests >/dev/null && echo "python: ok ($(shell $(PY) -c 'import sys;print(".".join(map(str,sys.version_info[:2])))'))"
	@# Compiling with the DEVELOPER's interpreter is not enough, and this is not
	@# hypothetical: a multi-line expression inside an f-string is PEP 701, legal
	@# on 3.12+ and a syntax error below it. It compiled locally on 3.14 and
	@# broke every CI job. The floor is what bin/porthole declares, so the floor
	@# is what must be checked.
	@if command -v podman >/dev/null; then \
	  podman run --rm --userns=keep-id:uid=0,gid=0 --security-opt label=disable \
	    -v "$(CURDIR):/src:ro" -w /tmp docker.io/library/python:$(PY_FLOOR)-slim \
	    sh -c 'cp -r /src /w && cd /w && python -m compileall -q lib bin tools tests' \
	    && echo "python $(PY_FLOOR) (the declared floor): ok"; \
	else \
	  echo "WARNING: no podman -- cannot check the python $(PY_FLOOR) floor."; \
	  echo "         Your interpreter is newer and accepts syntax CI will reject."; \
	fi
	@bash -n lib/porthole.sh && echo "shell lib: ok"

check: lint test ## lint then test

ci:              ## run what CI runs, including the python floor
	@echo "== tests on the declared floor (python $(PY_FLOOR)) =="
	@command -v podman >/dev/null || { echo "needs podman"; exit 1; }
	@podman run --rm --userns=keep-id:uid=0,gid=0 --security-opt label=disable \
	  -v "$(CURDIR):/src:ro" -w /tmp docker.io/library/python:$(PY_FLOOR)-slim \
	  sh -c 'cp -r /src /w && cd /w && for t in tests/test_*.py; do \
	    printf "%-26s " "$$(basename $$t)"; python "$$t" | tail -1; done'
	@echo
	@$(MAKE) --no-print-directory check

brain-index:     ## regenerate brain/INDEX.md
	@./bin/porthole brain --reindex

docs:            ## generate the documentation site sources
	@./bin/porthole docs build

docs-serve:      ## preview the docs at http://127.0.0.1:8000
	@./bin/porthole docs serve

tools-doc:       ## regenerate docs/TOOLS.md from the tool headers
	@$(PY) tools/gen-tools-doc.py > docs/TOOLS.md && echo "wrote docs/TOOLS.md"

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
