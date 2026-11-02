# Weather-Aware Satellite Scheduling in Serval: Reproducing USENIX NSDI '24 Query Bifurcation and Extending Constellation Schedulers with Dynamic Markov Cloud Tracking

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Reproducibility](https://img.shields.io/badge/NSDI%20'24-Artifact%20Reproduction-success.svg)](https://github.com/ConnectedSystemsLab/Serval)

Course project for **CSCI 4750/5750 Satellite Communication & Networking**, University of Colorado Denver.  
**Track:** Type B — Reproduction + Extension  
**Authors:**
- Gilberto Romero-Cano (`gilberto.romero-cano@ucdenver.edu`)
- Corey Greene (`corey.greene@ucdenver.edu`)
- JJ Wagner (`john.wagner@ucdenver.edu`)

---

## 1. Overview & Project Description

This repository contains the reproduction artifact and weather-aware scheduling extension of the USENIX NSDI '24 paper:
> **"Known Knowns and Unknowns: Near-realtime Earth Observation Via Query Bifurcation in Serval"**  
> *Bill Tao, Om Chabra, Ishani Janveja, Indranil Gupta, Deepak Vasisht (UIUC)*  
> Artifact: [ConnectedSystemsLab/Serval](https://github.com/ConnectedSystemsLab/Serval)

### Baseline System
Serval splits LEO satellite Earth Observation tasks across space and ground through **Query Bifurcation**:
- **On-Orbit Filtering:** Runs low-power anomaly detectors aboard ~200 PlanetScope CubeSats in under 1.0 second (5–10 W).
- **Prioritized Metadata Downlink:** Telemeters sub-1 KB bounding boxes during intermittent 5–10 minute ground passes, slashing dynamic emergency latency from 71.7 hours down to ~2 minutes.
- **Ground Verification:** Transmits full raw optical images during off-peak contact passes for validation on high-performance ground clusters.

### Our Extension: Dynamic Markov Weather-Aware Preemption
Baseline Serval assumes a 100% clear-sky Earth. However, over 60% of the planet is clouded at any given moment. Under overcast skies, satellites blindly burn edge accelerator power analyzing white/gray pixels that yield zero alerts. 

Our extension introduces:
1. **Atmospheric Layer (`weather_engine.py`):** Models target ground cells as a Two-State Discrete-Time Markov Chain (DTMC): State 0 (Clear Sky) and State 1 (Cloud-Occluded), parameterized with empirical MODIS and EarthEnv climatology data across Arid, Maritime, and Tropical regimes.
2. **Early-Abort Hook (`sat_scheduler.py`):** Intercepts the dispatch loop prior to accelerator wake-up. If occluded, the job is aborted early, conserving 5–10 W of active compute draw, immediately advancing the queue head-of-line, and rescheduling or offloading the task to microwave Synthetic Aperture Radar (SAR).

---

## 2. Repository Structure

```text
.
├── README.md                      # This documentation and reproduction guide
├── requirements.txt               # Pinned Python package dependencies
├── environment.yml                # Conda environment definition
├── data/
│   ├── ephemeris/                 # SGP4 ephemeris orbits (~200 PlanetScope Doves)
│   ├── ground_stations/           # Ground station coordinates and contact masks
│   ├── traces/                    # Serval 10M-image evaluation workload trace
│   └── weather/                   # MODIS / EarthEnv empirical transition priors
├── src/
│   ├── Sat_Simulator/             # Discrete-event constellation simulator
│   │   ├── sat_scheduler.py       # Core scheduler patched with early-abort hooks
│   │   ├── weather_engine.py      # Extension: Two-State DTMC weather model
│   │   ├── task.py                # Task structure with TTL and radar offload flags
│   │   └── orbit_engine.py        # SGP4 propagation and access-pass calculator
│   └── utils/
│       ├── metrics_tracker.py     # Energy (Joules) and Latency CDF logger
│       └── config.py              # Configuration flags (enable_cloud_model)
├── scripts/
│   ├── run_baseline.sh            # Runs unpatched Serval clear-sky reproduction
│   ├── run_three_arm_eval.sh      # Executes Baseline, Markov, and Oracle arms
│   └── run_parameter_sweeps.sh    # Executes climate regime and cloud ratio sweeps
├── plotting/
│   ├── plot_fig7_cdf.py           # Replicates Baseline NSDI '24 Figure 7
│   ├── plot_fig8_load.py          # Replicates Baseline NSDI '24 Figure 8
│   └── plot_extension_metrics.py  # Generates Energy-Latency Pareto & CDF curves
└── results/
    ├── baseline/                  # Output logs and raw simulation CSVs for baseline
    ├── extension/                 # Logs for Markov Early-Abort and Oracle runs
    └── figures/                   # Generated evaluation plots (PNG/PDF)
```

---

## 3. System Requirements & Environment Setup

### Prerequisites
- **Operating System:** Linux (Ubuntu 20.04/22.04 LTS recommended) or macOS (x86_64 / Apple Silicon). Windows users should use WSL2.
- **Python:** Version 3.9, 3.10, or 3.11.
- **Hardware:** Minimum 8 GB RAM, 10 GB disk space (for trace logs and SGP4 propagation caches).

### Setup via Conda (Recommended)

```bash
# 1. Clone this repository
git clone https://github.com/GIlbertoRCP/serval-weather-aware.git
cd serval-weather-aware

# 2. Create and activate the conda environment
conda create -n serval-sim python=3.10 -y
conda activate serval-sim

# 3. Install core dependencies
pip install -r requirements.txt
```

### Setup via Standard Python Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Dependency List (`requirements.txt`)
```text
numpy>=1.22.0
scipy>=1.8.0
sgp4>=2.20
pandas>=1.4.0
matplotlib>=3.5.0
seaborn>=0.11.2
pytz>=2022.1
tqdm>=4.64.0
```

---

## 4. Step-by-Step Execution & Reproduction Guide

### Step 4.1: Verify Baseline Simulation (NSDI '24 Replication)
Verify that the unmodified discrete-event simulator compiles and runs with `enable_cloud_model = False`.

```bash
# Run baseline validation trace (~2 minutes)
python src/Sat_Simulator/sat_scheduler.py \
    --config config/baseline_clearsky.json \
    --enable_cloud_model False \
    --output_dir results/baseline/
```

Generate the reproduced NSDI '24 baseline figures:
```bash
python plotting/plot_fig7_cdf.py --input results/baseline/run_log.csv --output results/figures/reproduced_fig7.png
python plotting/plot_fig8_load.py --input results/baseline/run_log.csv --output results/figures/reproduced_fig8.png
```
*Output plots will be saved to `results/figures/` for comparison with Figure 7 and Figure 8 in the original paper.*

---

### Step 4.2: Run the Three-Arm Comparative Benchmark
Execute all three experimental arms across identical SGP4 orbital passes and query traces:

1. **Arm 1 (Unmodified Baseline):** Runs Serval under cloud cover without weather checks (wasting accelerator power on white pixels).
2. **Arm 2 (Markov Early-Abort Policy):** Runs our weather-aware scheduler with pre-inference aborts and radar offloading.
3. **Arm 3 (Cloud Oracle):** Runs an idealized scheduler with clairvoyant, zero-overhead cloud knowledge.

Run all three arms using the automated driver script:
```bash
chmod +x scripts/run_three_arm_eval.sh
./scripts/run_three_arm_eval.sh
```

Or execute them individually:
```bash
# Arm 1: Unmodified Baseline under cloud cover
python src/Sat_Simulator/sat_scheduler.py --mode baseline_with_clouds --regime Maritime --output results/extension/arm1_baseline.csv

# Arm 2: Proposed Markov Early-Abort Policy
python src/Sat_Simulator/sat_scheduler.py --mode markov_abort --regime Maritime --output results/extension/arm2_markov.csv

# Arm 3: Clairvoyant Upper-Bound Oracle
python src/Sat_Simulator/sat_scheduler.py --mode oracle --regime Maritime --output results/extension/arm3_oracle.csv
```

---

### Step 4.3: Run Parameter Sensitivity Sweeps
Evaluate energy conservation and turnaround latency across climate regimes:

```bash
# Run Climate Sensitivity: Arid vs. Maritime vs. Tropical
python scripts/run_parameter_sweeps.py --sweep climate_regimes --output results/extension/regime_sweep.csv

# Run Global Cloud Cover Percentage Sweep (10% to 70%)
python scripts/run_parameter_sweeps.py --sweep cloud_fraction --range 0.10 0.70 0.10 --output results/extension/cloud_sweep.csv
```

---

## 5. Generating Evaluation Figures and Metrics

To reproduce the project report and presentation figures, run:

```bash
python plotting/plot_extension_metrics.py \
    --arm1 results/extension/arm1_baseline.csv \
    --arm2 results/extension/arm2_markov.csv \
    --arm3 results/extension/arm3_oracle.csv \
    --output_dir results/figures/
```

### Produced Output Artifacts
1. **`results/figures/energy_conservation_bar.png`:** Total energy (Joules / Watt-hours) conserved across the 200-satellite constellation over 7 simulated days.
2. **`results/figures/turnaround_latency_cdf.png`:** Empirical CDF of end-to-end alert latency comparing Baseline, Markov Early-Abort, and Oracle.
3. **`results/figures/queue_backlog_reduction.png`:** Compute queue waiting times and satellite buffer memory reduction achieved by preempting occluded tasks early.
4. **`results/figures/radar_offload_summary.txt`:** Text report logging total dynamic emergency tasks successfully rerouted to SAR radar upon 3-pass TTL expiration.

---

## 6. Configuration & Modifying Extension Parameters

All weather and scheduler thresholds are managed via `src/utils/config.py` or command-line overrides:

| Parameter | Default | Description |
| :--- | :--- | :--- |
| `enable_cloud_model` | `True` | Master toggle: when `False`, runs 100% identical Serval baseline |
| `p_cloud_cloud_tropical` | `0.85` | Markov persistence probability for tropical cloud-to-cloud |
| `p_cloud_cloud_maritime` | `0.65` | Markov persistence probability for maritime cloud-to-cloud |
| `p_cloud_cloud_arid` | `0.15` | Markov persistence probability for arid cloud-to-cloud |
| `p_accel_draw_watts` | `7.5` | Average active power draw of the onboard edge accelerator |
| `t_inference_sec` | `0.8` | Average execution duration of spatial anomaly filter |
| `query_ttl_passes` | `3` | Maximum occluded passes before dynamic tasks offload to SAR radar |

---

## 7. Troubleshooting & Common Issues

- **`ModuleNotFoundError: No module named 'Sat_Simulator'`:**  
  Set your `PYTHONPATH` to include the project root:
  ```bash
  export PYTHONPATH=$(pwd):$PYTHONPATH
  ```
- **SGP4 Cache Mismatch:**  
  If changing constellation altitude parameters, delete the precomputed ephemeris cache in `data/ephemeris/*.cache` to force orbital re-propagation.
- **Deterministic Replication:**  
  All random state transitions use fixed seeds initialized via `--seed <int>` (default: `42`).

---

## 8. License & Acknowledgments

This project is licensed under the MIT License. The constellation simulation engine is derived from the open-source USENIX NSDI '24 Serval artifact by Tao et al. ([UIUC Connected Systems Lab](https://github.com/ConnectedSystemsLab/Serval)). Climatological distributions are derived from NASA MODIS and EarthEnv datasets.