"""Proposal Scenario A, paper Fig. 8: high-priority latency vs. query arrival rate.

Each dataset differs only in --fire_share (the share of clear forest frames that hold a
fire), so the fire images at a lower rate are a subset of those at a higher rate and the
rest of the workload is identical. Reads the serval and inorder logs of every dataset.

    python analysis/plot_arrival_rate.py --datasets data/generated data/rate_sweep/fire15 ...
"""

import argparse
import csv
import os
import pickle
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze import COLORS, LABELS, latencies_hours, parse_log  # noqa: E402

ARMS = ["inorder", "serval"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--datasets", nargs="+", required=True)
    p.add_argument("--output", default="results")
    args = p.parse_args()

    rows = []
    for ds in args.datasets:
        fires = pickle.load(open(os.path.join(ds, "high_priority_list.pkl"), "rb"))
        for arm in ARMS:
            log = os.path.join(ds, "logs", f"{arm}.log")
            if not os.path.exists(log):
                continue
            capture, received, *_ = parse_log(log)
            end_time = max(list(capture.values()) + list(received.values()))
            lat, delivered = latencies_hours(fires, capture, received, end_time)
            days = (max(capture.values()) - min(capture.values())).total_seconds() / 86400
            rows.append({
                "dataset": ds,
                "arm": arm,
                "fire_images": len(lat),
                "fires_per_day": round(len(lat) / days, 1),
                "fire_delivered": delivered,
                "fire_median_h": round(float(np.median(lat)), 3),
                "fire_p90_h": round(float(np.percentile(lat, 90)), 3),
            })
    rows.sort(key=lambda r: (r["arm"], r["fires_per_day"]))

    os.makedirs(args.output, exist_ok=True)
    with open(os.path.join(args.output, "fig8_arrival_rate.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(r)

    fig, ax = plt.subplots(figsize=(5.5, 4))
    for arm in ARMS:
        pts = [r for r in rows if r["arm"] == arm]
        x = [r["fires_per_day"] for r in pts]
        ax.plot(x, [r["fire_median_h"] for r in pts], "o-", color=COLORS[arm], label=f"{LABELS[arm]}, median")
        ax.plot(x, [r["fire_p90_h"] for r in pts], "o--", color=COLORS[arm], alpha=0.6,
                label=f"{LABELS[arm]}, p90")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("High-priority query arrival rate (fire images per day)")
    ax.set_ylabel("End-to-end latency (hours)")
    ax.set_title("Reproduction of Fig. 8: latency vs. arrival rate")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(os.path.join(args.output, "fig8_arrival_rate.png"), dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    main()
