"""Conector real de OpenWeatherMap (activo cuando se configura OPENWEATHER_API_KEY)."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.db.database import utc_now_iso
from app.models.schemas import CurrentWeather, DailyPoint, HourlyPoint, LocationModel, ProviderSnapshot
from app.providers.base import (
    BaseWeatherProvider,
    compute_dew_point_c,
    decode_wmo,
    get_ssl_context,
    safe_float,
    wind_deg_to_cardinal,
)


def _owm_id_to_wmo(owm_id: int) -> int:
    if 200 <= owm_id < 300:
        return 95
    if 300 <= owm_id < 400:
        return 51
    if 500 <= owm_id <= 501:
        return 61
    if 502 <= owm_id < 600:
        return 65
    if 600 <= owm_id < 700:
        return 73
    if 700 <= owm_id < 800:
        return 45
    if owm_id == 800:
        return 0
    if owm_id in (801, 802):
        return 2
    return 3


class OpenWeatherProvider(BaseWeatherProvider):
    provider_id = "openweather"
    display_name = "OpenWeatherMap"
    requires_api_key = True
    base_url = "https://api.openweathermap.org/data/2.5"

    async def fetch(
        self,
        location: LocationModel,
        forecast_days: int = 7,
        api_key: str = "",
    ) -> ProviderSnapshot:
        key = (api_key or settings.openweather_api_key).strip()
        if not key:
            return ProviderSnapshot(
                provider_id=self.provider_id,
                display_name=self.display_name,
                status="needs_key",
                fetched_at=utc_now_iso(),
                error_message="Requiere clave API (OPENWEATHER_API_KEY) en Ajustes o archivo .env",
            )

        t0 = time.perf_counter()
        params = {
            "lat": round(location.latitude, 4),
            "lon": round(location.longitude, 4),
            "appid": key,
            "units": "metric",
            "lang": "es",
        }
        try:
            async with httpx.AsyncClient(
                timeout=settings.http_timeout_seconds,
                headers={"User-Agent": settings.user_agent},
                verify=get_ssl_context(),
            ) as client:
                cur_res, fc_res = await asyncio.gather(
                    client.get(f"{self.base_url}/weather", params=params),
                    client.get(f"{self.base_url}/forecast", params=params),
                )
                cur_res.raise_for_status()
                fc_res.raise_for_status()
                cur_data = cur_res.json()
                fc_data = fc_res.json()
            latency = round((time.perf_counter() - t0) * 1000, 1)
            return self._parse_response(cur_data, fc_data, latency)
        except Exception as exc:
            latency = round((time.perf_counter() - t0) * 1000, 1)
            return ProviderSnapshot(
                provider_id=self.provider_id,
                display_name=self.display_name,
                status="error",
                latency_ms=latency,
                fetched_at=utc_now_iso(),
                error_message=f"{type(exc).__name__}: {exc}",
            )

    def _parse_response(
        self,
        cur_data: dict[str, Any],
        fc_data: dict[str, Any],
        latency: float,
    ) -> ProviderSnapshot:
        main = cur_data.get("main") or {}
        wind = cur_data.get("wind") or {}
        weather0 = (cur_data.get("weather") or [{}])[0]
        temp_c = safe_float(main.get("temp"))
        humidity = safe_float(main.get("humidity"), 0)
        wind_ms = wind.get("speed")
        wind_kmh = round(float(wind_ms) * 3.6, 1) if wind_ms is not None else None
        gust_ms = wind.get("gust")
        gust_kmh = round(float(gust_ms) * 3.6, 1) if gust_ms is not None else None
        wind_deg = safe_float(wind.get("deg"), 0)
        precip = safe_float((cur_data.get("rain") or {}).get("1h"), 2) or 0.0
        wmo = _owm_id_to_wmo(int(weather0.get("id") or 800))
        ctext, cicon, theme = decode_wmo(wmo, temp_c)
        if weather0.get("description"):
            ctext = str(weather0["description"]).capitalize()
        vis_m = cur_data.get("visibility")

        current = CurrentWeather(
            observed_at=datetime.fromtimestamp(
                int(cur_data.get("dt") or time.time()), tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            temperature_c=temp_c,
            feels_like_c=safe_float(main.get("feels_like")),
            humidity_pct=humidity,
            dew_point_c=compute_dew_point_c(temp_c, humidity),
            pressure_hpa=safe_float(main.get("pressure")),
            wind_speed_kmh=wind_kmh,
            wind_gusts_kmh=gust_kmh,
            wind_direction_deg=wind_deg,
            wind_direction_cardinal=wind_deg_to_cardinal(wind_deg),
            precipitation_mm=precip,
            rain_mm=precip,
            cloud_cover_pct=safe_float((cur_data.get("clouds") or {}).get("all"), 0),
            visibility_km=round(float(vis_m) / 1000.0, 1) if vis_m is not None else None,
            weather_code=wmo,
            condition_text=ctext,
            condition_icon=cicon,
            theme=theme,
        )

        hourly_list: list[HourlyPoint] = []
        by_date: dict[str, list[HourlyPoint]] = defaultdict(list)
        for item in fc_data.get("list") or []:
            m = item.get("main") or {}
            w = item.get("wind") or {}
            w0 = (item.get("weather") or [{}])[0]
            ts = str(item.get("dt_txt") or "").replace(" ", "T")[:16]
            ht = safe_float(m.get("temp"))
            hw = round(float(w["speed"]) * 3.6, 1) if w.get("speed") is not None else None
            hp = safe_float((item.get("rain") or {}).get("3h"), 2) or 0.0
            hprob = round(float(item.get("pop") or 0.0) * 100.0, 0)
            hwmo = _owm_id_to_wmo(int(w0.get("id") or 800))
            htext, hicon, _ = decode_wmo(hwmo, ht)
            pt = HourlyPoint(
                time=ts,
                temperature_c=ht,
                feels_like_c=safe_float(m.get("feels_like")),
                humidity_pct=safe_float(m.get("humidity"), 0),
                pressure_hpa=safe_float(m.get("pressure")),
                wind_speed_kmh=hw,
                wind_direction_deg=safe_float(w.get("deg"), 0),
                precipitation_mm=hp,
                precip_probability_pct=hprob,
                cloud_cover_pct=safe_float((item.get("clouds") or {}).get("all"), 0),
                weather_code=hwmo,
                condition_text=str(w0.get("description") or htext).capitalize(),
                condition_icon=hicon,
            )
            hourly_list.append(pt)
            if len(ts) >= 10:
                by_date[ts[:10]].append(pt)

        daily_list: list[DailyPoint] = []
        for dstr in sorted(by_date.keys()):
            pts = by_date[dstr]
            temps = [p.temperature_c for p in pts if p.temperature_c is not None]
            winds = [p.wind_speed_kmh for p in pts if p.wind_speed_kmh is not None]
            probs = [p.precip_probability_pct for p in pts if p.precip_probability_pct is not None]
            mid_code = pts[len(pts) // 2].weather_code
            dtext, dicon, _ = decode_wmo(mid_code, max(temps) if temps else None)
            daily_list.append(
                DailyPoint(
                    date=dstr,
                    temp_max_c=max(temps) if temps else None,
                    temp_min_c=min(temps) if temps else None,
                    temp_mean_c=round(sum(temps) / len(temps), 1) if temps else None,
                    precipitation_sum_mm=round(sum(p.precipitation_mm or 0.0 for p in pts), 2),
                    precip_probability_max_pct=max(probs) if probs else None,
                    wind_speed_max_kmh=max(winds) if winds else None,
                    weather_code=mid_code,
                    condition_text=dtext,
                    condition_icon=dicon,
                )
            )

        return ProviderSnapshot(
            provider_id=self.provider_id,
            display_name=self.display_name,
            status="ok",
            latency_ms=latency,
            fetched_at=utc_now_iso(),
            current=current,
            hourly=hourly_list,
            daily=daily_list,
        )
