#!/usr/bin/env bash
# Measure vein's tracing overhead on a call-heavy workload.
# Usage: bench/overhead.sh [rounds]
set -euo pipefail
ROUNDS="${1:-200000}"
run() { python3 bench/workload.py "$ROUNDS"; }

echo "rounds: $ROUNDS  ($((ROUNDS * 4)) traced calls)"
printf 'baseline      %ss\n' "$(run)"
printf 'vein          %ss\n' "$(python3 -m vein run -n bench --root "$PWD" -- python3 bench/workload.py "$ROUNDS" 2>/dev/null | head -1)"
printf 'vein --no-time %ss\n' "$(python3 -m vein run -n bench-nt --no-time --root "$PWD" -- python3 bench/workload.py "$ROUNDS" 2>/dev/null | head -1)"
