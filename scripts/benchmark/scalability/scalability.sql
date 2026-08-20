-- =============================================================================
-- Scalability bench over synthetic records.
--
-- Builds a table with the same shape as daily_positions and measures how the
-- queries scale as the volume grows, with and without indexes, and under both
-- GiST operator classes.
--
-- The records are SYNTHETIC and serve ONLY to characterise the persistence
-- layer. They take no part in any geodetic result reported in the paper.
--
-- Two generation decisions matter:
--
--   * The key space is walked systematically (the cartesian product of station,
--     day, constellation and method) rather than at random. With random keys the
--     UNIQUE constraint aborts the load on the first collision, and the row
--     count would stop being exact.
--
--   * Positions are clustered around N centroids with a scatter of a few metres,
--     which is how real positions distribute. Spreading points uniformly over
--     the globe would give the spatial predicates a selectivity bearing no
--     resemblance to the real case, and the index measurement would mean
--     nothing.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS postgis;

-- Synthetic centroids, spread over plausible latitudes and longitudes and
-- converted to ECEF with the WGS84 ellipsoid.
CREATE OR REPLACE FUNCTION make_centroids(n_stations INT)
RETURNS TABLE (station_id INT, cx DOUBLE PRECISION, cy DOUBLE PRECISION, cz DOUBLE PRECISION)
LANGUAGE sql AS $$
    WITH s AS (
        SELECT g AS station_id,
               radians(27.0 + 17.0 * ((g * 7919) % 1000) / 1000.0) AS lat,
               radians(-18.0 + 22.0 * ((g * 6271) % 1000) / 1000.0) AS lon,
               50.0 + 2000.0 * ((g * 5039) % 1000) / 1000.0 AS h
        FROM generate_series(1, n_stations) g
    ),
    e AS (
        SELECT station_id, lat, lon, h,
               6378137.0 / sqrt(1 - 0.00669437999014 * sin(lat)^2) AS nrad
        FROM s
    )
    SELECT station_id,
           (nrad + h) * cos(lat) * cos(lon),
           (nrad + h) * cos(lat) * sin(lon),
           (nrad * (1 - 0.00669437999014) + h) * sin(lat)
    FROM e;
$$;

-- Build the table for the requested rung with exactly n_rows rows.
CREATE OR REPLACE PROCEDURE build_scale(n_rows BIGINT, n_stations INT DEFAULT 200)
LANGUAGE plpgsql AS $$
DECLARE
    n_days INT := ceil(n_rows::numeric / (n_stations * 4 * 5));
BEGIN
    DROP TABLE IF EXISTS synth_positions;
    CREATE TABLE synth_positions (
        id               BIGSERIAL PRIMARY KEY,
        station_id       INT      NOT NULL,
        date             DATE     NOT NULL,
        constellation_id SMALLINT NOT NULL,
        method_id        SMALLINT NOT NULL,
        position         GEOMETRY(POINTZ, 4978) NOT NULL,
        std_3d           NUMERIC,
        n_epochs         INTEGER,
        UNIQUE (station_id, date, constellation_id, method_id)
    );

    INSERT INTO synth_positions (station_id, date, constellation_id, method_id,
                                 position, std_3d, n_epochs)
    SELECT c.station_id,
           DATE '2000-01-01' + d,
           k % 4,
           1 + k / 4,
           ST_SetSRID(ST_MakePoint(
               c.cx + 6.0 * (random() - 0.5),
               c.cy + 6.0 * (random() - 0.5),
               c.cz + 6.0 * (random() - 0.5)), 4978),
           (random() * 9)::numeric(10,3),
           (200 + random() * 100)::int
    FROM make_centroids(n_stations) c
    CROSS JOIN generate_series(0, n_days - 1) d
    CROSS JOIN generate_series(0, 19) k
    LIMIT n_rows;

    ANALYZE synth_positions;
END;
$$;

-- Indexes, in both operator classes so that the two can be compared.
CREATE OR REPLACE PROCEDURE build_indexes()
LANGUAGE plpgsql AS $$
BEGIN
    CREATE INDEX synth_geom_2d ON synth_positions USING GIST (position);
    CREATE INDEX synth_geom_nd ON synth_positions USING GIST (position gist_geometry_ops_nd);
    CREATE INDEX synth_station ON synth_positions (station_id);
    ANALYZE synth_positions;
END;
$$;

CREATE OR REPLACE PROCEDURE drop_indexes()
LANGUAGE plpgsql AS $$
BEGIN
    DROP INDEX IF EXISTS synth_geom_2d;
    DROP INDEX IF EXISTS synth_geom_nd;
    DROP INDEX IF EXISTS synth_station;
    ANALYZE synth_positions;
END;
$$;
