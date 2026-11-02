#!/usr/bin/env bash
# Reproduce every result: generate the workload, run all arms on identical inputs, analyze.
#
#   scripts/run_all.sh            full experiment (all public Doves, 3 days, ~30-60 min)
#   QUICK=1 scripts/run_all.sh    smoke-size run (20 satellites, 1 day, ~3 min)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$ROOT/.conda-env/bin/python}"
cd "$ROOT"

if [[ "${QUICK:-0}" == "1" ]]; then
    DATASET=data/quick; SATS=20; START=2026-10-20T00:00:00; END=2026-10-21T00:00:00; RESULTS=results/quick
else
    DATASET=data/generated; SATS=200; START=2026-10-20T00:00:00; END=2026-10-23T00:00:00; RESULTS=results
fi
# Downlink scaled to 25% (as the paper does for its distributed-ground-station runs) so the
# constellation is downlink-bound, which is the regime Serval targets. See README Sec. 5.
COMMON=(--downlink_bandwidth_scaling 0.25)

"$PY" tools/generate_workload.py --num_sats "$SATS" --start "$START" --end "$END" \
    --ca_interval 2 --output_dir "$DATASET"
rm -rf "${DATASET:?}/logs" "${DATASET:?}/run"

PIDS=()
run() { scripts/run_arm.sh "$DATASET" "$@" "${COMMON[@]}" & PIDS+=($!); }
wait_all() { for p in "${PIDS[@]}"; do wait "$p"; done; PIDS=(); }  # fails if any arm failed

# Baseline reproduction (paper Fig. 7 and Fig. 8b) and extension arms. All arms share the
# same workload, orbits, ground stations, thresholds and seed; only the cloud side channel differs.
run inorder          "$START" "$END" --filter_config_file data/filter_config/inorder.json
run serval_noweather "$START" "$END" --cloud_threshold -1 2
run serval           "$START" "$END"
run markov           "$START" "$END" --weather_model markov --regime maritime
wait_all
run oracle           "$START" "$END" --weather_model oracle
# Persistence fitted on a disjoint week (2026-10-13..19) by tools/fit_markov.py
run markov_empirical "$START" "$END" --weather_model markov --regime empirical
# Same, but observations age by elapsed time instead of at least one whole orbit
run markov_empirical_cont "$START" "$END" --weather_model markov --regime empirical --decay continuous
# Forecast calibrated on the same training week, fused with the Markov prior by Bayes' rule
CAL=(--forecast_calibration "$ROOT/data/public/forecast_calibration.json")
run markov_empirical_cal "$START" "$END" --weather_model markov --regime empirical "${CAL[@]}"
wait_all
run markov_empirical_cal_cont "$START" "$END" --weather_model markov --regime empirical "${CAL[@]}" \
    --decay continuous
# Sensitivity of the extension to the climate-regime prior (Methods Sec. 2)
run markov_arid      "$START" "$END" --weather_model markov --regime arid
run markov_tropical  "$START" "$END" --weather_model markov --regime tropical
wait_all
# Cells without their own observation borrow an adjacent cell's, weighted by rho_neighbor
NBR=(--weather_cells_file "$ROOT/data/public/weather_california.csv")
run markov_empirical_nbr "$START" "$END" --weather_model markov --regime empirical "${NBR[@]}"
run markov_empirical_cont_nbr "$START" "$END" --weather_model markov --regime empirical "${NBR[@]}" \
    --decay continuous
wait_all
# Scenario C (proposal Sec. 4.2): sweep cloud persistence p_cc 0.1..0.7, fitted p_ss held fixed
for pcc in 10 20 30 40 50 60 70; do
    run "sweep_pcc$pcc" "$START" "$END" --weather_model markov --regime empirical --p_cc "0.$((pcc / 10))"
    if (( pcc == 40 )); then wait_all; fi
done
wait_all

# Scenario A / paper Fig. 8: latency vs. high-priority query arrival rate. Each workload is
# the main one with a different --fire_share (fire images nest, everything else identical).
RATE_DATASETS=("$DATASET")
for share in 0.02 0.15 0.5; do
    ds="$DATASET-rate/fire${share#0.}"
    "$PY" tools/generate_workload.py --num_sats "$SATS" --start "$START" --end "$END" \
        --ca_interval 2 --fire_share "$share" --output_dir "$ds"
    rm -rf "${ds:?}/logs" "${ds:?}/run"
    RATE_DATASETS+=("$ds")
done
for ds in "${RATE_DATASETS[@]:1}"; do
    scripts/run_arm.sh "$ds" inorder "$START" "$END" --filter_config_file data/filter_config/inorder.json \
        "${COMMON[@]}" & PIDS+=($!)
    scripts/run_arm.sh "$ds" serval "$START" "$END" "${COMMON[@]}" & PIDS+=($!)
    if [[ "$ds" == *fire15 ]]; then wait_all; fi
done
wait_all

"$PY" analysis/analyze.py --dataset "$DATASET" --output "$RESULTS"
"$PY" analysis/plot_arrival_rate.py --datasets "${RATE_DATASETS[@]}" --output "$RESULTS"
echo "Results written to $RESULTS/"
