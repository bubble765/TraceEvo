#!/usr/bin/env bash
# TraceEvo S5B — reproduce the final held-out stage from a frozen checkout.
#
# The order is §5.1's and it is the point: the freeze is written first, the
# held-out block is run and sealed second, and only then does any module open
# a reference. Running these out of order would produce the same files with a
# different meaning.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-python3}
OUT=S5B_final_heldout

$PY -m pip install -r "$OUT/requirements_lock.txt"

# §4: freeze, claim manifest, access log — before case 80 is opened
$PY traceevo/s5b_freeze.py --out "$OUT"

# §5.2: run, artifacts to disk, seal, per case, in order
$PY traceevo/s5b_run.py --out "$OUT" --run
$PY traceevo/s5b_run.py --out "$OUT" --check-seals
$PY traceevo/s5b_run.py --out "$OUT" --attempts-report

# §5.2: what each attempt actually died of, read back from the console log.
# Separate from the runner on purpose -- the runner's own `rerun_reason` field
# is derived from the retry number rather than from the failure.
$PY traceevo/s5b_run_audit.py --out "$OUT"

# §5.1 steps 6-12: audit, seal verification, then gold, judge, gate
$PY traceevo/s5b_pipeline.py --out "$OUT"
$PY traceevo/s5b_diagnostic.py --out "$OUT"

# §7-§9: controlled injections on copies only
$PY traceevo/s5b_anomaly.py --out "$OUT"

# §10-§11: Gate, §12-§14: ablation and overhead
$PY traceevo/s5b_gate.py --out "$OUT"
$PY traceevo/s5b_ablation.py --out "$OUT"
$PY traceevo/s5b_overhead.py --out "$OUT"

# §17-§21: tables, figures, claim matrix, success conditions
$PY traceevo/s5b_tables.py --out "$OUT"
$PY traceevo/s5b_figures.py --out "$OUT"
$PY traceevo/s5b_finalize.py --out "$OUT"
$PY traceevo/s5b_freeze.py --out "$OUT" --verify
