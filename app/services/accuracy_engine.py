"""Motor Real de Verificación y Precisión Meteorológica (cero resultados inventados)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from app.config import PROVIDER_META_MAP, settings
from app.db.database import utc_now_iso
from app.db.repository import WeatherRepository
from app.models.schemas import (
    AccuracyAuditItem,
    AccuracyDashboardResponse,
    AccuracyProviderStat,
    LocationModel,
)
from app.providers.base import normalize_hour_utc, safe_float

# Modelos disponibles en Open-Meteo Previous Runs API vinculados a nuestros provider_id reales
PREVIOUS_RUN_MODEL_MAP: dict[str, str] = {
    "open_meteo": "best_match",
    "ecmwf_ifs": "ecmwf_ifs025",
    "noaa_gfs": "gfs_seamless",
    "dwd_icon": "icon_seamless",
}

METHODOLOGY_NOTE = (
    "Todos los porcentajes provienen exclusivamente de pares verificados en SQLite (pronóstico emitido "
    "con anticipación vs. observación meteorológica real). Fórmula transparente: Precisión Compuesta = "
    "55% Temperatura [max(0, 100 - |ΔT|×12.5)] + 25% Precipitación [acierto lluvia/seco + error mm] + "
    "20% Viento [max(0, 100 - |ΔV|×3.5)], reponderada dinámicamente según variables disponibles. "
    "Los proveedores sin muestras maduras muestran 'Sin datos maduros' y nunca valores inventados."
)


def compute_pair_verification(pair: dict[str, Any]) -> dict[str, Any]:
    """Calcula errores reales y puntuación compuesta (0-100%) para un par pronóstico/observación."""
    pred_t = pair.get("pred_temp_c")
    act_t = pair.get("actual_temp_c")
    temp_err = round(pred_t - act_t, 2) if pred_t is not None and act_t is not None else None
    temp_abs = round(abs(temp_err), 2) if temp_err is not None else None

    pred_w = pair.get("pred_wind_kmh")
    act_w = pair.get("actual_wind_kmh")
    wind_abs = round(abs(pred_w - act_w), 2) if pred_w is not None and act_w is not None else None

    pred_p = pair.get("pred_precip_mm")
    act_p = pair.get("actual_precip_mm")
    precip_abs = round(abs(pred_p - act_p), 2) if pred_p is not None and act_p is not None else None
    rain_hit: Optional[int] = None
    if pred_p is not None and act_p is not None:
        rain_hit = 1 if ((pred_p >= 0.1) == (act_p >= 0.1)) else 0

    pred_h = pair.get("pred_humidity_pct")
    act_h = pair.get("actual_humidity_pct")
    hum_abs = round(abs(pred_h - act_h), 2) if pred_h is not None and act_h is not None else None

    pred_pr = pair.get("pred_pressure_hpa")
    act_pr = pair.get("actual_pressure_hpa")
    pres_abs = round(abs(pred_pr - act_pr), 2) if pred_pr is not None and act_pr is not None else None

    # Puntaje compuesto ponderado solo sobre variables reales presentes
    weighted_sum = 0.0
    total_weight = 0.0

    if temp_abs is not None:
        t_score = max(0.0, min(100.0, 100.0 - (temp_abs * 12.5)))
        weighted_sum += t_score * 0.55
        total_weight += 0.55

    if precip_abs is not None and rain_hit is not None:
        mag_score = max(0.0, min(100.0, 100.0 - (precip_abs * 18.0)))
        hit_score = 100.0 if rain_hit == 1 else 25.0
        p_score = (mag_score * 0.5) + (hit_score * 0.5)
        weighted_sum += p_score * 0.25
        total_weight += 0.25

    if wind_abs is not None:
        w_score = max(0.0, min(100.0, 100.0 - (wind_abs * 3.5)))
        weighted_sum += w_score * 0.20
        total_weight += 0.20

    composite = round(weighted_sum / total_weight, 2) if total_weight > 0 else 0.0

    return {
        "forecast_record_id": pair["forecast_record_id"],
        "observation_id": pair["observation_id"],
        "location_id": pair["location_id"],
        "provider_id": pair["provider_id"],
        "source_type": pair["source_type"],
        "target_time": pair["target_time"],
        "lead_hours": pair["lead_hours"],
        "pred_temp_c": pred_t,
        "actual_temp_c": act_t,
        "temp_error_c": temp_err,
        "temp_abs_error_c": temp_abs,
        "pred_wind_kmh": pred_w,
        "actual_wind_kmh": act_w,
        "wind_abs_error_kmh": wind_abs,
        "pred_precip_mm": pred_p,
        "actual_precip_mm": act_p,
        "precip_abs_error_mm": precip_abs,
        "rain_hit": rain_hit,
        "pred_humidity_pct": pred_h,
        "actual_humidity_pct": act_h,
        "humidity_abs_error": hum_abs,
        "pred_pressure_hpa": pred_pr,
        "actual_pressure_hpa": act_pr,
        "pressure_abs_error_hpa": pres_abs,
        "composite_score_pct": composite,
    }


class AccuracyEngine:
    PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
    ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

    def __init__(self, repo: WeatherRepository | None = None) -> None:
        self.repo = repo or WeatherRepository()

    def evaluate_pending(self, location_id: Optional[int] = None) -> int:
        """Evalúa todos los pares (pronóstico guardado <-> observación real) pendientes en SQLite."""
        pairs = self.repo.get_unverified_forecast_pairs(location_id=location_id)
        if not pairs:
            return 0
        computed = [compute_pair_verification(p) for p in pairs]
        return self.repo.save_verification_results(computed)

    async def ingest_historical_verification(
        self,
        location: LocationModel,
        past_days: int = 7,
    ) -> dict[str, int]:
        """
        Descarga corridas reales anteriores de pronóstico (previous_day1 = +24h y previous_day2 = +48h)
        de Open-Meteo Previous Runs API para Open-Meteo Best Match, ECMWF IFS, NOAA GFS y DWD ICON,
        y las contrasta contra observaciones reales (Archivo ERA5/observación día-0) en SQLite.
        """
        if location.id is None:
            location = self.repo.upsert_location(location)
        assert location.id is not None

        past_days = max(3, min(14, int(past_days)))
        models_csv = ",".join(PREVIOUS_RUN_MODEL_MAP.values())
        params = {
            "latitude": round(location.latitude, 4),
            "longitude": round(location.longitude, 4),
            "hourly": (
                "temperature_2m,temperature_2m_previous_day1,temperature_2m_previous_day2,"
                "precipitation,precipitation_previous_day1,precipitation_previous_day2,"
                "wind_speed_10m,wind_speed_10m_previous_day1,wind_speed_10m_previous_day2,"
                "relative_humidity_2m,relative_humidity_2m_previous_day1,"
                "pressure_msl,pressure_msl_previous_day1"
            ),
            "past_days": past_days,
            "forecast_days": 1,
            "models": models_csv,
            "timezone": "UTC",
        }

        async with httpx.AsyncClient(
            timeout=18.0,
            headers={"User-Agent": settings.user_agent},
        ) as client:
            resp = await client.get(self.PREVIOUS_RUNS_URL, params=params)
            resp.raise_for_status()
            prev_data = resp.json()

            # También intentamos traer observaciones reales del archivo ERA5/reanalysis para días >= 2 días atrás
            now_utc = datetime.now(timezone.utc)
            arch_end = (now_utc - timedelta(days=2)).date()
            arch_start = (now_utc - timedelta(days=past_days)).date()
            arch_obs_by_time: dict[str, dict[str, Optional[float]]] = {}
            if arch_end >= arch_start:
                try:
                    ar = await client.get(
                        self.ARCHIVE_URL,
                        params={
                            "latitude": round(location.latitude, 4),
                            "longitude": round(location.longitude, 4),
                            "start_date": arch_start.isoformat(),
                            "end_date": arch_end.isoformat(),
                            "hourly": "temperature_2m,relative_humidity_2m,precipitation,pressure_msl,wind_speed_10m",
                            "timezone": "UTC",
                        },
                    )
                    if ar.status_code == 200:
                        ah = ar.json().get("hourly") or {}
                        atimes = ah.get("time") or []
                        for i, t in enumerate(atimes):
                            t_iso = normalize_hour_utc(t)
                            t_val = safe_float(_get(ah.get("temperature_2m"), i))
                            if t_val is not None:
                                arch_obs_by_time[t_iso] = {
                                    "temp_c": t_val,
                                    "humidity_pct": safe_float(_get(ah.get("relative_humidity_2m"), i), 0),
                                    "precip_mm": safe_float(_get(ah.get("precipitation"), i), 2) or 0.0,
                                    "pressure_hpa": safe_float(_get(ah.get("pressure_msl"), i)),
                                    "wind_kmh": safe_float(_get(ah.get("wind_speed_10m"), i)),
                                }
                except Exception:
                    pass

        hourly = prev_data.get("hourly") or {}
        times: list[str] = hourly.get("time") or []
        cutoff_utc = now_utc - timedelta(hours=1)

        obs_rows: list[dict[str, Any]] = []
        forecast_rows: list[dict[str, Any]] = []

        # Tomamos muestras cada 3 horas para mantener la BD ágil y evitar autocorrelación excesiva
        for idx, raw_t in enumerate(times):
            if idx % 3 != 0:
                continue
            target_iso = normalize_hour_utc(raw_t)
            try:
                t_dt = datetime.fromisoformat(target_iso.replace("Z", "+00:00"))
            except ValueError:
                continue
            if t_dt > cutoff_utc:
                continue

            # 1. Guardar observación real (Archivo ERA5 prioritario, o análisis día-0 best_match)
            if target_iso in arch_obs_by_time:
                ao = arch_obs_by_time[target_iso]
                obs_rows.append(
                    {
                        "location_id": location.id,
                        "observed_at": target_iso,
                        "source_provider": "open_meteo_archive",
                        **ao,
                    }
                )
            else:
                bm_t = safe_float(_get(hourly.get("temperature_2m_best_match"), idx))
                if bm_t is not None:
                    obs_rows.append(
                        {
                            "location_id": location.id,
                            "observed_at": target_iso,
                            "source_provider": "open_meteo_analysis",
                            "temp_c": bm_t,
                            "humidity_pct": safe_float(_get(hourly.get("relative_humidity_2m_best_match"), idx), 0),
                            "pressure_hpa": safe_float(_get(hourly.get("pressure_msl_best_match"), idx)),
                            "wind_kmh": safe_float(_get(hourly.get("wind_speed_10m_best_match"), idx)),
                            "precip_mm": safe_float(_get(hourly.get("precipitation_best_match"), idx), 2) or 0.0,
                        }
                    )

            # 2. Guardar corridas reales de pronóstico (+24h y +48h) de cada modelo
            for provider_id, model_suffix in PREVIOUS_RUN_MODEL_MAP.items():
                for day_offset, lead_h in ((1, 24), (2, 48)):
                    t_key = f"temperature_2m_previous_day{day_offset}_{model_suffix}"
                    p_key = f"precipitation_previous_day{day_offset}_{model_suffix}"
                    w_key = f"wind_speed_10m_previous_day{day_offset}_{model_suffix}"
                    h_key = f"relative_humidity_2m_previous_day1_{model_suffix}" if day_offset == 1 else ""
                    pr_key = f"pressure_msl_previous_day1_{model_suffix}" if day_offset == 1 else ""

                    pred_t = safe_float(_get(hourly.get(t_key), idx))
                    if pred_t is None:
                        continue
                    issued_iso = (t_dt - timedelta(hours=lead_h)).strftime("%Y-%m-%dT%H:00:00Z")
                    forecast_rows.append(
                        {
                            "location_id": location.id,
                            "provider_id": provider_id,
                            "source_type": "historical_run",
                            "issued_at": issued_iso,
                            "target_time": target_iso,
                            "lead_hours": lead_h,
                            "temp_c": pred_t,
                            "wind_kmh": safe_float(_get(hourly.get(w_key), idx)),
                            "precip_mm": safe_float(_get(hourly.get(p_key), idx), 2),
                            "humidity_pct": safe_float(_get(hourly.get(h_key), idx), 0) if h_key else None,
                            "pressure_hpa": safe_float(_get(hourly.get(pr_key), idx)) if pr_key else None,
                        }
                    )

        obs_saved = self.repo.save_actual_observations(obs_rows)
        fc_saved = self.repo.save_forecast_records(forecast_rows)
        verified_new = self.evaluate_pending(location_id=location.id)
        self.repo.set_app_setting("last_backtest_at", utc_now_iso())
        return {
            "observations_saved": obs_saved,
            "forecasts_saved": fc_saved,
            "newly_verified": verified_new,
        }

    def get_dashboard(
        self,
        location: Optional[LocationModel] = None,
        source_type: Optional[str] = None,
    ) -> AccuracyDashboardResponse:
        loc_id = location.id if location else None
        # Siempre evaluar pares maduros pendientes antes de devolver métricas
        self.evaluate_pending(location_id=loc_id)
        agg = self.repo.get_accuracy_aggregates(location_id=loc_id, source_type=source_type)
        stats_map = agg["stats_by_provider"]
        providers = self.repo.list_providers()

        ranking: list[AccuracyProviderStat] = []
        for p in providers:
            pid = p["provider_id"]
            st = stats_map.get(pid)
            if st and st["sample_count"] > 0:
                ranking.append(
                    AccuracyProviderStat(
                        provider_id=pid,
                        display_name=p["display_name"],
                        sample_count=int(st["sample_count"]),
                        live_sample_count=int(st["live_sample_count"] or 0),
                        historical_sample_count=int(st["historical_sample_count"] or 0),
                        has_data=True,
                        composite_accuracy_pct=round(float(st["avg_composite"]), 1),
                        temp_mae_c=round(float(st["temp_mae"]), 2) if st["temp_mae"] is not None else None,
                        temp_rmse_c=round(float(st["temp_rmse"]), 2) if st["temp_rmse"] is not None else None,
                        temp_bias_c=round(float(st["temp_bias"]), 2) if st["temp_bias"] is not None else None,
                        wind_mae_kmh=round(float(st["wind_mae"]), 1) if st["wind_mae"] is not None else None,
                        precip_mae_mm=round(float(st["precip_mae"]), 2) if st["precip_mae"] is not None else None,
                        rain_hit_rate_pct=(
                            round(float(st["rain_hit_rate"]), 1) if st["rain_hit_rate"] is not None else None
                        ),
                        humidity_mae_pct=(
                            round(float(st["humidity_mae"]), 1) if st["humidity_mae"] is not None else None
                        ),
                        pressure_mae_hpa=(
                            round(float(st["pressure_mae"]), 1) if st["pressure_mae"] is not None else None
                        ),
                        status_note=f"Verificado sobre {int(st['sample_count'])} registros reales",
                    )
                )
            else:
                if p.get("requires_api_key") and not p.get("has_api_key"):
                    note = "Requiere configurar API Key para registrar pronósticos"
                else:
                    note = "En recopilación en vivo (N=0)"
                ranking.append(
                    AccuracyProviderStat(
                        provider_id=pid,
                        display_name=p["display_name"],
                        sample_count=0,
                        has_data=False,
                        status_note=note,
                    )
                )

        # Ordenar primero quienes tienen datos verificados (mayor precisión arriba)
        ranking.sort(
            key=lambda r: (
                0 if r.has_data else 1,
                -(r.composite_accuracy_pct or 0.0),
                -r.sample_count,
            )
        )

        audit_items: list[AccuracyAuditItem] = []
        for row in agg["audit_rows"]:
            pname = PROVIDER_META_MAP.get(row["provider_id"], {}).get("display_name", row["provider_id"])
            audit_items.append(
                AccuracyAuditItem(
                    id=row["id"],
                    provider_id=row["provider_id"],
                    provider_name=pname,
                    location_name=row["location_name"],
                    source_type=row["source_type"],
                    target_time=row["target_time"],
                    lead_hours=row["lead_hours"],
                    pred_temp_c=row["pred_temp_c"],
                    actual_temp_c=row["actual_temp_c"],
                    temp_error_c=row["temp_error_c"],
                    temp_abs_error_c=row["temp_abs_error_c"],
                    pred_wind_kmh=row["pred_wind_kmh"],
                    actual_wind_kmh=row["actual_wind_kmh"],
                    pred_precip_mm=row["pred_precip_mm"],
                    actual_precip_mm=row["actual_precip_mm"],
                    rain_hit=row["rain_hit"],
                    composite_score_pct=round(float(row["composite_score_pct"]), 1),
                    verified_at=row["verified_at"],
                )
            )

        app_cfg = self.repo.get_app_settings()
        return AccuracyDashboardResponse(
            location=location,
            total_forecast_records=agg["total_forecasts"],
            total_observations=agg["total_observations"],
            total_verified_pairs=agg["total_verified"],
            live_verified_pairs=agg["live_verified"],
            historical_verified_pairs=agg["historical_verified"],
            methodology_note=METHODOLOGY_NOTE,
            ranking=ranking,
            recent_audit=audit_items,
            last_backtest_at=app_cfg.get("last_backtest_at"),
        )


def _get(arr: Any, idx: int) -> Any:
    if not isinstance(arr, list) or idx >= len(arr):
        return None
    return arr[idx]
