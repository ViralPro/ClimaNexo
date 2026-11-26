"""Servicio Recolector Meteorológico Continuo: RECOPILAR → VALIDAR → COMPARAR → ALMACENAR → ACTUALIZAR."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, timedelta
from typing import Any, Optional

from app.config import DEFAULT_CITIES, PROVIDER_META_MAP, settings
from app.db.database import utc_now_iso
from app.db.repository import WeatherRepository
from app.models.schemas import (
    ComparisonProviderRow,
    LocationModel,
    ProviderComparisonResponse,
    ProviderSnapshot,
    WeatherAlertModel,
    WeatherBundleResponse,
)
from app.providers.registry import ProviderRegistry
from app.services.accuracy_engine import AccuracyEngine
from app.services.alerts_service import evaluate_weather_alerts
from app.services.narrative_service import build_forecast_narrative, resolve_theme
from app.services.query_parser import format_date_range_es
from app.services.smart_engine import SmartEvaluationResult, SmartMeteorologicalEngine


class CollectorService:
    """
    Orquesta el ciclo completo de recopilación y almacenamiento sin duplicados:
    1. RECOPILAR: Consulta múltiples fuentes concurrentemente con recuperación ante errores.
    2. VALIDAR: Filtra lecturas físicamente inválidas.
    3. COMPARAR: Evalúa frescura, precisión histórica real, salud y discrepancias.
    4. ALMACENAR: Persiste lecturas, pronósticos horarios/diarios, comparaciones, alertas y snapshot en SQLite.
    5. ACTUALIZAR: Solo refresca ubicaciones vencidas/faltantes y evalúa verificaciones pendientes.
    """

    def __init__(
        self,
        repo: WeatherRepository | None = None,
        registry: ProviderRegistry | None = None,
        accuracy: AccuracyEngine | None = None,
    ) -> None:
        self.repo = repo or WeatherRepository()
        self.registry = registry or ProviderRegistry(self.repo)
        self.accuracy = accuracy or AccuracyEngine(self.repo)
        self.smart_engine = SmartMeteorologicalEngine(self.repo)
        self._inflight: dict[tuple[int, int], asyncio.Task] = {}

    async def collect_location(
        self,
        location: LocationModel,
        forecast_days: int = 7,
        focus_metric: str = "general",
    ) -> dict[str, Any]:
        """Ejecuta el pipeline completo para una ubicación y almacena todo en SQLite."""
        if location.id is None:
            location = self.repo.upsert_location(location)
        assert location.id is not None

        days = max(1, min(16, int(forecast_days)))

        # 1. RECOPILAR con recuperación automática ante fallos transitorios
        snapshots = await self._fetch_with_recovery(location, days)

        # 2. VALIDAR y 3. COMPARAR mediante motor meteorológico inteligente
        eval_res = self.smart_engine.evaluate(location, snapshots)

        # 4. ALMACENAR en todas las tablas relacionales (sin duplicados)
        readings_stored, forecasts_stored = self._store_relational_records(location, snapshots)

        # Evaluar alertas reales incluyendo avisos oficiales de proveedores
        official_alerts: list[WeatherAlertModel] = []
        for s in snapshots:
            if s.raw_alerts:
                official_alerts.extend(s.raw_alerts)
        evaluated_alerts = evaluate_weather_alerts(
            location.name,
            eval_res.selected_current,
            eval_res.selected_hourly,
            eval_res.selected_daily,
            official_alerts=official_alerts,
        )
        saved_alerts = self.repo.sync_location_alerts(location.id, evaluated_alerts)

        # Construir y almacenar payload completo de comparación multiproveedor
        comp_payload = self._build_comparison_response(location, snapshots, eval_res)
        self.repo.save_comparison_run(
            location_id=location.id,
            ttl_seconds=settings.comparison_ttl_seconds,
            summary={
                "active_providers": comp_payload.active_providers,
                "selected_temp_c": eval_res.selected_current.temperature_c,
                "mean_temp_c": eval_res.consensus.get("temp_mean_c"),
                "median_temp_c": eval_res.consensus.get("temp_median_c"),
                "min_temp_c": eval_res.consensus.get("temp_min_c"),
                "max_temp_c": eval_res.consensus.get("temp_max_c"),
                "temp_spread_c": eval_res.consensus.get("temp_spread_c"),
                "std_temp_c": eval_res.consensus.get("temp_std_c"),
                "primary_provider_id": eval_res.primary_provider_id,
                "has_discrepancy": eval_res.discrepancy.get("has_significant_diff", False),
                "discrepancy_summary": eval_res.discrepancy.get("summary", ""),
            },
            payload=comp_payload.model_dump(),
        )

        # Construir y almacenar snapshot consolidado listo para lectura instantánea (< 5 ms)
        d1 = eval_res.selected_daily[0].date if eval_res.selected_daily else date.today().isoformat()
        d2 = (
            eval_res.selected_daily[-1].date
            if eval_res.selected_daily
            else (date.today() + timedelta(days=days - 1)).isoformat()
        )
        date_label = format_date_range_es(d1, d2)
        narrative = build_forecast_narrative(
            location, eval_res.selected_current, eval_res.selected_daily, focus_metric
        )
        precip_total = sum((d.precipitation_sum_mm or 0.0) for d in eval_res.selected_daily)
        theme = resolve_theme(
            weather_code=eval_res.selected_current.weather_code,
            temp_c=eval_res.selected_current.temperature_c,
            precip_total=precip_total,
            focus_metric=focus_metric,
        )

        now_iso = utc_now_iso()
        bundle = WeatherBundleResponse(
            location=location,
            provider=eval_res.primary_provider_name,
            fetched_at=now_iso,
            from_cache=False,
            from_database=False,
            is_stale=False,
            refreshing_in_background=False,
            data_age_seconds=0,
            theme=theme,
            narrative=narrative,
            date_focus_label=date_label,
            current=eval_res.selected_current,
            hourly=eval_res.selected_hourly,
            daily=eval_res.selected_daily,
            alerts=saved_alerts,
            consensus_preview=eval_res.consensus,
            provenance=eval_res.provenance,
            discrepancy=eval_res.discrepancy,
            confidence=eval_res.confidence,
        )

        today_daily = eval_res.selected_daily[0] if eval_res.selected_daily else None
        changed = self.repo.save_weather_snapshot(
            location_id=location.id,
            forecast_days=days,
            ttl_seconds=settings.snapshot_ttl_seconds,
            content_hash=eval_res.content_hash,
            source_count=eval_res.consensus.get("active_sources", 1),
            primary_provider_id=eval_res.primary_provider_id,
            summary_metrics={
                "temp_c": eval_res.selected_current.temperature_c,
                "temp_min_c": today_daily.temp_min_c if today_daily else None,
                "temp_max_c": today_daily.temp_max_c if today_daily else None,
                "feels_like_c": eval_res.selected_current.feels_like_c,
                "humidity_pct": eval_res.selected_current.humidity_pct,
                "pressure_hpa": eval_res.selected_current.pressure_hpa,
                "wind_kmh": eval_res.selected_current.wind_speed_kmh,
                "precip_mm": eval_res.selected_current.precipitation_mm,
                "has_discrepancy": eval_res.discrepancy.get("has_significant_diff", False),
                "discrepancy_summary": eval_res.discrepancy.get("summary", ""),
            },
            payload=bundle.model_dump(),
        )

        # Evaluar cualquier par maduro pendiente tras guardar las nuevas observaciones
        pairs_verified = self.accuracy.evaluate_pending(location_id=location.id)

        return {
            "bundle": bundle,
            "comparison": comp_payload,
            "changed": changed,
            "readings_stored": readings_stored,
            "forecasts_stored": forecasts_stored,
            "pairs_verified": pairs_verified,
            "rejected_readings": eval_res.rejected_count,
        }

    def trigger_background_refresh(self, location: LocationModel, forecast_days: int = 7) -> bool:
        """Dispara actualización asíncrona sin bloquear la consulta y evitando duplicar tareas."""
        if location.id is None:
            return False
        key = (location.id, int(forecast_days))
        existing = self._inflight.get(key)
        if existing is not None and not existing.done():
            return True

        async def _runner() -> None:
            try:
                # Ceder un ciclo breve al event loop para que la respuesta HTTP salga primero al usuario
                await asyncio.sleep(0.02)
                await self.collect_location(location, forecast_days=forecast_days)
            except Exception:
                pass
            finally:
                self._inflight.pop(key, None)

        self._inflight[key] = asyncio.create_task(_runner(), name=f"refresh-loc-{location.id}-{forecast_days}d")
        return True

    async def run_cycle(
        self,
        worker_name: str = "collector",
        force_all: bool = False,
        max_locations: int = 12,
    ) -> dict[str, Any]:
        """
        Ciclo autónomo del proceso recolector:
        - Asegura ubicaciones base si la BD está recién creada.
        - Identifica únicamente ubicaciones vencidas o sin snapshot (salvo force_all=True).
        - Recopila, valida, compara, almacena, verifica precisión y limpia caché vencida.
        """
        started_at = utc_now_iso()

        # Sembrar ciudades iniciales solo si la tabla de ubicaciones está vacía
        if not self.repo.list_locations(limit=1):
            for c in DEFAULT_CITIES:
                self.repo.upsert_location(LocationModel(**c))

        if force_all:
            targets = self.repo.list_tracked_locations(limit=max_locations)
        else:
            targets = self.repo.list_stale_or_uncollected_locations(limit=max_locations)

        locations_updated = 0
        changes_detected = 0
        readings_stored = 0
        forecasts_stored = 0
        pairs_verified = 0
        errors_count = 0
        backtests_run = 0

        for loc in targets:
            try:
                res = await self.collect_location(loc, forecast_days=7)
                locations_updated += 1
                if res["changed"]:
                    changes_detected += 1
                readings_stored += res["readings_stored"]
                forecasts_stored += res["forecasts_stored"]
                pairs_verified += res["pairs_verified"]

                # Ejecutar backtest histórico real (máx 2 ubicaciones por ciclo) si aún no tiene historial
                if loc.id is not None and backtests_run < 2:
                    agg = self.repo.get_accuracy_aggregates(location_id=loc.id)
                    if agg["historical_verified"] == 0:
                        bt = await self.accuracy.ingest_historical_verification(loc, past_days=7)
                        pairs_verified += int(bt.get("newly_verified", 0))
                        backtests_run += 1
            except Exception:
                errors_count += 1
                continue

        # Verificación global de cualquier pronóstico maduro
        try:
            pairs_verified += self.accuracy.evaluate_pending()
        except Exception:
            pass

        purged_cache = self.repo.purge_expired_cache()
        finished_at = utc_now_iso()
        status = "ok" if errors_count == 0 else ("partial" if locations_updated > 0 else "error")

        summary = {
            "worker_name": worker_name,
            "started_at": started_at,
            "finished_at": finished_at,
            "status": status,
            "locations_checked": len(targets),
            "locations_updated": locations_updated,
            "changes_detected": changes_detected,
            "readings_stored": readings_stored,
            "forecasts_stored": forecasts_stored,
            "pairs_verified": pairs_verified,
            "purged_cache_entries": purged_cache,
            "errors_count": errors_count,
            "notes": (
                f"Actualizadas {locations_updated}/{len(targets)} ubicaciones vencidas; "
                f"{changes_detected} con cambios meteorológicos."
                if targets
                else "Todas las ubicaciones monitoreadas tienen datos vigentes."
            ),
        }
        self.repo.log_collection_run(summary)
        return summary

    async def _fetch_with_recovery(self, location: LocationModel, forecast_days: int) -> list[ProviderSnapshot]:
        """Consulta concurrente multiproveedor con reintento automático para fuentes con error transitorio."""
        snapshots = await self.registry.fetch_all(
            location, forecast_days=forecast_days, record_for_verification=True
        )
        ok_count = sum(1 for s in snapshots if s.status == "ok" and s.current is not None)
        failed_indices = [
            i for i, s in enumerate(snapshots) if s.status == "error"
        ]

        # Reintentar únicamente proveedores con error de red transitorio si hay pocos activos o falló alguno clave
        attempt = 0
        while failed_indices and attempt < settings.collector_max_retries and (ok_count < 3 or attempt == 0):
            attempt += 1
            await asyncio.sleep(settings.collector_backoff_seconds * attempt)
            retry_tasks = [
                self.registry.fetch_single(snapshots[i].provider_id, location, forecast_days=forecast_days)
                for i in failed_indices
            ]
            retried = await asyncio.gather(*retry_tasks)
            new_failed: list[int] = []
            for idx, new_snap in zip(failed_indices, retried):
                snapshots[idx] = new_snap
                if new_snap.status == "error":
                    new_failed.append(idx)
            failed_indices = new_failed
            ok_count = sum(1 for s in snapshots if s.status == "ok" and s.current is not None)

        return snapshots

    def _store_relational_records(
        self,
        location: LocationModel,
        snapshots: list[ProviderSnapshot],
    ) -> tuple[int, int]:
        """Persiste lecturas actuales y pronósticos horarios/diarios en tablas dedicadas sin duplicar."""
        assert location.id is not None
        reading_rows: list[dict[str, Any]] = []
        hourly_rows: list[dict[str, Any]] = []
        daily_rows: list[dict[str, Any]] = []

        for snap in snapshots:
            if snap.status != "ok" or snap.current is None:
                continue
            cur = snap.current
            today_d = snap.daily[0] if snap.daily else None
            r_hash = hashlib.sha256(
                json.dumps(
                    {
                        "p": snap.provider_id,
                        "o": cur.observed_at,
                        "t": cur.temperature_c,
                        "h": cur.humidity_pct,
                        "pr": cur.pressure_hpa,
                        "w": cur.wind_speed_kmh,
                        "r": cur.precipitation_mm,
                    },
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()[:20]

            reading_rows.append(
                {
                    "location_id": location.id,
                    "provider_id": snap.provider_id,
                    "observed_at": cur.observed_at,
                    "fetched_at": snap.fetched_at,
                    "temp_c": cur.temperature_c,
                    "temp_min_c": today_d.temp_min_c if today_d else None,
                    "temp_max_c": today_d.temp_max_c if today_d else None,
                    "feels_like_c": cur.feels_like_c,
                    "humidity_pct": cur.humidity_pct,
                    "pressure_hpa": cur.pressure_hpa,
                    "wind_kmh": cur.wind_speed_kmh,
                    "wind_gusts_kmh": cur.wind_gusts_kmh,
                    "wind_dir_deg": cur.wind_direction_deg,
                    "precip_mm": cur.precipitation_mm,
                    "precip_prob_pct": cur.precip_probability_pct,
                    "weather_code": cur.weather_code,
                    "condition_text": cur.condition_text,
                    "latency_ms": snap.latency_ms,
                    "reading_hash": r_hash,
                }
            )

            # Guardar pronósticos horarios (48 h para fuente principal, 24 h para fuentes secundarias)
            h_limit = 48 if snap.provider_id == "open_meteo" else 24
            for hp in snap.hourly[:h_limit]:
                hourly_rows.append(
                    {
                        "location_id": location.id,
                        "provider_id": snap.provider_id,
                        "target_time": hp.time[:16],
                        "temp_c": hp.temperature_c,
                        "feels_like_c": hp.feels_like_c,
                        "humidity_pct": hp.humidity_pct,
                        "pressure_hpa": hp.pressure_hpa,
                        "wind_kmh": hp.wind_speed_kmh,
                        "precip_mm": hp.precipitation_mm,
                        "precip_prob_pct": hp.precip_probability_pct,
                        "uv_index": hp.uv_index,
                        "weather_code": hp.weather_code,
                    }
                )

            for dp in snap.daily[:16]:
                daily_rows.append(
                    {
                        "location_id": location.id,
                        "provider_id": snap.provider_id,
                        "forecast_date": dp.date,
                        "temp_min_c": dp.temp_min_c,
                        "temp_max_c": dp.temp_max_c,
                        "temp_mean_c": dp.temp_mean_c,
                        "precip_sum_mm": dp.precipitation_sum_mm,
                        "precip_prob_max": dp.precip_probability_max_pct,
                        "wind_max_kmh": dp.wind_speed_max_kmh,
                        "uv_max": dp.uv_index_max,
                        "sunrise": dp.sunrise,
                        "sunset": dp.sunset,
                        "weather_code": dp.weather_code,
                    }
                )

        readings_n = self.repo.save_provider_readings(reading_rows)
        hourly_n = self.repo.save_hourly_forecasts(hourly_rows)
        daily_n = self.repo.save_daily_forecasts(daily_rows)
        return readings_n, hourly_n + daily_n

    def _build_comparison_response(
        self,
        location: LocationModel,
        snapshots: list[ProviderSnapshot],
        eval_res: SmartEvaluationResult,
    ) -> ProviderComparisonResponse:
        cfg_map = {p["provider_id"]: p for p in self.repo.list_providers()}
        eval_map = {e.snapshot.provider_id: e for e in eval_res.evaluated_sources}
        selected_temp = eval_res.consensus.get("selected_temp_c") or eval_res.consensus.get("temp_mean_c")

        rows: list[ComparisonProviderRow] = []
        ok_snaps: list[ProviderSnapshot] = []
        for s in snapshots:
            cfg = cfg_map.get(s.provider_id, {})
            meta = PROVIDER_META_MAP.get(s.provider_id, {})
            ev = eval_map.get(s.provider_id)
            cur = s.current
            if s.status == "ok" and cur is not None:
                ok_snaps.append(s)

            dev = (
                round(cur.temperature_c - selected_temp, 1)
                if cur and cur.temperature_c is not None and selected_temp is not None
                else None
            )
            rows.append(
                ComparisonProviderRow(
                    provider_id=s.provider_id,
                    display_name=s.display_name,
                    description=meta.get("description", ""),
                    status=s.status,
                    requires_api_key=bool(cfg.get("requires_api_key", 0)),
                    has_api_key=bool(cfg.get("has_api_key", False)),
                    latency_ms=s.latency_ms,
                    error_message=s.error_message,
                    observed_at=cur.observed_at if cur else None,
                    freshness_minutes=ev.freshness_minutes if ev and ev.is_valid else None,
                    reliability_weight=ev.total_weight if ev and ev.is_valid else None,
                    historical_accuracy_pct=ev.historical_accuracy_pct if ev else None,
                    historical_samples=ev.historical_samples if ev else 0,
                    is_primary_selected=(s.provider_id == eval_res.primary_provider_id),
                    current_temp_c=cur.temperature_c if cur else None,
                    feels_like_c=cur.feels_like_c if cur else None,
                    humidity_pct=cur.humidity_pct if cur else None,
                    pressure_hpa=cur.pressure_hpa if cur else None,
                    wind_kmh=cur.wind_speed_kmh if cur else None,
                    precip_mm=cur.precipitation_mm if cur else None,
                    condition_text=cur.condition_text if cur else "—",
                    temp_deviation_c=dev,
                    hourly_24h=[
                        {
                            "time": h.time[:16],
                            "temp_c": h.temperature_c,
                            "precip_mm": h.precipitation_mm,
                            "wind_kmh": h.wind_speed_kmh,
                        }
                        for h in s.hourly[:24]
                    ],
                    daily_7d=[
                        {
                            "date": d.date,
                            "temp_max_c": d.temp_max_c,
                            "temp_min_c": d.temp_min_c,
                            "precip_mm": d.precipitation_sum_mm,
                            "wind_kmh": d.wind_speed_max_kmh,
                        }
                        for d in s.daily[:7]
                    ],
                )
            )

        hourly_chart = _align_hourly_series(ok_snaps)
        daily_chart = _align_daily_series(ok_snaps)

        return ProviderComparisonResponse(
            location=location,
            fetched_at=utc_now_iso(),
            from_database=False,
            is_stale=False,
            refreshing_in_background=False,
            active_providers=len(ok_snaps),
            total_providers=len(snapshots),
            consensus=eval_res.consensus,
            discrepancy=eval_res.discrepancy,
            provenance=eval_res.provenance,
            confidence=eval_res.confidence,
            providers=rows,
            hourly_chart=hourly_chart,
            daily_chart=daily_chart,
        )


def _align_hourly_series(snaps: list[ProviderSnapshot]) -> dict[str, Any]:
    ref = next((s for s in snaps if s.provider_id == "open_meteo" and s.hourly), None)
    if not ref:
        ref = next((s for s in snaps if len(s.hourly) >= 6), None)
    if not ref:
        return {"labels": [], "datasets": []}

    start_idx = 0
    if ref.current and ref.current.observed_at:
        prefix = ref.current.observed_at[:13]
        for i, h in enumerate(ref.hourly):
            if h.time.startswith(prefix):
                start_idx = i
                break
    ref_slice = ref.hourly[start_idx : start_idx + 24]
    labels = [h.time[:16] for h in ref_slice]
    hour_keys = [h.time[:13] for h in ref_slice]

    datasets = []
    for s in snaps:
        if not s.hourly:
            continue
        by_hour = {h.time[:13]: h.temperature_c for h in s.hourly if h.temperature_c is not None}
        vals = [by_hour.get(k) for k in hour_keys]
        if any(v is not None for v in vals):
            datasets.append({"provider_id": s.provider_id, "label": s.display_name, "values": vals})

    return {"labels": labels, "datasets": datasets}


def _align_daily_series(snaps: list[ProviderSnapshot]) -> dict[str, Any]:
    all_dates = sorted({d.date for s in snaps for d in s.daily if d.date})[:7]
    datasets = []
    for s in snaps:
        if not s.daily:
            continue
        by_date = {d.date: d.temp_max_c for d in s.daily}
        vals = [by_date.get(dt) for dt in all_dates]
        if any(v is not None for v in vals):
            datasets.append({"provider_id": s.provider_id, "label": s.display_name, "values": vals})
    return {"labels": all_dates, "datasets": datasets}
