#!/usr/bin/env bash
# Run one experimental arm of Serval's simulator.
# Usage: scripts/run_arm.sh <dataset_dir> <arm_name> <start> <end> [extra edgeCompute.py flags...]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$ROOT/.conda-env/bin/python}"
DATASET="$(cd "$1" && pwd)"
ARM="$2"
START="$3"
END="$4"
shift 4

SIM="$ROOT/Serval/Sat_Simulator"
LOG_DIR="$DATASET/logs"
# Serval opens data/..., dependencies/... and log/... relative to the working directory,
# so each arm runs in its own folder (arms can then run in parallel).
RUN_DIR="$DATASET/run/$ARM"
mkdir -p "$LOG_DIR" "$RUN_DIR/log"
ln -sfn "$DATASET" "$RUN_DIR/data"
ln -sfn "$SIM/dependencies" "$RUN_DIR/dependencies"

# One math thread per simulator: numpy/BLAS spin-waiting threads make parallel arms crawl
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
cd "$RUN_DIR"
echo "[$ARM] running $START -> $END $*"
PYTHONPATH="$SIM:$ROOT/compat" "$PY" "$SIM/edgeCompute.py" \
    --satellite_image_mapping_file data/sat_mapping.pkl \
    --ground_station_config_file data/gs_config/planet.json \
    --energy_config_file data/energy_config/30w.json \
    --filter_config_file data/filter_config/30w.json \
    --priority_bw_allocation 0.01 \
    --image_cells_file data/image_cells.csv \
    --start_datetime "$START" \
    --end_datetime "$END" \
    --logging_file "$LOG_DIR/$ARM.log" \
    "$@" 2>&1 | grep -v -e "Timestep took" -e "Creating maps" > "$LOG_DIR/$ARM.stdout"
tail -n 2 "$LOG_DIR/$ARM.stdout"
