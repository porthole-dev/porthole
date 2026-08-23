# porthole -- development entry points.
#
# Everything here runs without a device attached and without installing
# anything beyond what `porthole doctor` calls required. That is deliberate:
# a contributor should be able to check their work on a laptop with no phone.

SHELL := /bin/bash
PY    := python3
TOOLS := $(shell find tools profiles/*/tools -type f \( -name '*.sh' -o -name '*.py' \) \
                  -not -type l 2>/dev/null)

.PHONY: help test lint check fmt tools-doc brain-index clean install-completion

help:            ## show this help
	@grep -hE '^[a-z-]+:.*?##' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

test:            ## run every test (no device needed)
	@fail=0; \
	for t in tests/test_config.py tests/test_cli.py tests/test_tools.py; do \
	  printf '%-28s ' "$$(basename $$t)"; \
	  $(PY) $$t || fail=1; \
	done; \
	printf '%-28s ' test_shell_lib.sh; bash tests/test_shell_lib.sh || fail=1; \
	printf '%-28s ' tk-device-test.sh; bash tools/tk-device-test.sh >/dev/null \
	  && echo "ok" || { echo FAIL; fail=1; }; \
	exit $$fail

lint:            ## shellcheck + python syntax (skips what is not installed)
	@if command -v shellcheck >/dev/null; then \
	  shellcheck -S warning -e SC1091,SC2086,SC2181 $(filter %.sh,$(TOOLS)) \
	    lib/porthole.sh || true; \
	else echo "shellcheck not installed -- skipping (see porthole doctor)"; fi
	@$(PY) -m compileall -q lib bin tools tests >/dev/null && echo "python: ok"
	@bash -n lib/porthole.sh && echo "shell lib: ok"

check: lint test ## lint then test

brain-index:     ## regenerate brain/INDEX.md
	@./bin/porthole brain --reindex

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
