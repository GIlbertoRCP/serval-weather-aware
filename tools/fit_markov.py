"""Fit the two-state Markov cloud chain (clear/cloudy, one step = one 90-minute orbit)
from hourly ERA5 cloud cover.

Run it on a week that does NOT overlap the simulated period, so the extension's prior
carries no information about the test data:

    python tools/fit_markov.py data/public/weather_california_train.csv

Cloudy means truth cloud cover > 50%, the same rule tools/generate_workload.py uses for
image labels. Hourly values are linearly interpolated to 30 minutes so the 90-minute
transition can be counted directly. The printed values are the "empirical" entry of
REGIMES in Serval/Sat_Simulator/weather_engine.py.

It also fits rho_neighbor, the same-hour cloud correlation between adjacent grid cells,
and the forecast calibration used by --forecast_calibration: for each 10% bin of
forecast cloud cover, the likelihood ratio P(bin | clear) / P(bin | cloudy). A likelihood
ratio does not depend on how cloudy the training week was, unlike P(clear | bin).
"""

import argparse
import json

import numpy as np
import pandas as pd

LAG_MINUTES = 90
BINS = 10


def main():
    p = argparse.ArgumentParser()
    p.add_argument("weather_csv")
    p.add_argument("--calibration_out", default="data/public/forecast_calibration.json")
    args = p.parse_args()

    df = pd.read_csv(args.weather_csv, parse_dates=["time"])
    n_cc = n_c = n_ss = n_s = 0
    cloudy_total = samples = 0
    for _, cell in df.groupby("cell_id"):
        series = cell.set_index("time")["truth_cloud_pct"].sort_index()
        series = series.resample("30min").interpolate()
        cloudy = (series > 50).to_numpy()
        lag = LAG_MINUTES // 30
        now, later = cloudy[:-lag], cloudy[lag:]
        n_c += now.sum()
        n_cc += (now & later).sum()
        n_s += (~now).sum()
        n_ss += (~now & ~later).sum()
        cloudy_total += cloudy.sum()
        samples += len(cloudy)

    p_cc, p_ss = n_cc / n_c, n_ss / n_s
    print(f"cells={df.cell_id.nunique()}  {df.time.min()} .. {df.time.max()}")
    print(f"p_cc={p_cc:.3f}  p_ss={p_ss:.3f}  cloud_fraction={cloudy_total / samples:.3f}  "
          f"lambda={p_cc + p_ss - 1:.3f}")

    # Same-hour cloud correlation between adjacent cells (0.5 deg grid, diagonals included),
    # the weight of a neighbor's observation (rho_neighbor in REGIMES["empirical"])
    cells = df.groupby("cell_id")[["lat", "lon"]].first()
    cloudy_by_hour = (df.pivot(index="time", columns="cell_id", values="truth_cloud_pct") > 50)
    a, b = [], []
    for c, (la, lo) in cells.iterrows():
        near = cells[(abs(cells.lat - la) <= 0.501) & (abs(cells.lon - lo) <= 0.501) & (cells.index != c)]
        for n in near.index:
            a.append(cloudy_by_hour[c].to_numpy())
            b.append(cloudy_by_hour[n].to_numpy())
    rho = np.corrcoef(np.concatenate(a), np.concatenate(b))[0, 1]
    print(f"rho_neighbor={rho:.3f}")

    clear = df["truth_cloud_pct"] <= 50
    bins = np.minimum(df["forecast_cloud_pct"] // (100 // BINS), BINS - 1).astype(int)
    # Laplace smoothing keeps sparse bins finite
    n_clear = np.bincount(bins[clear], minlength=BINS) + 1
    n_cloudy = np.bincount(bins[~clear], minlength=BINS) + 1
    lr = (n_clear / n_clear.sum()) / (n_cloudy / n_cloudy.sum())
    with open(args.calibration_out, "w") as fh:
        json.dump({"source": args.weather_csv, "bin_width_pct": 100 // BINS,
                   "likelihood_ratio_clear": [round(float(x), 4) for x in lr]}, fh, indent=2)
    print("forecast likelihood ratio P(bin|clear)/P(bin|cloudy) per 10% bin:",
          " ".join(f"{x:.3f}" for x in lr))
    print(f"wrote {args.calibration_out}")


if __name__ == "__main__":
    main()
