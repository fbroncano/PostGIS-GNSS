"""The analytical patterns on SQLite + SpatiaLite, against PostGIS.

The axis of interest is not latency alone but whether a pattern can be
expressed on the platform at all. Where it cannot, the hybrid route is timed
instead, with SQL retrieving and client code computing, since that is what a
user would actually have to write.

Usage:
    POSTGRES_* ... python benchmark_spatialite.py
"""

import math
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "database"))

MOD = "/opt/homebrew/lib/mod_spatialite.8.dylib"
DB = HERE / "geognss.sqlite"
D0, D1 = "2025-01-01", "2025-12-31"
CONST, METHOD = 0, 5
REPEATS = 3


def spatialite_connection():
    cx = sqlite3.connect(str(DB))
    cx.enable_load_extension(True)
    cx.load_extension(MOD)
    return cx


def median_latency_ms(fn, repeats=REPEATS):
    ts = []
    for _ in range(repeats):
        t = time.perf_counter()
        out = fn()
        ts.append((time.perf_counter() - t) * 1000)
    ts.sort()
    return ts[len(ts) // 2], out


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


# --- P1: NOT expressible in SpatiaLite. Hybrid: SQL plus Weiszfeld in client --

def p1_representative_position_hybrid(cx):
    # The antenna-corrected reference is computed outside the database as well.
    # SpatiaLite has no procedural language, so station_reference() cannot exist.
    ref = {}
    for sid, x, y, z, dh, dn, de in cx.execute("""
            SELECT s.id, ST_X(s.location), ST_Y(s.location), ST_Z(s.location),
                   COALESCE(a.delta_h,0), COALESCE(a.delta_n,0), COALESCE(a.delta_e,0)
            FROM   stations s
            LEFT JOIN station_antenna a
                   ON a.station_id = s.id AND a.valid_to IS NULL"""):
        p = math.hypot(x, y)
        theta = math.atan2(z * 6378137.0, p * 6356752.314245)
        ep2 = (6378137.0**2 - 6356752.314245**2) / 6356752.314245**2
        lat = math.atan2(z + ep2 * 6356752.314245 * math.sin(theta)**3,
                         p - 0.00669437999014 * 6378137.0 * math.cos(theta)**3)
        lon = math.atan2(y, x)
        sla, cla, slo, clo = math.sin(lat), math.cos(lat), math.sin(lon), math.cos(lon)
        ref[sid] = (x + (-slo * de - sla * clo * dn + cla * clo * dh),
                    y + (clo * de - sla * slo * dn + cla * slo * dh),
                    z + (cla * dn + sla * dh))

    rows = cx.execute("""
        SELECT station_id, ST_X(position), ST_Y(position), ST_Z(position), std_3d
        FROM   daily_positions
        WHERE  constellation_id = ? AND method_id = ? AND date BETWEEN ? AND ?
        ORDER  BY station_id""", (CONST, METHOD, D0, D1)).fetchall()

    out, cur, buf = [], None, []
    for sid, x, y, z, s3 in rows + [(None, 0, 0, 0, 0)]:
        if sid != cur and buf:
            P = np.array([b[:3] for b in buf])
            m = geometric_median(P)
            r = np.array(ref[cur])
            out.append((cur, len(buf), *m, float(np.linalg.norm(m - r)),
                        float(np.linalg.norm(P - r, axis=1).mean()),
                        float(np.mean([b[3] for b in buf]))))
            buf = []
        cur, _ = sid, buf.append((x, y, z, s3))
    return out


# --- P3: the quality join yes, the geometric median no ------------------------

def p3_quality_filtered_hybrid(cx):
    rows = cx.execute("""
        SELECT dp.station_id, ST_X(dp.position), ST_Y(dp.position), ST_Z(dp.position),
               dp.std_3d, ds.pdop_avg
        FROM   daily_positions dp
        JOIN   daily_statistics ds
               ON  ds.station_id = dp.station_id
               AND ds.date = dp.date
               AND ds.constellation_id = dp.constellation_id
        WHERE  dp.constellation_id = ? AND dp.method_id = ? AND dp.date BETWEEN ? AND ?
        ORDER  BY dp.station_id""", (CONST, METHOD, D0, D1)).fetchall()
    out, cur, buf = [], None, []
    for r in rows + [(None, 0, 0, 0, 0, 0)]:
        if r[0] != cur and buf:
            P = np.array([b[:3] for b in buf])
            keep = np.array([b[3] is not None and b[3] <= 7.0 and
                             b[4] is not None and b[4] <= 2.5 for b in buf])
            out.append((cur, len(buf), int(keep.sum()),
                        geometric_median(P),
                        geometric_median(P[keep]) if keep.any() else None))
            buf = []
        cur = r[0]
        buf.append((r[1], r[2], r[3], r[4], r[5]))
    return out


# --- P4: expressible, through the median() aggregate SpatiaLite provides ------

P4_SQL = """
WITH med AS (
    SELECT station_id, constellation_id, median(rms_max) AS m
    FROM   daily_statistics GROUP BY station_id, constellation_id
),
mad AS (
    SELECT ds.station_id, ds.constellation_id,
           1.4826 * median(abs(ds.rms_max - med.m)) AS s
    FROM   daily_statistics ds
    JOIN   med ON med.station_id = ds.station_id
              AND med.constellation_id = ds.constellation_id
    GROUP  BY ds.station_id, ds.constellation_id
)
SELECT ds.station_id, ds.date, ds.constellation_id, ds.rms_max
FROM   daily_statistics ds
JOIN   med ON med.station_id = ds.station_id AND med.constellation_id = ds.constellation_id
JOIN   mad ON mad.station_id = ds.station_id AND mad.constellation_id = ds.constellation_id
WHERE  mad.s > 0 AND ds.rms_max - med.m > 3 * mad.s
"""


def main():
    cx = spatialite_connection()
    print(f"\nSpatiaLite {cx.execute('SELECT spatialite_version()').fetchone()[0]}"
          f" | {DB.stat().st_size/1e6:.1f} MB\n")

    t1, r1 = median_latency_ms(lambda: p1_representative_position_hybrid(cx))
    t3, r3 = median_latency_ms(lambda: p3_quality_filtered_hybrid(cx))
    t4, r4 = median_latency_ms(lambda: cx.execute(P4_SQL).fetchall())

    print(f"{'pattern':7s} {'expressible in SQL':22s} {'time':>10s}  result")
    print(f"{'P1':7s} {'no (ST_GeometricMedian)':22s} {t1:>9.1f}ms  {len(r1)} stations")
    print(f"{'P2':7s} {'no (no ENU functions)':22s} {'-':>10s}  derived from P1")
    print(f"{'P3':7s} {'partial (join only)':22s} {t3:>9.1f}ms  {len(r3)} stations")
    print(f"{'P4':7s} {'yes (median())':22s} {t4:>9.1f}ms  {len(r4)} sessions")

    # Cross-check against PostGIS, to verify that both produce the same answer
    try:
        from dao import Database
        db = Database()
        with db.get_cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM rms_outlier_sessions(3)")
            pg4 = cur.fetchone()["n"]
            cur.execute("SELECT station_code, bias_3d FROM representative_positions"
                        "(0::smallint,5::smallint,%s,%s)", (D0, D1))
            pg1 = {r["station_code"].strip(): float(r["bias_3d"]) for r in cur.fetchall()}
        db.close()
        dif = max(abs(pg1[r[0]] - r[5]) for r in r1 if r[0] in pg1)
        print(f"\nagreement with PostGIS: P4 {len(r4)} vs {pg4} | "
              f"P1 max|bias difference| = {dif:.4f} m")
    except Exception as e:
        print("\n(no cross-check against PostGIS:", type(e).__name__, str(e)[:40], ")")
    cx.close()


if __name__ == "__main__":
    main()
