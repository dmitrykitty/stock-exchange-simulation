#!/usr/bin/env bash
# Main study (docs/plans/main_study_protocol.md): every run, then the answer tables, the F8 checks, the report
# figures and the experiment comparison page. Interrupt it at any moment; started again, it continues where it
# stopped (complete runs are kept, tables are recomputed only for new runs).
#
#   scripts/run_main_study.sh                   # the study: runs/main, figures to docs/figures (~6 h)
#   SMOKE=1 ROOT=runs/smoke scripts/run_main_study.sh
#                                               # dry run: one seed per cell, every config at once (~20 min),
#                                               # figures and the comparison page under runs/smoke
#
# Environment: PYTHON (.venv/bin/python), WORKERS (10), ROOT (runs/main), FIGURES (docs/figures, or ROOT/figures
# with SMOKE), COMPARE (runs/replay/experiments.html, or ROOT/experiments.html with SMOKE). The control configs
# read their graphs from runs/main/controls/graphs, which the first step writes whatever ROOT is.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-.venv/bin/python}
W=${WORKERS:-10}
ROOT=${ROOT:-runs/main}
SMOKE=${SMOKE:-}
if [ -n "$SMOKE" ]; then
  FIGURES=${FIGURES:-$ROOT/figures}
  COMPARE=${COMPARE:-$ROOT/experiments.html}
else
  FIGURES=${FIGURES:-docs/figures}
  COMPARE=${COMPARE:-runs/replay/experiments.html}
fi
pids=()

run() {  # run <config under configs/main> <directory under ROOT> <seeds in the config>
  local args=("configs/main/$1.yaml" --out "$ROOT/$2" --workers "$W")
  if [ -n "$SMOKE" ]; then
    # Shard 1 of <seeds> keeps the first seed of every cell; these runs are also the first runs of the study.
    mkdir -p "$ROOT/$2"
    "$PY" -m marketsim.experiments.run "${args[@]}" --shard "1/$3" > "$ROOT/$2/smoke.log" 2>&1 &
    pids+=($!)
  else
    echo "=== $1 -> $ROOT/$2 ($(date +%H:%M))"
    "$PY" -m marketsim.experiments.run "${args[@]}"
  fi
}

"$PY" -m marketsim.validation.run prepare                  # control graphs and the flat scenario
run e6_shock e6/shock 30                                    # short runs first
for c in random_graph_1 random_graph_2 flat_inputs; do run "controls/$c" "controls/$c" 10; done
run e5 e5 30
for s in 0 25 50; do run "e7_network$s" "e7/network$s" 30; done
for c in no_stoploss no_rebalancing neither; do run "e6_$c" "e6/channels/$c" 30; done
if [ -n "$SMOKE" ]; then
  echo "=== ${#pids[@]} configs running at once ($(date +%H:%M)); logs: $ROOT/**/smoke.log"
  failed=0
  for pid in "${pids[@]}"; do wait "$pid" || failed=1; done
  if [ "$failed" -ne 0 ]; then
    echo "a config failed; see the smoke.log files" >&2
    exit 1
  fi
fi

echo "=== tables ($(date +%H:%M))"
"$PY" -m marketsim.analysis.study --root "$ROOT" --workers "$W"
"$PY" -m marketsim.validation.run checks --root "$ROOT" --workers "$W"
"$PY" -m marketsim.analysis.figures --root "$ROOT" --out "$FIGURES"
"$PY" -m marketsim.viz.compare --e5 "$ROOT/e5" --e7 "$ROOT/e7" -o "$COMPARE"
echo "=== done ($(date +%H:%M))"
