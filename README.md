# Weather-Aware Serval

CSCI 4750/5750 Type B project (reproduction + extension) by Gilberto Romero-Cano, Corey Greene and JJ Wagner.

This repository reproduces Serval (Tao et al., USENIX NSDI 2024) and adds Markov cloud tracking to its weather side channel. The upstream Serval code ([ConnectedSystemsLab/Serval](https://github.com/ConnectedSystemsLab/Serval), commit `284f83c`) is in `Serval/`, together with our changes. The method, experiments and results are described in the report.

Serval's original inputs (Planet image metadata and configs) are not public. `tools/generate_workload.py` regenerates them in the same file formats from public data: CelesTrak TLEs and Open-Meteo weather. That data is already cached in `data/public/`, so running the experiments needs no network access.

---

## 1. Requirements

- macOS or Linux
- [conda](https://docs.conda.io/) (Miniconda is enough)
- about 2 GB of disk and 8 GB of RAM; 4 or more CPU cores recommended

The Python dependencies (Python 3.9, numpy, pandas, matplotlib, skyfield, ...) are listed in `requirements.txt` and installed by the step below.

## 2. Setup

```bash
git clone https://github.com/GIlbertoRCP/serval-weather-aware.git
cd serval-weather-aware
conda env create -p .conda-env -f environment.yml
```

This creates the environment in `./.conda-env`, and every script uses `./.conda-env/bin/python` automatically, so there's no need to activate it. 

## 3. Running the code

Run all commands from the repository root.

**Step 1: smoke test (about 2 minutes).** Runs a small version of everything (20 satellites, 1 simulated day) to confirm the setup works.

```bash
QUICK=1 scripts/run_all.sh
```

Output goes to `results/quick/`.

**Step 2: full experiment (about 60–80 minutes).** Generates the workload (89 satellites, 3 simulated days), runs every configuration, and writes all tables and figures to `results/`.

```bash
scripts/run_all.sh
```

**Step 3: repeat on more seeds (about 25 minutes, optional).** Reruns the main configurations on workload seeds 1–4 and reports the mean ± standard deviation over all 5 seeds. Run it after Step 2.

```bash
scripts/run_seeds.sh
```

**Step 4: baseline equivalence check (a few minutes, needs internet).** Downloads unmodified upstream Serval and confirms that our copy, with the extension turned off, produces an identical event log. Run it after Step 1, since it uses the `data/quick/` workload.

```bash
scripts/check_baseline_equivalence.sh
```

## 4. Reproducing the main results and figures

Every number and figure in the report comes from these files:

| Report item | File (in `results/`) | Produced by |
|---|---|---|
| Fire-image latency, Serval vs. in-order (paper Fig. 7) | `fig7_reproduction.png` | Step 2 |
| Latency vs. arrival rate (paper Fig. 8) | `fig8_arrival_rate.png`, `fig8_arrival_rate.csv` | Step 2 |
| Serval with vs. without the weather side channel (paper Fig. 8b) | `fig8b_reproduction.png` | Step 2 |
| Main results table: latency, delivered fires, onboard compute and energy | `summary.csv` | Step 2 |
| Cloud-decision accuracy and clear images lost | `cloud_decisions.csv` | Step 2 |
| Extension latency CDF and energy | `extension_latency_cdf.png`, `extension_compute_energy.png` | Step 2 |
| Scenario B (climate regimes) | `regime_sweep.csv` | Step 2 |
| Scenario C (persistence sweep) | `scenario_c_persistence_sweep.csv`, `scenario_c_pareto.png` | Step 2 |
| Results across 5 seeds | `seed_summary.csv`, `seed_paired.csv` | Step 3 |

Inputs are fixed (cached weather, fixed seeds), so reruns give the same numbers.
