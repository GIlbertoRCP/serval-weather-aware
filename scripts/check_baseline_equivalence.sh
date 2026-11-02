#!/usr/bin/env bash
# Verify that our patched simulator with the extension disabled (--weather_model none)
# behaves exactly like the original, unmodified Serval code from the upstream commit.
#
# Usage: scripts/check_baseline_equivalence.sh [dataset_dir]   (default: data/quick)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$ROOT/.conda-env/bin/python}"
DATASET="$(cd "${1:-$ROOT/data/quick}" && pwd)"
START=2026-10-20T00:00:00
END=2026-10-20T12:00:00
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 1. Pristine upstream simulator at the commit this project is based on
UPSTREAM_COMMIT=284f83ccf7c360ee80ccb1a9e00969e80c6c522e
curl -sL "https://github.com/ConnectedSystemsLab/Serval/archive/$UPSTREAM_COMMIT.tar.gz" \
    | tar -xz -C "$WORK" "Serval-$UPSTREAM_COMMIT/Sat_Simulator"
UPSTREAM="$WORK/Serval-$UPSTREAM_COMMIT/Sat_Simulator"
# The only change needed to run upstream at all: accept public TLEs newer than the start date
# (identical to the fallback in our util.py).
"$PY" - "$UPSTREAM/util.py" <<'EOF'
import sys
p = sys.argv[1]
s = open(p).read()
s = s.replace("    tle_idx = [np.where(days < f_date)[0][-1]]",
              "    earlier = np.where(days < f_date)[0]\n    tle_idx = [earlier[-1] if len(earlier) else 0]")
open(p, "w").write(s)
EOF

run_sim() {  # $1 = simulator dir, $2 = log file
    local dir="$WORK/run_$(basename "$2" .log)"
    mkdir -p "$dir/log"
    ln -s "$DATASET" "$dir/data"
    ln -s "$1/dependencies" "$dir/dependencies"
    (cd "$dir" && OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
        PYTHONPATH="$1:$ROOT/compat" "$PY" "$1/edgeCompute.py" \
        --satellite_image_mapping_file data/sat_mapping.pkl \
        --ground_station_config_file data/gs_config/planet.json \
        --energy_config_file data/energy_config/30w.json \
        --filter_config_file data/filter_config/30w.json \
        --priority_bw_allocation 0.01 --downlink_bandwidth_scaling 0.25 \
        --start_datetime "$START" --end_datetime "$END" \
        --logging_file "$2" > /dev/null 2>&1)
}

run_sim "$UPSTREAM" "$WORK/upstream.log"
run_sim "$ROOT/Serval/Sat_Simulator" "$WORK/ours.log"

# Compare event streams (ignore object ids/memory addresses that differ between processes)
normalize() { sed -E 's/0x[0-9a-f]+//g' "$1"; }
if cmp -s <(normalize "$WORK/upstream.log") <(normalize "$WORK/ours.log"); then
    echo "PASS: extension disabled == upstream Serval ($(wc -l < "$WORK/ours.log") identical log lines)"
else
    echo "FAIL: logs differ"; diff <(normalize "$WORK/upstream.log") <(normalize "$WORK/ours.log") | head -20
    exit 1
fi
