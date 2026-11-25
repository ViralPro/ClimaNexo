"""Repositorio SQLite: CRUD y consultas analíticas para Weather Dashboard Pro."""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from app.config import PROVIDER_META_MAP
from app.db.database import DatabaseManager, get_db, utc_now_iso
from app.models.schemas import LocationModel, WeatherAlertModel


class WeatherRepository:
    def __init__(self, db: DatabaseManager | None = None) -> None:
        self.db = db or get_db()

    # ── Ubicaciones ───────────────────────────────────────────────────────
    def upsert_location(self, loc: LocationModel, set_favorite: Optional[bool] = None) -> LocationModel:
        lat_key = round(loc.latitude, 2)
        lon_key = round(loc.longitude, 2)
        now = utc_now_iso()
        with self.db.connection() as conn:
            existing = conn.execute(
                "SELECT * FROM locations WHERE lat_key = ? AND lon_key = ?",
                (lat_key, lon_key),
            ).fetchone()
            if existing:
                fav = existing["is_favorite"] if set_favorite is None else (1 if set_favorite else 0)
                name = loc.name or existing["name"]
                admin1 = loc.admin1 or existing["admin1"]
                country = loc.country or existing["country"]
                country_code = loc.country_code or existing["country_code"]
                tz = loc.timezone if loc.timezone != "auto" else existing["timezone"]
                elev = loc.elevation if loc.elevation is not None else existing["elevation"]
                conn.execute(
                    """
                    UPDATE locations
                    SET name = ?, admin1 = ?, country = ?, country_code = ?,
                        timezone = ?, elevation = ?, is_favorite = ?, last_queried_at = ?
                    WHERE id = ?
                    """,
                    (name, admin1, country, country_code, tz, elev, fav, now, existing["id"]),
                )
                row = conn.execute("SELECT * FROM locations WHERE id = ?", (existing["id"],)).fetchone()
            else:
                fav = 1 if set_favorite else (1 if loc.is_favorite else 0)
                cur = conn.execute(
                    """
                    INSERT INTO locations
                    (name, admin1, country, country_code, latitude, longitude, lat_key, lon_key,
                     timezone, elevation, is_favorite, is_tracked, created_at, last_queried_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        loc.name,
                        loc.admin1,
                        loc.country,
                        loc.country_code,
                        loc.latitude,
                        loc.longitude,
                        lat_key,
                        lon_key,
                        loc.timezone,
                        loc.elevation,
                        fav,
                        now,
                        now,
                    ),
                )
                row = conn.execute("SELECT * FROM locations WHERE id = ?", (cur.lastrowid,)).fetchone()
            return self._row_to_location(row)

    def get_location(self, location_id: int) -> Optional[LocationModel]:
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone()
            return self._row_to_location(row) if row else None

    def find_location_by_coords(self, lat: float, lon: float) -> Optional[LocationModel]:
        lat_key = round(lat, 2)
        lon_key = round(lon, 2)
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM locations WHERE lat_key = ? AND lon_key = ?",
                (lat_key, lon_key),
            ).fetchone()
            return self._row_to_location(row) if row else None

    def find_location_by_name(self, city_name: str) -> Optional[LocationModel]:
        clean = city_name.strip().lower()
        if not clean:
            return None
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM locations WHERE LOWER(name) = ? ORDER BY last_queried_at DESC LIMIT 1",
                (clean,),
            ).fetchone()
            return self._row_to_location(row) if row else None

    def list_locations(self, favorites_only: bool = False, limit: int = 30) -> list[LocationModel]:
        with self.db.connection() as conn:
            if favorites_only:
                rows = conn.execute(
                    "SELECT * FROM locations WHERE is_favorite = 1 ORDER BY last_queried_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM locations ORDER BY is_favorite DESC, last_queried_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self._row_to_location(r) for r in rows]

    def set_location_favorite(self, location_id: int, is_favorite: bool) -> Optional[LocationModel]:
        with self.db.connection() as conn:
            conn.execute(
                "UPDATE locations SET is_favorite = ? WHERE id = ?",
                (1 if is_favorite else 0, location_id),
            )
            row = conn.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone()
            return self._row_to_location(row) if row else None

    def delete_location(self, location_id: int) -> bool:
        with self.db.connection() as conn:
            cur = conn.execute("DELETE FROM locations WHERE id = ?", (location_id,))
            return cur.rowcount > 0

    def list_tracked_locations(self, limit: int = 8) -> list[LocationModel]:
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM locations WHERE is_tracked = 1 ORDER BY is_favorite DESC, last_queried_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_location(r) for r in rows]

    @staticmethod
    def _row_to_location(row: Any) -> LocationModel:
        loc = LocationModel(
            id=row["id"],
            name=row["name"],
            admin1=row["admin1"],
            country=row["country"],
            country_code=row["country_code"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            timezone=row["timezone"],
            elevation=row["elevation"],
            is_favorite=bool(row["is_favorite"]),
        )
        loc.display_label = loc.formatted_label()
        return loc

    # ── Proveedores y Salud ───────────────────────────────────────────────
    def list_providers(self) -> list[dict[str, Any]]:
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT s.*, h.last_success_at, h.last_error_at, h.last_error_message,
                       h.avg_latency_ms, h.success_count, h.error_count, h.status
                FROM provider_settings s
                LEFT JOIN provider_health h ON s.provider_id = h.provider_id
                ORDER BY s.priority ASC
                """
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                meta = PROVIDER_META_MAP.get(d["provider_id"], {})
                d["has_api_key"] = bool(d.get("api_key", "").strip())
                d["api_key_masked"] = (
                    (d["api_key"][:4] + "••••" + d["api_key"][-3:])
                    if len(d.get("api_key", "")) >= 8
                    else ("••••" if d.get("api_key") else "")
                )
                d["description"] = meta.get("description", "")
                d["supports_historical_backtest"] = meta.get("supports_historical_backtest", False)
                result.append(d)
            return result

    def get_provider_setting(self, provider_id: str) -> Optional[dict[str, Any]]:
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM provider_settings WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
            return dict(row) if row else None

    def update_provider_setting(
        self,
        provider_id: str,
        enabled: Optional[bool] = None,
        api_key: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        now = utc_now_iso()
        with self.db.connection() as conn:
            existing = conn.execute(
                "SELECT * FROM provider_settings WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
            if not existing:
                return None
            new_enabled = existing["enabled"] if enabled is None else (1 if enabled else 0)
            new_key = existing["api_key"] if api_key is None else api_key.strip()
            conn.execute(
                "UPDATE provider_settings SET enabled = ?, api_key = ?, updated_at = ? WHERE provider_id = ?",
                (new_enabled, new_key, now, provider_id),
            )
            status = "disabled" if not new_enabled else (
                "needs_key" if existing["requires_api_key"] and not new_key else "ready"
            )
            conn.execute(
                "UPDATE provider_health SET status = ? WHERE provider_id = ?",
                (status, provider_id),
            )
            row = conn.execute(
                "SELECT * FROM provider_settings WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
            return dict(row)

    def record_provider_health(
        self,
        provider_id: str,
        ok: bool,
        latency_ms: float = 0.0,
        error_message: Optional[str] = None,
        status_override: Optional[str] = None,
    ) -> None:
        now = utc_now_iso()
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM provider_health WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
            if not row:
                return
            if ok:
                sc = row["success_count"] + 1
                prev_avg = row["avg_latency_ms"] or 0.0
                new_avg = round(((prev_avg * (sc - 1)) + latency_ms) / sc, 1)
                conn.execute(
                    """
                    UPDATE provider_health
                    SET last_success_at = ?, avg_latency_ms = ?, success_count = ?,
                        status = ?, last_error_message = NULL
                    WHERE provider_id = ?
                    """,
                    (now, new_avg, sc, status_override or "online", provider_id),
                )
            else:
                ec = row["error_count"] + 1
                st = status_override or "error"
                conn.execute(
                    """
                    UPDATE provider_health
                    SET last_error_at = ?, last_error_message = ?, error_count = ?, status = ?
                    WHERE provider_id = ?
                    """,
                    (now, (error_message or "Error de conexión")[:300], ec, st, provider_id),
                )

    # ── Caché Persistente ─────────────────────────────────────────────────
    def get_cache_entry(self, cache_key: str, allow_stale: bool = False) -> Optional[tuple[Any, float]]:
        now = time.time()
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT payload_json, expires_at FROM cache_entries WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if not row:
                return None
            if not allow_stale and row["expires_at"] < now:
                return None
            conn.execute(
                "UPDATE cache_entries SET hit_count = hit_count + 1 WHERE cache_key = ?",
                (cache_key,),
            )
            return json.loads(row["payload_json"]), float(row["expires_at"])

    def get_cache(self, cache_key: str, allow_stale: bool = False) -> Optional[dict[str, Any]]:
        entry = self.get_cache_entry(cache_key, allow_stale=allow_stale)
        return entry[0] if entry is not None else None

    def set_cache(self, cache_key: str, namespace: str, payload: Any, ttl_seconds: int) -> None:
        now = time.time()
        with self.db.connection() as conn:
            conn.execute(
                """
                INSERT INTO cache_entries (cache_key, namespace, payload_json, created_at, expires_at, hit_count)
                VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at
                """,
                (cache_key, namespace, json.dumps(payload, ensure_ascii=False), now, now + ttl_seconds),
            )

    def purge_expired_cache(self) -> int:
        now = time.time()
        with self.db.connection() as conn:
            cur = conn.execute("DELETE FROM cache_entries WHERE expires_at < ?", (now - 3600,))
            return cur.rowcount

    def get_cache_stats(self) -> dict[str, Any]:
        now = time.time()
        with self.db.connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
            active = conn.execute("SELECT COUNT(*) FROM cache_entries WHERE expires_at >= ?", (now,)).fetchone()[0]
            hits = conn.execute("SELECT COALESCE(SUM(hit_count), 0) FROM cache_entries").fetchone()[0]
            return {"total_entries": total, "active_entries": active, "total_hits": hits}

    # ── Registros de Pronósticos y Observaciones Reales ───────────────────
    def save_forecast_records(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        now = utc_now_iso()
        params = [
            (
                r["location_id"],
                r["provider_id"],
                r.get("source_type", "live_forecast"),
                r["issued_at"],
                r["target_time"],
                int(r["lead_hours"]),
                r.get("temp_c"),
                r.get("feels_like_c"),
                r.get("humidity_pct"),
                r.get("pressure_hpa"),
                r.get("wind_kmh"),
                r.get("precip_mm"),
                r.get("precip_prob_pct"),
                r.get("weather_code"),
                now,
            )
            for r in records
        ]
        with self.db.connection() as conn:
            before = conn.total_changes
            conn.executemany(
                """
                INSERT OR IGNORE INTO forecast_records
                (location_id, provider_id, source_type, issued_at, target_time, lead_hours,
                 temp_c, feels_like_c, humidity_pct, pressure_hpa, wind_kmh, precip_mm,
                 precip_prob_pct, weather_code, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )
            return conn.total_changes - before

    def save_actual_observations(self, observations: list[dict[str, Any]]) -> int:
        if not observations:
            return 0
        now = utc_now_iso()
        params = [
            (
                o["location_id"],
                o["observed_at"],
                o["source_provider"],
                o.get("temp_c"),
                o.get("feels_like_c"),
                o.get("humidity_pct"),
                o.get("pressure_hpa"),
                o.get("wind_kmh"),
                o.get("precip_mm"),
                o.get("weather_code"),
                now,
            )
            for o in observations
        ]
        with self.db.connection() as conn:
            before = conn.total_changes
            conn.executemany(
                """
                INSERT INTO actual_observations
                (location_id, observed_at, source_provider, temp_c, feels_like_c,
                 humidity_pct, pressure_hpa, wind_kmh, precip_mm, weather_code, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(location_id, observed_at, source_provider) DO UPDATE SET
                    temp_c = COALESCE(excluded.temp_c, actual_observations.temp_c),
                    feels_like_c = COALESCE(excluded.feels_like_c, actual_observations.feels_like_c),
                    humidity_pct = COALESCE(excluded.humidity_pct, actual_observations.humidity_pct),
                    pressure_hpa = COALESCE(excluded.pressure_hpa, actual_observations.pressure_hpa),
                    wind_kmh = COALESCE(excluded.wind_kmh, actual_observations.wind_kmh),
                    precip_mm = COALESCE(excluded.precip_mm, actual_observations.precip_mm)
                """,
                params,
            )
            return conn.total_changes - before

    def count_local_observations(self, location_id: int) -> int:
        with self.db.connection() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM actual_observations WHERE location_id = ?",
                (location_id,),
            ).fetchone()[0]

    def get_unverified_forecast_pairs(self, location_id: Optional[int] = None) -> list[dict[str, Any]]:
        """Empareja pronósticos aún no verificados con observaciones reales en el mismo bucket horario."""
        where_loc = "AND f.location_id = ?" if location_id is not None else ""
        params = (location_id,) if location_id is not None else ()
        with self.db.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    f.id AS forecast_record_id,
                    f.location_id,
                    f.provider_id,
                    f.source_type,
                    f.target_time,
                    f.lead_hours,
                    f.temp_c AS pred_temp_c,
                    f.wind_kmh AS pred_wind_kmh,
                    f.precip_mm AS pred_precip_mm,
                    f.humidity_pct AS pred_humidity_pct,
                    f.pressure_hpa AS pred_pressure_hpa,
                    o.id AS observation_id,
                    o.temp_c AS actual_temp_c,
                    o.wind_kmh AS actual_wind_kmh,
                    o.precip_mm AS actual_precip_mm,
                    o.humidity_pct AS actual_humidity_pct,
                    o.pressure_hpa AS actual_pressure_hpa
                FROM forecast_records f
                INNER JOIN actual_observations o
                    ON o.location_id = f.location_id
                    AND o.observed_at = f.target_time
                    AND o.id = (
                        SELECT o2.id FROM actual_observations o2
                        WHERE o2.location_id = f.location_id AND o2.observed_at = f.target_time
                        ORDER BY CASE o2.source_provider
                            WHEN 'open_meteo_archive' THEN 1
                            WHEN 'consensus_live' THEN 2
                            WHEN 'open_meteo_current' THEN 3
                            ELSE 4 END
                        LIMIT 1
                    )
                LEFT JOIN verification_results v ON v.forecast_record_id = f.id
                WHERE v.id IS NULL
                  AND f.temp_c IS NOT NULL
                  AND o.temp_c IS NOT NULL
                  {where_loc}
                """,
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def save_verification_results(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        now = utc_now_iso()
        params = [
            (
                r["forecast_record_id"],
                r["observation_id"],
                r["location_id"],
                r["provider_id"],
                r["source_type"],
                r["target_time"],
                r["lead_hours"],
                r["pred_temp_c"],
                r["actual_temp_c"],
                r["temp_error_c"],
                r["temp_abs_error_c"],
                r["pred_wind_kmh"],
                r["actual_wind_kmh"],
                r["wind_abs_error_kmh"],
                r["pred_precip_mm"],
                r["actual_precip_mm"],
                r["precip_abs_error_mm"],
                r["rain_hit"],
                r["pred_humidity_pct"],
                r["actual_humidity_pct"],
                r["humidity_abs_error"],
                r["pred_pressure_hpa"],
                r["actual_pressure_hpa"],
                r["pressure_abs_error_hpa"],
                r["composite_score_pct"],
                now,
            )
            for r in rows
        ]
        with self.db.connection() as conn:
            before = conn.total_changes
            conn.executemany(
                """
                INSERT OR IGNORE INTO verification_results
                (forecast_record_id, observation_id, location_id, provider_id, source_type,
                 target_time, lead_hours, pred_temp_c, actual_temp_c, temp_error_c, temp_abs_error_c,
                 pred_wind_kmh, actual_wind_kmh, wind_abs_error_kmh, pred_precip_mm, actual_precip_mm,
                 precip_abs_error_mm, rain_hit, pred_humidity_pct, actual_humidity_pct, humidity_abs_error,
                 pred_pressure_hpa, actual_pressure_hpa, pressure_abs_error_hpa, composite_score_pct, verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )
            return conn.total_changes - before

    def get_accuracy_aggregates(
        self,
        location_id: Optional[int] = None,
        source_type: Optional[str] = None,
    ) -> dict[str, Any]:
        clauses = []
        params: list[Any] = []
        if location_id is not None:
            clauses.append("v.location_id = ?")
            params.append(location_id)
        if source_type in ("live_forecast", "historical_run"):
            clauses.append("v.source_type = ?")
            params.append(source_type)
        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        with self.db.connection() as conn:
            stats_rows = conn.execute(
                f"""
                SELECT
                    v.provider_id,
                    COUNT(*) AS sample_count,
                    SUM(CASE WHEN v.source_type = 'live_forecast' THEN 1 ELSE 0 END) AS live_sample_count,
                    SUM(CASE WHEN v.source_type = 'historical_run' THEN 1 ELSE 0 END) AS historical_sample_count,
                    AVG(v.composite_score_pct) AS avg_composite,
                    AVG(v.temp_abs_error_c) AS temp_mae,
                    SQRT(AVG(v.temp_error_c * v.temp_error_c)) AS temp_rmse,
                    AVG(v.temp_error_c) AS temp_bias,
                    AVG(v.wind_abs_error_kmh) AS wind_mae,
                    AVG(v.precip_abs_error_mm) AS precip_mae,
                    AVG(CASE WHEN v.rain_hit IS NOT NULL THEN v.rain_hit * 100.0 END) AS rain_hit_rate,
                    AVG(v.humidity_abs_error) AS humidity_mae,
                    AVG(v.pressure_abs_error_hpa) AS pressure_mae
                FROM verification_results v
                {where_sql}
                GROUP BY v.provider_id
                """,
                params,
            ).fetchall()

            audit_rows = conn.execute(
                f"""
                SELECT v.*, l.name AS location_name
                FROM verification_results v
                INNER JOIN locations l ON l.id = v.location_id
                {where_sql}
                ORDER BY v.target_time DESC, v.id DESC
                LIMIT 60
                """,
                params,
            ).fetchall()

            if location_id is not None:
                total_forecasts = conn.execute(
                    "SELECT COUNT(*) FROM forecast_records WHERE location_id = ?", (location_id,)
                ).fetchone()[0]
                total_obs = conn.execute(
                    "SELECT COUNT(*) FROM actual_observations WHERE location_id = ?", (location_id,)
                ).fetchone()[0]
                total_verified = conn.execute(
                    "SELECT COUNT(*) FROM verification_results WHERE location_id = ?", (location_id,)
                ).fetchone()[0]
                live_verified = conn.execute(
                    "SELECT COUNT(*) FROM verification_results WHERE location_id = ? AND source_type = 'live_forecast'",
                    (location_id,),
                ).fetchone()[0]
                hist_verified = conn.execute(
                    "SELECT COUNT(*) FROM verification_results WHERE location_id = ? AND source_type = 'historical_run'",
                    (location_id,),
                ).fetchone()[0]
            else:
                total_forecasts = conn.execute("SELECT COUNT(*) FROM forecast_records").fetchone()[0]
                total_obs = conn.execute("SELECT COUNT(*) FROM actual_observations").fetchone()[0]
                total_verified = conn.execute("SELECT COUNT(*) FROM verification_results").fetchone()[0]
                live_verified = conn.execute(
                    "SELECT COUNT(*) FROM verification_results WHERE source_type = 'live_forecast'"
                ).fetchone()[0]
                hist_verified = conn.execute(
                    "SELECT COUNT(*) FROM verification_results WHERE source_type = 'historical_run'"
                ).fetchone()[0]

            return {
                "stats_by_provider": {r["provider_id"]: dict(r) for r in stats_rows},
                "audit_rows": [dict(r) for r in audit_rows],
                "total_forecasts": total_forecasts,
                "total_observations": total_obs,
                "total_verified": total_verified,
                "live_verified": live_verified,
                "historical_verified": hist_verified,
            }

    # ── Alertas Climáticas ────────────────────────────────────────────────
    def sync_location_alerts(self, location_id: int, alerts: list[WeatherAlertModel]) -> list[WeatherAlertModel]:
        now = utc_now_iso()
        active_keys = [a.alert_key for a in alerts]
        with self.db.connection() as conn:
            if active_keys:
                placeholders = ",".join("?" for _ in active_keys)
                conn.execute(
                    f"UPDATE weather_alerts SET is_active = 0 WHERE location_id = ? AND alert_key NOT IN ({placeholders})",
                    [location_id, *active_keys],
                )
            else:
                conn.execute(
                    "UPDATE weather_alerts SET is_active = 0 WHERE location_id = ?",
                    (location_id,),
                )
            for a in alerts:
                conn.execute(
                    """
                    INSERT INTO weather_alerts
                    (location_id, alert_key, category, severity, title, description, recommendation,
                     metric_value, source, starts_at, ends_at, is_active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(location_id, alert_key) DO UPDATE SET
                        severity = excluded.severity,
                        title = excluded.title,
                        description = excluded.description,
                        recommendation = excluded.recommendation,
                        metric_value = excluded.metric_value,
                        starts_at = excluded.starts_at,
                        ends_at = excluded.ends_at,
                        is_active = 1
                    """,
                    (
                        location_id,
                        a.alert_key,
                        a.category,
                        a.severity,
                        a.title,
                        a.description,
                        a.recommendation,
                        a.metric_value,
                        a.source,
                        a.starts_at,
                        a.ends_at,
                        now,
                    ),
                )
            rows = conn.execute(
                """
                SELECT * FROM weather_alerts
                WHERE location_id = ? AND is_active = 1
                ORDER BY CASE severity WHEN 'extrema' THEN 1 WHEN 'alerta' THEN 2 WHEN 'precaucion' THEN 3 ELSE 4 END, id DESC
                """,
                (location_id,),
            ).fetchall()
            return [self._row_to_alert(r) for r in rows]

    def list_alerts(self, location_id: Optional[int] = None, active_only: bool = False, limit: int = 50) -> list[WeatherAlertModel]:
        clauses = []
        params: list[Any] = []
        if location_id is not None:
            clauses.append("location_id = ?")
            params.append(location_id)
        if active_only:
            clauses.append("is_active = 1")
        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.db.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM weather_alerts {where_sql}
                ORDER BY is_active DESC,
                         CASE severity WHEN 'extrema' THEN 1 WHEN 'alerta' THEN 2 WHEN 'precaucion' THEN 3 ELSE 4 END,
                         created_at DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
            return [self._row_to_alert(r) for r in rows]

    @staticmethod
    def _row_to_alert(row: Any) -> WeatherAlertModel:
        return WeatherAlertModel(
            id=row["id"],
            alert_key=row["alert_key"],
            category=row["category"],
            severity=row["severity"],
            title=row["title"],
            description=row["description"],
            recommendation=row["recommendation"],
            metric_value=row["metric_value"],
            source=row["source"],
            starts_at=row["starts_at"],
            ends_at=row["ends_at"],
            is_active=bool(row["is_active"]),
        )

    # ── Historial de consultas y configuración ────────────────────────────
    def log_query(self, query_text: str, intent: str, locations: list[str], date_range: str) -> None:
        with self.db.connection() as conn:
            conn.execute(
                """
                INSERT INTO query_history (query_text, parsed_intent, locations_json, date_range, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (query_text, intent, json.dumps(locations, ensure_ascii=False), date_range, utc_now_iso()),
            )

    def list_recent_queries(self, limit: int = 12) -> list[dict[str, Any]]:
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM query_history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["locations"] = json.loads(d.pop("locations_json", "[]"))
                out.append(d)
            return out

    def get_app_settings(self) -> dict[str, str]:
        with self.db.connection() as conn:
            rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
            return {r["key"]: r["value"] for r in rows}

    def set_app_setting(self, key: str, value: str) -> None:
        with self.db.connection() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, str(value), utc_now_iso()),
            )

    # ── Almacén Meteorológico Masivo y Consultas Rápidas desde BD ─────────
    def get_weather_snapshot(self, location_id: int, forecast_days: int = 7) -> Optional[dict[str, Any]]:
        """Recupera instantáneamente el último snapshot consolidado desde SQLite (< 5 ms)."""
        now = time.time()
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM weather_snapshots
                WHERE location_id = ? AND forecast_days >= ?
                ORDER BY CASE WHEN forecast_days = ? THEN 0 ELSE 1 END, forecast_days ASC
                LIMIT 1
                """,
                (location_id, forecast_days, forecast_days),
            ).fetchone()
            if not row:
                return None
            payload = json.loads(row["payload_json"])
            collected_ts = row["expires_at"] - 600.0
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(row["collected_at"].replace("Z", "+00:00"))
                age_sec = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
            except Exception:
                age_sec = max(0, int(now - collected_ts))
            return {
                "payload": payload,
                "collected_at": row["collected_at"],
                "expires_at": float(row["expires_at"]),
                "is_stale": now >= float(row["expires_at"]),
                "age_seconds": age_sec,
                "content_hash": row["content_hash"],
                "forecast_days": int(row["forecast_days"]),
            }

    def save_weather_snapshot(
        self,
        location_id: int,
        forecast_days: int,
        ttl_seconds: int,
        content_hash: str,
        source_count: int,
        primary_provider_id: str,
        summary_metrics: dict[str, Any],
        payload: dict[str, Any],
    ) -> bool:
        """Guarda o actualiza snapshot consolidado y devuelve True si detectó cambio meteorológico real."""
        now_ts = time.time()
        now_iso = utc_now_iso()
        with self.db.connection() as conn:
            prev = conn.execute(
                "SELECT content_hash FROM weather_snapshots WHERE location_id = ? AND forecast_days = ?",
                (location_id, forecast_days),
            ).fetchone()
            changed = (prev is None) or (prev["content_hash"] != content_hash)
            conn.execute(
                """
                INSERT INTO weather_snapshots
                (location_id, forecast_days, collected_at, expires_at, content_hash, source_count,
                 primary_provider_id, temp_c, temp_min_c, temp_max_c, feels_like_c, humidity_pct,
                 pressure_hpa, wind_kmh, precip_mm, has_discrepancy, discrepancy_summary, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(location_id, forecast_days) DO UPDATE SET
                    collected_at = excluded.collected_at,
                    expires_at = excluded.expires_at,
                    content_hash = excluded.content_hash,
                    source_count = excluded.source_count,
                    primary_provider_id = excluded.primary_provider_id,
                    temp_c = excluded.temp_c,
                    temp_min_c = excluded.temp_min_c,
                    temp_max_c = excluded.temp_max_c,
                    feels_like_c = excluded.feels_like_c,
                    humidity_pct = excluded.humidity_pct,
                    pressure_hpa = excluded.pressure_hpa,
                    wind_kmh = excluded.wind_kmh,
                    precip_mm = excluded.precip_mm,
                    has_discrepancy = excluded.has_discrepancy,
                    discrepancy_summary = excluded.discrepancy_summary,
                    payload_json = excluded.payload_json
                """,
                (
                    location_id,
                    forecast_days,
                    now_iso,
                    now_ts + ttl_seconds,
                    content_hash,
                    source_count,
                    primary_provider_id,
                    summary_metrics.get("temp_c"),
                    summary_metrics.get("temp_min_c"),
                    summary_metrics.get("temp_max_c"),
                    summary_metrics.get("feels_like_c"),
                    summary_metrics.get("humidity_pct"),
                    summary_metrics.get("pressure_hpa"),
                    summary_metrics.get("wind_kmh"),
                    summary_metrics.get("precip_mm"),
                    1 if summary_metrics.get("has_discrepancy") else 0,
                    str(summary_metrics.get("discrepancy_summary") or ""),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            return changed

    def save_provider_readings(self, rows: list[dict[str, Any]]) -> int:
        """Almacena lecturas individuales por proveedor evitando duplicados por (location, provider, observed_at)."""
        if not rows:
            return 0
        inserted = 0
        with self.db.connection() as conn:
            for r in rows:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO provider_readings
                    (location_id, provider_id, observed_at, fetched_at, temp_c, temp_min_c, temp_max_c,
                     feels_like_c, humidity_pct, pressure_hpa, wind_kmh, wind_gusts_kmh, wind_dir_deg,
                     precip_mm, precip_prob_pct, weather_code, condition_text, latency_ms, reading_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r["location_id"],
                        r["provider_id"],
                        r["observed_at"],
                        r["fetched_at"],
                        r.get("temp_c"),
                        r.get("temp_min_c"),
                        r.get("temp_max_c"),
                        r.get("feels_like_c"),
                        r.get("humidity_pct"),
                        r.get("pressure_hpa"),
                        r.get("wind_kmh"),
                        r.get("wind_gusts_kmh"),
                        r.get("wind_dir_deg"),
                        r.get("precip_mm"),
                        r.get("precip_prob_pct"),
                        r.get("weather_code"),
                        r.get("condition_text", ""),
                        float(r.get("latency_ms", 0.0)),
                        r["reading_hash"],
                    ),
                )
                if cur.rowcount > 0:
                    inserted += 1
        return inserted

    def save_hourly_forecasts(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        now = utc_now_iso()
        params = [
            (
                r["location_id"],
                r["provider_id"],
                r["target_time"],
                r.get("temp_c"),
                r.get("feels_like_c"),
                r.get("humidity_pct"),
                r.get("pressure_hpa"),
                r.get("wind_kmh"),
                r.get("precip_mm"),
                r.get("precip_prob_pct"),
                r.get("uv_index"),
                r.get("weather_code"),
                now,
            )
            for r in rows
        ]
        with self.db.connection() as conn:
            before = conn.total_changes
            conn.executemany(
                """
                INSERT INTO hourly_forecasts_store
                (location_id, provider_id, target_time, temp_c, feels_like_c, humidity_pct,
                 pressure_hpa, wind_kmh, precip_mm, precip_prob_pct, uv_index, weather_code, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(location_id, provider_id, target_time) DO UPDATE SET
                    temp_c = excluded.temp_c,
                    feels_like_c = excluded.feels_like_c,
                    humidity_pct = excluded.humidity_pct,
                    pressure_hpa = excluded.pressure_hpa,
                    wind_kmh = excluded.wind_kmh,
                    precip_mm = excluded.precip_mm,
                    precip_prob_pct = excluded.precip_prob_pct,
                    uv_index = excluded.uv_index,
                    weather_code = excluded.weather_code,
                    updated_at = excluded.updated_at
                """,
                params,
            )
            return conn.total_changes - before

    def save_daily_forecasts(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        now = utc_now_iso()
        params = [
            (
                r["location_id"],
                r["provider_id"],
                r["forecast_date"],
                r.get("temp_min_c"),
                r.get("temp_max_c"),
                r.get("temp_mean_c"),
                r.get("precip_sum_mm"),
                r.get("precip_prob_max"),
                r.get("wind_max_kmh"),
                r.get("uv_max"),
                r.get("sunrise"),
                r.get("sunset"),
                r.get("weather_code"),
                now,
            )
            for r in rows
        ]
        with self.db.connection() as conn:
            before = conn.total_changes
            conn.executemany(
                """
                INSERT INTO daily_forecasts_store
                (location_id, provider_id, forecast_date, temp_min_c, temp_max_c, temp_mean_c,
                 precip_sum_mm, precip_prob_max, wind_max_kmh, uv_max, sunrise, sunset, weather_code, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(location_id, provider_id, forecast_date) DO UPDATE SET
                    temp_min_c = excluded.temp_min_c,
                    temp_max_c = excluded.temp_max_c,
                    temp_mean_c = excluded.temp_mean_c,
                    precip_sum_mm = excluded.precip_sum_mm,
                    precip_prob_max = excluded.precip_prob_max,
                    wind_max_kmh = excluded.wind_max_kmh,
                    uv_max = excluded.uv_max,
                    sunrise = excluded.sunrise,
                    sunset = excluded.sunset,
                    weather_code = excluded.weather_code,
                    updated_at = excluded.updated_at
                """,
                params,
            )
            return conn.total_changes - before

    def save_comparison_run(
        self,
        location_id: int,
        ttl_seconds: int,
        summary: dict[str, Any],
        payload: dict[str, Any],
    ) -> int:
        now_ts = time.time()
        # Bucket por minuto para evitar duplicaciones si corren procesos concurrentes
        minute_iso = utc_now_iso()[:16] + ":00Z"
        with self.db.connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO comparison_runs
                (location_id, compared_at, expires_at, active_providers, selected_temp_c,
                 mean_temp_c, median_temp_c, min_temp_c, max_temp_c, temp_spread_c, std_temp_c,
                 primary_provider_id, has_discrepancy, discrepancy_summary, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(location_id, compared_at) DO UPDATE SET
                    expires_at = excluded.expires_at,
                    active_providers = excluded.active_providers,
                    selected_temp_c = excluded.selected_temp_c,
                    payload_json = excluded.payload_json
                """,
                (
                    location_id,
                    minute_iso,
                    now_ts + ttl_seconds,
                    int(summary.get("active_providers", 0)),
                    summary.get("selected_temp_c"),
                    summary.get("mean_temp_c"),
                    summary.get("median_temp_c"),
                    summary.get("min_temp_c"),
                    summary.get("max_temp_c"),
                    summary.get("temp_spread_c"),
                    summary.get("std_temp_c"),
                    str(summary.get("primary_provider_id") or "open_meteo"),
                    1 if summary.get("has_discrepancy") else 0,
                    str(summary.get("discrepancy_summary") or ""),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            return cur.rowcount

    def get_latest_comparison_run(self, location_id: int) -> Optional[dict[str, Any]]:
        now = time.time()
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM comparison_runs
                WHERE location_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (location_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "payload": json.loads(row["payload_json"]),
                "compared_at": row["compared_at"],
                "expires_at": float(row["expires_at"]),
                "is_stale": now >= float(row["expires_at"]),
            }

    def list_stale_or_uncollected_locations(self, limit: int = 12) -> list[LocationModel]:
        """Identifica únicamente las ubicaciones que necesitan actualización (vencidas o sin snapshot)."""
        now = time.time()
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT l.*
                FROM locations l
                LEFT JOIN weather_snapshots s
                    ON s.location_id = l.id AND s.forecast_days = 7
                WHERE l.is_tracked = 1
                  AND (s.location_id IS NULL OR s.expires_at <= ?)
                ORDER BY l.is_favorite DESC, COALESCE(s.expires_at, 0) ASC, l.last_queried_at DESC
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
            return [self._row_to_location(r) for r in rows]

    def log_collection_run(self, run: dict[str, Any]) -> int:
        with self.db.connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO collection_runs
                (worker_name, started_at, finished_at, status, locations_checked, locations_updated,
                 changes_detected, readings_stored, forecasts_stored, pairs_verified, errors_count, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.get("worker_name", "collector"),
                    run["started_at"],
                    run["finished_at"],
                    run.get("status", "ok"),
                    int(run.get("locations_checked", 0)),
                    int(run.get("locations_updated", 0)),
                    int(run.get("changes_detected", 0)),
                    int(run.get("readings_stored", 0)),
                    int(run.get("forecasts_stored", 0)),
                    int(run.get("pairs_verified", 0)),
                    int(run.get("errors_count", 0)),
                    str(run.get("notes") or "")[:300],
                ),
            )
            return int(cur.lastrowid)

    def list_collection_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM collection_runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
