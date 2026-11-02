"""Extension: two-state Markov cloud tracking for Serval's cloud side channel.

Baseline Serval (Sec. 3.3) looks at a single weather forecast per image and
bypasses the onboard cloud filter when forecast clear-sky probability is
< 0.2 (treat as cloudy, de-prioritise) or > 0.8 (treat as clear). It keeps no
memory of what the constellation has already observed over a location.

This engine keeps, per weather cell, the most recent *observed* cloud state
(from images whose cloud status became known on a satellite or at a ground
station) and propagates it forward with a two-state discrete-time Markov chain
(state 0 = clear, 1 = cloudy, one step = one ~90 minute orbit). The
observation is fused with the forecast by how much the chain still "remembers"
it after k steps:

    p_clear = lambda^k * p_markov(k) + (1 - lambda^k) * p_forecast

where lambda = p_cc + p_ss - 1 is the chain's second eigenvalue. A fresh
observation dominates; an old one decays back to the forecast. The fused value
replaces the image's cloud side-channel value, so the decision itself is made
by Serval's unchanged threshold logic (same 0.2 / 0.8 thresholds as baseline).

Recovery policy (Methods Sec. 2.1): when a forest image of a cell is
de-prioritised as cloudy `ttl` passes in a row, the satellite emits a small
high-priority "SAR offload" alert so operators can re-task a radar satellite.

Modes:
  none   - engine disabled, simulator is byte-for-byte baseline Serval
  markov - the extension described above
  oracle - side channel replaced with ground truth (upper bound)
"""

import csv

STEP_SECONDS = 90 * 60

# Cloud-to-cloud persistence per climate regime (Proposal Sec. 3.1, Methods Sec. 2), and
# the long-run cloud fraction used to derive clear-to-clear persistence when p_ss is not given.
REGIMES = {
    "arid": {"p_cc": 0.15, "p_ss": 0.925, "cloud_fraction": 0.2},  # proposal: p_ss 0.90-0.95
    "maritime": {"p_cc": 0.65, "cloud_fraction": 0.6},
    "tropical": {"p_cc": 0.85, "cloud_fraction": 0.75},
    # Fitted by tools/fit_markov.py on ERA5 for 2026-10-13..19, a week disjoint from the
    # simulated period (data/public/weather_california_train.csv).
    "empirical": {"p_cc": 0.770, "p_ss": 0.975, "cloud_fraction": 0.103},
}

ALERT_BITS = 8000  # ~1 KB metadata alert


class WeatherEngine:
    engine = None  # singleton used by PlanetSatellite / PlanetGS

    def __init__(self, image_cells_file, mode="markov", regime="maritime", p_cc=None, ttl=3,
                 thresholds=(0.2, 0.8)):
        self.mode = mode
        self.ttl = ttl
        self.low, self.high = thresholds
        if p_cc is None:
            p_cc = REGIMES[regime]["p_cc"]
        pi_c = REGIMES[regime]["cloud_fraction"]
        p_ss = REGIMES[regime].get("p_ss", 1 - pi_c * (1 - p_cc) / (1 - pi_c))
        self.p_cc, self.p_ss = p_cc, max(0.0, min(1.0, p_ss))
        self.lam = self.p_cc + self.p_ss - 1

        self.info = {}  # image name -> (cell, clear, forest)
        with open(image_cells_file) as fh:
            for row in csv.DictReader(fh):
                self.info[row["image"]] = (int(row["cell_id"]), int(row["clear"]), row["forest"] == "True")
        self.last_obs = {}  # cell -> (datetime, clear)
        self.cloudy_streak = {}  # cell -> consecutive forest images de-prioritised as cloudy
        self.stats = {"predictions": 0, "with_observation": 0, "observations": 0, "sar_alerts": 0}

    def _p_clear_markov(self, clear, steps):
        """P(clear after `steps` transitions | current state)."""
        pi_clear = (1 - self.p_cc) / ((1 - self.p_cc) + (1 - self.p_ss))
        start = 1.0 if clear else 0.0
        return pi_clear + (start - pi_clear) * (self.lam ** steps)

    def predict(self, image, now):
        """Clear-sky probability that replaces the image's cloud side channel."""
        if image.name not in self.info:
            return None
        cell, clear, _ = self.info[image.name]
        self.stats["predictions"] += 1
        if self.mode == "oracle":
            return float(clear)
        forecast = image.side_channel_info["cloud"]
        obs = self.last_obs.get(cell)
        if obs is None or obs[0] >= now:
            return forecast
        self.stats["with_observation"] += 1
        # The chain advances in whole orbits (lambda can be negative, e.g. the arid regime)
        steps = max(1, round((now - obs[0]).total_seconds() / STEP_SECONDS))
        memory = abs(self.lam) ** steps
        return memory * self._p_clear_markov(obs[1], steps) + (1 - memory) * forecast

    def observe(self, image):
        """Record the true cloud state of an image once it is known (called at that moment
        in simulated time, so later predictions can only use observations already made)."""
        if self.mode != "markov" or image.name not in self.info:
            return
        cell, clear, _ = self.info[image.name]
        img_time = image.time.to_datetime()
        prev = self.last_obs.get(cell)
        if prev is None or prev[0] < img_time:
            self.last_obs[cell] = (img_time, clear)
            self.stats["observations"] += 1

    def record_decision(self, image, p_clear):
        """Track cloudy de-prioritisations per pass; returns True when a SAR alert should be sent."""
        cell, _, forest = self.info[image.name]
        if not forest:
            return False
        img_time = image.time.to_datetime()
        count, last = self.cloudy_streak.get(cell, (0, None))
        if p_clear >= self.low:
            self.cloudy_streak[cell] = (0, None)
            return False
        # Several frames of one pass can land in the same cell; count passes, not frames.
        if last is not None and (img_time - last).total_seconds() < STEP_SECONDS / 3:
            return False
        count += 1
        if count >= self.ttl:
            self.cloudy_streak[cell] = (0, None)
            self.stats["sar_alerts"] += 1
            return True
        self.cloudy_streak[cell] = (count, img_time)
        return False
