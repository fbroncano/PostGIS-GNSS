"""Data-access layer over the GeoGNSS-PS PostGIS database.

A narrow interface centred on the natural keys of the schema. It hides the
cursor lifecycle, the transactional boundaries and the construction of
parameterised queries, and leaves the geometric and analytical work inside the
database.
"""

import os
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import List, Optional

import psycopg
from psycopg.rows import dict_row

from models import *

def _load_cfg(name="postgis.cfg"):
    """Read postgis.cfg, the same file that docker compose --env-file consumes.

    The search walks up from this module to the repository root. Environment
    variables take precedence, so another instance can be targeted without
    editing the file. Credentials never appear in the code: postgis.cfg is
    listed in .gitignore.
    """
    cfg = {}
    here = Path(__file__).resolve()
    for folder in [here.parent, *here.parents]:
        path = folder / name
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    cfg[key.strip()] = value.strip()
            break
    return cfg


_CFG = _load_cfg()


def _setting(key, default):
    return os.environ.get(key) or _CFG.get(key, default)


DB_DATABASE = _setting("POSTGRES_DB", "positions")
DB_USER = _setting("POSTGRES_USER", "postgres")
DB_PASSWORD = _setting("POSTGRES_PASSWORD", "")
DB_HOST = _setting("POSTGRES_HOST", "localhost")
DB_PORT = int(_setting("POSTGRES_PORT", 5432))

# psycopg 3 adapts numpy.float64/float32/int64/int32 without any registration,
# so no custom dumper is needed here.


class Database:
    def __init__(self, host=DB_HOST, port=DB_PORT, database=DB_DATABASE,
                 user=DB_USER, password=DB_PASSWORD):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.connection = None

    def connect(self):
        if self.connection is None or self.connection.closed:
            self.connection = psycopg.connect(
                host=self.host,
                port=self.port,
                dbname=self.database,
                user=self.user,
                password=self.password
            )
        return self.connection

    def close(self):
        if self.connection and not self.connection.closed:
            self.connection.close()

    @contextmanager
    def get_cursor(self, row_factory=dict_row):
        conn = self.connect()
        cursor = conn.cursor(row_factory=row_factory)
        try:
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()


class StationDAO:
    def __init__(self, db: Database = None):
        self.db = db or Database()

    def insert(self, id: str, name: str, city: str, province: str,
               initial_date: date, x: float, y: float, z: float) -> None:
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT insert_station(%s, %s, %s, %s, %s, %s, %s, %s)",
                (id, name, city, province, initial_date, x, y, z)
            )

    def update(self, id: str, x: float, y: float, z: float) -> None:
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """UPDATE stations
                   SET location = ST_SetSRID(ST_MakePoint(%s, %s, %s), 4978)
                   WHERE id = %s""",
                (x, y, z, id)
            )

    def get_by_id(self, id: str) -> Optional[Station]:
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM get_station(%s)", (id,))
            row = cursor.fetchone()
            if row:
                return Station(
                    id=row['station_id'].strip(),
                    name=row['name'],
                    city=row['city'],
                    province=row['province'],
                    initial_date=row['initial_date'],
                    x=row['x'],
                    y=row['y'],
                    z=row['z'],
                    frame_id=row['frame_id'],
                    coord_epoch=float(row['coord_epoch']) if row['coord_epoch'] is not None else None
                )
            return None

    def get_all(self) -> List[Station]:
        with self.db.get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, city, province, initial_date,
                       ST_X(location) as x, ST_Y(location) as y, ST_Z(location) as z,
                       frame_id, coord_epoch
                FROM stations ORDER BY id
            """)
            return [
                Station(
                    id=row['id'].strip(),
                    name=row['name'],
                    city=row['city'],
                    province=row['province'],
                    initial_date=row['initial_date'],
                    x=row['x'],
                    y=row['y'],
                    z=row['z'],
                    frame_id=row['frame_id'],
                    coord_epoch=float(row['coord_epoch']) if row['coord_epoch'] is not None else None
                )
                for row in cursor.fetchall()
            ]

    def delete(self, id: str) -> bool:
        with self.db.get_cursor() as cursor:
            cursor.execute("DELETE FROM stations WHERE id = %s", (id,))
            return cursor.rowcount > 0


class DailyPositionDAO:
    def __init__(self, db: Database = None):
        self.db = db or Database()

    def insert(self, position: DailyPosition) -> int:
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """SELECT insert_daily_position(
                    p_station_id       => %(station_id)s::char(4),
                    p_date             => %(date)s::date,
                    p_constellation_id => %(constellation_id)s::smallint,
                    p_method_id        => %(method_id)s::smallint,
                    p_x => %(x)s::numeric, p_y => %(y)s::numeric, p_z => %(z)s::numeric,
                    p_std_x => %(std_x)s::numeric, p_std_y => %(std_y)s::numeric,
                    p_std_z => %(std_z)s::numeric, p_std_3d => %(std_3d)s::numeric,
                    p_n_epochs_used => %(n_epochs_used)s::integer,
                    p_n_epochs      => %(n_epochs)s::integer,
                    p_run_id        => %(run_id)s::integer
                ) AS id""",
                vars(position)
            )
            return cursor.fetchone()['id']

    def insert_batch(self, positions: List[DailyPosition]) -> List[int]:
        return [self.insert(pos) for pos in positions]

    def get_by_station_date(self, station_id: str, pos_date: date) -> List[DailyPosition]:
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM get_daily_positions(%s, %s)", (station_id, pos_date))
            return [
                DailyPosition(
                    station_id=station_id,
                    date=pos_date,
                    constellation_id=None,
                    method_id=None,
                    x=row['x'],
                    y=row['y'],
                    z=row['z'],
                    std_3d=float(row['std_3d']) if row['std_3d'] else None,
                    n_epochs_used=row['n_epochs_used'],
                    n_epochs=row['n_epochs'],
                    run_id=row['run_id'],
                    constellation_code=row['constellation_code'],
                    constellation_name=row['constellation_name'],
                    method_code=row['method']
                )
                for row in cursor.fetchall()
            ]

    def get_errors(self, station_id: str, pos_date: date) -> List[PositionError]:
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM get_position_errors(%s, %s)", (station_id, pos_date))
            return [
                PositionError(
                    constellation_code=row['constellation_code'],
                    method=row['method'],
                    error_x=row['error_x'],
                    error_y=row['error_y'],
                    error_z=row['error_z'],
                    error_3d=row['error_3d']
                )
                for row in cursor.fetchall()
            ]

    def delete_by_station_date(self, station_id: str, pos_date: date) -> int:
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM daily_positions WHERE station_id = %s AND date = %s",
                (station_id, pos_date)
            )
            return cursor.rowcount

    def get_average_station(self, station_id: str, constellation_id: int,
                            method_id: int) -> DailyPosition:
        # The geometric median is evaluated once. Calling it separately for X, Y
        # and Z would recompute the whole estimate three times over.
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """WITH med AS (
                       SELECT ST_GeometricMedian(ST_Collect(position)) AS p
                       FROM   daily_positions
                       WHERE  station_id = %s AND constellation_id = %s
                         AND  method_id = %s
                   )
                   SELECT ST_X(p)::NUMERIC(12,3) AS x,
                          ST_Y(p)::NUMERIC(12,3) AS y,
                          ST_Z(p)::NUMERIC(12,3) AS z
                   FROM   med""",
                (station_id, constellation_id, method_id))
            row = cursor.fetchone()
            return DailyPosition(station_id=station_id, constellation_id=constellation_id,
                                 method_id=method_id, x=row['x'], y=row['y'], z=row['z'])


class DailyStatisticsDAO:
    def __init__(self, db: Database = None):
        self.db = db or Database()

    def insert(self, stats: DailyStatistics) -> int:
        # Named rather than positional notation. These are consecutive numeric
        # parameters, so a transposition would raise no error at all: it would
        # quietly store TDOP where GDOP belongs.
        with self.db.get_cursor() as cursor:
            cursor.execute(
                """SELECT insert_daily_statistics(
                    p_station_id       => %(station_id)s::char(4),
                    p_date             => %(date)s::date,
                    p_constellation_id => %(constellation_id)s::smallint,
                    p_n_satellites_avg => %(n_satellites_avg)s::numeric,
                    p_clock_bias_m     => %(clock_bias_m)s::numeric,
                    p_gdop_avg => %(gdop_avg)s::numeric,
                    p_pdop_avg => %(pdop_avg)s::numeric,
                    p_pdop_max => %(pdop_max)s::numeric,
                    p_tdop_avg => %(tdop_avg)s::numeric,
                    p_rms_avg  => %(rms_avg)s::numeric,
                    p_rms_max  => %(rms_max)s::numeric,
                    p_run_id   => %(run_id)s::integer
                ) AS id""",
                vars(stats)
            )
            return cursor.fetchone()['id']

    def insert_batch(self, stats_list: List[DailyStatistics]) -> List[int]:
        return [self.insert(stats) for stats in stats_list]

    def get_by_station_date(self, station_id: str, stats_date: date) -> List[DailyStatistics]:
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM get_daily_statistics(%s, %s)", (station_id, stats_date))
            return [
                DailyStatistics(
                    station_id=station_id,
                    date=stats_date,
                    constellation_id=None,
                    n_satellites_avg=float(row['n_satellites_avg']) if row['n_satellites_avg'] else None,
                    clock_bias_m=float(row['clock_bias_m']) if row['clock_bias_m'] else None,
                    gdop_avg=float(row['gdop_avg']) if row['gdop_avg'] else None,
                    pdop_avg=float(row['pdop_avg']) if row['pdop_avg'] else None,
                    pdop_max=float(row['pdop_max']) if row['pdop_max'] else None,
                    tdop_avg=float(row['tdop_avg']) if row['tdop_avg'] else None,
                    rms_avg=float(row['rms_avg']) if row['rms_avg'] else None,
                    rms_max=float(row['rms_max']) if row['rms_max'] else None,
                    run_id=row['run_id'],
                    constellation_code=row['constellation_code'],
                    constellation_name=row['constellation_name']
                )
                for row in cursor.fetchall()
            ]

    def get_constellation_stats(self, station_id: str, method_id: int = 5) -> List[ConstellationStats]:
        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM get_constellation_stats(%s, %s)", (station_id, method_id))
            return [
                ConstellationStats(
                    constellation_name=row['constellation_name'],
                    n_days=row['n_days'],
                    avg_error_3d=row['avg_error_3d'],
                    avg_n_satellites=float(row['avg_n_satellites']) if row['avg_n_satellites'] else None,
                    avg_pdop=float(row['avg_pdop']) if row['avg_pdop'] else None,
                    avg_rms=float(row['avg_rms']) if row['avg_rms'] else None
                )
                for row in cursor.fetchall()
            ]

    def delete_by_station_date(self, station_id: str, stats_date: date) -> int:
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM daily_statistics WHERE station_id = %s AND date = %s",
                (station_id, stats_date)
            )
            return cursor.rowcount
