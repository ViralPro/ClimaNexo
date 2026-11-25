"""Servicio de historial meteorológico real (Open-Meteo Archive API + observaciones SQLite)."""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from typing import Any

import httpx

from app.config import settings
from app.db.repository import WeatherRepository
from app.models.schemas import HistoryDayPoint, HistoryResponse, LocationModel
from app.providers.base import decode_wmo, safe_float
from app.services.cache_service import CacheService
from app.services.narrative_service import build_history_narrative


class HistoryService:
    ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

    def __init__(
        self,
        repo: WeatherRepository | None = None,
        cache: CacheService | None = None,
    ) -> None:
        self.repo = repo or WeatherRepository()
        self.cache = cache or CacheService(self.repo)

    async def get_history(
        self,
        location: LocationModel,
        start_date: str | None = None,
        end_date: str | None = None,
        focus_metric: str = "general",
    ) -> HistoryResponse:
        today = date.today()
        max_archive_day = today - timedelta(days=1)

        if end_date:
            try:
                d_end = min(date.fromisoformat(end_date), max_archive_day)
            except ValueError:
                d_end = max_archive_day
        else:
            d_end = max_archive_day

        if start_date:
            try:
                d_start = date.fromisoformat(start_date)
            except ValueError:
                d_start = d_end - timedelta(days=29)
        else:
            d_start = d_end - timedelta(days=29)

        if d_start > d_end:
            d_start = d_end - timedelta(days=6)
        # Limitar consulta máxima a 366 días para rapidez y estabilidad
        if (d_end - d_start).days > 366:
            d_start = d_end - timedelta(days=366)

        s_iso = d_start.isoformat()
        e_iso = d_end.isoformat()

        cache_key = self.cache.build_key(
            "history",
            lat=round(location.latitude, 3),
            lon=round(location.longitude, 3),
            start=s_iso,
            end=e_iso,
        )
        cached = self.cache.get(cache_key)
        if cached is not None:
            resp = HistoryResponse(**cached)
            if focus_metric != "general":
                resp.narrative = build_history_narrative(location, resp.daily, s_iso, e_iso, focus_metric)
            return resp

        params = {
            "latitude": round(location.latitude, 4),
            "longitude": round(location.longitude, 4),
            "start_date": s_iso,
            "end_date": e_iso,
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,temperature_2m_mean,"
                "precipitation_sum,rain_sum,snowfall_sum,wind_speed_10m_max,wind_gusts_10m_max"
            ),
            "timezone": "auto",
        }

        async with httpx.AsyncClient(
            timeout=15.0,
            headers={"User-Agent": settings.user_agent},
        ) as client:
            r = await client.get(self.ARCHIVE_URL, params=params)
            r.raise_for_status()
            data = r.json()

        daily_raw = data.get("daily") or {}
        dates: list[str] = daily_raw.get("time") or []
        days: list[HistoryDayPoint] = []
        for idx, dstr in enumerate(dates):
            tmax = safe_float(_at(daily_raw.get("temperature_2m_max"), idx))
            tmin = safe_float(_at(daily_raw.get("temperature_2m_min"), idx))
            tmean = safe_float(_at(daily_raw.get("temperature_2m_mean"), idx))
            if tmean is None and tmax is not None and tmin is not None:
                tmean = round((tmax + tmin) / 2.0, 1)
            wcode = int(_at(daily_raw.get("weather_code"), idx) or 0)
            ctext, _, _ = decode_wmo(wcode, tmax)
            days.append(
                HistoryDayPoint(
                    date=dstr,
                    temp_max_c=tmax,
                    temp_min_c=tmin,
                    temp_mean_c=tmean,
                    precipitation_sum_mm=safe_float(_at(daily_raw.get("precipitation_sum"), idx), 2) or 0.0,
                    rain_sum_mm=safe_float(_at(daily_raw.get("rain_sum"), idx), 2) or 0.0,
                    snowfall_sum_cm=safe_float(_at(daily_raw.get("snowfall_sum"), idx), 2) or 0.0,
                    wind_speed_max_kmh=safe_float(_at(daily_raw.get("wind_speed_10m_max"), idx)),
                    wind_gusts_max_kmh=safe_float(_at(daily_raw.get("wind_gusts_10m_max"), idx)),
                    weather_code=wcode,
                    condition_text=ctext,
                )
            )

        summary = self._compute_summary(days)
        narrative = build_history_narrative(location, days, s_iso, e_iso, focus_metric)
        local_obs = self.repo.count_local_observations(location.id) if location.id else 0

        res = HistoryResponse(
            location=location,
            start_date=s_iso,
            end_date=e_iso,
            days_count=len(days),
            source="Open-Meteo Historical Archive (ERA5 / Reanálisis)",
            summary=summary,
            narrative=narrative,
            daily=days,
            local_observations_count=local_obs,
        )
        self.cache.set(cache_key, "history", res.model_dump(), settings.ttl_history)
        return res

    @staticmethod
    def _compute_summary(days: list[HistoryDayPoint]) -> dict[str, Any]:
        if not days:
            return {}
        maxs = [(d.date, d.temp_max_c) for d in days if d.temp_max_c is not None]
        mins = [(d.date, d.temp_min_c) for d in days if d.temp_min_c is not None]
        means = [d.temp_mean_c for d in days if d.temp_mean_c is not None]
        rains = [(d.date, d.precipitation_sum_mm or 0.0) for d in days]
        winds = [(d.date, d.wind_speed_max_kmh or 0.0) for d in days]

        hot_day, hot_val = max(maxs, key=lambda x: x[1]) if maxs else ("—", None)
        cold_day, cold_val = min(mins, key=lambda x: x[1]) if mins else ("—", None)
        wet_day, wet_val = max(rains, key=lambda x: x[1]) if rains else ("—", 0.0)
        wind_day, wind_val = max(winds, key=lambda x: x[1]) if winds else ("—", 0.0)

        rainy_days = sum(1 for _, v in rains if v >= 0.5)
        frost_days = sum(1 for _, v in mins if v <= 0.0)
        hot_days = sum(1 for _, v in maxs if v >= 30.0)

        return {
            "temp_max_c": hot_val,
            "temp_max_date": hot_day,
            "temp_min_c": cold_val,
            "temp_min_date": cold_day,
            "temp_avg_c": round(sum(means) / len(means), 1) if means else None,
            "precip_total_mm": round(sum(v for _, v in rains), 1),
            "precip_avg_mm": round(sum(v for _, v in rains) / len(days), 2),
            "wettest_day": wet_day,
            "wettest_day_mm": round(wet_val, 1),
            "rainy_days": rainy_days,
            "wind_max_kmh": round(wind_val, 1),
            "windiest_day": wind_day,
            "frost_days": frost_days,
            "hot_days": hot_days,
        }

    @staticmethod
    def to_csv(history: HistoryResponse) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "Fecha",
            "Temp Max (°C)",
            "Temp Min (°C)",
            "Temp Media (°C)",
            "Precipitacion (mm)",
            "Viento Max (km/h)",
            "Rafagas Max (km/h)",
            "Condicion",
        ])
        for d in history.daily:
            writer.writerow([
                d.date,
                d.temp_max_c if d.temp_max_c is not None else "",
                d.temp_min_c if d.temp_min_c is not None else "",
                d.temp_mean_c if d.temp_mean_c is not None else "",
                d.precipitation_sum_mm if d.precipitation_sum_mm is not None else 0.0,
                d.wind_speed_max_kmh if d.wind_speed_max_kmh is not None else "",
                d.wind_gusts_max_kmh if d.wind_gusts_max_kmh is not None else "",
                d.condition_text,
            ])
        return buf.getvalue()


def _at(arr: Any, idx: int) -> Any:
    if not isinstance(arr, list) or idx >= len(arr):
        return None
    return arr[idx]
