#!/bin/sh
# SPDX-License-Identifier: MIT
# Run every tests/test_*.py at once; print them back in filename order.
#
# WHY
#   The suite was 73s and nearly all of it was waiting. Two files were 63s of
#   that (see tests/_runner.py), and those are now internally parallel -- but
#   the file loop itself was still serial, so the rest queued behind them.
#
# WHY THE OUTPUT IS BUFFERED
#   Parallel suites writing to one terminal interleave into nonsense, and this
#   output is read by humans deciding whether a run was clean. So each suite's
#   output is captured and replayed in the same order the serial loop used.
#   What you see is identical to before; only the waiting changed.
#
#   That ordering also matters for `make console`, which greps this output to
#   assert the console suites did NOT skip.
set -u

PY=${PY:-python3}
JOBS=${TEST_JOBS:-8}
export PY

out=$(mktemp -d) || exit 1
trap 'rm -rf "$out"' EXIT
export out

# `sh -c '...' _ {}` rather than -I{} substitution inside the script body:
# the filename arrives as "$1", so a path is never re-parsed by the shell.
printf '%s\n' tests/test_*.py \
  | xargs -P "$JOBS" -I{} sh -c '
      n=$(basename "$1")
      if "$PY" "$1" > "$out/$n.out" 2>&1; then :; else : > "$out/$n.fail"; fi
    ' _ {}

fail=0
for t in tests/test_*.py; do
    n=$(basename "$t")
    printf '%-28s ' "$n"
    cat "$out/$n.out"
    if [ -f "$out/$n.fail" ]; then
        fail=1
    fi
done

exit "$fail"
