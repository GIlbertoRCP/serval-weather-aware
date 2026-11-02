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

Calibrated fusion (--forecast_calibration): instead of the lambda^k blend, the forecast
is treated as evidence with likelihood ratios fitted on a separate week
(tools/fit_markov.py) and combined with the Markov prior by Bayes' rule:

    odds(clear | obs, forecast) = odds_prior * LR(forecast bin)

The prior relaxes from the observed state toward an uninformative 0.5 (not the fitted
chain's stationary clear fraction, which reflects how clear the training week was).

Neighbor observations (--weather_cells_file): when a cell has no observation of its own,
the most recent observation of an adjacent cell (0.5 degree grid, diagonals included) is
used instead, its weight scaled by rho, the same-hour cloud correlation between adjacent
cells fitted on the training week.

Modes:
  none   - engine disabled, simulator is byte-for-byte baseline Serval
  markov - the extension described above
  oracle - side channel replaced with ground truth (upper bound)
"""

import csv
import json

STEP_SECONDS = 90 * 60

# Cloud-to-cloud persistence per climate regime (Proposal Sec. 3.1, Methods Sec. 2), and
# the long-run cloud fraction used to derive clear-to-clear persistence when p_ss is not given.
REGIMES = {
    "arid": {"p_cc": 0.15, "p_ss": 0.925, "cloud_fraction": 0.2},  # proposal: p_ss 0.90-0.95
    "maritime": {"p_cc": 0.65, "cloud_fraction": 0.6},
    "tropical": {"p_cc": 0.85, "cloud_fraction": 0.75},
    # Fitted by tools/fit_markov.py on ERA5 for 2026-10-13..19, a week disjoint from the
    # simulated period (data/public/weather_california_train.csv).
    "empirical": {"p_cc": 0.770, "p_ss": 0.975, "cloud_fraction": 0.103, "rho_neighbor": 0.640},
}

ALERT_BITS = 8000  # ~1 KB metadata alert


class WeatherEngine:
    engine = None  # singleton used by PlanetSatellite / PlanetGS

    def __init__(self, image_cells_file, mode="markov", regime="maritime", p_cc=None, ttl=3,
                 thresholds=(0.2, 0.8), decay="orbit", calibration_file=None,
                 weather_cells_file=None):
        self.mode = mode
        self.ttl = ttl
        self.decay = decay
        self.lr = None
        if calibration_file:
            with open(calibration_file) as fh:
                cal = json.load(fh)
            self.lr, self.bin_width = cal["likelihood_ratio_clear"], cal["bin_width_pct"]
        self.low, self.high = thresholds
        if p_cc is None:
            p_cc = REGIMES[regime]["p_cc"]
        pi_c = REGIMES[regime]["cloud_fraction"]
        p_ss = REGIMES[regime].get("p_ss", 1 - pi_c * (1 - p_cc) / (1 - pi_c))
        self.p_cc, self.p_ss = p_cc, max(0.0, min(1.0, p_ss))
        self.lam = self.p_cc + self.p_ss - 1
        self.rho = REGIMES[regime].get("rho_neighbor", 0.0)
        self.neighbors = {}  # cell -> adjacent cells
        if weather_cells_file:
            coords = {}
            with open(weather_cells_file) as fh:
                for row in csv.DictReader(fh):
                    coords.setdefault(int(row["cell_id"]), (float(row["lat"]), float(row["lon"])))
            for c, (la, lo) in coords.items():
                self.neighbors[c] = [n for n, (la2, lo2) in coords.items()
                                     if n != c and abs(la2 - la) <= 0.501 and abs(lo2 - lo) <= 0.501]

        self.info = {}  # image name -> (cell, clear, forest)
        with open(image_cells_file) as fh:
            for row in csv.DictReader(fh):
                self.info[row["image"]] = (int(row["cell_id"]), int(row["clear"]), row["forest"] == "True")
        self.last_obs = {}  # cell -> (datetime, clear)
        self.cloudy_streak = {}  # cell -> consecutive forest images de-prioritised as cloudy
        self.stats = {"predictions": 0, "with_observation": 0, "with_neighbor_observation": 0,
                      "observations": 0, "sar_alerts": 0}

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
        obs, weight = self.last_obs.get(cell), 1.0
        if (obs is None or obs[0] >= now) and self.rho > 0:
            nearby = [self.last_obs[n] for n in self.neighbors.get(cell, ())
                      if n in self.last_obs and self.last_obs[n][0] < now]
            obs, weight = (max(nearby), self.rho) if nearby else (None, 1.0)
            if obs is not None:
                self.stats["with_neighbor_observation"] += 1
        if obs is None or obs[0] >= now:
            return forecast if self.lr is None else self._calibrated(0.5, forecast)
        if weight == 1.0:
            self.stats["with_observation"] += 1
        elapsed = (now - obs[0]).total_seconds() / STEP_SECONDS
        if self.decay == "continuous" and self.lam > 0:
            # Elapsed time in (fractional) orbits, so a frame seconds after an observation in
            # the same pass trusts it almost fully
            steps = elapsed
        else:
            # The chain advances in whole orbits (lambda can be negative, e.g. the arid regime)
            steps = max(1, round(elapsed))
        memory = weight * abs(self.lam) ** steps
        if self.lr is not None:
            prior = 0.5 + ((1.0 if obs[1] else 0.0) - 0.5) * memory
            return self._calibrated(prior, forecast)
        return memory * self._p_clear_markov(obs[1], steps) + (1 - memory) * forecast

    def _calibrated(self, prior, forecast):
        """Bayes update of P(clear) with the forecast's fitted likelihood ratio."""
        cloud_pct = (1 - forecast) * 100
        lr = self.lr[min(int(cloud_pct // self.bin_width), len(self.lr) - 1)]
        if prior >= 1.0:
            return 1.0
        odds = prior / (1 - prior) * lr
        return odds / (1 + odds)

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
