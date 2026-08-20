-- Insert a station
CREATE OR REPLACE FUNCTION insert_station(
    p_id CHAR(4),
    p_name VARCHAR(25),
    p_city VARCHAR(27),
    p_province VARCHAR(13),
    p_initial_date DATE,
    p_x DOUBLE PRECISION,
    p_y DOUBLE PRECISION,
    p_z DOUBLE PRECISION
)
RETURNS VOID AS $$
BEGIN
    INSERT INTO stations (id, name, city, province, initial_date, location)
    VALUES (
        p_id,
        p_name,
        p_city,
        p_province,
        p_initial_date,
        ST_SetSRID(ST_MakePoint(p_x, p_y, p_z), 4978)
    );
END;
$$ LANGUAGE plpgsql;

-- Retrieve a station. frame_id and coord_epoch travel with the coordinates:
-- location alone does not say which realisation or epoch it refers to.
DROP FUNCTION IF EXISTS get_station(CHAR(4));
CREATE OR REPLACE FUNCTION get_station(
    p_id CHAR(4)
)
RETURNS TABLE(
    station_id CHAR(4),
    name VARCHAR(25),
    city VARCHAR(27),
    province VARCHAR(13),
    initial_date DATE,
    x DOUBLE PRECISION,
    y DOUBLE PRECISION,
    z DOUBLE PRECISION,
    frame_id SMALLINT,
    coord_epoch NUMERIC
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        s.id,
        s.name,
        s.city,
        s.province,
        s.initial_date,
        ST_X(s.location),
        ST_Y(s.location),
        ST_Z(s.location),
        s.frame_id,
        s.coord_epoch
    FROM stations s
    WHERE s.id = p_id;
END;
$$ LANGUAGE plpgsql;

-- Insert (or update) a daily position
CREATE OR REPLACE FUNCTION insert_daily_position(
    p_station_id CHAR(4),
    p_date DATE,
    p_constellation_id SMALLINT,
    p_method_id SMALLINT,
    p_x NUMERIC,
    p_y NUMERIC,
    p_z NUMERIC,
    p_std_x NUMERIC,
    p_std_y NUMERIC,
    p_std_z NUMERIC,
    p_std_3d NUMERIC,
    p_n_epochs_used INTEGER,
    p_n_epochs INTEGER,
    p_run_id INTEGER
)
RETURNS INTEGER AS $$
DECLARE
    v_id INTEGER;
BEGIN
    INSERT INTO daily_positions (
        station_id, date, constellation_id, method_id,
        position, std_x, std_y, std_z, std_3d,
        n_epochs_used, n_epochs, run_id
    )
    VALUES (
        p_station_id, p_date, p_constellation_id, p_method_id,
        ST_SetSRID(ST_MakePoint(p_x, p_y, p_z), 4978),
        p_std_x, p_std_y, p_std_z, p_std_3d,
        p_n_epochs_used, p_n_epochs, p_run_id
    )
    ON CONFLICT (station_id, date, constellation_id, method_id)
    DO UPDATE SET
        position = EXCLUDED.position,
        std_x = EXCLUDED.std_x,
        std_y = EXCLUDED.std_y,
        std_z = EXCLUDED.std_z,
        std_3d = EXCLUDED.std_3d,
        n_epochs_used = EXCLUDED.n_epochs_used,
        n_epochs = EXCLUDED.n_epochs,
        run_id = EXCLUDED.run_id
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$ LANGUAGE plpgsql;

-- Insert (or update) the daily statistics of a session
CREATE OR REPLACE FUNCTION insert_daily_statistics(
    p_station_id CHAR(4),
    p_date DATE,
    p_constellation_id SMALLINT,
    p_n_satellites_avg NUMERIC,
    p_clock_bias_m NUMERIC,
    p_gdop_avg NUMERIC,
    p_pdop_avg NUMERIC,
    p_pdop_max NUMERIC,
    p_tdop_avg NUMERIC,
    p_rms_avg NUMERIC,
    p_rms_max NUMERIC,
    p_run_id INTEGER
)
RETURNS INTEGER AS $$
DECLARE
    v_id INTEGER;
BEGIN
    INSERT INTO daily_statistics (
        station_id, date, constellation_id,
        n_satellites_avg, clock_bias_m,
        gdop_avg, pdop_avg, pdop_max, tdop_avg,
        rms_avg, rms_max, run_id
    )
    VALUES (
        p_station_id, p_date, p_constellation_id,
        p_n_satellites_avg, p_clock_bias_m,
        p_gdop_avg, p_pdop_avg, p_pdop_max, p_tdop_avg,
        p_rms_avg, p_rms_max, p_run_id
    )
    ON CONFLICT (station_id, date, constellation_id)
    DO UPDATE SET
        n_satellites_avg = EXCLUDED.n_satellites_avg,
        clock_bias_m = EXCLUDED.clock_bias_m,
        gdop_avg = EXCLUDED.gdop_avg,
        pdop_avg = EXCLUDED.pdop_avg,
        pdop_max = EXCLUDED.pdop_max,
        tdop_avg = EXCLUDED.tdop_avg,
        rms_avg = EXCLUDED.rms_avg,
        rms_max = EXCLUDED.rms_max,
        run_id = EXCLUDED.run_id
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$ LANGUAGE plpgsql;

-- Retrieve the daily positions of a station on a given date.
-- Dropped first: CREATE OR REPLACE cannot change the row type declared by the
-- OUT parameters, so replaying this script over a database created before
-- run_id was added would otherwise fail.
DROP FUNCTION IF EXISTS get_daily_positions(CHAR(4), DATE);
CREATE OR REPLACE FUNCTION get_daily_positions(
    p_station_id CHAR(4),
    p_date DATE
)
RETURNS TABLE(
    constellation_code VARCHAR(3),
    constellation_name VARCHAR(20),
    method VARCHAR(15),
    x DOUBLE PRECISION,
    y DOUBLE PRECISION,
    z DOUBLE PRECISION,
    std_3d NUMERIC,
    n_epochs_used INTEGER,
    n_epochs INTEGER,
    run_id INTEGER
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        (SELECT c.code FROM constellations c WHERE c.id = dp.constellation_id),
        (SELECT c.name FROM constellations c WHERE c.id = dp.constellation_id),
        (SELECT m.code FROM methods m WHERE m.id = dp.method_id),
        ST_X(dp.position),
        ST_Y(dp.position),
        ST_Z(dp.position),
        dp.std_3d,
        dp.n_epochs_used,
        dp.n_epochs,
        dp.run_id
    FROM daily_positions dp
    WHERE dp.station_id = p_station_id AND dp.date = p_date
    ORDER BY dp.constellation_id, dp.method_id;
END;
$$ LANGUAGE plpgsql;

-- Retrieve the daily statistics of a station on a given date (see above)
DROP FUNCTION IF EXISTS get_daily_statistics(CHAR(4), DATE);
CREATE OR REPLACE FUNCTION get_daily_statistics(
    p_station_id CHAR(4),
    p_date DATE
)
RETURNS TABLE(
    constellation_code VARCHAR(3),
    constellation_name VARCHAR(20),
    n_satellites_avg NUMERIC,
    clock_bias_m NUMERIC,
    gdop_avg NUMERIC,
    pdop_avg NUMERIC,
    pdop_max NUMERIC,
    tdop_avg NUMERIC,
    rms_avg NUMERIC,
    rms_max NUMERIC,
    run_id INTEGER
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        (SELECT c.code FROM constellations c WHERE c.id = ds.constellation_id),
        (SELECT c.name FROM constellations c WHERE c.id = ds.constellation_id),
        ds.n_satellites_avg,
        ds.clock_bias_m,
        ds.gdop_avg,
        ds.pdop_avg,
        ds.pdop_max,
        ds.tdop_avg,
        ds.rms_avg,
        ds.rms_max,
        ds.run_id
    FROM daily_statistics ds
    WHERE ds.station_id = p_station_id AND ds.date = p_date
    ORDER BY ds.constellation_id;
END;
$$ LANGUAGE plpgsql;

-- Error of each daily position with respect to the reference coordinates
CREATE OR REPLACE FUNCTION get_position_errors(
    p_station_id CHAR(4),
    p_date DATE
)
RETURNS TABLE(
    constellation_code VARCHAR(3),
    method VARCHAR(15),
    error_x DOUBLE PRECISION,
    error_y DOUBLE PRECISION,
    error_z DOUBLE PRECISION,
    error_3d DOUBLE PRECISION
) AS $$
DECLARE
    v_ref_x DOUBLE PRECISION;
    v_ref_y DOUBLE PRECISION;
    v_ref_z DOUBLE PRECISION;
BEGIN
    SELECT ST_X(s.location), ST_Y(s.location), ST_Z(s.location)
    INTO v_ref_x, v_ref_y, v_ref_z
    FROM stations s
    WHERE s.id = p_station_id;

    RETURN QUERY
    SELECT
        (SELECT c.code FROM constellations c WHERE c.id = dp.constellation_id),
        (SELECT m.code FROM methods m WHERE m.id = dp.method_id),
        ST_X(dp.position) - v_ref_x,
        ST_Y(dp.position) - v_ref_y,
        ST_Z(dp.position) - v_ref_z,
        SQRT(
            POWER(ST_X(dp.position) - v_ref_x, 2) +
            POWER(ST_Y(dp.position) - v_ref_y, 2) +
            POWER(ST_Z(dp.position) - v_ref_z, 2)
        )
    FROM daily_positions dp
    WHERE dp.station_id = p_station_id AND dp.date = p_date
    ORDER BY dp.constellation_id, dp.method_id;
END;
$$ LANGUAGE plpgsql;

-- Aggregated statistics per constellation
CREATE OR REPLACE FUNCTION get_constellation_stats(
    p_station_id CHAR(4),
    p_method_id SMALLINT DEFAULT 5
)
RETURNS TABLE(
    constellation_name VARCHAR(20),
    n_days BIGINT,
    avg_error_3d DOUBLE PRECISION,
    avg_n_satellites NUMERIC,
    avg_pdop NUMERIC,
    avg_rms NUMERIC
) AS $$
DECLARE
    v_ref_x DOUBLE PRECISION;
    v_ref_y DOUBLE PRECISION;
    v_ref_z DOUBLE PRECISION;
BEGIN
    SELECT ST_X(s.location), ST_Y(s.location), ST_Z(s.location)
    INTO v_ref_x, v_ref_y, v_ref_z
    FROM stations s
    WHERE s.id = p_station_id;

    RETURN QUERY
    SELECT
        (SELECT c.name FROM constellations c WHERE c.id = dp.constellation_id),
        COUNT(*),
        AVG(SQRT(
            POWER(ST_X(dp.position) - v_ref_x, 2) +
            POWER(ST_Y(dp.position) - v_ref_y, 2) +
            POWER(ST_Z(dp.position) - v_ref_z, 2)
        )),
        AVG(ds.n_satellites_avg),
        AVG(ds.pdop_avg),
        AVG(ds.rms_avg)
    FROM daily_positions dp
    JOIN daily_statistics ds ON dp.station_id = ds.station_id
        AND dp.date = ds.date
        AND dp.constellation_id = ds.constellation_id
    WHERE dp.station_id = p_station_id AND dp.method_id = p_method_id
    GROUP BY dp.constellation_id
    ORDER BY dp.constellation_id;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- Conversions between the global ECEF frame and the local topocentric ENU
-- frame, and reduction of the reference coordinates by the antenna height.
-- =============================================================================

-- Composite type returned by ecef_to_enu()
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'enu_offset') THEN
        CREATE TYPE enu_offset AS (
            east  DOUBLE PRECISION,
            north DOUBLE PRECISION,
            up    DOUBLE PRECISION
        );
    END IF;
END$$;

-- Rotates an ECEF offset into the topocentric frame of a reference point.
-- The geodetic latitude and longitude of the origin are derived from the
-- reference geometry itself, so callers never handle angles.
CREATE OR REPLACE FUNCTION ecef_to_enu(
    p_position  GEOMETRY,
    p_reference GEOMETRY
)
RETURNS enu_offset
LANGUAGE sql STABLE AS $$
    SELECT ROW(
        -sin(lon) * dx + cos(lon) * dy,
        -sin(lat) * cos(lon) * dx - sin(lat) * sin(lon) * dy + cos(lat) * dz,
         cos(lat) * cos(lon) * dx + cos(lat) * sin(lon) * dy + sin(lat) * dz
    )::enu_offset
    FROM (
        SELECT ST_X(p_position) - ST_X(p_reference) AS dx,
               ST_Y(p_position) - ST_Y(p_reference) AS dy,
               ST_Z(p_position) - ST_Z(p_reference) AS dz,
               radians(ST_Y(ST_Transform(p_reference, 4326))) AS lat,
               radians(ST_X(ST_Transform(p_reference, 4326))) AS lon
    ) t;
$$;

-- Bulk variant of ecef_to_enu(): the caller supplies the geodetic angles of the
-- origin, computed once per station instead of once per row.
--
-- The two-geometry form above derives them with ST_Transform on every call,
-- which dominates the cost when the function is applied row by row inside an
-- aggregation: on a 100k-row scan the per-row transform is two orders of
-- magnitude more expensive than the rotation itself. Use this form whenever the
-- origin is constant across a group.
CREATE OR REPLACE FUNCTION ecef_to_enu(
    p_position  GEOMETRY,
    p_reference GEOMETRY,
    p_lat       DOUBLE PRECISION,
    p_lon       DOUBLE PRECISION
)
RETURNS enu_offset
LANGUAGE sql IMMUTABLE AS $$
    SELECT ROW(
        -sin(p_lon) * dx + cos(p_lon) * dy,
        -sin(p_lat) * cos(p_lon) * dx - sin(p_lat) * sin(p_lon) * dy + cos(p_lat) * dz,
         cos(p_lat) * cos(p_lon) * dx + cos(p_lat) * sin(p_lon) * dy + sin(p_lat) * dz
    )::enu_offset
    FROM (
        SELECT ST_X(p_position) - ST_X(p_reference) AS dx,
               ST_Y(p_position) - ST_Y(p_reference) AS dy,
               ST_Z(p_position) - ST_Z(p_reference) AS dz
    ) t;
$$;

-- Inverse rotation: applies an ENU offset, in metres, to an ECEF point.
-- The matrix is the transpose of the one used in ecef_to_enu().
CREATE OR REPLACE FUNCTION enu_to_ecef(
    p_east      DOUBLE PRECISION,
    p_north     DOUBLE PRECISION,
    p_up        DOUBLE PRECISION,
    p_reference GEOMETRY
)
RETURNS GEOMETRY
LANGUAGE sql STABLE AS $$
    SELECT ST_SetSRID(ST_MakePoint(
        ST_X(p_reference) + (-sin(lon) * p_east - sin(lat) * cos(lon) * p_north
                             + cos(lat) * cos(lon) * p_up),
        ST_Y(p_reference) + ( cos(lon) * p_east - sin(lat) * sin(lon) * p_north
                             + cos(lat) * sin(lon) * p_up),
        ST_Z(p_reference) + ( cos(lat) * p_north + sin(lat) * p_up)
    ), 4978)
    FROM (
        SELECT radians(ST_Y(ST_Transform(p_reference, 4326))) AS lat,
               radians(ST_X(ST_Transform(p_reference, 4326))) AS lon
    ) t;
$$;

-- Reference coordinate directly comparable with a GNSS solution.
--
-- stations.location holds the official IGN position referred to the monument
-- marker, whereas the SPP solution is estimated at the antenna. This function
-- translates the reference by the antenna offset in force on the given date,
-- so that the two quantities become directly comparable.
--
-- If the station has no antenna configuration recorded for that date, the
-- reference is returned uncorrected.
CREATE OR REPLACE FUNCTION station_reference(
    p_station_id CHAR(4),
    p_date       DATE
)
RETURNS GEOMETRY
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(
        (SELECT enu_to_ecef(a.delta_e::DOUBLE PRECISION,
                            a.delta_n::DOUBLE PRECISION,
                            a.delta_h::DOUBLE PRECISION,
                            s.location)
         FROM   station_antenna a
         WHERE  a.station_id = s.id
           AND  p_date >= a.valid_from::DATE
           AND  (a.valid_to IS NULL OR p_date < a.valid_to::DATE)
         ORDER  BY a.valid_from DESC
         LIMIT  1),
        s.location)
    FROM stations s
    WHERE s.id = p_station_id;
$$;
