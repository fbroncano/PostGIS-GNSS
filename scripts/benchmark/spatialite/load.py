"""Load the schema and the 2025 data into a SQLite + SpatiaLite database.

Ports the tables the analytical patterns touch onto the alternative platform
that Table 1 compares against, so that its rows can be checked one by one
rather than asserted. The data comes from the CSV files in ../data, the same
ones that feed the file-centric harness.

Usage:
    python load.py [target.sqlite]
"""

import csv
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
MOD = "/opt/homebrew/lib/mod_spatialite.8.dylib"

# EPSG:4978 is not part of the set that InitSpatialMetaData installs, so it has
# to be registered by hand. PostGIS ships it out of the box.
SRID_4978 = (
    4978, "epsg", 4978, "WGS 84 (geocentric)",
    "+proj=geocent +datum=WGS84 +units=m +no_defs",
    'GEOCCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
    'PRIMEM["Greenwich",0],UNIT["metre",1],AXIS["Geocentric X",OTHER],'
    'AXIS["Geocentric Y",OTHER],AXIS["Geocentric Z",NORTH],AUTHORITY["EPSG","4978"]]'
)

SCHEMA = """
CREATE TABLE constellations (
    id   INTEGER PRIMARY KEY,
    code TEXT NOT NULL,
    name TEXT NOT NULL
);

CREATE TABLE methods (
    id          INTEGER PRIMARY KEY,
    code        TEXT NOT NULL,
    description TEXT
);

CREATE TABLE stations (
    id           TEXT PRIMARY KEY,
    name         TEXT,
    city         TEXT,
    province     TEXT,
    initial_date TEXT
);

CREATE TABLE daily_positions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id       TEXT    NOT NULL REFERENCES stations(id),
    date             TEXT    NOT NULL,
    constellation_id INTEGER NOT NULL REFERENCES constellations(id),
    method_id        INTEGER NOT NULL REFERENCES methods(id),
    std_x REAL, std_y REAL, std_z REAL, std_3d REAL,
    n_epochs_used INTEGER, n_epochs INTEGER,
    UNIQUE (station_id, date, constellation_id, method_id)
);

CREATE TABLE daily_statistics (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id       TEXT    NOT NULL REFERENCES stations(id),
    date             TEXT    NOT NULL,
    constellation_id INTEGER NOT NULL REFERENCES constellations(id),
    n_satellites_avg REAL, clock_bias_m REAL,
    gdop_avg REAL, pdop_avg REAL, pdop_max REAL, tdop_avg REAL,
    rms_avg REAL, rms_max REAL,
    UNIQUE (station_id, date, constellation_id)
);

CREATE TABLE station_antenna (
    station_id TEXT NOT NULL REFERENCES stations(id),
    valid_from TEXT NOT NULL,
    valid_to   TEXT,
    antenna_type TEXT, radome TEXT,
    delta_h REAL NOT NULL, delta_n REAL NOT NULL, delta_e REAL NOT NULL
);

CREATE INDEX idx_dp_station ON daily_positions(station_id);
CREATE INDEX idx_dp_date    ON daily_positions(date);
"""

CATALOGUES = {
    "constellations": [(0, "GEC", "Multi-GNSS"), (1, "G", "GPS"),
                       (2, "E", "Galileo"), (3, "C", "BeiDou")],
    "methods": [(1, "mean", "Arithmetic mean"), (2, "median", "Median"),
                (3, "weighted", "PDOP-weighted mean"), (4, "mad", "MAD filter + mean"),
                (5, "weighted_mad", "MAD filter + PDOP-weighted mean")],
}


def connect(path):
    cx = sqlite3.connect(path)
    cx.enable_load_extension(True)
    cx.load_extension(MOD)
    return cx


def read_csv(name):
    with (DATA / name).open(encoding="utf-8") as fh:
        yield from csv.DictReader(fh)


def as_float(v):
    return float(v) if v not in ("", None) else None


def build_database(path):
    if Path(path).exists():
        Path(path).unlink()
    cx = connect(path)
    cur = cx.cursor()
    cur.execute("SELECT InitSpatialMetaData(1)")
    cur.execute("""INSERT INTO spatial_ref_sys
                   (srid, auth_name, auth_srid, ref_sys_name, proj4text, srtext)
                   VALUES (?,?,?,?,?,?)""", SRID_4978)
    cur.executescript(SCHEMA)

    for table, data in CATALOGUES.items():
        n = len(data[0])
        cur.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * n)})", data)

    # Geometry columns are added with AddGeometryColumn, which registers them in
    # the SpatiaLite metadata. This is the equivalent of geometry(POINTZ, 4978).
    cur.execute("SELECT AddGeometryColumn('stations','location',4978,'POINT','XYZ')")
    cur.execute("SELECT AddGeometryColumn('daily_positions','position',4978,'POINT','XYZ')")

    t = time.perf_counter()
    cur.executemany(
        """INSERT INTO stations (id,name,city,province,initial_date,location)
           VALUES (?,?,?,?,?, GeomFromText(?,4978))""",
        [(r["id"].strip(), r["name"], r["city"], r["province"], r["initial_date"],
          f"POINTZ({r['x']} {r['y']} {r['z']})") for r in read_csv("stations.csv")])

    cur.executemany(
        """INSERT INTO station_antenna VALUES (?,?,?,?,?,?,?,?)""",
        [(r["station_id"].strip(), r["valid_from"], r["valid_to"] or None,
          r["antenna_type"], r["radome"],
          as_float(r["delta_h"]), as_float(r["delta_n"]), as_float(r["delta_e"]))
         for r in read_csv("station_antenna.csv")])

    cur.executemany(
        """INSERT INTO daily_positions
           (station_id,date,constellation_id,method_id,position,
            std_x,std_y,std_z,std_3d,n_epochs_used,n_epochs)
           VALUES (?,?,?,?, GeomFromText(?,4978), ?,?,?,?,?,?)""",
        [(r["station_id"].strip(), r["date"], int(r["constellation_id"]),
          int(r["method_id"]), f"POINTZ({r['x']} {r['y']} {r['z']})",
          as_float(r["std_x"]), as_float(r["std_y"]), as_float(r["std_z"]), as_float(r["std_3d"]),
          int(r["n_epochs_used"]), int(r["n_epochs"]))
         for r in read_csv("daily_positions.csv")])

    cur.executemany(
        """INSERT INTO daily_statistics
           (station_id,date,constellation_id,n_satellites_avg,clock_bias_m,
            gdop_avg,pdop_avg,pdop_max,tdop_avg,rms_avg,rms_max)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [(r["station_id"].strip(), r["date"], int(r["constellation_id"]),
          as_float(r["n_satellites_avg"]), as_float(r["clock_bias_m"]), as_float(r["gdop_avg"]),
          as_float(r["pdop_avg"]), as_float(r["pdop_max"]), as_float(r["tdop_avg"]),
          as_float(r["rms_avg"]), as_float(r["rms_max"])) for r in read_csv("daily_statistics.csv")])
    elapsed = time.perf_counter() - t

    cur.execute("SELECT CreateSpatialIndex('daily_positions','position')")
    cx.commit()

    for table in ("stations", "daily_positions", "daily_statistics", "station_antenna"):
        n = cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        print(f"  {table:20s} {n:>8,}")
    print(f"\nload: {elapsed:.1f} s | file: {Path(path).stat().st_size/1e6:.1f} MB")
    cx.close()


if __name__ == "__main__":
    build_database(sys.argv[1] if len(sys.argv) > 1 else str(HERE / "geognss.sqlite"))
