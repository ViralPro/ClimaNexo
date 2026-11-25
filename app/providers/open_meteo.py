"""Conector Open-Meteo (Best Match) y conectores de modelos numéricos ECMWF IFS, NOAA GFS y DWD ICON."""

from __future__ import annotations

import time
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


class OpenMeteoProvider(BaseWeatherProvider):
    provider_id = "open_meteo"
    display_name = "Open-Meteo (Best Match)"
    requires_api_key = False
    endpoint_url = "https://api.open-meteo.com/v1/forecast"
    model_param: str | None = None
    extended_variables = True

    async def fetch(
        self,
        location: LocationModel,
        forecast_days: int = 7,
        api_key: str = "",
    ) -> ProviderSnapshot:
        t0 = time.perf_counter()
        days = max(1, min(16 if self.extended_variables else 10, int(forecast_days)))

        if self.extended_variables:
            current_vars = (
                "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,"
                "precipitation,rain,snowfall,weather_code,cloud_cover,pressure_msl,"
                "wind_speed_10m,wind_direction_10m,wind_gusts_10m"
            )
            hourly_vars = (
                "temperature_2m,relative_humidity_2m,dew_point_2m,apparent_temperature,"
                "precipitation_probability,precipitation,weather_code,pressure_msl,"
                "cloud_cover,visibility,wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
                "uv_index,is_day"
            )
            daily_vars = (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "apparent_temperature_max,apparent_temperature_min,sunrise,sunset,"
                "uv_index_max,precipitation_sum,precipitation_probability_max,"
                "wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant"
            )
        else:
            current_vars = (
                "temperature_2m,relative_humidity_2m,precipitation,"
                "weather_code,cloud_cover,pressure_msl,wind_speed_10m,wind_direction_10m"
            )
            hourly_vars = (
                "temperature_2m,relative_humidity_2m,precipitation,weather_code,"
                "pressure_msl,cloud_cover,wind_speed_10m,wind_direction_10m"
            )
            daily_vars = (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_sum,wind_speed_10m_max"
            )

        params = {
            "latitude": round(location.latitude, 4),
            "longitude": round(location.longitude, 4),
            "current": current_vars,
            "hourly": hourly_vars,
            "daily": daily_vars,
            "timezone": "auto",
            "forecast_days": days,
        }
        if self.model_param:
            params["models"] = self.model_param

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
        cur_raw = data.get("current") or {}
        hourly_raw = data.get("hourly") or {}
        daily_raw = data.get("daily") or {}

        hourly_times: list[str] = hourly_raw.get("time") or []
        hourly_list: list[HourlyPoint] = []
        for idx, ts in enumerate(hourly_times):
            t_c = safe_float(_idx(hourly_raw.get("temperature_2m"), idx))
            rh = safe_float(_idx(hourly_raw.get("relative_humidity_2m"), idx), 0)
            wind = safe_float(_idx(hourly_raw.get("wind_speed_10m"), idx))
            feels = safe_float(_idx(hourly_raw.get("apparent_temperature"), idx))
            if feels is None:
                feels = compute_apparent_temp_c(t_c, rh, wind)
            wcode = _idx_int(hourly_raw.get("weather_code"), idx, 0)
            ctext, cicon, _ = decode_wmo(wcode, t_c)
            is_day_val = _idx(hourly_raw.get("is_day"), idx)
            hourly_list.append(
                HourlyPoint(
                    time=ts,
                    temperature_c=t_c,
                    feels_like_c=feels,
                    humidity_pct=rh,
                    pressure_hpa=safe_float(_idx(hourly_raw.get("pressure_msl"), idx)),
                    wind_speed_kmh=wind,
                    wind_gusts_kmh=safe_float(_idx(hourly_raw.get("wind_gusts_10m"), idx)),
                    wind_direction_deg=safe_float(_idx(hourly_raw.get("wind_direction_10m"), idx), 0),
                    precipitation_mm=safe_float(_idx(hourly_raw.get("precipitation"), idx), 2) or 0.0,
                    precip_probability_pct=safe_float(_idx(hourly_raw.get("precipitation_probability"), idx), 0),
                    cloud_cover_pct=safe_float(_idx(hourly_raw.get("cloud_cover"), idx), 0),
                    uv_index=safe_float(_idx(hourly_raw.get("uv_index"), idx)),
                    weather_code=wcode,
                    condition_text=ctext,
                    condition_icon=cicon,
                    is_day=bool(is_day_val) if is_day_val is not None else True,
                )
            )

        # Encontrar índice horario actual para enriquecer visibilidad/UV/probabilidad de lluvia
        cur_time = str(cur_raw.get("time") or utc_now_iso())
        cur_hour_prefix = cur_time[:13]
        matching_idx = 0
        for idx, ts in enumerate(hourly_times):
            if ts.startswith(cur_hour_prefix):
                matching_idx = idx
                break

        fallback_hp = hourly_list[matching_idx] if matching_idx < len(hourly_list) else None
        temp_c = safe_float(cur_raw.get("temperature_2m"))
        if temp_c is None and fallback_hp:
            temp_c = fallback_hp.temperature_c
        humidity = safe_float(cur_raw.get("relative_humidity_2m"), 0)
        if humidity is None and fallback_hp:
            humidity = fallback_hp.humidity_pct
        wind_kmh = safe_float(cur_raw.get("wind_speed_10m"))
        if wind_kmh is None and fallback_hp:
            wind_kmh = fallback_hp.wind_speed_kmh
        feels_c = safe_float(cur_raw.get("apparent_temperature"))
        if feels_c is None:
            feels_c = compute_apparent_temp_c(temp_c, humidity, wind_kmh)

        dew_c = safe_float(_idx(hourly_raw.get("dew_point_2m"), matching_idx))
        if dew_c is None:
            dew_c = compute_dew_point_c(temp_c, humidity)

        vis_m = _idx(hourly_raw.get("visibility"), matching_idx)
        vis_km = round(float(vis_m) / 1000.0, 1) if vis_m is not None else None
        uv_now = safe_float(_idx(hourly_raw.get("uv_index"), matching_idx))
        prob_now = safe_float(_idx(hourly_raw.get("precipitation_probability"), matching_idx), 0)

        pressures_window = [h.pressure_hpa for h in hourly_list[matching_idx : matching_idx + 6]]
        wcode_raw = cur_raw.get("weather_code")
        wcode_now = int(wcode_raw) if wcode_raw is not None else (fallback_hp.weather_code if fallback_hp else 0)
        cond_text, cond_icon, theme = decode_wmo(wcode_now, temp_c)
        wind_deg = safe_float(cur_raw.get("wind_direction_10m"), 0)
        if wind_deg is None and fallback_hp:
            wind_deg = fallback_hp.wind_direction_deg
        pressure_now = safe_float(cur_raw.get("pressure_msl"))
        if pressure_now is None and fallback_hp:
            pressure_now = fallback_hp.pressure_hpa

        current = CurrentWeather(
            observed_at=cur_time,
            temperature_c=temp_c,
            feels_like_c=feels_c,
            humidity_pct=humidity,
            dew_point_c=dew_c,
            pressure_hpa=pressure_now,
            pressure_trend=compute_pressure_trend(pressures_window),
            wind_speed_kmh=wind_kmh,
            wind_gusts_kmh=safe_float(cur_raw.get("wind_gusts_10m")),
            wind_direction_deg=wind_deg,
            wind_direction_cardinal=wind_deg_to_cardinal(wind_deg),
            precipitation_mm=safe_float(cur_raw.get("precipitation"), 2) or 0.0,
            rain_mm=safe_float(cur_raw.get("rain"), 2) or 0.0,
            snowfall_cm=safe_float(cur_raw.get("snowfall"), 2) or 0.0,
            precip_probability_pct=prob_now,
            cloud_cover_pct=safe_float(cur_raw.get("cloud_cover"), 0),
            visibility_km=vis_km,
            uv_index=uv_now,
            is_day=bool(cur_raw.get("is_day", 1)),
            weather_code=wcode_now,
            condition_text=cond_text,
            condition_icon=cond_icon,
            theme=theme,
        )

        daily_dates: list[str] = daily_raw.get("time") or []
        daily_list: list[DailyPoint] = []
        for idx, dstr in enumerate(daily_dates):
            tmax = safe_float(_idx(daily_raw.get("temperature_2m_max"), idx))
            tmin = safe_float(_idx(daily_raw.get("temperature_2m_min"), idx))
            tmean = round((tmax + tmin) / 2.0, 1) if tmax is not None and tmin is not None else tmax
            dwcode = _idx_int(daily_raw.get("weather_code"), idx, 0)
            dtext, dicon, _ = decode_wmo(dwcode, tmax)
            daily_list.append(
                DailyPoint(
                    date=dstr,
                    temp_max_c=tmax,
                    temp_min_c=tmin,
                    temp_mean_c=tmean,
                    feels_like_max_c=safe_float(_idx(daily_raw.get("apparent_temperature_max"), idx)),
                    feels_like_min_c=safe_float(_idx(daily_raw.get("apparent_temperature_min"), idx)),
                    precipitation_sum_mm=safe_float(_idx(daily_raw.get("precipitation_sum"), idx), 2) or 0.0,
                    precip_probability_max_pct=safe_float(_idx(daily_raw.get("precipitation_probability_max"), idx), 0),
                    wind_speed_max_kmh=safe_float(_idx(daily_raw.get("wind_speed_10m_max"), idx)),
                    wind_gusts_max_kmh=safe_float(_idx(daily_raw.get("wind_gusts_10m_max"), idx)),
                    wind_direction_dominant_deg=safe_float(_idx(daily_raw.get("wind_direction_10m_dominant"), idx), 0),
                    uv_index_max=safe_float(_idx(daily_raw.get("uv_index_max"), idx)),
                    sunrise=_idx(daily_raw.get("sunrise"), idx),
                    sunset=_idx(daily_raw.get("sunset"), idx),
                    weather_code=dwcode,
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


class EcmwfIfsProvider(OpenMeteoProvider):
    provider_id = "ecmwf_ifs"
    display_name = "ECMWF IFS (Europa)"
    endpoint_url = "https://api.open-meteo.com/v1/forecast"
    model_param = "ecmwf_ifs025"
    extended_variables = False


class NoaaGfsProvider(OpenMeteoProvider):
    provider_id = "noaa_gfs"
    display_name = "NOAA GFS (EE. UU.)"
    endpoint_url = "https://api.open-meteo.com/v1/gfs"
    extended_variables = False


class DwdIconProvider(OpenMeteoProvider):
    provider_id = "dwd_icon"
    display_name = "DWD ICON (Alemania)"
    endpoint_url = "https://api.open-meteo.com/v1/dwd-icon"
    extended_variables = False


def _idx(arr: Any, idx: int) -> Any:
    if not isinstance(arr, list) or idx >= len(arr):
        return None
    return arr[idx]


def _idx_int(arr: Any, idx: int, default: int = 0) -> int:
    val = _idx(arr, idx)
    if val is None:
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default
