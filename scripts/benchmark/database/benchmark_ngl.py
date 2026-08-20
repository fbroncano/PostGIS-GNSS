"""Harness over the published collection of the Nevada Geodetic Laboratory.

Where benchmark.py compares against a CSV exported by the database itself,
here the file-centric side is the product as its provider distributes it: one
plain-text file per station, with no join keys, the date as a string and no
index of any kind. This is the honest baseline.

Three analytical workloads, equivalent to P1, P2 and P4 of patterns.sql. P3
has no counterpart, because the NGL files publish no quality indicators to
join against.

  N1  representative position per station (geometric median)
  N2  dispersion of the series in the local ENU frame
  N3  robust detection of anomalous days by MAD over the 3D displacement

Usage:
    POSTGRES_DB=... POSTGRES_PORT=... POSTGRES_PASSWORD=... python benchmark_ngl.py
"""

import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TXT = HERE.parent / "data_txt"
REPEATS = 5
D0, D1 = "2015-01-01", "2026-12-31"

SQL = {
    "N1": f"""
        WITH med AS (
            SELECT station_id, COUNT(*) AS n,
                   ST_GeometricMedian(ST_Collect(position)) AS p
            FROM   ngl_positions
            WHERE  date BETWEEN '{D0}' AND '{D1}'
            GROUP  BY station_id
        )
        SELECT station_id, n, ST_X(p), ST_Y(p), ST_Z(p) FROM med ORDER BY station_id
    """,
    "N2": f"""
        WITH med AS (
            SELECT station_id, ST_GeometricMedian(ST_Collect(position)) AS p
            FROM   ngl_positions
            WHERE  date BETWEEN '{D0}' AND '{D1}'
            GROUP  BY station_id
        ),
        origin AS (   -- lat/lon once per station, not once per row
            SELECT station_id, p,
                   radians(ST_Y(ST_Transform(p, 4326))) AS lat,
                   radians(ST_X(ST_Transform(p, 4326))) AS lon
            FROM   med
        ),
        enu AS (   -- LATERAL: (f(...)).* would evaluate f once per field requested
            SELECT n.station_id, e.east, e.north, e.up
            FROM   ngl_positions n
            JOIN   origin o USING (station_id),
            LATERAL ecef_to_enu(n.position, o.p, o.lat, o.lon) AS e
            WHERE  n.date BETWEEN '{D0}' AND '{D1}'
        )
        SELECT station_id, stddev(east), stddev(north), stddev(up)
        FROM   enu GROUP BY station_id ORDER BY station_id
    """,
    "N3": f"""
        WITH med AS (
            SELECT station_id, ST_GeometricMedian(ST_Collect(position)) AS p
            FROM   ngl_positions
            WHERE  date BETWEEN '{D0}' AND '{D1}'
            GROUP  BY station_id
        ),
        d AS (
            SELECT n.station_id, n.date,
                   ST_3DDistance(n.position, m.p) AS dist
            FROM   ngl_positions n JOIN med m USING (station_id)
            WHERE  n.date BETWEEN '{D0}' AND '{D1}'
        ),
        stat AS (
            SELECT station_id,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY dist) AS dmed
            FROM   d GROUP BY station_id
        ),
        sigma AS (
            SELECT d.station_id,
                   1.4826 * percentile_cont(0.5)
                            WITHIN GROUP (ORDER BY abs(d.dist - s.dmed)) AS sg
            FROM   d JOIN stat s USING (station_id) GROUP BY d.station_id
        )
        SELECT d.station_id, d.date, d.dist
        FROM   d JOIN stat s USING (station_id) JOIN sigma g USING (station_id)
        WHERE  g.sg > 0 AND d.dist - s.dmed > 3 * g.sg
    """,
}


# --- file side -----------------------------------------------------------

def read_station_files():
    """Read the collection as distributed, one file per station."""
    out = {}
    for path in sorted(glob.glob(str(TXT / "*.csv"))):
        code = os.path.basename(path)[:-4]
        dates, xyz = [], []
        with open(path, encoding="utf-8") as fh:
            next(fh)
            for line in fh:
                d, x, y, z = line.rstrip("\n").split(",")
                if D0 <= d <= D1:
                    dates.append(d)
                    xyz.append((float(x), float(y), float(z)))
        if xyz:
            out[code] = (np.array(dates), np.array(xyz))
    return out


def geometric_median(P, tol=1e-8, max_iter=1000):
    y = P.mean(axis=0)
    for _ in range(max_iter):
        d = np.linalg.norm(P - y, axis=1)
        d = np.where(d < 1e-12, 1e-12, d)
        w = 1.0 / d
        yn = (P * w[:, None]).sum(axis=0) / w.sum()
        if np.linalg.norm(yn - y) < tol:
            return yn
        y = yn
    return y


def representative_position(data):
    return [(c, len(P), *geometric_median(P)) for c, (_, P) in data.items()]


def enu_dispersion(data):
    import patterns_numpy as pn
    out = []
    for c, (_, P) in data.items():
        m = geometric_median(P)
        lat, lon = pn.ecef_to_geodetic(*m)
        e, n, u = pn.ecef_to_enu(P[:, 0] - m[0], P[:, 1] - m[1], P[:, 2] - m[2], lat, lon)
        out.append((c, e.std(), n.std(), u.std()))
    return out


def mad_outlier_days(data):
    out = []
    for c, (dates, P) in data.items():
        m = geometric_median(P)
        dist = np.linalg.norm(P - m, axis=1)
        dmed = np.median(dist)
        sg = 1.4826 * np.median(np.abs(dist - dmed))
        if sg > 0:
            k = dist - dmed > 3 * sg
            out.extend(zip([c] * k.sum(), dates[k], dist[k]))
    return out


# --- arnes -------------------------------------------------------------------

def peak_resident_mb():
    import resource
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024 * 1024) if sys.platform == "darwin" else r / 1024


def median_latency_ms(fn, repeats=REPEATS):
    ts = []
    for _ in range(repeats):
        t = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t) * 1000.0)
    ts.sort()
    return ts[len(ts) // 2]


def main():
    if len(sys.argv) > 1:
        regime = sys.argv[1]
        if regime == "postgis":
            from dao import Database
            db = Database()

            def run(q):
                def _():
                    with db.get_cursor() as cur:
                        cur.execute(q)
                        cur.fetchall()
                return _
            res = {k: median_latency_ms(run(v)) for k, v in SQL.items()}
            db.close()
        else:
            reparse = regime == "txt-reparse"
            cached = None if reparse else read_station_files()

            def d():
                return read_station_files() if reparse else cached
            res = {"N1": median_latency_ms(lambda: representative_position(d())),
                   "N2": median_latency_ms(lambda: enu_dispersion(d())),
                   "N3": median_latency_ms(lambda: mad_outlier_days(d()))}
        print(json.dumps({"times": res, "rss": peak_resident_mb()}))
        return

    results = {}
    for regime in ("txt-reparse", "txt-in-memory", "postgis"):
        p = subprocess.run([sys.executable, __file__, regime], capture_output=True,
                           text=True, cwd=str(HERE), env=os.environ)
        if p.returncode != 0:
            print(f"{regime} FALLO:\n{p.stderr}", file=sys.stderr)
            continue
        results[regime] = json.loads(p.stdout.strip().splitlines()[-1])

    print(f"\nNGL collection, {D0}..{D1}. Median latency over {REPEATS} repetitions, in ms\n")
    print(f"{'':6s} {'txt-reparse':>13s} {'txt-in-memory':>13s} {'postgis':>13s}")
    for k in ("N1", "N2", "N3"):
        row = f"{k:6s}"
        for r in ("txt-reparse", "txt-in-memory", "postgis"):
            v = results.get(r, {}).get("times", {}).get(k)
            row += f" {v:>13,.1f}" if v is not None else f" {'-':>13s}"
        print(row)
    row = "\nRSS   "
    for r in ("txt-reparse", "txt-in-memory", "postgis"):
        v = results.get(r, {}).get("rss")
        row += f" {v:>10,.0f} MB" if v is not None else f" {'-':>13s}"
    print(row)


if __name__ == "__main__":
    main()
