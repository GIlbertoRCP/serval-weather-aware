#!/usr/bin/env bash
# Repeat the key arms with different workload seeds (forest cells and fire labels; orbits
# and weather are fixed) and report mean +- std across seeds. Seed 42 is the main run from
# scripts/run_all.sh, so run that first.
#
#   scripts/run_seeds.sh            seeds 1-4 on the full workload (~25 min with JOBS=6 on 12 cores)
#   QUICK=1 scripts/run_seeds.sh    20 satellites, 1 day
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$ROOT/.conda-env/bin/python}"
JOBS="${JOBS:-6}"
SEEDS="${SEEDS:-1 2 3 4}"
cd "$ROOT"

if [[ "${QUICK:-0}" == "1" ]]; then
    MAIN=data/quick; ROOT_DS=data/quick-seeds; SATS=20; START=2026-10-20T00:00:00; END=2026-10-21T00:00:00
    RESULTS=results/quick
else
    MAIN=data/generated; ROOT_DS=data/generated-seeds; SATS=200; START=2026-10-20T00:00:00; END=2026-10-23T00:00:00
    RESULTS=results
fi
COMMON=(--downlink_bandwidth_scaling 0.25)
EMP=(--weather_model markov --regime empirical)
NBR=(--weather_cells_file "$ROOT/data/public/weather_california.csv")

# arm name | flags (same as scripts/run_all.sh)
ARMS=(
    "serval_noweather|--cloud_threshold -1 2"
    "serval|"
    "oracle|--weather_model oracle"
    "markov_empirical|${EMP[*]}"
    "markov_empirical_nbr|${EMP[*]} ${NBR[*]}"
    "markov_empirical_cont_nbr|${EMP[*]} ${NBR[*]} --decay continuous"
)

PIDS=()
wait_all() { for p in ${PIDS[@]+"${PIDS[@]}"}; do wait "$p"; done; PIDS=(); }  # fails if any arm failed

DATASETS=("$MAIN")
for seed in $SEEDS; do
    ds="$ROOT_DS/seed$seed"
    "$PY" tools/generate_workload.py --num_sats "$SATS" --start "$START" --end "$END" \
        --ca_interval 2 --seed "$seed" --output_dir "$ds"
    rm -rf "${ds:?}/logs" "${ds:?}/run"
    DATASETS+=("$ds")
    for spec in "${ARMS[@]}"; do
        arm="${spec%%|*}"
        read -r -a flags <<< "${spec#*|}"
        scripts/run_arm.sh "$ds" "$arm" "$START" "$END" ${flags[@]+"${flags[@]}"} "${COMMON[@]}" &
        PIDS+=($!)
        if (( ${#PIDS[@]} >= JOBS )); then wait_all; fi
    done
done
wait_all

for ds in "${DATASETS[@]}"; do
    "$PY" analysis/analyze.py --dataset "$ds" --output "$ds/analysis" > /dev/null
done
"$PY" analysis/seed_summary.py --dirs "${DATASETS[@]/%//analysis}" --output "$RESULTS"
