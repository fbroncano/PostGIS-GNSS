"""Export the dataset to CSV.

Produces the artefact the database is compared against: the file-centric
organisation that is standard practice in operational GNSS workflows. Each
table is written to a flat CSV, as a pipeline writing its results to disk
rather than to a database would leave them.

Usage:
    POSTGRES_DB=posiciones POSTGRES_PORT=5431 POSTGRES_PASSWORD=... \
        python export_csv.py [target_directory]
"""

import sys
from pathlib import Path

from dao import Database

EXPORTS = {
    "daily_positions.csv": """
        SELECT station_id, date, constellation_id, method_id,
               ST_X(position) AS x, ST_Y(position) AS y, ST_Z(position) AS z,
               std_x, std_y, std_z, std_3d, n_epochs_used, n_epochs, run_id
        FROM   daily_positions
    """,
    "daily_statistics.csv": """
        SELECT station_id, date, constellation_id,
               n_satellites_avg, clock_bias_m,
               gdop_avg, pdop_avg, pdop_max, tdop_avg, rms_avg, rms_max, run_id
        FROM   daily_statistics
    """,
    "stations.csv": """
        SELECT id, name, city, province, initial_date,
               ST_X(location) AS x, ST_Y(location) AS y, ST_Z(location) AS z,
               frame_id, coord_epoch
        FROM   stations
    """,
    "station_antenna.csv": """
        SELECT station_id, valid_from, valid_to, antenna_type, radome,
               delta_h, delta_n, delta_e
        FROM   station_antenna
    """,
}


def export(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    db = Database()
    conn = db.connect()
    for filename, query in EXPORTS.items():
        path = dest / filename
        with conn.cursor() as cur, path.open("w", encoding="utf-8") as fh:
            with cur.copy(f"COPY ({query}) TO STDOUT WITH CSV HEADER") as copy:
                for block in copy:
                    fh.write(bytes(block).decode("utf-8"))
        rows = sum(1 for _ in path.open(encoding="utf-8")) - 1
        print(f"{filename:24s} {rows:>8,} rows  {path.stat().st_size/1024:>9,.0f} kB")
    db.close()


if __name__ == "__main__":
    export(Path(sys.argv[1] if len(sys.argv) > 1 else "data"))
