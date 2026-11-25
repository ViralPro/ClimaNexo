"""Conector real independiente wttr.in (formato JSON j1)."""

from __future__ import annotations

import time
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

# Códigos WWO (wttr.in) -> códigos WMO estándar
WWO_TO_WMO: dict[int, int] = {
    113: 0,
    116: 2,
    119: 3,
    122: 3,
    143: 45,
    176: 80,
    179: 85,
    182: 66,
    185: 56,
    200: 95,
    227: 73,
    230: 75,
    248: 45,
    260: 48,
    263: 51,
    266: 51,
    281: 56,
    284: 57,
    293: 61,
    296: 61,
    299: 63,
    302: 63,
    305: 65,
    308: 65,
    311: 66,
    314: 67,
    317: 66,
    320: 67,
    323: 71,
    326: 71,
    329: 73,
    332: 73,
    335: 75,
    338: 75,
    350: 77,
    353: 80,
    356: 81,
    359: 82,
    362: 85,
    365: 86,
    368: 85,
    371: 86,
    374: 77,
    377: 77,
    386: 95,
    389: 95,
    392: 96,
    395: 99,
}


class WttrInProvider(BaseWeatherProvider):
    provider_id = "wttr_in"
    display_name = "wttr.in (WorldWeather)"
    requires_api_key = False

    async def fetch(
        self,
        location: LocationModel,
        forecast_days: int = 7,
        api_key: str = "",
    ) -> ProviderSnapshot:
        t0 = time.perf_counter()
        url = f"https://wttr.in/{location.latitude:.4f},{location.longitude:.4f}"
        try:
            async with httpx.AsyncClient(
                timeout=settings.http_timeout_seconds,
                headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
                follow_redirects=True,
                verify=get_ssl_context(),
            ) as client:
                resp = await client.get(url, params={"format": "j1"})
                resp.raise_for_status()
                data = resp.json()
            latency = round((time.perf_counter() - t0) * 1000, 1)
            return self._parse_response(data, latency)
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

    def _parse_response(self, data: dict[str, Any], latency: float) -> ProviderSnapshot:
        cur_arr = data.get("current_condition") or []
        if not cur_arr:
            return ProviderSnapshot(
                provider_id=self.provider_id,
                display_name=self.display_name,
                status="error",
                latency_ms=latency,
                fetched_at=utc_now_iso(),
                error_message="wttr.in sin condiciones actuales",
            )
        c = cur_arr[0]
        temp_c = safe_float(c.get("temp_C"))
        feels_c = safe_float(c.get("FeelsLikeC"))
        humidity = safe_float(c.get("humidity"), 0)
        pressure = safe_float(c.get("pressure"))
        wind_kmh = safe_float(c.get("windspeedKmph"))
        wind_deg = safe_float(c.get("winddirDegree"), 0)
        precip = safe_float(c.get("precipMM"), 2) or 0.0
        wwo_code = int(c.get("weatherCode") or 113)
        wmo = WWO_TO_WMO.get(wwo_code, 2)
        ctext, cicon, theme = decode_wmo(wmo, temp_c)

        current = CurrentWeather(
            observed_at=utc_now_iso(),
            temperature_c=temp_c,
            feels_like_c=feels_c,
            humidity_pct=humidity,
            dew_point_c=compute_dew_point_c(temp_c, humidity),
            pressure_hpa=pressure,
            wind_speed_kmh=wind_kmh,
            wind_direction_deg=wind_deg,
            wind_direction_cardinal=wind_deg_to_cardinal(wind_deg),
            precipitation_mm=precip,
            rain_mm=precip,
            cloud_cover_pct=safe_float(c.get("cloudcover"), 0),
            visibility_km=safe_float(c.get("visibility")),
            uv_index=safe_float(c.get("uvIndex")),
            weather_code=wmo,
            condition_text=ctext,
            condition_icon=cicon,
            theme=theme,
        )

        hourly_list: list[HourlyPoint] = []
        daily_list: list[DailyPoint] = []
        for day in data.get("weather") or []:
            dstr = str(day.get("date") or "")
            tmax = safe_float(day.get("maxtempC"))
            tmin = safe_float(day.get("mintempC"))
            tavg = safe_float(day.get("avgtempC"))
            day_precip = 0.0
            day_max_wind = 0.0
            day_max_prob = 0.0
            day_wmo = 0
            for h in day.get("hourly") or []:
                hh = int(h.get("time") or 0) // 100
                ts = f"{dstr}T{hh:02d}:00"
                ht = safe_float(h.get("tempC"))
                hw = safe_float(h.get("windspeedKmph")) or 0.0
                hp = safe_float(h.get("precipMM"), 2) or 0.0
                hprob = safe_float(h.get("chanceofrain"), 0) or 0.0
                hwmo = WWO_TO_WMO.get(int(h.get("weatherCode") or 113), 2)
                if hh == 12:
                    day_wmo = hwmo
                day_precip += hp
                day_max_wind = max(day_max_wind, hw)
                day_max_prob = max(day_max_prob, hprob)
                htext, hicon, _ = decode_wmo(hwmo, ht)
                hourly_list.append(
                    HourlyPoint(
                        time=ts,
                        temperature_c=ht,
                        feels_like_c=safe_float(h.get("FeelsLikeC")),
                        humidity_pct=safe_float(h.get("humidity"), 0),
                        pressure_hpa=safe_float(h.get("pressure")),
                        wind_speed_kmh=hw,
                        wind_gusts_kmh=safe_float(h.get("WindGustKmph")),
                        wind_direction_deg=safe_float(h.get("winddirDegree"), 0),
                        precipitation_mm=hp,
                        precip_probability_pct=hprob,
                        cloud_cover_pct=safe_float(h.get("cloudcover"), 0),
                        uv_index=safe_float(h.get("uvIndex")),
                        weather_code=hwmo,
                        condition_text=htext,
                        condition_icon=hicon,
                    )
                )
            dtext, dicon, _ = decode_wmo(day_wmo, tmax)
            astro = (day.get("astronomy") or [{}])[0]
            daily_list.append(
                DailyPoint(
                    date=dstr,
                    temp_max_c=tmax,
                    temp_min_c=tmin,
                    temp_mean_c=tavg,
                    precipitation_sum_mm=round(day_precip, 2),
                    precip_probability_max_pct=round(day_max_prob, 0),
                    wind_speed_max_kmh=round(day_max_wind, 1) if day_max_wind else None,
                    uv_index_max=safe_float(day.get("uvIndex")),
                    sunrise=astro.get("sunrise"),
                    sunset=astro.get("sunset"),
                    weather_code=day_wmo,
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
