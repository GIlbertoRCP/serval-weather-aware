# Weather-Aware Satellite Scheduling in Serval: Reproducing USENIX NSDI '24 Query Bifurcation and Extending It with Markov Cloud Tracking

Course project for **CSCI 4750/5750 Satellite Communication & Networking**, University of Colorado Denver.
**Track:** Type B — Reproduction + Extension
**Authors:** Gilberto Romero-Cano (`gilberto.romero-cano@ucdenver.edu`), Corey Greene (`corey.greene@ucdenver.edu`), JJ Wagner (`john.wagner@ucdenver.edu`)

Baseline paper: B. Tao, O. Chabra, I. Janveja, I. Gupta, D. Vasisht, *"Known Knowns and Unknowns: Near-realtime Earth Observation Via Query Bifurcation in Serval,"* USENIX NSDI 2024.
Baseline code: [ConnectedSystemsLab/Serval](https://github.com/ConnectedSystemsLab/Serval) at commit `284f83c`, vendored in [`Serval/`](Serval/).

---

## TL;DR for the TA

```bash
conda env create -p .conda-env -f environment.yml   # Python 3.9 environment in ./.conda-env
QUICK=1 scripts/run_all.sh                          # ~1 minute smoke test -> results/quick/
scripts/run_all.sh                                  # full experiment       -> results/
scripts/check_baseline_equivalence.sh               # proves the extension-off code == upstream Serval
```

Every number and figure in our report comes from `results/` after `scripts/run_all.sh`.

---

## 1. What this project does

**Serval (baseline).** Serval splits each Earth-observation query into *glacial* filters (slow-changing, e.g. "is this California?", "is this forest?") that the ground precomputes from older imagery, and *dynamic* filters (e.g. cloud, fire) that run on the satellite. Images that pass every filter of a latency-sensitive query jump ahead in the downlink queue. Serval also uses weather forecasts as a side channel: if forecast clear-sky probability is below 0.2 the image is treated as cloudy and de-prioritized, above 0.8 it is treated as clear, and only in between does the satellite run its cloud-detection network (paper Sec. 3.3).

**Our extension.** Serval's forecast check is stateless: each image is judged from one forecast, ignoring what the constellation has *already observed* over the same place. We add a weather engine ([`Serval/Sat_Simulator/weather_engine.py`](Serval/Sat_Simulator/weather_engine.py)) that

1. remembers, per weather cell, the most recent observed cloud state (from images whose cloud status became known onboard or after reaching a ground station);
2. propagates that state forward with a two-state Markov chain (clear/cloudy, one step = one ~90-minute orbit), parameterized by a climate-regime prior (arid / maritime / tropical, as in the proposal) or by persistence fitted from a separate week of weather data;
3. fuses it with the forecast, weighting the observation by how much the chain still remembers it after *k* orbits (λᵏ, where λ is the chain's second eigenvalue);
4. feeds the fused probability into Serval's **unchanged** 0.2 / 0.8 threshold logic; and
5. after a forest location has been judged cloudy on `TTL = 3` consecutive passes, emits a ~1 KB high-priority "SAR offload" alert so operators can re-task a cloud-penetrating radar satellite.

With `--weather_model none` (the default) the simulator is byte-for-byte upstream Serval; `scripts/check_baseline_equivalence.sh` checks this.

**Relation to our proposal (Milestone 2).** The proposal described baseline Serval as cloud-unaware ("100% clear sky"). The published system in fact already has a forecast side channel (paper Sec. 3.3, Fig. 8b), so we evaluate against two baselines: `serval_noweather` is the cloud-unaware baseline the proposal describes (the cloud filter runs on every image), and `serval` is Serval as published. The proposal's pieces map onto the code as follows:

| Proposal | Here |
|---|---|
| Two-state DTMC, arid / maritime / tropical regimes (Sec. 3.1) | `weather_engine.py`, `--regime arid|maritime|tropical` (p_cc 0.15 / 0.65 / 0.85; arid p_ss 0.925) |
| Pre-inference early-abort hook (Sec. 3.2) | Fused clear-sky probability below 0.2 skips the cloud and fire filters (Serval's own threshold logic) |
| Radar offloading for emergencies, hard TTL of 3 passes (Sec. 3.3, Risk 3) | `--query_ttl_passes 3`: ~1 KB high-priority SAR offload alert |
| Re-queue background queries for a clear pass (Sec. 3.3) | Skipped images stay in Serval's low-priority downlink queue; no re-capture is modeled |
| Metric 1: energy conserved (Sec. 4.1) | `onboard_compute_J` in `summary.csv`; Wh saved in `scenario_c_persistence_sweep.csv` |
| Metric 2: turnaround latency CDF | `fig7_reproduction.png`, `fig8b_reproduction.png`, `extension_latency_cdf.png` |
| Metric 3: queue backlog and drop rate | Not measured separately; `clear_images_lost_pct` in `cloud_decisions.csv` is the drop rate of usable images |
| Scenario A: baseline reproduction | `inorder`, `serval_noweather`, `serval` arms |
| Scenario B: regional climate sweep | `markov`, `markov_arid`, `markov_tropical` arms |
| Scenario C: persistence sweep, 10% to 70% | `sweep_pcc10` ... `sweep_pcc70` arms, `scenario_c_pareto.png` |
| 200 satellites, 7-day trace | 89 satellites (every Planet Dove with a public TLE), 3 days, to keep the full run under an hour |

---

## 2. Why the inputs are regenerated (data availability)

Serval's simulator reads a `data/` folder (Planet image metadata for ~10M images, ground-station/energy/filter configs, historical TLEs) that is **not in the public repository**: it is derived from Planet's commercial archive. We therefore regenerate every input **in the exact file formats the simulator expects**, from public sources:

| Input | Paper | This project |
|---|---|---|
| Satellite orbits | Historical Planet TLEs, Jul 2021 | Current Planet Dove TLEs from CelesTrak ([`data/public/planet_tle.txt`](data/public/planet_tle.txt)) |
| Image trace | 10M real Planet image footprints, 151 sats, 20 days | Frames generated along each satellite's sunlit ground track with Serval's own `Image` class; dense over California, sparse elsewhere (each background frame carries the data volume of the real frames it stands for, ~1 TB/sat/day as in the paper) |
| Cloud ground truth | Cloud-filter output on downloaded images | ERA5 reanalysis cloud cover, 0.5° California grid ([Open-Meteo archive](https://open-meteo.com/)) |
| Weather side channel | Open-Meteo forecasts | Archived Open-Meteo forecasts for the same grid and hours |
| Forest / fire labels | USDA forest map, hand-labelled fires | Seeded synthetic labels: 70.6% of California cells forested (Table 3 ratio); fire in 5% of clear forest frames (paper: ~1%, raised so 3 simulated days give enough fire images for a CDF) |
| Ground stations | 12 Planet stations × 4 antennas | 12 public Planet/KSAT-style polar and mid-latitude sites × 4 antennas |
| Filter runtimes / power | Tables 2 and 4 | Same values (Jetson AGX Orin 30 W mode) |

Consequently we reproduce the paper's **qualitative trends**, not its absolute numbers. All weather data is cached in the repo, so no network access is needed to rerun the experiments.

---

## 3. Repository layout

```text
.
├── README.md
├── environment.yml, requirements.txt   Python 3.9 environment
├── Serval/                             Upstream Serval (commit 284f83c) + our changes
│   └── Sat_Simulator/
│       ├── edgeCompute.py              Simulator entry point (+ --weather_model/--regime/--query_ttl_passes)
│       ├── weather_engine.py           EXTENSION: Markov cloud tracking + SAR offload alerts
│       ├── util.py                     (+ fallback for public TLEs newer than the start date)
│       └── src/
│           ├── planetSatellite.py      (+ hook that applies the weather engine at capture time)
│           └── planetGroundStation.py  (+ hook that records observed cloud state at the ground)
├── compat/fastlogging.py               Pure-Python stand-in for the `fastlogging` C extension
├── tools/
│   ├── fetch_weather.py                Downloads the Open-Meteo cloud data (already cached)
│   ├── fit_markov.py                   Fits p_cc / p_ss from a separate week of ERA5 data
│   └── generate_workload.py            Builds data/generated/* in Serval's input formats
├── scripts/
│   ├── run_arm.sh                      Runs one experimental arm
│   ├── run_all.sh                      Generates the workload, runs all arms, analyzes
│   └── check_baseline_equivalence.sh   Extension-off simulator vs pristine upstream
├── analysis/analyze.py                 Parses logs -> tables and figures in results/
├── data/public/                        Cached public inputs (TLEs, weather)
├── data/generated/                     Generated workload + per-arm logs (created by run_all.sh)
└── results/                            Figures and CSV tables (created by run_all.sh)
```

All changes to upstream are shown by `git diff 2731f1d -- Serval/` (commit 2731f1d is the unmodified upstream copy).

---

## 4. Setup

Requirements: macOS or Linux, [conda](https://docs.conda.io/) (Miniconda is enough), ~2 GB disk, 8 GB RAM.

```bash
git clone https://github.com/GIlbertoRCP/serval-weather-aware.git
cd serval-weather-aware
conda env create -p .conda-env -f environment.yml
```

The scripts use `./.conda-env/bin/python` by default. To use another interpreter (Python 3.9 with `requirements.txt` installed), prefix commands with `PYTHON=/path/to/python`.

---

## 5. Reproducing the results

### 5.1 One command

```bash
scripts/run_all.sh
```

This (1) generates the workload into `data/generated/` (all public Planet Doves, 2026-10-20 to 2026-10-23, seed 42), (2) runs every arm below on identical inputs, four at a time, and (3) writes all tables and figures to `results/`. Expect roughly 40–60 minutes on a laptop. `QUICK=1 scripts/run_all.sh` runs a 20-satellite, 1-day version in about a minute (outputs in `results/quick/`).

### 5.2 Experimental arms

All arms share the workload, orbits, ground stations, 1% compute/network budget for the query (paper Sec. 4.3), and 25% downlink scaling (the paper applies the same scaling in its distributed-ground-station runs; it keeps the constellation downlink-bound, the regime Serval is designed for). Only the treatment of clouds differs.

| Arm | What it is | Flags |
|---|---|---|
| `inorder` | No prioritization: images downlinked in capture order | `--filter_config_file data/filter_config/inorder.json` |
| `serval_noweather` | Serval, weather side channel disabled (always run the cloud filter) | `--cloud_threshold -1 2` |
| `serval` | Serval as published (forecast side channel, 0.2/0.8) | *(none)* |
| `markov` | Serval + our Markov cloud tracking, maritime prior | `--weather_model markov --regime maritime` |
| `oracle` | Side channel replaced by ground truth (upper bound) | `--weather_model oracle` |
| `markov_empirical` | Markov tracking with persistence fitted by `tools/fit_markov.py` on a disjoint week (2026-10-13 to 19) | `--weather_model markov --regime empirical` |
| `markov_arid`, `markov_tropical` | Scenario B: sensitivity to the regime prior | `--regime arid` / `--regime tropical` |
| `sweep_pcc10` ... `sweep_pcc70` | Scenario C: cloud persistence p_cc = 0.1 ... 0.7 (fitted p_ss held fixed) | `--regime empirical --p_cc 0.1` ... |

To run one arm by hand:
```bash
scripts/run_arm.sh data/generated markov 2026-10-20T00:00:00 2026-10-23T00:00:00 \
    --downlink_bandwidth_scaling 0.25 --weather_model markov --regime maritime
```

### 5.3 Outputs

| File | Shows | Paper counterpart |
|---|---|---|
| `results/fig7_reproduction.png` | High-priority (fire) latency CDF, in-order vs Serval | Fig. 7 |
| `results/fig8b_reproduction.png` | Serval with vs without the weather side channel | Fig. 8b |
| `results/summary.csv` | Per arm: fire latency (median/p90), delivered counts, onboard compute (s, J), SAR alerts | Sec. 6.1, Table 5 discussion |
| `results/cloud_decisions.csv` | Per arm: how forest images' cloud decisions compare with ground truth (skipped correctly, skipped wrongly, ran filter) | Table 5 |
| `results/extension_latency_cdf.png` | Fire latency CDF: no-weather vs Serval vs Markov vs Oracle | (extension) |
| `results/extension_compute_energy.png` | Onboard filter energy per arm | (extension) |
| `results/regime_sweep.csv` | Markov arm under arid / tropical / fitted priors (Scenario B) | (extension) |
| `results/scenario_c_persistence_sweep.csv`, `results/scenario_c_pareto.png` | Energy saved vs. clear images lost and fire latency, p_cc 0.1 to 0.7 | (extension, Scenario C) |

Images not delivered by the end of the run are counted with their latency so far (right-censored), so a CDF never hides undelivered images.

### 5.4 Baseline equivalence check

```bash
scripts/check_baseline_equivalence.sh
```
Downloads pristine upstream `Sat_Simulator` at commit `284f83c`, runs it and our patched copy (extension disabled) on the same 12-hour workload, and requires the two event logs to be identical. Needs network access for the download.

---

## 6. Results (full run: 89 satellites, 2026-10-20 to 2026-10-23, 183,196 frames, 45 fire images)

From `results/summary.csv` and `results/cloud_decisions.csv`:

| Arm | Fires delivered | Fire median latency | Onboard filter compute | Cloud decisions correct | Clear forest images lost | SAR alerts |
|---|---|---|---|---|---|---|
| `inorder` | 24 / 45 | 34.4 h | 0 s | — | — | — |
| `serval_noweather` | 45 / 45 | 1.25 h | 7,999 s (66.7 Wh) | 100% (filter always runs) | 0% | — |
| `serval` | 42 / 45 | 1.23 h | 1,334 s (11.1 Wh) | 88.0% | 7.0% | — |
| `markov` (maritime prior) | 42 / 45 | 1.23 h | 1,402 s | 88.6% | 7.0% | 50 |
| `markov_empirical` (fitted prior) | 42 / 45 | 1.23 h | 1,526 s (12.7 Wh) | 92.8% | 5.4% | 50 |
| `oracle` | 45 / 45 | 0.87 h | 204 s | 100% | 0% | 86 |

**Baseline reproduction (Scenario A).** Serval delivers fire images about 28x faster than in-order downlink (median 1.23 h vs 34.4 h, and all 45 vs 24 delivered), the trend of paper Fig. 7. The weather side channel cuts onboard filter compute by 83% (paper Table 5: 83.6%). Unlike paper Fig. 8b, it does not shorten fire latency here: at our scale compute is not the bottleneck, and the forecast wrongly calls 3 clear fire images cloudy.

**Extension.** With persistence fitted on a separate week, Markov tracking raises cloud-decision accuracy from 88.0% to 92.8% and cuts clear images wrongly skipped as cloudy from 263 to 149 (−43%), at 14% more compute than Serval (still 81% below no-weather Serval). Fire latency is unchanged: the 3 fires Serval loses are still lost. The SAR offload alerts reach the ground in a median 1.23 h.

**Scenario B (regime priors).** The proposal's maritime prior gives almost no gain (88.6%) and the arid prior none (88.0%): with λ = p_cc + p_ss − 1 near 0 the chain forgets an observation within one orbit, and the estimate falls back to the forecast. The tropical prior (93.7%) and the fitted prior (92.8%) carry memory across orbits (λ = 0.40 and 0.745). Measured California persistence is high: λ = 0.745 on the training week and 0.687 on the test week.

**Scenario C (persistence sweep, `scenario_c_pareto.png`).** At p_cc ≤ 0.2 the extension behaves like Serval (7.0% clear images lost, 55.9 Wh saved). Raising p_cc to 0.5 lowers losses to 4.4% while still saving 51.7 Wh, 78% of no-weather Serval's filter energy. Beyond 0.5 nothing changes. Fire latency is flat across the sweep (median 1.23 h; 42 to 43 of 45 delivered), so the trade-off is energy vs. usable clear imagery, not latency.

## 7. Changing parameters

| Parameter | Where | Default |
|---|---|---|
| Climate regime prior (cloud persistence p_cc, p_ss or cloud fraction) | `--regime`, `REGIMES` in `weather_engine.py` | maritime (0.65, 0.6) |
| Override p_cc of the chosen regime | `--p_cc` | regime value |
| Fitted persistence | `python tools/fit_markov.py data/public/weather_california_train.csv` | p_cc 0.770, p_ss 0.975 |
| Passes before SAR alert | `--query_ttl_passes` | 3 |
| Side-channel thresholds | `--cloud_threshold LOW HIGH` | 0.2 0.8 |
| Satellites, dates, frame spacing, fire rate, seed | flags of `tools/generate_workload.py` | see `--help` |
| Filter runtimes, power model, ground stations | `write_configs()` in `tools/generate_workload.py` | paper Tables 2 and 4 |

To use a different period, fetch matching weather first: `python tools/fetch_weather.py --start YYYY-MM-DD --end YYYY-MM-DD`.

---

## 8. Known limitations

- Synthetic image trace and labels (Section 2): absolute latencies differ from the paper; we compare trends between arms that share identical inputs.
- ERA5 cloud cover stands in for the cloud filter's output, so the onboard cloud filter is assumed perfect.
- Ground-station cloud observations are assumed to reach all satellites as soon as an image is downlinked (optimistic uplink).
- Three simulated days (proposal: seven) and 89 satellites (proposal: 200; that is every Dove with a public TLE), to keep the full run under an hour on a laptop.
- The energy metric uses the paper's measured Jetson AGX Orin numbers (30 W, 3 s cloud filter, Tables 1 and 4) rather than the proposal's 5 to 10 W, 0.5 to 1.5 s estimate, so the baseline and extension share the paper's filter cost model.
- The fitted persistence comes from a week that was mostly clear (10% cloudy) while the simulated week was 54% cloudy. The transition rates still transfer well (training λ = 0.745 vs. 0.687 measured on the test week), but the long-run cloud fraction does not.
- Serval's power model hard-codes a 25 kJ battery, which only works if its energy configs are in watts and joules; our configs follow that.

## 9. License and acknowledgments

Serval code © its authors (GPL-3.0, see `Serval/LICENSE`); our additions are released under the same license. Orbital data from [CelesTrak](https://celestrak.org/); weather data from [Open-Meteo](https://open-meteo.com/) (ERA5 / archived forecasts, CC BY 4.0).
