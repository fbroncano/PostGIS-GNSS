# GeoGNSS-PS: PostGIS Information System for Multi-Constellation GNSS Data

Data model, deployment scripts and measurement harnesses for a PostGIS-based
information system that stores, queries and analyses daily position estimates
and quality indicators produced by GNSS permanent-station processing.

This repository is the reproducibility artefact for the paper:

> F. Broncano, P. G. Rodríguez, A. Caro, A. Rivero-Cacho and A. Cuartero,
> *A PostGIS-Based Information System for Trustworthy and Quality-Aware
> Management of Multi-Constellation GNSS Data from Permanent Reference
> Stations*.

Positions are stored as first-class three-dimensional geometries in the
Earth-Centered Earth-Fixed (ECEF) frame (`SRID 4978`), together with the
processing provenance (station, date, constellation, estimation method), the
per-session quality statistics (DOP factors, residual RMS, satellite count,
receiver clock bias) and the conditions under which each row was produced: the
antenna and receiver in force at the station, the reference frame and epoch of
its published coordinates, and the software version and ephemeris product of
the run.

## Repository structure

```
repo/
├── postgis.cfg.example        # Connection settings template (copy to postgis.cfg)
├── .gitignore                 # Ignores credentials, benchmark data and bytecode
├── LICENSE
├── readme.md
└── scripts/
    ├── docker/
    │   ├── Dockerfile             # PostgreSQL 18 + PostGIS 3.6 image
    │   ├── docker-compose.yml     # Containerised deployment
    │   ├── initdb-postgis.sh      # Enables PostGIS extensions on first start
    │   └── update-postgis.sh      # Helper to upgrade the PostGIS extensions
    ├── sql/
    │   ├── create_tables.sql      # Relational and spatial schema, indexes
    │   ├── functions.sql          # Insert, retrieve, and frame-conversion functions
    │   └── patterns.sql           # The analytical patterns P1 to P5
    └── benchmark/
        ├── database/              # Data-access layer and the two file-centric harnesses
        │   ├── models.py          #   dataclasses mirroring the schema
        │   ├── dao.py             #   narrow interface over psycopg 3
        │   ├── export_csv.py      #   writes the CSV baseline into ../data
        │   ├── ingestion.py       #   cost of the persistence stage, isolated
        │   ├── patterns_numpy.py  #   the patterns reimplemented with numpy/pandas
        │   ├── benchmark.py       #   PostGIS vs files, on the case-study data
        │   └── benchmark_ngl.py   #   PostGIS vs files, on the NGL collection
        ├── scalability/
        │   ├── scalability.sql    #   synthetic generator, runs inside PostgreSQL
        │   └── scalability.py     #   drives the ladder and reports the timings
        └── spatialite/
            ├── load.py            #   ports the schema and data to SpatiaLite
            └── benchmark_spatialite.py
```

Three directories are absent from the repository by design and are regenerated
locally: `benchmark/data/` (produced by `export_csv.py`), `benchmark/data_txt/`
(downloaded from the Nevada Geodetic Laboratory) and the SpatiaLite database
(produced by `load.py`). See *Reproducing the measurements* below.

## Data model

Eleven tables in three groups.

**Data.**

| Table              | Purpose                                                                 |
|--------------------|-------------------------------------------------------------------------|
| `stations`         | Permanent reference stations, their published ECEF location, and the frame and epoch that location belongs to. |
| `daily_positions`  | One daily position per station, date, constellation and method, with per-component standard deviations and epoch counts. |
| `daily_statistics` | Per-session quality statistics, keyed without the method because they describe the session rather than the aggregation. |

**Conditions of production.**

| Table              | Purpose                                                                 |
|--------------------|-------------------------------------------------------------------------|
| `station_antenna`  | Antenna type, radome and offsets from the monument, over a validity interval. |
| `station_receiver` | Receiver type and firmware, over a validity interval.                   |
| `runs`             | One row per execution: software version, ephemeris product, decimation interval and reference frame. |

**Catalogues of controlled vocabularies.**

| Table              | Purpose                                                                 |
|--------------------|-------------------------------------------------------------------------|
| `constellations`   | GNSS configurations: Multi-GNSS, GPS, Galileo, BeiDou.                  |
| `methods`          | Daily aggregation methods: mean, median, PDOP-weighted, MAD-filtered.   |
| `software`         | Processing software, identified by name and version.                    |
| `ephemeris_type`   | Ephemeris products: broadcast, final, rapid, ultra-rapid.               |
| `reference_frames` | Datum realisations: ETRS89, REGCAN95, ITRF2014, ITRF2020, IGS20.        |

The last of these matters more than it looks. `SRID 4978` names the coordinate
reference system and not its realisation, so ETRS89, REGCAN95 and ITRF
coordinates are all stored under the same identifier while differing from one
another by up to 0.8 m. Recording which realisation a coordinate belongs to,
and at which epoch, is what makes it interpretable.

## SQL functions

`functions.sql` provides insertion (`insert_station`, `insert_daily_position`,
`insert_daily_statistics`), retrieval (`get_station`, `get_daily_positions`,
`get_daily_statistics`), analysis (`get_position_errors`,
`get_constellation_stats`) and coordinate-frame work (`ecef_to_enu`,
`enu_to_ecef`, `station_reference`).

`station_reference(station_id, date)` deserves a mention: it translates the
published coordinates of a station by the antenna offset in force on that date.
Comparing a solution, which is estimated at the antenna, against a published
coordinate, which refers to the monument, without that correction leaves a
systematic vertical error of the order of three metres.

`patterns.sql` provides the analytical patterns used in the paper:

| Pattern | Function                                                              |
|---------|-----------------------------------------------------------------------|
| P1      | `representative_positions` — geometric median, bias and mean daily error |
| P2      | `representative_enu` — the same, decomposed into East, North and Up    |
| P3      | `quality_filtered_positions` — aggregation filtered by quality indicators |
| P4      | `rms_outlier_sessions` — MAD-based detection of degraded sessions      |
| P5      | `positions_in_envelope_2d`, `positions_in_envelope_nd`, `nearest_positions_nd` — spatial predicates |

**Load order matters.** `patterns.sql` calls `ecef_to_enu` and
`station_reference`, which live in `functions.sql`, so the three files must be
applied as `create_tables.sql`, then `functions.sql`, then `patterns.sql`.

## Requirements

- [Docker](https://docs.docker.com/get-docker/) and the Docker Compose plugin, **or**
- An existing PostgreSQL 18 server with the PostGIS 3.x extension available.

To run the measurement harnesses, Python 3.11 or later with `psycopg`, `numpy`
and `pandas`. The SpatiaLite comparison additionally needs `libspatialite`
(`brew install libspatialite`, or the equivalent package on Linux).

## Configuration

All connection settings live in a single file, **`postgis.cfg`**, which is kept
out of version control so your credentials are never published. Create it from
the provided template:

```bash
cp postgis.cfg.example postgis.cfg
```

Then edit `postgis.cfg` with your own values:

```ini
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=positions
POSTGRES_USER=user
POSTGRES_PASSWORD=password
POSTGRES_SCHEMA=public
```

The same file is read by Docker Compose through `--env-file` and by the Python
data-access layer, which walks up from its own directory to find it.
Environment variables take precedence, so another instance can be targeted
without editing the file.

> **Note:** keep `POSTGRES_DB=positions` unless you also update the first two
> `ALTER DATABASE positions ...` lines of `scripts/sql/create_tables.sql`, which
> reference the database by name.

## Deployment with Docker

All commands below are run from the repository root and pass your settings with
`--env-file postgis.cfg`.

### 1. Start the database

```bash
docker compose --env-file postgis.cfg -f scripts/docker/docker-compose.yml up -d --build
```

This builds the PostgreSQL 18 + PostGIS 3.6 image, creates the database defined
by `POSTGRES_DB` and enables the PostGIS extensions automatically.

### 2. Load the schema, the functions and the patterns

```bash
for f in create_tables functions patterns; do
  docker compose --env-file postgis.cfg -f scripts/docker/docker-compose.yml \
    exec -T postgis-spp psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    < "scripts/sql/$f.sql"
done
```

(If your shell has not exported `POSTGRES_USER`/`POSTGRES_DB`, replace them with
the literal values from your `postgis.cfg`.)

### 3. Stop / remove

```bash
docker compose -f scripts/docker/docker-compose.yml down        # stop
docker compose -f scripts/docker/docker-compose.yml down -v     # stop and delete data
```

## Deployment on an existing PostgreSQL/PostGIS server

```bash
createdb -h "$POSTGRES_HOST" -U "$POSTGRES_USER" "$POSTGRES_DB"
for f in create_tables functions patterns; do
  psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "scripts/sql/$f.sql"
done
```

## Reproducing the measurements

All harnesses read the connection settings from `postgis.cfg` and are run from
their own directory, so that their relative paths to `data/` resolve.

```bash
cd scripts/benchmark/database

python export_csv.py ../data        # writes the CSV baseline
python benchmark.py                 # PostGIS vs files, case-study data
python ingestion.py                 # cost of the persistence stage
```

The NGL comparison needs the published collection first. Download the station
series from the Nevada Geodetic Laboratory into `scripts/benchmark/data_txt/`,
one file per station, then:

```bash
cd scripts/benchmark/database && python benchmark_ngl.py
```

The scalability ladder runs against a **separate** database so that the
synthetic records never mix with the real ones:

```bash
# from the repository root
docker build -t geognss-postgis scripts/docker
docker run -d --name geognss-bench -p 5433:5432 \
  -e POSTGRES_PASSWORD=bench -e POSTGRES_DB=bench geognss-postgis

PGPASSWORD=bench psql -h localhost -p 5433 -U postgres -d bench \
  -f scripts/benchmark/scalability/scalability.sql

cd scripts/benchmark/scalability
BENCH_PORT=5433 BENCH_PASSWORD=bench python scalability.py 1e4 1e5 1e6
```

The generator lives in `scalability.sql` and runs inside PostgreSQL, not in
Python. `build_scale(n_rows)` walks the cartesian product of station, day,
constellation and method and clusters the positions around synthetic
centroids with a scatter of a few metres; `drop_indexes()` and
`build_indexes()` let each rung be timed with and without the GiST indexes.
The reasoning behind those choices is documented in the file itself.

The SpatiaLite comparison needs the CSV baseline to exist:

```bash
cd scripts/benchmark/spatialite
python load.py && python benchmark_spatialite.py
```

## A note on BeiDou navigation files

Broadcast navigation records for BeiDou may carry a data field padded with 19
blanks, which is exactly one `D19.12` field of the RINEX 3 format. Some readers
do not strip it and fail to parse the ephemeris, which silently removes BeiDou
from the affected sessions without producing an error. Stripping the field
before parsing resolves it. This was observed with `georinex` and reported
upstream.

## License

MIT. See `LICENSE`.

## Citation

If you use this software or data model, please cite the paper referenced above.
