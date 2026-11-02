"""Download hourly cloud cover for a California grid from Open-Meteo (free, no API key).

Two products are fetched for the same grid/hours, mirroring the paper's setup:
  * truth    - ERA5 reanalysis cloud cover (archive API), stands in for what the
               onboard cloud filter would see.
  * forecast - archived numerical weather forecasts (historical-forecast API),
               the side channel the ground station uploads (Serval Sec. 3.3).

Output: data/public/weather_california.csv with columns
  cell_id, lat, lon, time (UTC hour), truth_cloud_pct, forecast_cloud_pct
The CSV is committed so the TA never needs network access.
"""

import argparse
import csv
import json
import time
import urllib.parse
import urllib.request

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
HIST_FORECAST = "https://historical-forecast-api.open-meteo.com/v1/forecast"

# Bounding box of California; cells outside the state polygon are dropped later
# by the workload generator's point-in-polygon test.
LAT_MIN, LAT_MAX = 32.5, 42.0
LON_MIN, LON_MAX = -124.5, -114.0


def grid(step):
    cells = []
    lat = LAT_MIN + step / 2
    while lat < LAT_MAX:
        lon = LON_MIN + step / 2
        while lon < LON_MAX:
            cells.append((round(lat, 3), round(lon, 3)))
            lon += step
        lat += step
    return cells


def fetch(url, cells, start, end):
    params = {
        "latitude": ",".join(str(c[0]) for c in cells),
        "longitude": ",".join(str(c[1]) for c in cells),
        "hourly": "cloud_cover",
        "start_date": start,
        "end_date": end,
        "timezone": "GMT",
    }
    full = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(full, timeout=120) as r:
                data = json.loads(r.read())
            return data if isinstance(data, list) else [data]
        except Exception as e:  # rate limits / transient errors
            print(f"  retry {attempt + 1}: {e}")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (inclusive)")
    p.add_argument("--step", type=float, default=0.5, help="grid spacing in degrees")
    p.add_argument("--output", default="data/public/weather_california.csv")
    args = p.parse_args()

    cells = grid(args.step)
    print(f"{len(cells)} grid cells")
    rows = []
    batch = 100
    for i in range(0, len(cells), batch):
        chunk = cells[i:i + batch]
        truth = fetch(ARCHIVE, chunk, args.start, args.end)
        fcst = fetch(HIST_FORECAST, chunk, args.start, args.end)
        for j, (t, f) in enumerate(zip(truth, fcst)):
            lat, lon = chunk[j]
            for hour, tc, fc in zip(t["hourly"]["time"], t["hourly"]["cloud_cover"], f["hourly"]["cloud_cover"]):
                if tc is None or fc is None:
                    continue
                rows.append([i + j, lat, lon, hour, tc, fc])
        print(f"  fetched cells {i}..{i + len(chunk) - 1}")

    with open(args.output, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["cell_id", "lat", "lon", "time", "truth_cloud_pct", "forecast_cloud_pct"])
        w.writerows(rows)
    print(f"wrote {len(rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
