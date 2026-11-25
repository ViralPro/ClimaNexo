"""Gestor de base de datos SQLite en modo WAL con integridad referencial."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.config import DEFAULT_PROVIDERS, settings

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA temp_store = MEMORY;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    admin1 TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    country_code TEXT NOT NULL DEFAULT '',
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    lat_key REAL NOT NULL,
    lon_key REAL NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'auto',
    elevation REAL,
    is_favorite INTEGER NOT NULL DEFAULT 0,
    is_tracked INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_queried_at TEXT NOT NULL,
    UNIQUE(lat_key, lon_key)
);

CREATE TABLE IF NOT EXISTS provider_settings (
    provider_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    requires_api_key INTEGER NOT NULL DEFAULT 0,
    api_key TEXT NOT NULL DEFAULT '',
    base_url TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 10,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_health (
    provider_id TEXT PRIMARY KEY,
    last_success_at TEXT,
    last_error_at TEXT,
    last_error_message TEXT,
    avg_latency_ms REAL NOT NULL DEFAULT 0.0,
    success_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'unknown',
    FOREIGN KEY (provider_id) REFERENCES provider_settings(provider_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cache_entries (
    cache_key TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache_entries(expires_at);
CREATE INDEX IF NOT EXISTS idx_cache_namespace ON cache_entries(namespace);

CREATE TABLE IF NOT EXISTS forecast_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER NOT NULL,
    provider_id TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'live_forecast',
    issued_at TEXT NOT NULL,
    target_time TEXT NOT NULL,
    lead_hours INTEGER NOT NULL,
    temp_c REAL,
    feels_like_c REAL,
    humidity_pct REAL,
    pressure_hpa REAL,
    wind_kmh REAL,
    precip_mm REAL,
    precip_prob_pct REAL,
    weather_code INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE CASCADE,
    UNIQUE(location_id, provider_id, source_type, target_time, lead_hours)
);
CREATE INDEX IF NOT EXISTS idx_forecast_target ON forecast_records(location_id, target_time);
CREATE INDEX IF NOT EXISTS idx_forecast_provider ON forecast_records(provider_id, source_type);

CREATE TABLE IF NOT EXISTS actual_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    temp_c REAL,
    feels_like_c REAL,
    humidity_pct REAL,
    pressure_hpa REAL,
    wind_kmh REAL,
    precip_mm REAL,
    weather_code INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE CASCADE,
    UNIQUE(location_id, observed_at, source_provider)
);
CREATE INDEX IF NOT EXISTS idx_obs_loc_time ON actual_observations(location_id, observed_at);

CREATE TABLE IF NOT EXISTS verification_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_record_id INTEGER NOT NULL UNIQUE,
    observation_id INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    provider_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    target_time TEXT NOT NULL,
    lead_hours INTEGER NOT NULL,
    pred_temp_c REAL,
    actual_temp_c REAL,
    temp_error_c REAL,
    temp_abs_error_c REAL,
    pred_wind_kmh REAL,
    actual_wind_kmh REAL,
    wind_abs_error_kmh REAL,
    pred_precip_mm REAL,
    actual_precip_mm REAL,
    precip_abs_error_mm REAL,
    rain_hit INTEGER,
    pred_humidity_pct REAL,
    actual_humidity_pct REAL,
    humidity_abs_error REAL,
    pred_pressure_hpa REAL,
    actual_pressure_hpa REAL,
    pressure_abs_error_hpa REAL,
    composite_score_pct REAL NOT NULL,
    verified_at TEXT NOT NULL,
    FOREIGN KEY (forecast_record_id) REFERENCES forecast_records(id) ON DELETE CASCADE,
    FOREIGN KEY (observation_id) REFERENCES actual_observations(id) ON DELETE CASCADE,
    FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_verif_provider ON verification_results(provider_id, location_id);
CREATE INDEX IF NOT EXISTS idx_verif_target ON verification_results(target_time DESC);

CREATE TABLE IF NOT EXISTS weather_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER NOT NULL,
    alert_key TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    metric_value TEXT NOT NULL,
    source TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE CASCADE,
    UNIQUE(location_id, alert_key)
);
CREATE INDEX IF NOT EXISTS idx_alerts_loc_active ON weather_alerts(location_id, is_active);

CREATE TABLE IF NOT EXISTS query_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_text TEXT NOT NULL,
    parsed_intent TEXT NOT NULL,
    locations_json TEXT NOT NULL,
    date_range TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Instantáneas consolidadas y validadas listas para consulta ultrarrápida desde BD
CREATE TABLE IF NOT EXISTS weather_snapshots (
    location_id INTEGER NOT NULL,
    forecast_days INTEGER NOT NULL DEFAULT 7,
    collected_at TEXT NOT NULL,
    expires_at REAL NOT NULL,
    content_hash TEXT NOT NULL,
    source_count INTEGER NOT NULL DEFAULT 1,
    primary_provider_id TEXT NOT NULL,
    temp_c REAL,
    temp_min_c REAL,
    temp_max_c REAL,
    feels_like_c REAL,
    humidity_pct REAL,
    pressure_hpa REAL,
    wind_kmh REAL,
    precip_mm REAL,
    has_discrepancy INTEGER NOT NULL DEFAULT 0,
    discrepancy_summary TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    PRIMARY KEY (location_id, forecast_days),
    FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_snapshots_expires ON weather_snapshots(expires_at);

-- Archivo histórico deduplicado de lecturas individuales de cada proveedor
CREATE TABLE IF NOT EXISTS provider_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER NOT NULL,
    provider_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    temp_c REAL,
    temp_min_c REAL,
    temp_max_c REAL,
    feels_like_c REAL,
    humidity_pct REAL,
    pressure_hpa REAL,
    wind_kmh REAL,
    wind_gusts_kmh REAL,
    wind_dir_deg REAL,
    precip_mm REAL,
    precip_prob_pct REAL,
    weather_code INTEGER,
    condition_text TEXT NOT NULL DEFAULT '',
    latency_ms REAL NOT NULL DEFAULT 0.0,
    reading_hash TEXT NOT NULL,
    FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE CASCADE,
    UNIQUE(location_id, provider_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_readings_loc_time ON provider_readings(location_id, fetched_at DESC);

-- Almacén estructurado de pronósticos horarios por ubicación y proveedor (sin duplicados)
CREATE TABLE IF NOT EXISTS hourly_forecasts_store (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER NOT NULL,
    provider_id TEXT NOT NULL,
    target_time TEXT NOT NULL,
    temp_c REAL,
    feels_like_c REAL,
    humidity_pct REAL,
    pressure_hpa REAL,
    wind_kmh REAL,
    precip_mm REAL,
    precip_prob_pct REAL,
    uv_index REAL,
    weather_code INTEGER,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE CASCADE,
    UNIQUE(location_id, provider_id, target_time)
);
CREATE INDEX IF NOT EXISTS idx_hourly_store_loc ON hourly_forecasts_store(location_id, target_time);

-- Almacén estructurado de pronósticos diarios por ubicación y proveedor (sin duplicados)
CREATE TABLE IF NOT EXISTS daily_forecasts_store (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER NOT NULL,
    provider_id TEXT NOT NULL,
    forecast_date TEXT NOT NULL,
    temp_min_c REAL,
    temp_max_c REAL,
    temp_mean_c REAL,
    precip_sum_mm REAL,
    precip_prob_max REAL,
    wind_max_kmh REAL,
    uv_max REAL,
    sunrise TEXT,
    sunset TEXT,
    weather_code INTEGER,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE CASCADE,
    UNIQUE(location_id, provider_id, forecast_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_store_loc ON daily_forecasts_store(location_id, forecast_date);

-- Historial de comparaciones multiproveedor y discrepancias detectadas
CREATE TABLE IF NOT EXISTS comparison_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER NOT NULL,
    compared_at TEXT NOT NULL,
    expires_at REAL NOT NULL,
    active_providers INTEGER NOT NULL,
    selected_temp_c REAL,
    mean_temp_c REAL,
    median_temp_c REAL,
    min_temp_c REAL,
    max_temp_c REAL,
    temp_spread_c REAL,
    std_temp_c REAL,
    primary_provider_id TEXT NOT NULL,
    has_discrepancy INTEGER NOT NULL DEFAULT 0,
    discrepancy_summary TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE CASCADE,
    UNIQUE(location_id, compared_at)
);
CREATE INDEX IF NOT EXISTS idx_comp_runs_loc ON comparison_runs(location_id, id DESC);

-- Bitácora de ciclos del proceso recolector automático
CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_name TEXT NOT NULL DEFAULT 'collector',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    status TEXT NOT NULL,
    locations_checked INTEGER NOT NULL DEFAULT 0,
    locations_updated INTEGER NOT NULL DEFAULT 0,
    changes_detected INTEGER NOT NULL DEFAULT 0,
    readings_stored INTEGER NOT NULL DEFAULT 0,
    forecasts_stored INTEGER NOT NULL DEFAULT 0,
    pairs_verified INTEGER NOT NULL DEFAULT 0,
    errors_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT ''
);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DatabaseManager:
    """Administrador seguro para hilos de SQLite en modo WAL."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else settings.database_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connect()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def init_db(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA_SQL)
            now = utc_now_iso()
            for p in DEFAULT_PROVIDERS:
                env_key = ""
                if p["provider_id"] == "weatherapi":
                    env_key = settings.weatherapi_key
                elif p["provider_id"] == "openweather":
                    env_key = settings.openweather_api_key

                existing = conn.execute(
                    "SELECT api_key FROM provider_settings WHERE provider_id = ?",
                    (p["provider_id"],),
                ).fetchone()

                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO provider_settings
                        (provider_id, display_name, enabled, requires_api_key, api_key, base_url, priority, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            p["provider_id"],
                            p["display_name"],
                            p["enabled"],
                            p["requires_api_key"],
                            env_key,
                            p["base_url"],
                            p["priority"],
                            now,
                        ),
                    )
                elif env_key and not existing["api_key"]:
                    conn.execute(
                        "UPDATE provider_settings SET api_key = ?, updated_at = ? WHERE provider_id = ?",
                        (env_key, now, p["provider_id"]),
                    )

                initial_status = (
                    "needs_key"
                    if p["requires_api_key"] and not (existing["api_key"] if existing else env_key)
                    else "ready"
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO provider_health (provider_id, status)
                    VALUES (?, ?)
                    """,
                    (p["provider_id"], initial_status),
                )

            default_app_settings = {
                "unit_system": "metric",
                "auto_refresh_seconds": str(settings.auto_refresh_seconds),
            }
            for k, v in default_app_settings.items():
                conn.execute(
                    "INSERT OR IGNORE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (k, v, now),
                )

    def check_integrity(self) -> dict[str, object]:
        with self.connection() as conn:
            journal = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
            counts = {}
            for tbl in (
                "locations",
                "provider_settings",
                "cache_entries",
                "weather_snapshots",
                "provider_readings",
                "hourly_forecasts_store",
                "daily_forecasts_store",
                "comparison_runs",
                "collection_runs",
                "forecast_records",
                "actual_observations",
                "verification_results",
                "weather_alerts",
                "query_history",
            ):
                counts[tbl] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
            return {
                "ok": integrity == "ok",
                "journal_mode": journal,
                "foreign_keys": bool(fk),
                "integrity_check": integrity,
                "size_kb": round(size_bytes / 1024, 2),
                "table_counts": counts,
            }


_db_instance: DatabaseManager | None = None


def get_db() -> DatabaseManager:
    global _db_instance
    if _db_instance is None:
        _db_instance = DatabaseManager()
    return _db_instance


def reset_db_singleton(db_path: Path | str | None = None) -> DatabaseManager:
    """Útil para suites de prueba aisladas."""
    global _db_instance
    _db_instance = DatabaseManager(db_path)
    return _db_instance
