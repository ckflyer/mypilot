#!/usr/bin/env bash
# Run every test suite and report the total.
#
# WHY A RUNNER EXISTS AT ALL. Until 1.31.0 the suites were listed by hand
# in the README, and the list was WRONG: tests_radar_filter.py and
# tests_reserve_days.py shipped during the undocumented 1.26–1.29 range
# and were never added to it, so 82 assertions went unrun for five
# releases by anyone following the documented procedure. A runner that
# globs the directory cannot drift from what is actually there.
#
# Usage, from anywhere:
#     bash dev/run_tests.sh
#
# Each suite gets its own scratch database via PT_DB_FILE. They must
# never share one: several write the same flight ids, and a suite that
# inherited another's rows would pass or fail for reasons that have
# nothing to do with what it is testing.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

cd "$ROOT" || exit 1

total=0
failed=0
suites=0

for t in "$HERE"/tests/tests_*.py; do
    name="$(basename "$t" .py)"
    suites=$((suites + 1))
    printf '%-32s ' "$name"
    if out="$(PT_DB_FILE="$SCRATCH/$name.db" timeout 900 python "$t" 2>&1)"; then
        line="$(printf '%s\n' "$out" | tail -1)"
        n="$(printf '%s' "$line" | grep -oE '^[0-9]+' || true)"
        total=$((total + ${n:-0}))
        case "$line" in
            *"0 failed") printf 'ok    %s\n' "$line" ;;
            *)           printf 'FAIL  %s\n' "$line"; failed=$((failed + 1)) ;;
        esac
    else
        printf 'ERROR\n'
        printf '%s\n' "$out" | tail -15 | sed 's/^/      /'
        failed=$((failed + 1))
    fi
done

echo "------------------------------------------------------------"
if [ "$failed" -eq 0 ]; then
    echo "$total assertions across $suites suites, all passing."
else
    echo "$total assertions across $suites suites, $failed SUITE(S) FAILING."
    exit 1
fi
