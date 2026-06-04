# PostGIS Information System for Multi-Constellation GNSS Data

Data model and deployment scripts for a PostGIS-based information system that
stores, queries and analyses daily position estimates and quality indicators
produced by GNSS permanent-station processing.

This repository is the reproducibility artefact for the paper:

> F. Broncano, P. G. Rodríguez, A. Caro and A. Cuartero,
> *A PostGIS-Based Information System for Trustworthy and Quality-Aware
> Management of Multi-Constellation GNSS Data from Permanent Reference
> Stations*.

Positions are stored as first-class three-dimensional geometries in the
Earth-Centered Earth-Fixed (ECEF) frame (`SRID 4978`), together with the full
processing provenance (station, date, constellation, estimation method) and the
per-session quality statistics (DOP factors, residual RMS, satellite count,
receiver clock bias).

## Repository structure

```
repo/
├── postgis.cfg.example        # Connection settings template (copy to postgis.cfg)
├── .gitignore                 # Ignores the real postgis.cfg with your credentials
├── readme.md
└── scripts/
    ├── docker/
    │   ├── Dockerfile             # PostgreSQL 18 + PostGIS 3.6 image
    │   ├── docker-compose.yml     # Containerised deployment
    │   ├── initdb-postgis.sh      # Enables PostGIS extensions on first start
    │   └── update-postgis.sh      # Helper to upgrade the PostGIS extensions
    └── sql/
        ├── create_tables.sql      # Relational + spatial schema (tables, indexes)
        └── functions.sql          # Analytical SQL functions (insert/query/analysis)
```

## Data model

| Table             | Purpose                                                                 |
|-------------------|-------------------------------------------------------------------------|
| `stations`        | Permanent reference stations and their reference ECEF location.         |
| `constellations`  | GNSS configurations (Multi-GNSS, GPS, Galileo, BeiDou).                 |
| `methods`         | Positional estimation methods (mean, median, weighted, MAD, ...).       |
| `daily_positions` | One daily position per station/date/constellation/method, with std-dev. |
| `daily_statistics`| Per-session quality statistics (DOP, RMS, satellites, clock bias).      |

The analytical functions in `functions.sql` cover insertion
(`insert_station`, `insert_daily_position`, `insert_daily_statistics`),
retrieval (`get_station`, `get_daily_positions`, `get_daily_statistics`) and
analysis (`get_position_errors`, `get_constellation_stats`).

## Requirements

- [Docker](https://docs.docker.com/get-docker/) and the Docker Compose plugin, **or**
- An existing PostgreSQL 18 server with the PostGIS 3.x extension available.

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

`postgis.cfg` is listed in `.gitignore`; only `postgis.cfg.example` (with
placeholder values) is committed.

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

### 2. Load the schema and the analytical functions

```bash
docker compose --env-file postgis.cfg -f scripts/docker/docker-compose.yml \
  exec -T postgis-ppp psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  < scripts/sql/create_tables.sql

docker compose --env-file postgis.cfg -f scripts/docker/docker-compose.yml \
  exec -T postgis-ppp psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  < scripts/sql/functions.sql
```

(If your shell has not exported `POSTGRES_USER`/`POSTGRES_DB`, replace them with
the literal values from your `postgis.cfg`.)

### 3. Stop / remove

```bash
docker compose -f scripts/docker/docker-compose.yml down        # stop
docker compose -f scripts/docker/docker-compose.yml down -v      # stop and delete data
```

## Deployment on an existing PostgreSQL/PostGIS server

If you already run PostgreSQL 18 with PostGIS, create the database and apply the
scripts directly:

```bash
createdb -h "$POSTGRES_HOST" -U "$POSTGRES_USER" "$POSTGRES_DB"
psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f scripts/sql/create_tables.sql
psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f scripts/sql/functions.sql
```

## Connecting from a client

Any GNSS-aware client (a Python data-access layer, `psql`, QGIS, Folium, ...)
reads the same `postgis.cfg`. For example, with `psql`:

```bash
PGPASSWORD="$POSTGRES_PASSWORD" psql \
  -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

A minimal Python loader of the configuration:

```python
def load_config(path="postgis.cfg"):
    cfg = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                cfg[key.strip()] = value.strip()
    return cfg
```

## License

See the repository license file (to be added before publication).

## Citation

If you use this software or data model, please cite the paper referenced above.
