"""Conector real de WeatherAPI.com (activo cuando se configura WEATHERAPI_KEY)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import settings
from app.db.database import utc_now_iso
from app.models.schemas import (
    CurrentWeather,
    DailyPoint,
    HourlyPoint,
    LocationModel,
    ProviderSnapshot,
    WeatherAlertModel,
)
from app.providers.base import (
    BaseWeatherProvider,
    compute_dew_point_c,
    decode_wmo,
    get_ssl_context,
    safe_float,
    wind_deg_to_cardinal,
)


def _weatherapi_code_to_wmo(code: int) -> int:
    if code == 1000:
        return 0
    if code == 1003:
        return 2
    if code in (1006, 1009):
        return 3
    if code in (1030, 1135, 1147):
        return 45
    if code in (1063, 1150, 1153, 1180, 1183, 1240):
        return 61
    if code in (1186, 1189, 1243):
        return 63
    if code in (1192, 1195, 1246):
        return 65
    if code in (1066, 1210, 1213, 1216, 1219, 1222, 1225, 1255, 1258):
        return 73
    if code in (1087, 1273, 1276, 1279, 1282):
        return 95
    return 2


class WeatherApiProvider(BaseWeatherProvider):
    provider_id = "weatherapi"
    display_name = "WeatherAPI.com"
    requires_api_key = True
    endpoint_url = "https://api.weatherapi.com/v1/forecast.json"

    async def fetch(
        self,
        location: LocationModel,
        forecast_days: int = 7,
        api_key: str = "",
    ) -> ProviderSnapshot:
        key = (api_key or settings.weatherapi_key).strip()
        if not key:
            return ProviderSnapshot(
                provider_id=self.provider_id,
                display_name=self.display_name,
                status="needs_key",
                fetched_at=utc_now_iso(),
                error_message="Requiere clave API (WEATHERAPI_KEY) en Ajustes o archivo .env",
            )

        t0 = time.perf_counter()
        params = {
            "key": key,
            "q": f"{location.latitude:.4f},{location.longitude:.4f}",
            "days": max(1, min(10, forecast_days)),
            "aqi": "no",
            "alerts": "yes",
            "lang": "es",
        }
        try:
            async with httpx.AsyncClient(
                timeout=settings.http_timeout_seconds,
                headers={"User-Agent": settings.user_agent},
                verify=get_ssl_context(),
            ) as client:
                resp = await client.get(self.endpoint_url, params=params)
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
        c = data.get("current") or {}
        cond = c.get("condition") or {}
        temp_c = safe_float(c.get("temp_c"))
        humidity = safe_float(c.get("humidity"), 0)
        wmo = _weatherapi_code_to_wmo(int(cond.get("code") or 1000))
        ctext, cicon, theme = decode_wmo(wmo, temp_c)
        if cond.get("text"):
            ctext = str(cond["text"])
        wind_deg = safe_float(c.get("wind_degree"), 0)

        current = CurrentWeather(
            observed_at=str(c.get("last_updated") or utc_now_iso()),
            temperature_c=temp_c,
            feels_like_c=safe_float(c.get("feelslike_c")),
            humidity_pct=humidity,
            dew_point_c=safe_float(c.get("dewpoint_c")) or compute_dew_point_c(temp_c, humidity),
            pressure_hpa=safe_float(c.get("pressure_mb")),
            wind_speed_kmh=safe_float(c.get("wind_kph")),
            wind_gusts_kmh=safe_float(c.get("gust_kph")),
            wind_direction_deg=wind_deg,
            wind_direction_cardinal=wind_deg_to_cardinal(wind_deg),
            precipitation_mm=safe_float(c.get("precip_mm"), 2) or 0.0,
            rain_mm=safe_float(c.get("precip_mm"), 2) or 0.0,
            cloud_cover_pct=safe_float(c.get("cloud"), 0),
            visibility_km=safe_float(c.get("vis_km")),
            uv_index=safe_float(c.get("uv")),
            is_day=bool(c.get("is_day", 1)),
            weather_code=wmo,
            condition_text=ctext,
            condition_icon=cicon,
            theme=theme,
        )

        hourly_list: list[HourlyPoint] = []
        daily_list: list[DailyPoint] = []
        for fd in (data.get("forecast") or {}).get("forecastday") or []:
            dstr = str(fd.get("date") or "")
            day = fd.get("day") or {}
            astro = fd.get("astro") or {}
            dcond = day.get("condition") or {}
            dwmo = _weatherapi_code_to_wmo(int(dcond.get("code") or 1000))
            dtext, dicon, _ = decode_wmo(dwmo, safe_float(day.get("maxtemp_c")))
            if dcond.get("text"):
                dtext = str(dcond["text"])
            daily_list.append(
                DailyPoint(
                    date=dstr,
                    temp_max_c=safe_float(day.get("maxtemp_c")),
                    temp_min_c=safe_float(day.get("mintemp_c")),
                    temp_mean_c=safe_float(day.get("avgtemp_c")),
                    precipitation_sum_mm=safe_float(day.get("totalprecip_mm"), 2) or 0.0,
                    precip_probability_max_pct=safe_float(day.get("daily_chance_of_rain"), 0),
                    wind_speed_max_kmh=safe_float(day.get("maxwind_kph")),
                    uv_index_max=safe_float(day.get("uv")),
                    sunrise=astro.get("sunrise"),
                    sunset=astro.get("sunset"),
                    weather_code=dwmo,
                    condition_text=dtext,
                    condition_icon=dicon,
                )
            )
            for h in fd.get("hour") or []:
                hcond = h.get("condition") or {}
                hwmo = _weatherapi_code_to_wmo(int(hcond.get("code") or 1000))
                htext, hicon, _ = decode_wmo(hwmo, safe_float(h.get("temp_c")))
                hourly_list.append(
                    HourlyPoint(
                        time=str(h.get("time") or "").replace(" ", "T"),
                        temperature_c=safe_float(h.get("temp_c")),
                        feels_like_c=safe_float(h.get("feelslike_c")),
                        humidity_pct=safe_float(h.get("humidity"), 0),
                        pressure_hpa=safe_float(h.get("pressure_mb")),
                        wind_speed_kmh=safe_float(h.get("wind_kph")),
                        wind_gusts_kmh=safe_float(h.get("gust_kph")),
                        wind_direction_deg=safe_float(h.get("wind_degree"), 0),
                        precipitation_mm=safe_float(h.get("precip_mm"), 2) or 0.0,
                        precip_probability_pct=safe_float(h.get("chance_of_rain"), 0),
                        cloud_cover_pct=safe_float(h.get("cloud"), 0),
                        uv_index=safe_float(h.get("uv")),
                        weather_code=hwmo,
                        condition_text=str(hcond.get("text") or htext),
                        condition_icon=hicon,
                        is_day=bool(h.get("is_day", 1)),
                    )
                )

        raw_alerts: list[WeatherAlertModel] = []
        for idx, al in enumerate((data.get("alerts") or {}).get("alert") or []):
            sev_raw = str(al.get("severity") or "").lower()
            sev = "extrema" if "extreme" in sev_raw else ("alerta" if "severe" in sev_raw else "precaucion")
            raw_alerts.append(
                WeatherAlertModel(
                    alert_key=f"weatherapi_official_{idx}_{str(al.get('event') or 'alert')[:24]}",
                    category="oficial",
                    severity=sev,
                    title=str(al.get("headline") or al.get("event") or "Alerta meteorológica oficial"),
                    description=str(al.get("desc") or "")[:500],
                    recommendation=str(al.get("instruction") or "Seguí las indicaciones de las autoridades locales.")[:300],
                    metric_value=str(al.get("event") or "Aviso oficial"),
                    source="WeatherAPI (Oficial)",
                    starts_at=str(al.get("effective") or utc_now_iso()),
                    ends_at=str(al.get("expires") or utc_now_iso()),
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
            raw_alerts=raw_alerts,
        )
