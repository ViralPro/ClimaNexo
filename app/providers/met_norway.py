"""Conector real de MET Norway (Instituto Meteorológico de Noruega — Locationforecast 2.0)."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

import httpx

from app.config import settings
from app.db.database import utc_now_iso
from app.models.schemas import CurrentWeather, DailyPoint, HourlyPoint, LocationModel, ProviderSnapshot
from app.providers.base import (
    BaseWeatherProvider,
    compute_apparent_temp_c,
    compute_dew_point_c,
    compute_pressure_trend,
    decode_wmo,
    get_ssl_context,
    safe_float,
    wind_deg_to_cardinal,
)

# Mapeo de códigos simbólicos de MET Norway a códigos WMO estándar
MET_SYMBOL_TO_WMO: dict[str, int] = {
    "clearsky": 0,
    "fair": 1,
    "partlycloudy": 2,
    "cloudy": 3,
    "fog": 45,
    "lightrainshowers": 80,
    "rainshowers": 81,
    "heavyrainshowers": 82,
    "lightrain": 61,
    "rain": 63,
    "heavyrain": 65,
    "lightsleet": 66,
    "sleet": 67,
    "heavysleet": 67,
    "lightsnow": 71,
    "snow": 73,
    "heavysnow": 75,
    "lightsnowshowers": 85,
    "snowshowers": 85,
    "heavysnowshowers": 86,
}


def _symbol_to_wmo(symbol_code: str) -> int:
    if not symbol_code:
        return 0
    base = symbol_code.split("_")[0].lower()
    if "thunder" in base:
        return 95
    if base in MET_SYMBOL_TO_WMO:
        return MET_SYMBOL_TO_WMO[base]
    if "snow" in base:
        return 73
    if "rain" in base or "sleet" in base:
        return 61
    if "fog" in base:
        return 45
    if "cloud" in base:
        return 3
    return 1


class MetNorwayProvider(BaseWeatherProvider):
    provider_id = "met_norway"
    display_name = "MET Norway"
    requires_api_key = False
    endpoint_url = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

    async def fetch(
        self,
        location: LocationModel,
        forecast_days: int = 7,
        api_key: str = "",
    ) -> ProviderSnapshot:
        t0 = time.perf_counter()
        params = {
            "lat": round(location.latitude, 4),
            "lon": round(location.longitude, 4),
        }
        try:
            async with httpx.AsyncClient(
                timeout=settings.http_timeout_seconds,
                headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
                verify=get_ssl_context(),
            ) as client:
                resp = await client.get(self.endpoint_url, params=params)
                resp.raise_for_status()
                data = resp.json()
            latency = round((time.perf_counter() - t0) * 1000, 1)
            return self._parse_response(data, latency, forecast_days)
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

    def _parse_response(self, data: dict[str, Any], latency: float, forecast_days: int) -> ProviderSnapshot:
        series = data.get("properties", {}).get("timeseries") or []
        if not series:
            return ProviderSnapshot(
                provider_id=self.provider_id,
                display_name=self.display_name,
                status="error",
                latency_ms=latency,
                fetched_at=utc_now_iso(),
                error_message="MET Norway devolvió una serie vacía",
            )

        hourly_list: list[HourlyPoint] = []
        by_date: dict[str, list[HourlyPoint]] = defaultdict(list)

        for entry in series:
            ts = str(entry.get("time") or "")
            d_obj = entry.get("data") or {}
            inst = (d_obj.get("instant") or {}).get("details") or {}
            n1 = d_obj.get("next_1_hours") or {}
            n6 = d_obj.get("next_6_hours") or {}
            n12 = d_obj.get("next_12_hours") or {}

            temp_c = safe_float(inst.get("air_temperature"))
            humidity = safe_float(inst.get("relative_humidity"), 0)
            pressure = safe_float(inst.get("air_pressure_at_sea_level"))
            cloud = safe_float(inst.get("cloud_area_fraction"), 0)
            # MET Norway entrega viento en m/s -> convertir a km/h
            wind_ms = inst.get("wind_speed")
            wind_kmh = round(float(wind_ms) * 3.6, 1) if wind_ms is not None else None
            wind_dir = safe_float(inst.get("wind_from_direction"), 0)

            precip = (n1.get("details") or {}).get("precipitation_amount")
            if precip is None:
                p6 = (n6.get("details") or {}).get("precipitation_amount")
                precip = round(float(p6) / 6.0, 2) if p6 is not None else 0.0
            else:
                precip = round(float(precip), 2)

            symbol = (
                (n1.get("summary") or {}).get("symbol_code")
                or (n6.get("summary") or {}).get("symbol_code")
                or (n12.get("summary") or {}).get("symbol_code")
                or ""
            )
            wcode = _symbol_to_wmo(symbol)
            ctext, cicon, _ = decode_wmo(wcode, temp_c)
            feels = compute_apparent_temp_c(temp_c, humidity, wind_kmh)

            hp = HourlyPoint(
                time=ts,
                temperature_c=temp_c,
                feels_like_c=feels,
                humidity_pct=humidity,
                pressure_hpa=pressure,
                wind_speed_kmh=wind_kmh,
                wind_direction_deg=wind_dir,
                precipitation_mm=precip,
                cloud_cover_pct=cloud,
                weather_code=wcode,
                condition_text=ctext,
                condition_icon=cicon,
                is_day="night" not in symbol.lower(),
            )
            hourly_list.append(hp)
            if len(ts) >= 10:
                by_date[ts[:10]].append(hp)

        first = hourly_list[0]
        _, _, theme = decode_wmo(first.weather_code, first.temperature_c)
        current = CurrentWeather(
            observed_at=first.time,
            temperature_c=first.temperature_c,
            feels_like_c=first.feels_like_c,
            humidity_pct=first.humidity_pct,
            dew_point_c=compute_dew_point_c(first.temperature_c, first.humidity_pct),
            pressure_hpa=first.pressure_hpa,
            pressure_trend=compute_pressure_trend([h.pressure_hpa for h in hourly_list[:6]]),
            wind_speed_kmh=first.wind_speed_kmh,
            wind_direction_deg=first.wind_direction_deg,
            wind_direction_cardinal=wind_deg_to_cardinal(first.wind_direction_deg),
            precipitation_mm=first.precipitation_mm,
            rain_mm=first.precipitation_mm,
            cloud_cover_pct=first.cloud_cover_pct,
            is_day=first.is_day,
            weather_code=first.weather_code,
            condition_text=first.condition_text,
            condition_icon=first.condition_icon,
            theme=theme,
        )

        daily_list: list[DailyPoint] = []
        for dstr in sorted(by_date.keys())[: max(1, min(10, forecast_days))]:
            pts = by_date[dstr]
            temps = [p.temperature_c for p in pts if p.temperature_c is not None]
            winds = [p.wind_speed_kmh for p in pts if p.wind_speed_kmh is not None]
            precips = [p.precipitation_mm or 0.0 for p in pts]
            tmax = max(temps) if temps else None
            tmin = min(temps) if temps else None
            tmean = round(sum(temps) / len(temps), 1) if temps else None
            mid_code = pts[len(pts) // 2].weather_code
            dtext, dicon, _ = decode_wmo(mid_code, tmax)
            daily_list.append(
                DailyPoint(
                    date=dstr,
                    temp_max_c=tmax,
                    temp_min_c=tmin,
                    temp_mean_c=tmean,
                    precipitation_sum_mm=round(sum(precips), 2),
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
            hourly=hourly_list[:72],
            daily=daily_list,
        )
