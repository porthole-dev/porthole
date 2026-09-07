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
# HOME lives OUTSIDE the extracted tree, and that is load-bearing.
#
# It used to be "$WORK/.home", inside the very directory the suite walks. With
# no .git in an archive extraction the secrets scanner and the
# file-cleanliness check fall back to walking the tree, so porthole's own
# `$HOME/.cache/porthole/registry.json` -- written by any test that runs the
# CLI, and seven of them do -- was picked up as a tracked file and failed two
# suites that have nothing to do with it. Which test wrote it first decided
# whether the run was red, so the failure moved whenever anything else
# changed. Outside the tree, the whole class is gone.
SANDBOX_HOME="$(mktemp -d)"
trap 'rm -rf "$WORK" "$SANDBOX_HOME"' EXIT

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
export HOME="$SANDBOX_HOME"
export XDG_CONFIG_HOME="$SANDBOX_HOME/.config"
export XDG_CACHE_HOME="$SANDBOX_HOME/.cache"
mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME"

# A PATH that is bare in the way a RUNNER's is, not in the way /usr/bin is.
#
# `PATH=/usr/bin:/bin` was the simulation, and on a porting host it is not one:
# `android-tools` puts fastboot in /usr/bin, so the "doctor is honest on a host
# with nothing installed" assertion -- which requires doctor to NOTICE that
# fastboot is missing -- was green on the runner and red on every machine that
# had done a flash. That is precisely the drift this file's header says it
# exists to catch, wearing the file's own clothes.
#
# So: link everything through, minus the tools a GitHub runner genuinely does
# not have. Excluded by NAME, and the list is short and reviewable, because
# "which tools does the runner have" is a question a diff should be able to
# answer.
NOT_ON_A_RUNNER="fastboot adb pmbootstrap podman heimdall mkbootimg abootimg
                 android-tools-fastboot android-tools-adb"
# OUTSIDE $WORK, for the same reason HOME is: it is full of symlinks to
# /usr/bin, and the tree-walking fallback would scan every one of them.
RUNNER_BIN="$SANDBOX_HOME/.bin"
mkdir -p "$RUNNER_BIN"
for _d in /usr/bin /bin; do
	[ -d "$_d" ] || continue
	for _f in "$_d"/*; do
		_n=${_f##*/}
		case " $NOT_ON_A_RUNNER " in *" $_n "*) continue ;; esac
		[ -e "$RUNNER_BIN/$_n" ] || ln -s "$_f" "$RUNNER_BIN/$_n" 2>/dev/null || :
	done
done
export PATH="$RUNNER_BIN"

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
bash tests/test_ph_build.sh >/dev/null 2>&1 && say ok "ph-build rungs" || { say FAIL "ph-build rungs"; fail=1; }

echo
echo "== smoke (nothing configured) =="
for c in "" "--help" "version" "devices"; do
	# shellcheck disable=SC2086
	./bin/porthole $c >/dev/null 2>&1 && say ok "porthole $c" || { say FAIL "porthole $c"; fail=1; }
done
./bin/porthole init google-taimen --user ci --host 172.16.42.1 --yes >/dev/null 2>&1 \
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
echo "== doctor is honest on a host with nothing installed =="
# There is no fastboot on this bare PATH, so doctor SHOULD exit non-zero. That
# is the behaviour under test, not a failure -- `|| :` keeps the run alive so
# the assertions below check the SHAPE of the report, not its exit code.
./bin/porthole doctor --no-device --all --json > doctor.json 2>/dev/null || :
if python3 - <<'PYEOF' >/dev/null 2>&1
import json
d = json.load(open("doctor.json"))
assert d["checks"], "no checks ran"

# The contract: a failure must always name a fix. A check that says something
# is wrong without saying what to do is the thing doctor exists to replace.
for c in d["checks"]:
    assert c["status"] != "fail" or c["fix"], f"no fix offered for {c['name']}"

# And it must actually notice a missing prerequisite rather than reporting a
# clean bill of health on a bare PATH.
fastboot = next(c for c in d["checks"] if c["name"] == "host: fastboot")
assert fastboot["status"] == "fail", (
    "doctor did not notice fastboot is missing on a bare PATH")
# "an install command", not the literal word `install`. Only apt, dnf and
# zypper spell it that way; `pacman -S` and `apk add` do not, and doctor prints
# whichever the host uses -- so this assertion was green on the Debian runner
# and red on every Arch and Alpine machine, about a fix that was perfectly
# correct. Checked against the set doctor can actually emit -- which means
# every manager in install_hint's table in lib/porthole_cmd_doctor.py, not
# just the ones on hand when this list was first written: `rpm-ostree
# install` (fedora-atomic) was missing the same way, and was red on an
# ostree host about a fix -- unpack the SDK, or rpm-ostree install -- that
# was correct too. Add here whenever that table gains a new manager.
MANAGERS = ("apt install", "pacman -S", "dnf install", "apk add",
            "zypper install", "brew install", "rpm-ostree install")
assert any(m in fastboot["fix"] for m in MANAGERS), (
    f"the fix should be an install command, got {fastboot['fix']!r}")
PYEOF
then say ok "doctor"; else say FAIL "doctor"; fail=1; fi

echo
echo "== python syntax (compileall) =="
python3 -m compileall -q lib bin tools tests >/dev/null 2>&1 \
	&& say ok "compileall" || { say FAIL "compileall"; fail=1; }

echo
[ "$fail" = 0 ] && say PASS "CI would be green" || say FAILED "CI would be RED"
exit "$fail"
