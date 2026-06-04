ALTER DATABASE positions SET DATESTYLE TO 'SQL, DMY';
ALTER DATABASE positions SET TIMEZONE TO 'Europe/Madrid';
CREATE EXTENSION IF NOT EXISTS postgis;

-- Tabla de estaciones (sin cambios en estructura)
CREATE TABLE stations(
    id CHAR(4) PRIMARY KEY,
    name VARCHAR(25),
    city VARCHAR(27),
    province VARCHAR(13),
    initial_date DATE,
    location GEOMETRY(POINTZ, 4978)
);

-- Tabla de constelaciones
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

-- Tabla de métodos de promediado
CREATE TABLE methods (
    id SMALLINT PRIMARY KEY,
    code VARCHAR(15) NOT NULL,
    description VARCHAR(50)
);

INSERT INTO methods (id, code, description) VALUES
    (1, 'mean', 'Media aritmética'),
    (2, 'median', 'Mediana'),
    (3, 'weighted', 'Media ponderada por PDOP'),
    (4, 'mad', 'Filtro MAD + media'),
    (5, 'weighted_mad', 'Filtro MAD + media ponderada');

-- Tabla de posiciones diarias
CREATE TABLE daily_positions (
    id SERIAL PRIMARY KEY,
    station_id CHAR(4) REFERENCES stations(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    constellation_id SMALLINT REFERENCES constellations(id),
    method_id SMALLINT REFERENCES methods(id),
    
    -- Posición ECEF
    position GEOMETRY(POINTZ, 4978) NOT NULL,
    
    -- Desviación estándar
    std_x NUMERIC,
    std_y NUMERIC,
    std_z NUMERIC,
    std_3d NUMERIC,

    -- Estadísticas
    n_epochs_used INTEGER,
    n_epochs INTEGER,

    UNIQUE(station_id, date, constellation_id, method_id)
);

-- Estadísticas diarias por constelación (1 registro por día/constelación)
CREATE TABLE daily_statistics (
    id SERIAL PRIMARY KEY,
    station_id CHAR(4) REFERENCES stations(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    constellation_id SMALLINT REFERENCES constellations(id),

    -- Estadísticas
    n_satellites_avg NUMERIC,
    clock_bias_m NUMERIC,

    -- DOP
    gdop_avg NUMERIC,
    pdop_avg NUMERIC,
    pdop_max NUMERIC,
    tdop_avg NUMERIC,

    -- RMS
    rms_avg NUMERIC,
    rms_max NUMERIC,

    UNIQUE(station_id, date, constellation_id)
);

-- Índices
CREATE INDEX idx_daily_pos_constellation ON daily_statistics(station_id);
CREATE INDEX idx_daily_pos_method ON daily_statistics(date);
CREATE INDEX idx_daily_pos_station ON daily_positions(station_id);
CREATE INDEX idx_daily_pos_date ON daily_positions(date);
CREATE INDEX idx_daily_pos_geom ON daily_positions USING GIST(position);
