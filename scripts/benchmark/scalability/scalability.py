"""Scalability of the persistence layer, measured on synthetic records.

Walks a ladder of volumes and, at each rung, times four queries with and
without the declared indexes, recording whether the planner actually chooses
them. The publishable result is not the latency at any single rung but the
point at which the planner abandons the sequential scan, and whether such a
point exists at all for each query shape.

The records are synthetic and serve only to characterise the database. They
take no part in any geodetic result reported in the paper. They are generated
by build_scale() in scalability.sql, which runs inside PostgreSQL rather than
in this script.

Usage:
    python scalability.py [rung ...]      # defaults to 10^4 10^5 10^6
"""

import os
import re
import sys

import psycopg

DSN = dict(host=os.environ.get("BENCH_HOST", "localhost"),
           port=int(os.environ.get("BENCH_PORT", 5433)),
           dbname=os.environ.get("BENCH_DB", "bench"),
           user=os.environ.get("BENCH_USER", "postgres"),
           password=os.environ.get("BENCH_PASSWORD", ""))

SCALES = [10**4, 10**5, 10**6]

QUERIES = {
    # Planar spatial predicate, served by gist_geometry_ops_2d through &&.
    # This is also the operator a GIS client issues when filtering by map extent.
    "bbox 2D": """
        SELECT count(*) FROM synth_positions
        WHERE position && ST_MakeEnvelope({x0},{y0},{x1},{y1}, 4978)
    """,
    # N-dimensional spatial predicate. Only gist_geometry_ops_nd can serve it,
    # because it is the only class in which the Z extent enters the index scan.
    "bbox ND": """
        SELECT count(*) FROM synth_positions
        WHERE position &&& ST_3DMakeBox(
            ST_SetSRID(ST_MakePoint({x0},{y0},{z0}),4978),
            ST_SetSRID(ST_MakePoint({x1},{y1},{z1}),4978))
    """,
    # Nearest neighbours through the n-dimensional distance operator. The LIMIT
    # bounds the output, which is what lets the index keep the cost flat as the
    # table grows.
    "KNN ND": """
        SELECT id FROM synth_positions
        ORDER BY position <<->> ST_SetSRID(ST_MakePoint({cx},{cy},{cz}),4978)
        LIMIT 10
    """,
    # Aggregation with no spatial predicate. Included as the control: it shows
    # that a pattern which never mentions geometry cannot benefit from a
    # geometric index, at any volume.
    "aggregation": """
        SELECT station_id, count(*) FROM synth_positions
        GROUP BY station_id
    """,
}


def explain_analyze(cur, sql):
    """Run a query under EXPLAIN ANALYZE and return (milliseconds, used_index).

    The second element comes from inspecting the plan text for a scan node, so
    it reports what the planner actually chose rather than what was available.
    """
    cur.execute("EXPLAIN (ANALYZE, TIMING ON, FORMAT TEXT) " + sql)
    plan = "\n".join(r[0] for r in cur.fetchall())
    ms = float(re.search(r"Execution Time: ([\d.]+) ms", plan).group(1))
    used_index = bool(re.search(r"(Index Scan|Index Only Scan|Bitmap Index Scan)", plan))
    return ms, used_index


def median_of_runs(cur, params, repeats=3):
    """Time every query in QUERIES and return the median of `repeats` runs.

    One untimed execution precedes the measurement so that the comparison is
    between warm caches rather than between a cold and a warm one.
    """
    out = {}
    for name, template in QUERIES.items():
        sql = template.format(**params)
        explain_analyze(cur, sql)
        runs = [explain_analyze(cur, sql) for _ in range(repeats)]
        runs.sort(key=lambda t: t[0])
        out[name] = runs[len(runs) // 2]
    return out


def selective_box_around_station(cur, half_side=50.0):
    """Build the query parameters for a small box around one real centroid.

    Selectivity is what decides whether a spatial index pays off, so the box is
    deliberately small. Spreading the probe over the whole extent would measure
    a case no operational query ever issues.
    """
    cur.execute("""SELECT ST_X(position), ST_Y(position), ST_Z(position)
                   FROM synth_positions WHERE station_id = 1 LIMIT 1""")
    cx, cy, cz = cur.fetchone()
    d = half_side
    return dict(cx=cx, cy=cy, cz=cz,
                x0=cx - d, y0=cy - d, z0=cz - d,
                x1=cx + d, y1=cy + d, z1=cz + d)


def main():
    scales = [int(float(a)) for a in sys.argv[1:]] or SCALES
    results = []
    with psycopg.connect(**DSN, autocommit=True) as cx, cx.cursor() as cur:
        for n_rows in scales:
            print(f"generating {n_rows:,} rows...", file=sys.stderr)
            cur.execute("CALL build_scale(%s)", (n_rows,))
            params = selective_box_around_station(cur)

            cur.execute("CALL drop_indexes()")
            without_indexes = median_of_runs(cur, params)
            cur.execute("CALL build_indexes()")
            with_indexes = median_of_runs(cur, params)

            cur.execute("""SELECT pg_size_pretty(pg_relation_size('synth_positions')),
                                  pg_size_pretty(pg_relation_size('synth_geom_2d')),
                                  pg_size_pretty(pg_relation_size('synth_geom_nd'))""")
            results.append((n_rows, without_indexes, with_indexes, cur.fetchone()))

    print(f"\n{'rows':>12s}  {'query':12s} {'no index':>11s} {'indexed':>11s} "
          f"{'uses index':>11s} {'speedup':>9s}")
    for n_rows, without, with_, _ in results:
        for query in QUERIES:
            t_plain, _ = without[query]
            t_index, used_index = with_[query]
            speedup = f"{t_plain/t_index:.1f}x" if t_index > 0 else "-"
            print(f"{n_rows:>12,}  {query:12s} {t_plain:>10.2f}ms {t_index:>10.2f}ms "
                  f"{('yes' if used_index else 'no'):>11s} {speedup:>9s}")
        print()

    print(f"{'rows':>12s}  {'table':>10s} {'GiST 2D':>10s} {'GiST ND':>10s}")
    for n_rows, _, _, sizes in results:
        print(f"{n_rows:>12,}  {sizes[0]:>10s} {sizes[1]:>10s} {sizes[2]:>10s}")


if __name__ == "__main__":
    main()
