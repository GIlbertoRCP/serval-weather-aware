"""Build the inputs Serval's simulator (Sat_Simulator/edgeCompute.py) expects, from public data.

The authors' `data/` folder (Planet image metadata, configs) is proprietary and
not in the public repo. This script regenerates every input in the *same file
formats* so the unmodified simulator can run:

  data/generated/sat_mapping.pkl          [ {sat_id: [Image, ...]}, next_image_id ]
  data/generated/high_priority_list.pkl   names of ground-truth high-priority images
  data/generated/names.txt                satellite id -> NORAD id (Planet format)
  data/generated/HistoricalTLEs/sat*.txt  TLEs per satellite
  data/generated/gs_config/planet.json    12 Planet-style stations x 4 antennas
  data/generated/energy_config/30w.json   power model (Table 2 of the paper)
  data/generated/filter_config/*.json     query = California -> forest -> cloud -> fire
  data/generated/image_cells.csv          image -> weather cell lookup for the extension

Sources: CelesTrak Planet Dove TLEs (orbits), Open-Meteo cloud cover (truth and
forecast, see fetch_weather.py). Fire/forest labels are synthetic and seeded,
calibrated to the ratios in Table 3 of the paper.
"""

import argparse
import csv
import json
import os
import pickle
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from shapely.geometry import Point, Polygon
from skyfield.api import EarthSatellite, load, wgs84

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "Serval", "Sat_Simulator"))
sys.path.insert(0, os.path.join(ROOT, "compat"))
import const  # noqa: E402

const.LOGGING_FILE = os.devnull  # Serval's log module opens this file on import
from src.image import Image  # noqa: E402  (Serval's own Image class, pickled as-is)

# Coarse California outline (lon, lat). Good enough to decide which frames are "in California".
CALIFORNIA = Polygon([
    (-124.2, 42.0), (-120.0, 42.0), (-120.0, 39.0), (-114.6, 35.0), (-114.7, 32.7),
    (-117.1, 32.5), (-118.6, 34.0), (-120.6, 34.5), (-121.9, 36.6), (-122.5, 37.8),
    (-124.4, 40.4),
])

# Approximate Planet / KSAT polar and mid-latitude ground station sites (public information).
GROUND_STATIONS = {
    "Svalbard": (78.23, 15.39), "Tromso": (69.66, 18.94), "Inuvik": (68.36, -133.72),
    "Fairbanks": (64.86, -147.85), "Punta Arenas": (-52.94, -70.85), "Awarua": (-46.53, 168.38),
    "Troll": (-72.01, 2.53), "Hartebeesthoek": (-25.89, 27.69), "Singapore": (1.35, 103.82),
    "Hawaii": (19.82, -155.47), "Dubai": (25.20, 55.27), "Mauritius": (-20.16, 57.50),
}

FRAME_KM = (24.0, 16.0)  # PS2.SD footprint (Sec. 4.2)
IMAGE_BITS = 2_400_000_000  # const.DATA_SIZE in Serval
FOREST_SHARE = 26153 / 37037  # Table 3: forest / California
# Real Doves capture ~3300 frames/day (10M images / 151 sats / 20 days), i.e. one per
# ~13 s of sunlit time. Background frames are sampled sparsely, so each one carries
# the data volume of all frames captured in its interval to keep downlink load realistic.
SECONDS_PER_REAL_FRAME = 13


def load_tles(path, limit):
    lines = [l.rstrip() for l in open(path) if l.strip()]
    sats = []
    for i in range(0, len(lines) - 2, 3):
        name, l1, l2 = lines[i].strip(), lines[i + 1], lines[i + 2]
        if name.startswith("FLOCK"):
            sats.append((name, l1, l2))
    return sats[:limit]


def sun_elevation(lat, lon, when):
    """Approximate solar elevation (degrees) at lat/lon arrays for UTC datetimes."""
    doy = np.array([t.timetuple().tm_yday for t in when])
    hours = np.array([t.hour + t.minute / 60 + t.second / 3600 for t in when])
    decl = np.radians(23.44) * np.sin(2 * np.pi * (284 + doy) / 365)
    hour_angle = np.radians(15 * (hours - 12) + lon)
    lat_r = np.radians(lat)
    s = np.sin(lat_r) * np.sin(decl) + np.cos(lat_r) * np.cos(decl) * np.cos(hour_angle)
    return np.degrees(np.arcsin(np.clip(s, -1, 1)))


def frame(lat, lon):
    dlat = FRAME_KM[1] / 2 / 111.0
    dlon = FRAME_KM[0] / 2 / (111.0 * max(np.cos(np.radians(lat)), 0.05))
    return Polygon([(lon - dlon, lat - dlat), (lon + dlon, lat - dlat),
                    (lon + dlon, lat + dlat), (lon - dlon, lat + dlat)])


def write_tle_files(out, sats):
    os.makedirs(os.path.join(out, "HistoricalTLEs"), exist_ok=True)
    with open(os.path.join(out, "names.txt"), "w") as fh:
        fh.write("# id a b c d e f norad   (column layout read by util.get_planet_sat_tle)\n")
        for name, l1, l2 in sats:
            norad = l1[2:7].strip()
            sat_id = f"d{norad}"
            fh.write(f"{sat_id} - - - - - - {norad}\n")
            with open(os.path.join(out, "HistoricalTLEs", f"sat{norad}.txt"), "w") as t:
                t.write(l1 + "\n" + l2 + "\n")


def write_configs(out):
    os.makedirs(os.path.join(out, "gs_config"), exist_ok=True)
    gs = {}
    for k, (name, pos) in enumerate(GROUND_STATIONS.items()):
        for antenna in range(4):  # 12 stations x 4 antennas = 48 (Sec. 4.3)
            gs[f"{name} {antenna + 1}"] = {"position": list(pos), "bandwidth": 10_000_000_000}
    json.dump(gs, open(os.path.join(out, "gs_config", "planet.json"), "w"), indent=2)

    # Power model, Table 2, in watts / joules: PlanetSatellite hard-codes a 25000-unit battery,
    # which only fits a 60 s step of 50 W transmission if the configs are in W and J.
    # "receive_energy" is what PlanetSatellite uses as solar power generation.
    os.makedirs(os.path.join(out, "energy_config"), exist_ok=True)
    json.dump({"compute_energy": 30, "transmit_energy": 50, "camera_energy": 6,
               "receive_energy": 10, "normal_energy": 1.13},
              open(os.path.join(out, "energy_config", "30w.json"), "w"), indent=2)

    # Query: California (glacial) -> forest (glacial) -> cloud (dynamic) -> fire (dynamic).
    # Runtimes are Table 4, 30 W column.
    os.makedirs(os.path.join(out, "filter_config"), exist_ok=True)
    filters = {
        "california": {"preemptive": True, "runtime": 0},
        "forest": {"preemptive": True, "runtime": 0},
        "cloud": {"preemptive": False, "runtime": 3.0},
        "fire": {"preemptive": False, "runtime": 0.245},
    }
    graph = [["california", "forest", "cloud", "fire"]]
    json.dump({"filters": filters, "global_filter_graph": graph},
              open(os.path.join(out, "filter_config", "30w.json"), "w"), indent=2)
    # In-order baseline: no image can pass, so everything lands in the time-ordered low queue.
    inorder = {k: dict(v, score_threshold=2.0) for k, v in filters.items()}
    json.dump({"filters": inorder, "global_filter_graph": graph},
              open(os.path.join(out, "filter_config", "inorder.json"), "w"), indent=2)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tle_file", default="data/public/planet_tle.txt")
    p.add_argument("--weather_file", default="data/public/weather_california.csv")
    p.add_argument("--start", default="2026-10-20T00:00:00")
    p.add_argument("--end", default="2026-10-23T00:00:00")
    p.add_argument("--num_sats", type=int, default=40)
    p.add_argument("--bg_interval", type=int, default=60,
                   help="seconds between background (non-California) frames")
    p.add_argument("--ca_interval", type=int, default=4,
                   help="seconds between frames over California")
    p.add_argument("--fire_share", type=float, default=0.05,
                   help="P(fire | forest, clear). Paper ratio is 243/24578 ~ 1%%; raised so a "
                        "short simulation still yields enough fire images for a latency CDF")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output_dir", default="data/generated")
    args = p.parse_args()

    rng = np.random.default_rng(args.seed)
    out = args.output_dir
    os.makedirs(out, exist_ok=True)
    start = datetime.strptime(args.start, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)

    sats = load_tles(args.tle_file, args.num_sats)
    write_tle_files(out, sats)
    write_configs(out)

    weather = pd.read_csv(args.weather_file)
    weather["time"] = pd.to_datetime(weather["time"], utc=True)
    cells = weather[["cell_id", "lat", "lon"]].drop_duplicates("cell_id")
    cells = cells[[CALIFORNIA.contains(Point(r.lon, r.lat)) for r in cells.itertuples()]]
    cell_ids = cells["cell_id"].to_numpy()
    cell_xy = cells[["lat", "lon"]].to_numpy()
    wx = weather.set_index(["cell_id", "time"])
    # Forest is glacial: a fixed, seeded subset of California cells.
    forest_cells = set(cell_ids[rng.random(len(cell_ids)) < FOREST_SHARE])
    print(f"{len(cell_ids)} California weather cells, {len(forest_cells)} forested")

    ts = load.timescale()
    mapping, high_priority, cell_rows = {}, [], []
    total = 0
    for name, l1, l2 in sats:
        sat_id = f"d{l1[2:7].strip()}"
        sat = EarthSatellite(l1, l2, name, ts)
        # Coarse pass to find where the satellite is, then dense sampling only over California.
        coarse = [start + timedelta(seconds=s) for s in range(0, int((end - start).total_seconds()), args.ca_interval)]
        t = ts.from_datetimes(coarse)
        sub = wgs84.subpoint(sat.at(t))
        lat, lon = sub.latitude.degrees, sub.longitude.degrees
        sunlit = sun_elevation(lat, lon, coarse) > 10
        near_ca = (lat > 32) & (lat < 42.5) & (lon > -125) & (lon < -114)
        step = max(1, args.bg_interval // args.ca_interval)
        keep = sunlit & (near_ca | (np.arange(len(coarse)) % step == 0))
        images = []
        for k in np.nonzero(keep)[0]:
            when = coarse[k]
            in_ca = bool(near_ca[k] and CALIFORNIA.contains(Point(lon[k], lat[k])))
            name_img = f"{sat_id}_{when.strftime('%Y%m%d_%H%M%S')}"
            fr = {"california": [float(in_ca), 0.0], "forest": [0.0, 0.0],
                  "cloud": [0.0, 0.0], "fire": [0.0, 0.0]}
            side = {}
            if in_ca:
                cell = int(cell_ids[np.argmin(((cell_xy - [lat[k], lon[k]]) ** 2).sum(1))])
                hour = pd.Timestamp(when).floor("h")
                truth_pct, fcst_pct = wx.loc[(cell, hour), ["truth_cloud_pct", "forecast_cloud_pct"]]
                clear = truth_pct <= 50
                forest = cell in forest_cells
                fire = forest and clear and rng.random() < args.fire_share
                fr["forest"][0] = float(forest)
                fr["cloud"][0] = float(clear)  # cloud filter "passes" when the scene is clear
                fr["fire"][0] = float(fire)
                side["cloud"] = 1 - fcst_pct / 100  # same convention as filters/weather.py
                cell_rows.append([name_img, cell, when.isoformat(), int(clear), forest, int(fire), fcst_pct])
                if fire:
                    high_priority.append(name_img)
            size = IMAGE_BITS if near_ca[k] else IMAGE_BITS * args.bg_interval // SECONDS_PER_REAL_FRAME
            img = Image(size, frame(lat[k], lon[k]), None, filter_result=fr,
                        side_channel_info=side, name=name_img)
            img.time = _serval_time(when)
            images.append(img)
        mapping[sat_id] = images
        total += len(images)
        print(f"  {sat_id}: {len(images)} frames")

    pickle.dump([mapping, Image.id], open(os.path.join(out, "sat_mapping.pkl"), "wb"))
    pickle.dump(high_priority, open(os.path.join(out, "high_priority_list.pkl"), "wb"))
    with open(os.path.join(out, "image_cells.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["image", "cell_id", "time", "clear", "forest", "fire", "forecast_cloud_pct"])
        w.writerows(cell_rows)
    print(f"{total} frames total, {len(cell_rows)} over California, {len(high_priority)} fire images")


def _serval_time(when):
    from src.utils import Time
    return Time().from_datetime(when.replace(tzinfo=None))


if __name__ == "__main__":
    main()
