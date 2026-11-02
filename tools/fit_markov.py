"""Fit the two-state Markov cloud chain (clear/cloudy, one step = one 90-minute orbit)
from hourly ERA5 cloud cover.

Run it on a week that does NOT overlap the simulated period, so the extension's prior
carries no information about the test data:

    python tools/fit_markov.py data/public/weather_california_train.csv

Cloudy means truth cloud cover > 50%, the same rule tools/generate_workload.py uses for
image labels. Hourly values are linearly interpolated to 30 minutes so the 90-minute
transition can be counted directly. The printed values are the "empirical" entry of
REGIMES in Serval/Sat_Simulator/weather_engine.py.
"""

import argparse

import pandas as pd

LAG_MINUTES = 90


def main():
    p = argparse.ArgumentParser()
    p.add_argument("weather_csv")
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


if __name__ == "__main__":
    main()
