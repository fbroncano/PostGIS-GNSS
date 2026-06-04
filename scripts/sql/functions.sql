-- Insertar estación
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

-- Obtener estación
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
    z DOUBLE PRECISION
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
        ST_Z(s.location)
    FROM stations s
    WHERE s.id = p_id;
END;
$$ LANGUAGE plpgsql;

-- Insertar posición diaria
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
    p_n_epochs INTEGER
)
RETURNS INTEGER AS $$
DECLARE
    v_id INTEGER;
BEGIN
    INSERT INTO daily_positions (
        station_id, date, constellation_id, method_id,
        position, std_x, std_y, std_z, std_3d,
        n_epochs_used, n_epochs
    )
    VALUES (
        p_station_id, p_date, p_constellation_id, p_method_id,
        ST_SetSRID(ST_MakePoint(p_x, p_y, p_z), 4978),
        p_std_x, p_std_y, p_std_z, p_std_3d,
        p_n_epochs_used, p_n_epochs
    )
    ON CONFLICT (station_id, date, constellation_id, method_id)
    DO UPDATE SET
        position = EXCLUDED.position,
        std_x = EXCLUDED.std_x,
        std_y = EXCLUDED.std_y,
        std_z = EXCLUDED.std_z,
        std_3d = EXCLUDED.std_3d,
        n_epochs_used = EXCLUDED.n_epochs_used,
        n_epochs = EXCLUDED.n_epochs
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$ LANGUAGE plpgsql;

-- Insertar estadísticas diarias
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
    p_rms_max NUMERIC
)
RETURNS INTEGER AS $$
DECLARE
    v_id INTEGER;
BEGIN
    INSERT INTO daily_statistics (
        station_id, date, constellation_id,
        n_satellites_avg, clock_bias_m,
        gdop_avg, pdop_avg, pdop_max, tdop_avg,
        rms_avg, rms_max
    )
    VALUES (
        p_station_id, p_date, p_constellation_id,
        p_n_satellites_avg, p_clock_bias_m,
        p_gdop_avg, p_pdop_avg, p_pdop_max, p_tdop_avg,
        p_rms_avg, p_rms_max
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
        rms_max = EXCLUDED.rms_max
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$ LANGUAGE plpgsql;

-- Obtener posiciones diarias por estación y fecha
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
    n_epochs INTEGER
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
        dp.n_epochs
    FROM daily_positions dp
    WHERE dp.station_id = p_station_id AND dp.date = p_date
    ORDER BY dp.constellation_id, dp.method_id;
END;
$$ LANGUAGE plpgsql;

-- Obtener estadísticas diarias por estación y fecha
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
    rms_max NUMERIC
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
        ds.rms_max
    FROM daily_statistics ds
    WHERE ds.station_id = p_station_id AND ds.date = p_date
    ORDER BY ds.constellation_id;
END;
$$ LANGUAGE plpgsql;

-- Calcular error respecto a posición de referencia
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

-- Estadísticas agregadas por constelación
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