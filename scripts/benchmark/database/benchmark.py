"""Harness comparing PostGIS against a file-centric organisation.

Runs the analytical patterns over three regimes:

  A) csv-reparse   the CSV is read and parsed again on every query, which is
                   how a stateless file-centric workflow behaves
  B) csv-memory    the CSV is parsed once and the queries operate on arrays
                   already resident in memory, the strongest form of baseline
  C) postgis       an SQL query against the database

Latency and peak resident memory are reported for each. Every regime runs in
its own process so that the memory figure is attributable to it alone.

Usage:
    POSTGRES_DB=... POSTGRES_PORT=... POSTGRES_PASSWORD=... python benchmark.py
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SQL = ROOT.parent / "sql" / "patterns.sql"
NPY = Path(__file__).parent / "patterns_numpy.py"
REPEATS = 5
D0, D1 = "2025-01-01", "2025-12-31"
CONST, METHOD = 0, 5
STD3D_MAX, PDOP_MAX = 7.0, 2.5

SQL_CALLS = {
    "P1": f"SELECT * FROM representative_positions({CONST}::smallint,{METHOD}::smallint,'{D0}','{D1}')",
    "P2": f"SELECT * FROM representative_enu({CONST}::smallint,{METHOD}::smallint,'{D0}','{D1}')",
    "P3": f"SELECT * FROM quality_filtered_positions({CONST}::smallint,{METHOD}::smallint,'{D0}','{D1}',{STD3D_MAX},{PDOP_MAX})",
    "P4": "SELECT * FROM rms_outlier_sessions(3)",
}


def peak_resident_mb():
    import resource
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux informa en kB, macOS en bytes
    return rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024


def median_latency_ms(fn, repeats=REPEATS):
    times = []
    for _ in range(repeats):
        t = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t) * 1000.0)
    times.sort()
    return times[len(times) // 2]


# --- regimenes ---------------------------------------------------------------

def regime_csv(reparse: bool):
    import pandas as pd
    import patterns_numpy as pn

    d0, d1 = pd.Timestamp(D0), pd.Timestamp(D1)
    cached = None if reparse else pn.load_csv(str(DATA))

    def data():
        return pn.load_csv(str(DATA)) if reparse else cached

    calls = {
        "P1": lambda: pn.p1_representative_positions(data(), CONST, METHOD, d0, d1),
        "P2": lambda: pn.p2_representative_enu(data(), CONST, METHOD, d0, d1),
        "P3": lambda: pn.p3_quality_filtered(data(), CONST, METHOD, d0, d1,
                                             STD3D_MAX, PDOP_MAX),
        "P4": lambda: pn.p4_rms_outliers(data(), 3.0),
    }
    return {k: median_latency_ms(v) for k, v in calls.items()}


def regime_postgis():
    from dao import Database
    db = Database()

    def run(sql):
        def _():
            with db.get_cursor() as cur:
                cur.execute(sql)
                cur.fetchall()
        return _

    out = {k: median_latency_ms(run(v)) for k, v in SQL_CALLS.items()}
    db.close()
    return out


# --- lineas de codigo --------------------------------------------------------

def lines_of_code_numpy():
    """Effective lines of each pattern, between its LOC-BEGIN/LOC-END markers."""
    text = NPY.read_text(encoding="utf-8").splitlines()
    out, current, count = {}, None, 0
    for line in text:
        if "LOC-BEGIN" in line:
            current, count = line.split("LOC-BEGIN")[1].strip(), 0
        elif "LOC-END" in line and current:
            out[current.upper()] = count
            current = None
        elif current is not None:
            s = line.strip()
            if s and not s.startswith("#"):
                count += 1
    return out


def lines_of_code_infrastructure():
    """Code the file-centric side must write that PostGIS already provides.

    Only what has a NATIVE equivalent in PostGIS is counted. The ECEF/ENU
    rotations and the antenna reduction are excluded, because they are written
    by hand on both platforms and therefore favour neither.
    """
    text = NPY.read_text(encoding="utf-8")
    out = {}
    for fn, equiv in (("ecef_to_geodetic", "ST_Transform"),
                      ("geometric_median", "ST_GeometricMedian"),
                      ("load_csv", "the database")):
        m = re.search(rf"^def {fn}\(.*?(?=\n\S|\Z)", text, re.S | re.M)
        if m:
            body = [l for l in m.group(0).splitlines()[1:]
                    if l.strip() and not l.strip().startswith(("#", '"""'))]
            out[fn] = (len(body), equiv)
    return out


def lines_of_code_sql():
    """Effective lines in the body of each function of patterns.sql."""
    text = SQL.read_text(encoding="utf-8")
    names = {"representative_positions": "P1", "representative_enu": "P2",
             "quality_filtered_positions": "P3", "rms_outlier_sessions": "P4"}
    out = {}
    for fn, label in names.items():
        m = re.search(rf"CREATE OR REPLACE FUNCTION {fn}\b.*?AS \$\$(.*?)\$\$;",
                      text, re.S)
        if m:
            out[label] = sum(1 for l in m.group(1).splitlines()
                             if l.strip() and not l.strip().startswith("--"))
    return out


# --- output ------------------------------------------------------------------

def main():
    if len(sys.argv) > 1:                       # subprocess: a single regime
        name = sys.argv[1]
        res = (regime_postgis() if name == "postgis"
               else regime_csv(reparse=(name == "csv-reparse")))
        print(json.dumps({"times": res, "rss": peak_resident_mb()}))
        return

    results = {}
    for regime in ("csv-reparse", "csv-in-memory", "postgis"):
        proc = subprocess.run([sys.executable, __file__, regime],
                              capture_output=True, text=True,
                              cwd=str(Path(__file__).parent), env=os.environ)
        if proc.returncode != 0:
            print(f"{regime} FALLO:\n{proc.stderr}", file=sys.stderr)
            continue
        results[regime] = json.loads(proc.stdout.strip().splitlines()[-1])

    print(f"\nMedian latency over {REPEATS} repetitions, in ms\n")
    print(f"{'':6s} {'csv-reparse':>13s} {'csv-in-memory':>13s} {'postgis':>13s}")
    for p in ("P1", "P2", "P3", "P4"):
        row = f"{p:6s}"
        for r in ("csv-reparse", "csv-in-memory", "postgis"):
            v = results.get(r, {}).get("times", {}).get(p)
            row += f" {v:>13,.1f}" if v is not None else f" {'-':>13s}"
        print(row)

    print(f"\n{'':6s} {'csv-reparse':>13s} {'csv-in-memory':>13s} {'postgis':>13s}")
    row = "RSS   "
    for r in ("csv-reparse", "csv-in-memory", "postgis"):
        v = results.get(r, {}).get("rss")
        row += f" {v:>10,.0f} MB" if v is not None else f" {'-':>13s}"
    print(row)

    ln, ls = lines_of_code_numpy(), lines_of_code_sql()
    print(f"\nEffective lines of code: pattern logic\n")
    print(f"{'':6s} {'numpy/pandas':>13s} {'sql':>13s}")
    for p in ("P1", "P2", "P3", "P4"):
        print(f"{p:6s} {ln.get(p, 0):>13d} {ls.get(p, 0):>13d}")
    print(f"{'total':6s} {sum(ln.values()):>13d} {sum(ls.values()):>13d}")

    inf = lines_of_code_infrastructure()
    print(f"\nInfrastructure the file-centric side must implement\n")
    print(f"{'funcion':22s} {'lineas':>7s}   equivalente nativo en PostGIS")
    for fn, (n, equiv) in inf.items():
        print(f"{fn:22s} {n:>7d}   {equiv}")
    print(f"{'total':22s} {sum(n for n, _ in inf.values()):>7d}   0")


if __name__ == "__main__":
    main()
