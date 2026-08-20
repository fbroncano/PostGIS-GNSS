"""Cost of the persistence stage, isolated from the processing.

An end-to-end ingestion measurement is dominated by the SPP module, which is
not the contribution of this work. This script measures what is: how much it
costs to move the results of a session into the database.

Three routes, all over the same load:

  DAO row by row    one call to insert_daily_position() per row, which is how
                    the pipeline ingests
  executemany       the same SQL with the batch grouped into a single round trip
  COPY              bulk load, the lower bound of the platform

Re-running the same ingestion is also timed. That is the UPSERT route, and it
is what supports the claim of idempotence.

Usage:
    POSTGRES_DB=... POSTGRES_PORT=... POSTGRES_PASSWORD=... python ingestion.py [n_rows]
"""

import io
import sys
import time

import psycopg

from dao import DB_DATABASE, DB_HOST, DB_PASSWORD, DB_PORT, DB_USER

# A station-day session that tracks all four constellations yields
# 4 constellations x 5 methods of position rows.
ROWS_PER_SESSION = 4 * 5

SCRATCH = "ingest_scratch"


def connect():
    return psycopg.connect(host=DB_HOST, port=DB_PORT, dbname=DB_DATABASE,
                           user=DB_USER, password=DB_PASSWORD, autocommit=True)


def setup(cx, n):
    with cx.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {SCRATCH}")
        cur.execute(f"""
            CREATE TABLE {SCRATCH} (LIKE daily_positions INCLUDING ALL)
        """)
        cur.execute("""
            SELECT station_id, date, constellation_id, method_id,
                   ST_X(position), ST_Y(position), ST_Z(position),
                   std_x, std_y, std_z, std_3d, n_epochs_used, n_epochs, run_id
            FROM   daily_positions
            ORDER  BY station_id, date, constellation_id, method_id
            LIMIT  %s
        """, (n,))
        return cur.fetchall()


INSERT = """
    INSERT INTO {t} (station_id, date, constellation_id, method_id, position,
                     std_x, std_y, std_z, std_3d, n_epochs_used, n_epochs, run_id)
    VALUES (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s,%s,%s),4978),
            %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (station_id, date, constellation_id, method_id) DO UPDATE SET
        position = EXCLUDED.position, std_3d = EXCLUDED.std_3d,
        n_epochs_used = EXCLUDED.n_epochs_used, run_id = EXCLUDED.run_id
"""


def run_rowwise(cx, rows):
    sql = INSERT.format(t=SCRATCH)
    t = time.perf_counter()
    with cx.cursor() as cur:
        for r in rows:
            cur.execute(sql, r)
    return time.perf_counter() - t


def run_executemany(cx, rows):
    sql = INSERT.format(t=SCRATCH)
    t = time.perf_counter()
    with cx.cursor() as cur:
        cur.executemany(sql, rows)
    return time.perf_counter() - t


def run_copy(cx, rows):
    buf = io.StringIO()
    for r in rows:
        sid, d, c, m, x, y, z, sx, sy, sz, s3, neu, ne, run = r
        buf.write(f"{sid}\t{d}\t{c}\t{m}\tSRID=4978;POINTZ({x} {y} {z})\t"
                  f"{sx}\t{sy}\t{sz}\t{s3}\t{neu}\t{ne}\t{run}\n")
    buf.seek(0)
    payload = buf.read()
    t = time.perf_counter()
    with cx.cursor() as cur:
        cur.execute(f"TRUNCATE {SCRATCH}")
        with cur.copy(f"""COPY {SCRATCH} (station_id, date, constellation_id,
                          method_id, position, std_x, std_y, std_z, std_3d,
                          n_epochs_used, n_epochs, run_id) FROM STDIN""") as cp:
            cp.write(payload)
    return time.perf_counter() - t


def report(name, secs, n):
    per_row = secs / n * 1000
    per_session = per_row * ROWS_PER_SESSION
    print(f"{name:22s} {secs:8.2f} s  {n/secs:>10,.0f} rows/s  "
          f"{per_row:>8.3f} ms/row  {per_session:>8.1f} ms/session")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    with connect() as cx:
        rows = setup(cx, n)
        n = len(rows)
        print(f"\n{n:,} rows = {n/ROWS_PER_SESSION:,.0f} station-day sessions\n")

        with cx.cursor() as cur:
            cur.execute(f"TRUNCATE {SCRATCH}")
        report("DAO fila a fila", run_rowwise(cx, rows), n)
        report("  reejecucion (UPSERT)", run_rowwise(cx, rows), n)

        with cx.cursor() as cur:
            cur.execute(f"TRUNCATE {SCRATCH}")
        report("executemany", run_executemany(cx, rows), n)
        report("  reejecucion (UPSERT)", run_executemany(cx, rows), n)

        report("COPY", run_copy(cx, rows), n)

        with cx.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {SCRATCH}")


if __name__ == "__main__":
    main()
