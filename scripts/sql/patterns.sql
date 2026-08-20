-- =============================================================================
-- Analytical SQL patterns (P1-P4) of GeoGNSS-PS.
--
-- Each pattern is installed as a stored function so that the exact statement
-- used to produce the published results is version-controlled and callable by
-- name from psql, from the Python DAO and from QGIS alike.
--
-- Distances are 3D. ST_Distance() on geometry values evaluates the Cartesian
-- distance in the XY plane only and must not be used on ECEF POINTZ data.
--
-- Reference coordinates come from station_reference(), which translates the
-- official IGN monument coordinates by the antenna offset in force on the
-- given date. Comparing an SPP solution against the raw monument coordinates
-- leaves the antenna height as a systematic error in the Up component.
--
-- Requires functions.sql to be loaded first.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- P1. Representative position of each station over a date interval.
--
-- The geometric median of the daily estimates is computed once per station and
-- reported together with two distinct accuracy measures:
--
--   bias_3d          ||median - IGN reference||, the systematic offset that
--                    survives aggregation over the interval.
--   mean_daily_error mean of ||daily estimate - IGN reference||, the accuracy
--                    of a single daily solution (bias and scatter combined).
--
-- mean_daily_error >= bias_3d always holds; the gap quantifies how much daily
-- scatter the aggregation removes.
--
-- N counts the daily positions of one (station, constellation, method) triple,
-- so it is bounded by the number of processed days in the interval.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION representative_positions(
    p_constellation_id SMALLINT,
    p_method_id        SMALLINT,
    p_date_from        DATE,
    p_date_to          DATE
)
RETURNS TABLE (
    station_code     CHAR(4),
    n_positions      BIGINT,
    med_x            NUMERIC,
    med_y            NUMERIC,
    med_z            NUMERIC,
    bias_3d          NUMERIC,
    mean_daily_error NUMERIC,
    mean_std_3d      NUMERIC
)
LANGUAGE sql STABLE AS $$
    -- The antenna-corrected reference is resolved once per station, for the
    -- configuration in force at the end of the interval, instead of once per
    -- row. Mixing references within a single series would otherwise introduce
    -- a step at every antenna change.
    WITH ref AS (
        SELECT s.id AS station_id,
               station_reference(s.id, p_date_to) AS apc
        FROM   stations s
    ),
    agg AS (
        -- ST_GeometricMedian is evaluated once per station here; calling it
        -- separately for X, Y and Z would recompute the whole estimate.
        SELECT dp.station_id,
               COUNT(*)                                   AS n,
               ST_GeometricMedian(ST_Collect(dp.position)) AS p_med,
               AVG(ST_3DDistance(dp.position, r.apc))      AS mean_err,
               AVG(dp.std_3d)                              AS mean_std
        FROM   daily_positions dp
        JOIN   ref r ON r.station_id = dp.station_id
        WHERE  dp.constellation_id = p_constellation_id
          AND  dp.method_id        = p_method_id
          AND  dp.date BETWEEN p_date_from AND p_date_to
        GROUP  BY dp.station_id
    )
    SELECT a.station_id,
           a.n,
           ST_X(a.p_med)::NUMERIC(12,3),
           ST_Y(a.p_med)::NUMERIC(12,3),
           ST_Z(a.p_med)::NUMERIC(12,3),
           ST_3DDistance(a.p_med, r.apc)::NUMERIC(10,3),
           a.mean_err::NUMERIC(10,3),
           a.mean_std::NUMERIC(10,3)
    FROM   agg a
    JOIN   ref r ON r.station_id = a.station_id
    ORDER  BY a.station_id;
$$;


-- -----------------------------------------------------------------------------
-- P2. Positional error decomposed in the local East-North-Up frame.
--
-- The rotation itself lives in functions.sql as ecef_to_enu(), alongside its
-- inverse. P1 composed with it gives the representative offset of every
-- station expressed in metres of East, North and Up.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION representative_enu(
    p_constellation_id SMALLINT,
    p_method_id        SMALLINT,
    p_date_from        DATE,
    p_date_to          DATE
)
RETURNS TABLE (
    station_code CHAR(4),
    n_positions  BIGINT,
    east         NUMERIC,
    north        NUMERIC,
    up           NUMERIC,
    horizontal   NUMERIC
)
LANGUAGE sql STABLE AS $$
    WITH agg AS (
        SELECT dp.station_id,
               COUNT(*)                                    AS n,
               ST_GeometricMedian(ST_Collect(dp.position))  AS p_med
        FROM   daily_positions dp
        WHERE  dp.constellation_id = p_constellation_id
          AND  dp.method_id        = p_method_id
          AND  dp.date BETWEEN p_date_from AND p_date_to
        GROUP  BY dp.station_id
    ),
    -- LATERAL, y no (ecef_to_enu(...)).*, porque la seleccion de campo sobre el
    -- resultado de una funcion la evalua una vez por cada campo pedido. Con
    -- tres componentes eso triplica el coste, y en un barrido fila a fila la
    -- diferencia llega a ser de un orden de magnitud.
    enu AS (
        SELECT a.station_id, a.n, e.east, e.north, e.up
        FROM   agg a,
        LATERAL ecef_to_enu(a.p_med,
                            station_reference(a.station_id, p_date_to)) AS e
    )
    SELECT station_id,
           n,
           east::NUMERIC(10,3),
           north::NUMERIC(10,3),
           up::NUMERIC(10,3),
           sqrt(pow(east, 2) + pow(north, 2))::NUMERIC(10,3)
    FROM   enu
    ORDER  BY station_id;
$$;


-- -----------------------------------------------------------------------------
-- P3. Quality-aware aggregation.
--
-- Joins daily_positions against daily_statistics on the natural key
-- (station, date, constellation) and restricts the aggregation to the
-- sessions that pass the given quality thresholds.
--
-- The primary predicate is std_3d, the formal dispersion of the daily
-- estimate, which lives in daily_positions and is therefore specific to the
-- aggregation method. Over the case-study dataset it is the stored indicator
-- that tracks positional error most closely (r = 0.59), well ahead of the
-- session-level ones: mean PDOP reaches r = 0.19 and mean residual RMS is
-- uncorrelated (r = 0.00). The RMS indicators are averaged over every epoch
-- of the session, including the ones that the MAD-based methods discard, so
-- a handful of outlying epochs inflates them without affecting the position.
--
-- Mean PDOP is kept as a secondary, geometry-based predicate.
--
-- Both the total and the retained session counts are reported, so the effect
-- of the quality filter on the representative position is directly visible.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION quality_filtered_positions(
    p_constellation_id SMALLINT,
    p_method_id        SMALLINT,
    p_date_from        DATE,
    p_date_to          DATE,
    p_std_3d_max       NUMERIC,
    p_pdop_max         NUMERIC
)
RETURNS TABLE (
    station_code CHAR(4),
    n_total      BIGINT,
    n_kept       BIGINT,
    bias_all     NUMERIC,
    bias_kept    NUMERIC,
    mean_std_3d  NUMERIC,
    mean_pdop    NUMERIC
)
LANGUAGE sql STABLE AS $$
    WITH joined AS (
        SELECT dp.station_id,
               dp.position,
               dp.std_3d,
               ds.pdop_avg,
               (dp.std_3d <= p_std_3d_max AND ds.pdop_avg <= p_pdop_max) AS keep
        FROM   daily_positions  dp
        JOIN   daily_statistics ds
               ON  ds.station_id       = dp.station_id
               AND ds.date             = dp.date
               AND ds.constellation_id = dp.constellation_id
        WHERE  dp.constellation_id = p_constellation_id
          AND  dp.method_id        = p_method_id
          AND  dp.date BETWEEN p_date_from AND p_date_to
    ),
    agg AS (
        SELECT j.station_id,
               COUNT(*)                            AS n_total,
               COUNT(*) FILTER (WHERE j.keep)      AS n_kept,
               ST_GeometricMedian(ST_Collect(j.position))                       AS p_all,
               ST_GeometricMedian(ST_Collect(j.position) FILTER (WHERE j.keep)) AS p_kept,
               AVG(j.std_3d)                       AS mean_std_3d,
               AVG(j.pdop_avg)                     AS mean_pdop
        FROM   joined j
        GROUP  BY j.station_id
    )
    SELECT a.station_id,
           a.n_total,
           a.n_kept,
           ST_3DDistance(a.p_all,  station_reference(a.station_id, p_date_to))::NUMERIC(10,3),
           ST_3DDistance(a.p_kept, station_reference(a.station_id, p_date_to))::NUMERIC(10,3),
           a.mean_std_3d::NUMERIC(10,2),
           a.mean_pdop::NUMERIC(10,2)
    FROM   agg a
    ORDER  BY a.station_id;
$$;


-- -----------------------------------------------------------------------------
-- P4. MAD-based detection of degraded processing sessions.
--
-- Flags sessions whose maximum residual RMS exceeds the per-station,
-- per-constellation median by more than p_sigmas robust standard deviations,
-- the MAD being rescaled by 1.4826 to estimate sigma under approximately
-- Gaussian residuals.
--
-- The test is one-sided on purpose. A two-sided test on abs() also reports
-- the sessions whose residuals are unusually *low*, which are the best ones,
-- not the degraded ones this pattern is meant to surface.
--
-- Groups whose MAD is zero are skipped: with a degenerate scale estimate any
-- non-zero deviation would be reported as an outlier.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rms_outlier_sessions(
    p_sigmas NUMERIC DEFAULT 3
)
RETURNS TABLE (
    station_code     CHAR(4),
    session_date     DATE,
    constellation_id SMALLINT,
    rms_max          NUMERIC,
    rms_median       NUMERIC,
    rms_sigma        NUMERIC
)
LANGUAGE sql STABLE AS $$
    WITH med AS (
        SELECT ds.station_id,
               ds.constellation_id,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY ds.rms_max) AS rms_med
        FROM   daily_statistics ds
        GROUP  BY ds.station_id, ds.constellation_id
    ),
    mad AS (
        SELECT ds.station_id,
               ds.constellation_id,
               1.4826 * percentile_cont(0.5)
                        WITHIN GROUP (ORDER BY abs(ds.rms_max - med.rms_med))
                        AS rms_sigma
        FROM   daily_statistics ds
        JOIN   med USING (station_id, constellation_id)
        GROUP  BY ds.station_id, ds.constellation_id
    )
    SELECT ds.station_id,
           ds.date,
           ds.constellation_id,
           ds.rms_max::NUMERIC(10,3),
           med.rms_med::NUMERIC(10,3),
           mad.rms_sigma::NUMERIC(10,3)
    FROM   daily_statistics ds
    JOIN   med USING (station_id, constellation_id)
    JOIN   mad USING (station_id, constellation_id)
    WHERE  mad.rms_sigma > 0
      AND  ds.rms_max - med.rms_med > p_sigmas * mad.rms_sigma
    ORDER  BY ds.station_id, ds.constellation_id, ds.date;
$$;


-- -----------------------------------------------------------------------------
-- P5. Spatial-predicate patterns, used by the scalability benchmark.
--
-- P1-P4 filter on the natural key and on scalar quality indicators, so their
-- plans never exercise the geometry indexes. The two statements below do: the
-- first is served by the 2D operator class through &&, the second by the
-- n-dimensional one through &&&, which is the only way the Z extent of the
-- stored POINTZ geometries takes part in the index scan.
-- -----------------------------------------------------------------------------

-- Bounding-box selection (2D operator class, gist_geometry_ops_2d).
CREATE OR REPLACE FUNCTION positions_in_envelope_2d(
    p_xmin DOUBLE PRECISION, p_ymin DOUBLE PRECISION,
    p_xmax DOUBLE PRECISION, p_ymax DOUBLE PRECISION
)
RETURNS BIGINT
LANGUAGE sql STABLE AS $$
    SELECT COUNT(*)
    FROM   daily_positions dp
    WHERE  dp.position && ST_MakeEnvelope(p_xmin, p_ymin, p_xmax, p_ymax, 4978);
$$;

-- N-dimensional bounding-box selection (gist_geometry_ops_nd).
CREATE OR REPLACE FUNCTION positions_in_envelope_nd(
    p_xmin DOUBLE PRECISION, p_ymin DOUBLE PRECISION, p_zmin DOUBLE PRECISION,
    p_xmax DOUBLE PRECISION, p_ymax DOUBLE PRECISION, p_zmax DOUBLE PRECISION
)
RETURNS BIGINT
LANGUAGE sql STABLE AS $$
    SELECT COUNT(*)
    FROM   daily_positions dp
    WHERE  dp.position &&& ST_3DMakeBox(
               ST_SetSRID(ST_MakePoint(p_xmin, p_ymin, p_zmin), 4978),
               ST_SetSRID(ST_MakePoint(p_xmax, p_ymax, p_zmax), 4978));
$$;

-- Nearest daily positions to an arbitrary ECEF point, ordered by the
-- n-dimensional distance operator so that the KNN search is index-assisted.
CREATE OR REPLACE FUNCTION nearest_positions_nd(
    p_x DOUBLE PRECISION,
    p_y DOUBLE PRECISION,
    p_z DOUBLE PRECISION,
    p_limit INTEGER DEFAULT 5
)
RETURNS TABLE (
    station_code CHAR(4),
    session_date DATE,
    distance_3d  DOUBLE PRECISION
)
LANGUAGE sql STABLE AS $$
    SELECT dp.station_id,
           dp.date,
           ST_3DDistance(dp.position,
                         ST_SetSRID(ST_MakePoint(p_x, p_y, p_z), 4978))
    FROM   daily_positions dp
    ORDER  BY dp.position <<->> ST_SetSRID(ST_MakePoint(p_x, p_y, p_z), 4978)
    LIMIT  p_limit;
$$;
