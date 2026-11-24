#!/usr/bin/env bash
# Main study (docs/plans/main_study_protocol.md): every run, then the answer tables, the F8 checks, the report
# figures and the experiment comparison page. Interrupt it at any moment; started again, it continues where it
# stopped (complete runs are kept, tables are recomputed only for new runs).
#
#   scripts/run_main_study.sh            # WORKERS=10 PYTHON=.venv/bin/python by default
#
# The study root is runs/main: the control configs point to runs/main/controls/graphs.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-.venv/bin/python}
W=${WORKERS:-10}
ROOT=runs/main

run() {  # run <config under configs/main> <directory under runs/main>
  echo "=== $1 -> $ROOT/$2 ($(date +%H:%M))"
  "$PY" -m marketsim.experiments.run "configs/main/$1.yaml" --out "$ROOT/$2" --workers "$W"
}

"$PY" -m marketsim.validation.run prepare --root "$ROOT"     # control graphs and the flat scenario
run e6_shock e6/shock                                       # short runs first: ~10 min
for c in random_graph_1 random_graph_2 flat_inputs; do run "controls/$c" "controls/$c"; done
run e5 e5
for s in 0 25 50; do run "e7_network$s" "e7/network$s"; done
for c in no_stoploss no_rebalancing neither; do run "e6_$c" "e6/channels/$c"; done

echo "=== tables ($(date +%H:%M))"
"$PY" -m marketsim.analysis.study --root "$ROOT" --workers "$W"
"$PY" -m marketsim.validation.run checks --root "$ROOT" --workers "$W"
"$PY" -m marketsim.analysis.figures --root "$ROOT" --out docs/figures
"$PY" -m marketsim.viz.compare --e5 "$ROOT/e5" --e7 "$ROOT/e7" -o runs/replay/experiments.html
echo "=== done ($(date +%H:%M))"
