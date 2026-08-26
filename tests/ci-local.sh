#!/bin/sh
# ci-local.sh -- run what GitHub runs, in a fresh clone, on a bare PATH.
#
# scope:  generic
# needs:  -
# env:    -
# exits:  0 everything GitHub runs would pass · 1 something would fail
#
# Written after CI went red three times on things that passed locally, each
# time because this host has something the runner does not: pmbootstrap, a
# pmaports checkout, a populated ~/.config. Checking `gh run list --limit 1`
# is worse than useless -- it returns whichever workflow finished last, which
# is how a red CI run got reported as green.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# `git archive`, not a copy loop: it preserves file MODES and SYMLINKS, and
# tools/tk-lib.sh is a symlink whose identity a test checks. A cp-based copy
# reported five false failures before this was noticed -- a simulation that
# lies costs more than no simulation.
git -C "$ROOT" archive HEAD | tar -x -C "$WORK"
# Uncommitted work matters too: CI runs what you are about to push.
git -C "$ROOT" diff --name-only HEAD | while read -r f; do
	[ -e "$ROOT/$f" ] || continue
	mkdir -p "$WORK/$(dirname "$f")"
	cp -a "$ROOT/$f" "$WORK/$f"
done
# Untracked-but-staged files too (a new test is invisible otherwise).
git -C "$ROOT" diff --cached --name-only | while read -r f; do
	[ -e "$ROOT/$f" ] || continue
	mkdir -p "$WORK/$(dirname "$f")"
	cp -a "$ROOT/$f" "$WORK/$f"
done
# ...and files that are not in the index AT ALL. A brand new verb and its tests
# are untracked until `git add`, so this script reported PASS for a tree that
# did not contain them -- and the failures appeared only after the commit. A
# simulation that lies costs more than no simulation, which is what the header
# above says about the cp-based copy it replaced.
git -C "$ROOT" ls-files --others --exclude-standard | while read -r f; do
	[ -e "$ROOT/$f" ] || continue
	mkdir -p "$WORK/$(dirname "$f")"
	cp -a "$ROOT/$f" "$WORK/$f"
done

cd "$WORK"
# A runner has no pmbootstrap, no pmaports, and an empty HOME.
export HOME="$WORK/.home"
export XDG_CONFIG_HOME="$WORK/.config"
export XDG_CACHE_HOME="$WORK/.cache"
export PATH=/usr/bin:/bin
mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME"

# ...and nothing porthole reads in its environment. A developer who has run
# `porthole use` has the whole set exported, and the config layer puts the
# environment ABOVE the config file -- correctly. So `init --user ci` wrote
# PORTHOLE_USER=ci to the config while `config PHONE` still answered
# user@172.16.42.1 from the environment, and the headless-bootstrap check was
# red on every developer machine and green on the runner. That is the exact
# drift this script's header says it exists to catch, so clear them rather
# than teaching people to ignore one permanently red line.
#
# The bare legacy knobs (lib/porthole.sh "compatibility surface") carry no
# prefix, so a PORTHOLE_*/TK_* pattern alone does not reach them -- PHONE is
# precisely the one that was winning.
for _v in $(env | sed -n 's/^\(PORTHOLE_[A-Z0-9_]*\|TK_[A-Z0-9_]*\)=.*/\1/p') \
          PHONE HOST FASTBOOT; do
	unset "$_v" || :
done

fail=0
say() { printf '%s %s\n' "$1" "$2"; }

echo "== test suite =="
for t in tests/test_*.py; do
	if out=$(python3 "$t" 2>&1); then
		say ok "$(basename "$t")  $(echo "$out" | tail -1)"
	else
		say FAIL "$(basename "$t")"
		echo "$out" | grep -E "FAIL|ERR " | head -5 | sed 's/^/     /'
		fail=1
	fi
done

echo
echo "== brain lint =="
./bin/porthole brain lint >/dev/null 2>&1 && say ok "brain lint" || { say FAIL "brain lint"; fail=1; }

echo
echo "== shell lib =="
bash tests/test_shell_lib.sh >/dev/null 2>&1 && say ok "shell lib" || { say FAIL "shell lib"; fail=1; }

echo
echo "== smoke (nothing configured) =="
for c in "" "--help" "version" "devices"; do
	# shellcheck disable=SC2086
	./bin/porthole $c >/dev/null 2>&1 && say ok "porthole $c" || { say FAIL "porthole $c"; fail=1; }
done
./bin/porthole init google-taimen --user ci --host 172.16.42.1 >/dev/null 2>&1 \
	&& ./bin/porthole config PHONE | grep -q '^ci@172.16.42.1$' \
	&& say ok "headless bootstrap" || { say FAIL "headless bootstrap"; fail=1; }
for c in "brief --no-device --json" "tools --json" "brain --json" "next --json"; do
	# shellcheck disable=SC2086
	./bin/porthole $c 2>/dev/null | python3 -m json.tool >/dev/null 2>&1 \
		&& say ok "$c" || { say FAIL "$c"; fail=1; }
done
for s in bash zsh fish; do
	./bin/porthole completion "$s" >/dev/null 2>&1 || { say FAIL "completion $s"; fail=1; }
done
say ok "completions"
./bin/porthole new-device test-device >/dev/null 2>&1 \
	&& [ -f profiles/test-device/device.env ] \
	&& [ -f profiles/test-device/checklist.md ] \
	&& say ok "new-device" || { say FAIL "new-device"; fail=1; }

echo
echo "== python syntax (compileall) =="
python3 -m compileall -q lib bin tools tests >/dev/null 2>&1 \
	&& say ok "compileall" || { say FAIL "compileall"; fail=1; }

echo
[ "$fail" = 0 ] && say PASS "CI would be green" || say FAILED "CI would be RED"
exit "$fail"
