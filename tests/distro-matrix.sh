#!/bin/sh
# distro-matrix.sh -- does `porthole doctor` give advice that WORKS, per distro?
#
# scope:  generic
# needs:  podman
# env:    -
# exits:  0 every distro got usable advice · 1 at least one did not
#
# WHY THIS EXISTS
#   `doctor` picks its install hints from /etc/os-release, and that table was
#   wrong on the machine this toolbox is developed on: Fedora Silverblue
#   reports ID=fedora and has NO dnf, so doctor printed
#   `sudo dnf install android-tools` -- a command that cannot run. The table
#   was folklore, asserted and never executed.
#
#   So this runs doctor inside each distro and checks the advice names that
#   distro's actual package manager. It does not install anything: what is
#   under test is the ADVICE, and running it would only prove the network
#   works.
#
# Not part of `make test` -- it pulls four images. `make distros`.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
flagfile="$(mktemp)"
rm -f "$flagfile"

say() { printf '  %-10s %s\n' "$1" "$2"; }

# distro | image | the package manager its hints MUST name
matrix='debian|docker.io/library/debian:13|apt
arch|docker.io/library/archlinux:latest|pacman
alpine|docker.io/library/alpine:3.24|apk
fedora|docker.io/library/fedora:44|dnf'

echo "== doctor's install hints, per distro =="

echo "$matrix" | while IFS='|' read -r name image pkgmgr; do
    out=$(podman run --rm --security-opt label=disable \
        -v "$ROOT:/src:ro" -w /src "$image" sh -c '
            # python3 is the only thing porthole itself needs. Install it
            # quietly where the base image lacks it; that is setup, not the
            # thing under test.
            if ! command -v python3 >/dev/null 2>&1; then
                if command -v apk >/dev/null 2>&1; then
                    apk add -q python3
                elif command -v apt-get >/dev/null 2>&1; then
                    apt-get -qq update && apt-get -qq install -y python3
                elif command -v pacman >/dev/null 2>&1; then
                    # -Syu, not -Sy: a partial sync installs a python
                    # built against a newer glibc than the base image ships,
                    # and it dies on `import math`. Arch says never partial.
                    pacman -Syu --noconfirm --quiet python
                elif command -v dnf >/dev/null 2>&1; then
                    dnf install -y -q python3
                fi
            fi >/dev/null 2>&1
            command -v python3 >/dev/null 2>&1 || { echo "NOPYTHON"; exit 0; }
            python3 - <<PYEOF
import sys
sys.path.insert(0, "/src/lib")
import porthole_cmd_doctor as d
fam = d.distro_family()
hints = [d.install_hint(t, fam) for t in ("fastboot", "adb", "ssh", "podman")]
print("FAMILY", fam)
for h in hints:
    print("HINT", h.splitlines()[0])
PYEOF
        ' 2>/dev/null) || out="RUNFAILED"

    case "$out" in
        RUNFAILED) say FAIL "$name: could not run doctor at all"; : > "$flagfile"; continue ;;
        *NOPYTHON*)
            say FAIL "$name: no python3, and installing it failed -- NOT verified"
            : > "$flagfile"
            continue ;;
    esac

    got_family=$(echo "$out" | sed -n 's/^FAMILY //p')
    if [ "$got_family" != "$name" ]; then
        say FAIL "$name: doctor called it '$got_family'"
        : > "$flagfile"
        continue
    fi

    # Every hint that names a package manager must name THIS one. A hint that
    # is prose ("preinstalled", "porthole sandbox up") is not a claim about
    # packaging and is left alone.
    bad=$(echo "$out" | sed -n 's/^HINT //p' \
        | grep -E 'apt|pacman|apk|dnf|zypper|brew' \
        | grep -v "$pkgmgr" || :)
    if [ -n "$bad" ]; then
        say FAIL "$name: hints name the wrong package manager:"
        echo "$bad" | sed 's/^/               /'
        : > "$flagfile"
    else
        say ok "$name: advice names $pkgmgr"
    fi
done

echo
# The `while` above runs in a pipeline, so its `fail` never reaches here. The
# marker file is how the subshell reports back -- and getting this wrong would
# make the whole matrix exit 0 no matter what it found, which is the failure
# mode it exists to prevent.
if [ -f "$flagfile" ]; then
    echo "at least one distro was given advice it cannot run"
    rm -f "$flagfile"
    exit 1
fi
echo "every distro got advice it can actually run"
exit 0
