"""Parse simulator logs for each arm and produce the evaluation figures and tables.

Usage:
  python analysis/analyze.py --dataset data/generated --output results

Reads <dataset>/logs/<arm>.log (one per arm) plus the dataset's ground truth and writes:
  results/summary.csv                     per-arm latency percentiles, compute, accuracy
  results/fig7_reproduction.png           in-order vs Serval latency CDF (paper Fig. 7)
  results/fig8b_reproduction.png          Serval with/without weather side channel (paper Fig. 8b)
  results/extension_latency_cdf.png       Serval vs Markov extension vs Oracle
  results/extension_compute_energy.png    onboard cloud/fire compute energy per arm
  results/regime_sweep.csv                Markov arm across climate-regime priors (if run)
"""

import argparse
import csv
import os
import pickle
import re
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

NAME = re.compile(r"imageName: ([^}]+)}")
TIME_FIELD = re.compile(r"'time': ([0-9.]+)")
P_CLEAR = re.compile(r"'p_clear': ([0-9.eE+-]+)")
THRESHOLDS = (0.2, 0.8)  # Serval's side-channel thresholds (Sec. 3.3)
CLOUD_FILTER_S = 3.0  # Table 4, 30 W column
COMPUTE_WATTS = 30.0  # Jetson AGX Orin, 30 W mode (Table 1)

LABELS = {
    "inorder": "In-order",
    "serval_noweather": "Serval (no weather)",
    "serval": "Serval (forecast side channel)",
    "markov": "Serval + Markov tracking (ours)",
    "markov_empirical": "Serval + Markov, fitted prior (ours)",
    "markov_empirical_cont": "Serval + Markov, fitted prior, time decay (ours)",
    "markov_empirical_cal": "Serval + Markov, calibrated forecast (ours)",
    "markov_empirical_cal_cont": "Serval + Markov, calibrated, time decay (ours)",
    "markov_empirical_nbr": "Serval + Markov, neighbor cells (ours)",
    "markov_empirical_cont_nbr": "Serval + Markov, time decay + neighbors (ours)",
    "oracle": "Cloud oracle",
}
COLORS = {
    "inorder": "#8a8a8a",
    "serval_noweather": "#d08c2b",
    "serval": "#2b6cb0",
    "markov": "#2f9e44",
    "markov_empirical": "#0c8599",
    "markov_empirical_cont": "#e8590c",
    "markov_empirical_cal": "#c2255c",
    "markov_empirical_cal_cont": "#5f3dc4",
    "markov_empirical_nbr": "#66a80f",
    "markov_empirical_cont_nbr": "#d6336c",
    "oracle": "#7b4fb3",
}


def parse_log(path):
    capture, received, compute_s = {}, {}, 0.0
    alerts, p_clear = [], {}
    with open(path, errors="replace") as fh:
        for line in fh:
            parts = line.split("\t", 2)
            if len(parts) < 3:
                continue
            ts, msg, rest = parts
            if msg == "Image captured by sat":
                m = NAME.search(rest)
                if m:
                    capture[m.group(1)] = ts
            elif msg.startswith("Data Center Received data"):
                m = NAME.search(rest)
                if m and m.group(1) not in received:
                    received[m.group(1)] = ts
            elif msg == "Doing computation on sat":
                m = TIME_FIELD.search(rest)
                if m:
                    compute_s += float(m.group(1))
            elif msg == "SAR offload alert created":
                alerts.append(NAME.findall(rest)[-1])
            elif msg == "Weather engine prediction":
                n, pc = NAME.search(rest), P_CLEAR.search(rest)
                if n and pc:
                    p_clear[n.group(1)] = float(pc.group(1))
    fmt = "%Y-%m-%d %H:%M:%S"
    to_dt = lambda s: datetime.strptime(s, fmt)  # noqa: E731
    return ({k: to_dt(v) for k, v in capture.items()}, {k: to_dt(v) for k, v in received.items()},
            compute_s, alerts, p_clear)


def cloud_decisions(arm, truth, p_clear, captured):
    """Classify each California forest image's cloud decision against ground truth.

    Only forest images reach the cloud filter (California -> forest -> cloud -> fire), so
    these are the images where the side channel matters.
    """
    if arm.startswith("inorder"):
        return None
    c = {"skip_cloudy_but_clear": 0, "skip_cloudy_correct": 0, "skip_clear_but_cloudy": 0,
         "skip_clear_correct": 0, "ran_cloud_filter": 0}
    for name, row in truth.items():
        if row["forest"] != "True" or name not in captured:
            continue
        if arm.startswith("serval_noweather"):
            p = 0.5  # side channel disabled: always run the cloud filter
        else:
            p = p_clear.get(name, 1 - float(row["forecast_cloud_pct"]) / 100)
        clear = row["clear"] == "1"
        if p < THRESHOLDS[0]:
            c["skip_cloudy_but_clear" if clear else "skip_cloudy_correct"] += 1
        elif p > THRESHOLDS[1]:
            c["skip_clear_correct" if clear else "skip_clear_but_cloudy"] += 1
        else:
            c["ran_cloud_filter"] += 1
    total = sum(c.values())
    clear_total = sum(1 for n, r in truth.items() if r["forest"] == "True" and r["clear"] == "1" and n in captured)
    c["forest_images"] = total
    c["cloud_filter_skipped_pct"] = round(100 * (total - c["ran_cloud_filter"]) / total, 1) if total else ""
    c["decision_accuracy_pct"] = round(100 * (c["skip_cloudy_correct"] + c["skip_clear_correct"] + c["ran_cloud_filter"])
                                       / total, 1) if total else ""
    # Clear forest images wrongly sent to the low-priority queue: potential fires that are delayed
    c["clear_images_lost_pct"] = round(100 * c["skip_cloudy_but_clear"] / clear_total, 1) if clear_total else ""
    return c


def latencies_hours(names, capture, received, end_time):
    """Latency per image; images never delivered are censored at the end of the run."""
    out, delivered = [], 0
    for n in names:
        if n not in capture:
            continue
        if n in received:
            delivered += 1
            out.append((received[n] - capture[n]).total_seconds() / 3600)
        else:
            out.append((end_time - capture[n]).total_seconds() / 3600)
    return np.array(out), delivered


def cdf_plot(ax, values, arm):
    v = np.sort(np.maximum(values, 1 / 60))  # floor at one simulation step for the log axis
    ax.plot(v, np.arange(1, len(v) + 1) / len(v), label=LABELS.get(arm, arm), color=COLORS.get(arm), lw=2)


def finish(ax, title, path):
    ax.set_xscale("log")
    ax.set_xlabel("End-to-end latency (hours)")
    ax.set_ylabel("CDF")
    ax.set_title(title)
    ax.grid(alpha=0.3)
    ax.legend(loc="lower right", fontsize=8)
    ax.figure.tight_layout()
    ax.figure.savefig(path, dpi=200)
    plt.close(ax.figure)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", default="data/generated")
    p.add_argument("--output", default="results")
    args = p.parse_args()
    os.makedirs(args.output, exist_ok=True)

    fires = pickle.load(open(os.path.join(args.dataset, "high_priority_list.pkl"), "rb"))
    truth = {}
    with open(os.path.join(args.dataset, "image_cells.csv")) as fh:
        for row in csv.DictReader(fh):
            truth[row["image"]] = row
    log_dir = os.path.join(args.dataset, "logs")
    arms = [f[:-4] for f in sorted(os.listdir(log_dir)) if f.endswith(".log")]

    parsed, rows, decisions = {}, [], []
    for arm in arms:
        capture, received, compute_s, alerts, p_clear = parse_log(os.path.join(log_dir, f"{arm}.log"))
        d = cloud_decisions(arm, truth, p_clear, capture)
        if d:
            decisions.append(dict(arm=arm, **d))
        end_time = max(list(capture.values()) + list(received.values()))
        fire_lat, fire_delivered = latencies_hours(fires, capture, received, end_time)
        all_lat, all_delivered = latencies_hours(list(capture), capture, received, end_time)
        alert_lat = [(received[a] - capture[a.replace("_sar_offload", "")]).total_seconds() / 3600
                     for a in alerts if a in received]
        parsed[arm] = {"fire": fire_lat, "all": all_lat}
        rows.append({
            "arm": arm,
            "fire_images": len(fire_lat),
            "fire_delivered": fire_delivered,
            "fire_median_h": round(float(np.median(fire_lat)), 3) if len(fire_lat) else "",
            "fire_p90_h": round(float(np.percentile(fire_lat, 90)), 3) if len(fire_lat) else "",
            "all_median_h": round(float(np.median(all_lat)), 3),
            "all_delivered": all_delivered,
            "images": len(all_lat),
            "onboard_compute_s": round(compute_s, 1),
            "onboard_compute_J": round(compute_s * COMPUTE_WATTS, 1),
            "sar_alerts": len(alerts),
            "sar_alert_median_h": round(float(np.median(alert_lat)), 3) if alert_lat else "",
        })

    with open(os.path.join(args.output, "summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(r)
    if decisions:
        with open(os.path.join(args.output, "cloud_decisions.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(decisions[0]))
            w.writeheader()
            w.writerows(decisions)
        for d in decisions:
            print(d)

    def figure(arm_list, title, fname, which="fire"):
        present = [a for a in arm_list if a in parsed and len(parsed[a][which])]
        if not present:
            return
        fig, ax = plt.subplots(figsize=(5.5, 4))
        for a in present:
            cdf_plot(ax, parsed[a][which], a)
        finish(ax, title, os.path.join(args.output, fname))

    figure(["inorder", "serval"], "Reproduction of Fig. 7: high-priority latency", "fig7_reproduction.png")
    figure(["serval_noweather", "serval"], "Reproduction of Fig. 8b: effect of weather", "fig8b_reproduction.png")
    figure(["serval_noweather", "serval", "markov", "markov_empirical", "markov_empirical_cont", "oracle"], "Extension: cloud tracking",
           "extension_latency_cdf.png")

    ext = [r for r in rows if r["arm"] in ("serval_noweather", "serval", "markov", "markov_empirical", "markov_empirical_cont", "oracle")]
    if ext:
        fig, ax = plt.subplots(figsize=(5.5, 3.5))
        ax.bar([LABELS[r["arm"]].replace(" (", "\n(") for r in ext], [r["onboard_compute_J"] for r in ext],
               color=[COLORS[r["arm"]] for r in ext])
        ax.set_ylabel("Onboard filter energy (J)")
        ax.set_title("Compute spent on cloud + fire filters")
        ax.tick_params(axis="x", labelsize=7)
        fig.tight_layout()
        fig.savefig(os.path.join(args.output, "extension_compute_energy.png"), dpi=200)
        plt.close(fig)

    # Scenario C (proposal Sec. 4.2): cloud-persistence sweep, energy conserved vs delay/loss
    by_arm = {r["arm"]: r for r in rows}
    dec_by_arm = {d["arm"]: d for d in decisions}
    base_j = by_arm.get("serval_noweather", {}).get("onboard_compute_J")
    pcc = sorted(a for a in by_arm if a.startswith("sweep_pcc"))
    if pcc and base_j is not None:
        sweep_rows = []
        for a in pcc:
            r, d = by_arm[a], dec_by_arm.get(a, {})
            sweep_rows.append({
                "p_cc": int(a[len("sweep_pcc"):]) / 100,
                "onboard_compute_Wh": round(r["onboard_compute_J"] / 3600, 2),
                "energy_saved_Wh_vs_noweather": round((base_j - r["onboard_compute_J"]) / 3600, 2),
                "fire_delivered": r["fire_delivered"],
                "fire_median_h": r["fire_median_h"],
                "decision_accuracy_pct": d.get("decision_accuracy_pct", ""),
                "clear_images_lost_pct": d.get("clear_images_lost_pct", ""),
            })
        with open(os.path.join(args.output, "scenario_c_persistence_sweep.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(sweep_rows[0]))
            w.writeheader()
            w.writerows(sweep_rows)
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 3.6))
        x = [s["energy_saved_Wh_vs_noweather"] for s in sweep_rows]
        for ax, key, ylabel in ((ax1, "clear_images_lost_pct", "Clear forest images lost (%)"),
                                (ax2, "fire_median_h", "Fire latency, median (h)")):
            ax.plot(x, [s[key] for s in sweep_rows], "o-", color=COLORS["markov_empirical"])
            for s, xi in zip(sweep_rows, x):
                ax.annotate(f"{s['p_cc']:.1f}", (xi, s[key]), textcoords="offset points", xytext=(4, 4),
                            fontsize=7)
            if "serval" in by_arm and "serval" in dec_by_arm:
                sv = by_arm["serval"]
                ax.scatter([(base_j - sv["onboard_compute_J"]) / 3600],
                           [dec_by_arm["serval"][key] if key in dec_by_arm["serval"] else sv[key]],
                           marker="s", color=COLORS["serval"], label=LABELS["serval"], zorder=3)
            ax.set_xlabel("Energy saved vs. no-weather Serval (Wh)")
            ax.set_ylabel(ylabel)
            ax.grid(alpha=0.3)
        ax1.legend(fontsize=7)
        fig.suptitle("Scenario C: cloud-persistence sweep (labels = p_cc)", fontsize=10)
        fig.tight_layout()
        fig.savefig(os.path.join(args.output, "scenario_c_pareto.png"), dpi=200)
        plt.close(fig)

    sweep = [r for r in rows if r["arm"].startswith("markov_")]
    if sweep:
        with open(os.path.join(args.output, "regime_sweep.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(sweep[0]))
            w.writeheader()
            w.writerows(sweep)


if __name__ == "__main__":
    main()
