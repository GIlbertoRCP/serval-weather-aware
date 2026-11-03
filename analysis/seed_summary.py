"""Aggregate the key arms across workload seeds (scripts/run_seeds.sh).

Reads summary.csv and cloud_decisions.csv from each per-seed analysis folder and writes
  results/seed_summary.csv   mean and std per arm and metric
  results/seed_paired.csv    per-metric difference vs. Serval, paired by seed
                             (mean, std, and in how many seeds the arm did better)
"""

import argparse
import csv
import os

import numpy as np

ARMS = ["serval_noweather", "serval", "markov_empirical", "markov_empirical_nbr",
        "markov_empirical_cont_nbr", "oracle"]
# metric -> (file, column, True if higher is better)
METRICS = {
    "fire_delivered_pct": ("summary", None, True),
    "fire_median_h": ("summary", "fire_median_h", False),
    "onboard_compute_s": ("summary", "onboard_compute_s", False),
    "decision_accuracy_pct": ("cloud_decisions", "decision_accuracy_pct", True),
    "clear_images_lost_pct": ("cloud_decisions", "clear_images_lost_pct", False),
}


def load(folder):
    tables = {}
    for name in ("summary", "cloud_decisions"):
        with open(os.path.join(folder, f"{name}.csv")) as fh:
            tables[name] = {r["arm"]: r for r in csv.DictReader(fh)}
    values = {}
    for arm in ARMS:
        s, d = tables["summary"].get(arm), tables["cloud_decisions"].get(arm)
        if s is None:
            continue
        v = {"fire_delivered_pct": 100 * int(s["fire_delivered"]) / int(s["fire_images"])}
        for metric, (src, col, _) in METRICS.items():
            row = s if src == "summary" else d
            if col and row is not None:
                v[metric] = float(row[col])
        values[arm] = v
    return values


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dirs", nargs="+", required=True)
    p.add_argument("--output", default="results")
    args = p.parse_args()

    per_seed = [load(d) for d in args.dirs]
    n = len(per_seed)
    summary, paired = [], []
    for arm in ARMS:
        row = {"arm": arm, "seeds": n}
        prow = {"arm": arm, "seeds": n}
        for metric, (_, _, higher_better) in METRICS.items():
            xs = [s[arm][metric] for s in per_seed if arm in s and metric in s[arm]]
            if len(xs) == n:
                row[f"{metric}_mean"] = round(float(np.mean(xs)), 2)
                row[f"{metric}_std"] = round(float(np.std(xs, ddof=1)), 2) if n > 1 else 0.0
            base = [s["serval"][metric] for s in per_seed if metric in s.get("serval", {})]
            if arm != "serval" and len(xs) == n and len(base) == n:
                diff = np.array(xs) - np.array(base)
                prow[f"{metric}_diff_mean"] = round(float(diff.mean()), 2)
                prow[f"{metric}_diff_std"] = round(float(diff.std(ddof=1)), 2) if n > 1 else 0.0
                better = diff > 0 if higher_better else diff < 0
                prow[f"{metric}_better_in"] = f"{int(better.sum())}/{n}"
        summary.append(row)
        if arm != "serval":
            paired.append(prow)

    os.makedirs(args.output, exist_ok=True)
    for name, rows in (("seed_summary", summary), ("seed_paired", paired)):
        fields = list(dict.fromkeys(k for r in rows for k in r))
        with open(os.path.join(args.output, f"{name}.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    for r in summary:
        print(r)
    for r in paired:
        print(r)


if __name__ == "__main__":
    main()
