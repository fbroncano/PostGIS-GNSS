"""The analytical patterns implemented over CSV with numpy and pandas.

A functional equivalent of patterns.sql, so that the same analytical work can
be compared on a file-centric platform and on PostGIS. The implementations are
deliberately idiomatic: this is the code someone would write to answer these
questions without a spatial database.

Each pattern lives in its own function, delimited by LOC-BEGIN/LOC-END markers
so that the harness can count its effective lines.
"""

import numpy as np
import pandas as pd

# Elipsoide WGS84
_A = 6378137.0
_F = 1.0 / 298.257223563
_B = _A * (1.0 - _F)
_E2 = _F * (2.0 - _F)
_EP2 = (_A * _A - _B * _B) / (_B * _B)


def load_csv(path):
    """Load the whole dataset from the exported CSV files."""
    pos = pd.read_csv(f"{path}/daily_positions.csv", parse_dates=["date"])
    sta = pd.read_csv(f"{path}/stations.csv")
    sts = pd.read_csv(f"{path}/daily_statistics.csv", parse_dates=["date"])
    ant = pd.read_csv(f"{path}/station_antenna.csv",
                      parse_dates=["valid_from", "valid_to"])
    # valid_from/valid_to are TIMESTAMPTZ. The comparison is at day resolution,
    # so the zone is dropped to allow comparison against naive dates.
    for col in ("valid_from", "valid_to"):
        ant[col] = ant[col].dt.tz_localize(None)
    for df in (pos, sta, sts, ant):
        for col in ("station_id", "id"):
            if col in df.columns and df[col].dtype == object:
                df[col] = df[col].str.strip()
    return {"positions": pos, "stations": sta, "statistics": sts, "antenna": ant}


# --- geodesy that PostGIS already provides -----------------------------------

def ecef_to_geodetic(x, y, z):
    """Geodetic latitude and longitude, in radians (Bowring's method)."""
    p = np.hypot(x, y)
    theta = np.arctan2(z * _A, p * _B)
    lat = np.arctan2(z + _EP2 * _B * np.sin(theta) ** 3,
                     p - _E2 * _A * np.cos(theta) ** 3)
    lon = np.arctan2(y, x)
    return lat, lon


def ecef_to_enu(dx, dy, dz, lat, lon):
    """Rotate an ECEF offset into the local topocentric frame."""
    sla, cla = np.sin(lat), np.cos(lat)
    slo, clo = np.sin(lon), np.cos(lon)
    east = -slo * dx + clo * dy
    north = -sla * clo * dx - sla * slo * dy + cla * dz
    up = cla * clo * dx + cla * slo * dy + sla * dz
    return east, north, up


def enu_to_ecef(east, north, up, lat, lon):
    """Inverse rotation: apply an ENU offset to an ECEF point."""
    sla, cla = np.sin(lat), np.cos(lat)
    slo, clo = np.sin(lon), np.cos(lon)
    dx = -slo * east - sla * clo * north + cla * clo * up
    dy = clo * east - sla * slo * north + cla * slo * up
    dz = cla * north + sla * up
    return dx, dy, dz


def station_reference(data, on_date):
    """Per-station reference, translated by the antenna in force on on_date.

    Equivalent of station_reference() in functions.sql.
    """
    sta = data["stations"].set_index("id")
    ant = data["antenna"]
    valid = ant[(ant["valid_from"] <= on_date) &
                (ant["valid_to"].isna() | (ant["valid_to"] > on_date))]
    valid = valid.sort_values("valid_from").groupby("station_id").last()

    ref = {}
    for code, row in sta.iterrows():
        x, y, z = row["x"], row["y"], row["z"]
        if code in valid.index:
            a = valid.loc[code]
            lat, lon = ecef_to_geodetic(x, y, z)
            dx, dy, dz = enu_to_ecef(a["delta_e"], a["delta_n"], a["delta_h"], lat, lon)
            x, y, z = x + dx, y + dy, z + dz
        ref[code] = (x, y, z)
    return ref


def geometric_median(P, tol=1e-8, max_iter=1000):
    """Geometric median through Weiszfeld's algorithm.

    Equivalent of ST_GeometricMedian, which PostGIS provides natively.
    """
    y = P.mean(axis=0)
    for _ in range(max_iter):
        d = np.linalg.norm(P - y, axis=1)
        d = np.where(d < 1e-12, 1e-12, d)
        w = 1.0 / d
        y_new = (P * w[:, None]).sum(axis=0) / w.sum()
        if np.linalg.norm(y_new - y) < tol:
            return y_new
        y = y_new
    return y


# --- P1 ----------------------------------------------------------------------

def p1_representative_positions(data, constellation_id, method_id,
                                date_from, date_to):
    # LOC-BEGIN p1
    pos = data["positions"]
    sel = pos[(pos["constellation_id"] == constellation_id) &
              (pos["method_id"] == method_id) &
              (pos["date"] >= date_from) & (pos["date"] <= date_to)]
    ref = station_reference(data, date_to)
    out = []
    for code, grp in sel.groupby("station_id"):
        P = grp[["x", "y", "z"]].to_numpy()
        med = geometric_median(P)
        rx, ry, rz = ref[code]
        bias = np.linalg.norm(med - np.array([rx, ry, rz]))
        daily = np.linalg.norm(P - np.array([rx, ry, rz]), axis=1)
        out.append((code, len(grp), med[0], med[1], med[2],
                    bias, daily.mean(), grp["std_3d"].mean()))
    # LOC-END p1
    return pd.DataFrame(out, columns=["station_code", "n_positions", "med_x",
                                      "med_y", "med_z", "bias_3d",
                                      "mean_daily_error", "mean_std_3d"])


# --- P2 ----------------------------------------------------------------------

def p2_representative_enu(data, constellation_id, method_id, date_from, date_to):
    # LOC-BEGIN p2
    p1 = p1_representative_positions(data, constellation_id, method_id,
                                     date_from, date_to)
    ref = station_reference(data, date_to)
    out = []
    for _, r in p1.iterrows():
        rx, ry, rz = ref[r["station_code"]]
        lat, lon = ecef_to_geodetic(rx, ry, rz)
        e, n, u = ecef_to_enu(r["med_x"] - rx, r["med_y"] - ry,
                              r["med_z"] - rz, lat, lon)
        out.append((r["station_code"], r["n_positions"], e, n, u, np.hypot(e, n)))
    # LOC-END p2
    return pd.DataFrame(out, columns=["station_code", "n_positions", "east",
                                      "north", "up", "horizontal"])


# --- P3 ----------------------------------------------------------------------

def p3_quality_filtered(data, constellation_id, method_id, date_from, date_to,
                        std_3d_max, pdop_max):
    # LOC-BEGIN p3
    pos, sts = data["positions"], data["statistics"]
    sel = pos[(pos["constellation_id"] == constellation_id) &
              (pos["method_id"] == method_id) &
              (pos["date"] >= date_from) & (pos["date"] <= date_to)]
    joined = sel.merge(sts, on=["station_id", "date", "constellation_id"],
                       suffixes=("", "_s"))
    joined["keep"] = ((joined["std_3d"] <= std_3d_max) &
                      (joined["pdop_avg"] <= pdop_max))
    ref = station_reference(data, date_to)
    out = []
    for code, grp in joined.groupby("station_id"):
        rx, ry, rz = ref[code]
        r = np.array([rx, ry, rz])
        all_med = geometric_median(grp[["x", "y", "z"]].to_numpy())
        kept = grp[grp["keep"]]
        kept_med = (geometric_median(kept[["x", "y", "z"]].to_numpy())
                    if len(kept) else None)
        out.append((code, len(grp), int(grp["keep"].sum()),
                    np.linalg.norm(all_med - r),
                    np.linalg.norm(kept_med - r) if kept_med is not None else np.nan,
                    grp["std_3d"].mean(), grp["pdop_avg"].mean()))
    # LOC-END p3
    return pd.DataFrame(out, columns=["station_code", "n_total", "n_kept",
                                      "bias_all", "bias_kept", "mean_std_3d",
                                      "mean_pdop"])


# --- P4 ----------------------------------------------------------------------

def p4_rms_outliers(data, sigmas=3.0):
    # LOC-BEGIN p4
    sts = data["statistics"]
    g = sts.groupby(["station_id", "constellation_id"])["rms_max"]
    med = g.transform("median")
    sigma = 1.4826 * (sts["rms_max"] - med).abs().groupby(
        [sts["station_id"], sts["constellation_id"]]).transform("median")
    flagged = sts[(sigma > 0) & (sts["rms_max"] - med > sigmas * sigma)]
    out = flagged[["station_id", "date", "constellation_id", "rms_max"]].copy()
    out["rms_median"] = med[flagged.index]
    out["rms_sigma"] = sigma[flagged.index]
    # LOC-END p4
    return out.sort_values(["station_id", "constellation_id", "date"])
