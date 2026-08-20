from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class Station:
    id: str
    name: str
    city: str
    province: str
    initial_date: date
    x: float
    y: float
    z: float
    # A coordinate is interpretable only together with its realisation and its
    # epoch. ETRS89, REGCAN95 and ITRF are all stored under SRID 4978, yet they
    # differ from one another by up to 0.8 m.
    frame_id: Optional[int] = None
    coord_epoch: Optional[float] = None

    def __repr__(self):
        return f"Station({self.id}, {self.name}, {self.city}, {self.province})"


@dataclass
class Constellation:
    id: int
    code: str
    name: str

    def __repr__(self):
        return f"Constellation({self.id}, {self.code}, {self.name})"


@dataclass
class Method:
    id: int
    code: str
    description: str

    def __repr__(self):
        return f"Method({self.id}, {self.code})"


@dataclass
class DailyPosition:
    station_id: str
    constellation_id: int
    method_id: int
    x: float
    y: float
    z: float
    date: Optional[date] = None
    std_x: Optional[float] = None
    std_y: Optional[float] = None
    std_z: Optional[float] = None
    std_3d: Optional[float] = None
    n_epochs_used: Optional[int] = None
    n_epochs: Optional[int] = None
    run_id: Optional[int] = None
    id: Optional[int] = None
    # Populated by queries, not stored as columns
    constellation_code: Optional[str] = None
    constellation_name: Optional[str] = None
    method_code: Optional[str] = None

    def __repr__(self):
        return f"DailyPosition({self.station_id}, {self.date}, c={self.constellation_id}, m={self.method_id})"


@dataclass
class DailyStatistics:
    station_id: str
    date: date
    constellation_id: int
    n_satellites_avg: Optional[float] = None
    clock_bias_m: Optional[float] = None

    # Dilution of precision
    gdop_avg: Optional[float] = None
    pdop_avg: Optional[float] = None
    pdop_max: Optional[float] = None
    tdop_avg: Optional[float] = None

    # RMS of the least-squares residuals
    rms_avg: Optional[float] = None
    rms_max: Optional[float] = None

    run_id: Optional[int] = None
    id: Optional[int] = None
    # Populated by queries, not stored as columns
    constellation_code: Optional[str] = None
    constellation_name: Optional[str] = None

    def __repr__(self):
        return f"DailyStatistics({self.station_id}, {self.date}, c={self.constellation_id})"


@dataclass
class PositionError:
    constellation_code: str
    method: str
    error_x: float
    error_y: float
    error_z: float
    error_3d: float

    def __repr__(self):
        return f"PositionError({self.constellation_code}, {self.method}, error_3d={self.error_3d:.2f}m)"


@dataclass
class ConstellationStats:
    constellation_name: str
    n_days: int
    avg_error_3d: float
    avg_n_satellites: float
    avg_pdop: float
    avg_rms: float

    def __repr__(self):
        return f"ConstellationStats({self.constellation_name}, n_days={self.n_days}, error_3d={self.avg_error_3d:.2f}m)"