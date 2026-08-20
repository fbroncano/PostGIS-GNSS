ALTER DATABASE positions SET DATESTYLE TO 'SQL, DMY';
ALTER DATABASE positions SET TIMEZONE TO 'Europe/Madrid';
CREATE EXTENSION IF NOT EXISTS postgis;

-- Reference frames (datum realisations).
CREATE TABLE reference_frames (
    frame_id SMALLINT PRIMARY KEY,
    code     VARCHAR(16) NOT NULL UNIQUE,
    name     VARCHAR(60) NOT NULL
);

INSERT INTO reference_frames (frame_id, code, name) VALUES
    (0, 'unknown',  'Unknown or unspecified realisation'),
    (1, 'ETRS89',   'European Terrestrial Reference System 1989'),
    (2, 'REGCAN95', 'Red Geodesica de Canarias 1995'),
    (3, 'ITRF2014', 'International Terrestrial Reference Frame 2014'),
    (4, 'ITRF2020', 'International Terrestrial Reference Frame 2020'),
    (5, 'IGS20',    'IGS realisation of ITRF2020');

-- Permanent reference stations.
CREATE TABLE stations(
    id CHAR(4) PRIMARY KEY,
    name VARCHAR(25),
    city VARCHAR(27),
    province VARCHAR(13),
    initial_date DATE,
    location GEOMETRY(POINTZ, 4978),
    frame_id SMALLINT REFERENCES reference_frames(frame_id),
    coord_epoch NUMERIC(7,2)
);

-- Antenna configuration of each station, with its validity interval.
-- delta_h / delta_n / delta_e are the offset of the antenna reference point
-- from the monument marker, in the local ENU frame.
CREATE TABLE station_antenna (
    id             SERIAL PRIMARY KEY,
    station_id     CHAR(4) NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    valid_from     TIMESTAMPTZ NOT NULL,
    valid_to       TIMESTAMPTZ,
    antenna_type   VARCHAR(20),
    antenna_serial VARCHAR(30),
    radome         VARCHAR(10),
    delta_h        NUMERIC(8,4) NOT NULL,
    delta_n        NUMERIC(8,4) NOT NULL DEFAULT 0,
    delta_e        NUMERIC(8,4) NOT NULL DEFAULT 0,
    UNIQUE (station_id, valid_from)
);

-- Receiver configuration of each station, with its validity interval
CREATE TABLE station_receiver (
    id            SERIAL PRIMARY KEY,
    station_id    CHAR(4) NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    valid_from    TIMESTAMPTZ NOT NULL,
    valid_to      TIMESTAMPTZ,
    receiver_type VARCHAR(30),
    firmware      VARCHAR(40),
    UNIQUE (station_id, valid_from)
);

-- Satellite constellations and the multi-GNSS combination
CREATE TABLE constellations (
    id SMALLINT PRIMARY KEY,
    code VARCHAR(3) NOT NULL,
    name VARCHAR(20) NOT NULL
);

INSERT INTO constellations (id, code, name) VALUES
    (0, 'GEC', 'Multi-GNSS'),
    (1, 'G', 'GPS'),
    (2, 'E', 'Galileo'),
    (3, 'C', 'BeiDou');

-- Daily aggregation methods
CREATE TABLE methods (
    id SMALLINT PRIMARY KEY,
    code VARCHAR(15) NOT NULL,
    description VARCHAR(50)
);

INSERT INTO methods (id, code, description) VALUES
    (1, 'mean', 'Arithmetic mean'),
    (2, 'median', 'Median'),
    (3, 'weighted', 'PDOP-weighted mean'),
    (4, 'mad', 'MAD filter + mean'),
    (5, 'weighted_mad', 'MAD filter + PDOP-weighted mean');

-- Processing software, identified by name and version
CREATE TABLE software (
    sw_id SMALLINT PRIMARY KEY,
    sw_name VARCHAR NOT NULL,
    sw_version VARCHAR NOT NULL,
    UNIQUE (sw_name, sw_version)
);

INSERT INTO software (sw_id, sw_name, sw_version) VALUES
    (0, 'unknown', 'unknown'),
    (1, 'SPPs own implementation', '1.1.0');

-- Ephemeris products
CREATE TABLE ephemeris_type (
    ep_id SMALLINT PRIMARY KEY,
    ep_description VARCHAR NOT NULL
);

INSERT INTO ephemeris_type (ep_id, ep_description) VALUES
    (0, 'unknown'),
    (1, 'broadcast'),
    (2, 'final'),
    (3, 'rapid'),
    (4, 'ultra-rapid');

-- Processing runs
CREATE TABLE runs (
    run_id SERIAL PRIMARY KEY,
    sw_id SMALLINT NOT NULL REFERENCES software(sw_id),
    ep_id SMALLINT NOT NULL REFERENCES ephemeris_type(ep_id),
    frame_id SMALLINT REFERENCES reference_frames(frame_id),
    decimation_s INT NOT NULL,
    time TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Daily position estimates
CREATE TABLE daily_positions (
    id SERIAL PRIMARY KEY,
    station_id CHAR(4) NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    constellation_id SMALLINT NOT NULL REFERENCES constellations(id),
    method_id SMALLINT NOT NULL REFERENCES methods(id),
    run_id INTEGER NOT NULL REFERENCES runs(run_id),

    -- ECEF position
    position GEOMETRY(POINTZ, 4978) NOT NULL,
    
    -- Standard deviations
    std_x NUMERIC,
    std_y NUMERIC,
    std_z NUMERIC,
    std_3d NUMERIC,

    -- Statistics
    n_epochs_used INTEGER,
    n_epochs INTEGER,

    UNIQUE(station_id, date, constellation_id, method_id)
);

-- Per-session quality indicators (one row per station, date and constellation)
CREATE TABLE daily_statistics (
    id SERIAL PRIMARY KEY,
    station_id CHAR(4) NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    constellation_id SMALLINT NOT NULL REFERENCES constellations(id),
    run_id INTEGER NOT NULL REFERENCES runs(run_id),

    -- Statistics
    n_satellites_avg NUMERIC,
    clock_bias_m NUMERIC,

    -- Dilution of precision
    gdop_avg NUMERIC, pdop_avg NUMERIC, pdop_max NUMERIC, tdop_avg NUMERIC,

    -- Residual RMS
    rms_avg NUMERIC, rms_max NUMERIC,

    UNIQUE(station_id, date, constellation_id)
);

-- Indexes
CREATE INDEX idx_daily_pos_constellation ON daily_statistics(station_id);
CREATE INDEX idx_daily_pos_method ON daily_statistics(date);
CREATE INDEX idx_daily_pos_station ON daily_positions(station_id);
CREATE INDEX idx_daily_pos_date ON daily_positions(date);
CREATE INDEX idx_daily_pos_geom ON daily_positions USING GIST(position);
CREATE INDEX idx_daily_pos_geom_nd ON daily_positions USING GIST (position gist_geometry_ops_nd);
CREATE INDEX idx_stations_geom_nd ON stations USING GIST (location gist_geometry_ops_nd);
CREATE INDEX idx_station_antenna_lookup ON station_antenna(station_id, valid_from, valid_to);
CREATE INDEX idx_station_receiver_lookup ON station_receiver(station_id, valid_from, valid_to);
