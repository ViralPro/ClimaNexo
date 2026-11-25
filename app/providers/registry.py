"""Registro y orquestador concurrente de todos los proveedores meteorológicos."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from app.db.database import utc_now_iso
from app.db.repository import WeatherRepository
from app.models.schemas import LocationModel, ProviderSnapshot
from app.providers.base import BaseWeatherProvider, normalize_hour_utc
from app.providers.met_norway import MetNorwayProvider
from app.providers.open_meteo import (
    DwdIconProvider,
    EcmwfIfsProvider,
    NoaaGfsProvider,
    OpenMeteoProvider,
)
from app.providers.openweather import OpenWeatherProvider
from app.providers.weatherapi import WeatherApiProvider
from app.providers.wttr_provider import WttrInProvider


class ProviderRegistry:
    def __init__(self, repo: WeatherRepository | None = None) -> None:
        self.repo = repo or WeatherRepository()
        self._providers: dict[str, BaseWeatherProvider] = {
            "open_meteo": OpenMeteoProvider(),
            "met_norway": MetNorwayProvider(),
            "ecmwf_ifs": EcmwfIfsProvider(),
            "noaa_gfs": NoaaGfsProvider(),
            "dwd_icon": DwdIconProvider(),
            "wttr_in": WttrInProvider(),
            "weatherapi": WeatherApiProvider(),
            "openweather": OpenWeatherProvider(),
        }

    def get_provider(self, provider_id: str) -> Optional[BaseWeatherProvider]:
        return self._providers.get(provider_id)

    async def fetch_single(
        self,
        provider_id: str,
        location: LocationModel,
        forecast_days: int = 7,
    ) -> ProviderSnapshot:
        provider = self._providers.get(provider_id)
        if not provider:
            return ProviderSnapshot(
                provider_id=provider_id,
                display_name=provider_id,
                status="error",
                fetched_at=utc_now_iso(),
                error_message="Proveedor desconocido",
            )

        cfg = self.repo.get_provider_setting(provider_id) or {}
        if cfg and not cfg.get("enabled", 1):
            return ProviderSnapshot(
                provider_id=provider_id,
                display_name=provider.display_name,
                status="disabled",
                fetched_at=utc_now_iso(),
                error_message="Deshabilitado por el usuario",
            )

        api_key = str(cfg.get("api_key") or "")
        snap = await provider.fetch(location=location, forecast_days=forecast_days, api_key=api_key)

        if snap.status == "ok":
            self.repo.record_provider_health(provider_id, ok=True, latency_ms=snap.latency_ms)
        elif snap.status == "needs_key":
            self.repo.record_provider_health(
                provider_id, ok=False, error_message=snap.error_message, status_override="needs_key"
            )
        elif snap.status == "disabled":
            self.repo.record_provider_health(
                provider_id, ok=False, error_message=snap.error_message, status_override="disabled"
            )
        else:
            self.repo.record_provider_health(
                provider_id, ok=False, latency_ms=snap.latency_ms, error_message=snap.error_message
            )

        return snap

    async def fetch_all(
        self,
        location: LocationModel,
        forecast_days: int = 7,
        record_for_verification: bool = True,
    ) -> list[ProviderSnapshot]:
        provider_cfgs = self.repo.list_providers()
        ordered_ids = [p["provider_id"] for p in provider_cfgs if p["provider_id"] in self._providers]
        tasks = [self.fetch_single(pid, location, forecast_days=forecast_days) for pid in ordered_ids]
        snapshots: list[ProviderSnapshot] = list(await asyncio.gather(*tasks))

        if record_for_verification and location.id is not None:
            self._persist_live_records(location, snapshots)

        return snapshots

    async def fetch_primary_with_fallback(
        self,
        location: LocationModel,
        forecast_days: int = 7,
    ) -> ProviderSnapshot:
        """Obtiene el pronóstico principal (Open-Meteo) con respaldo automático en MET Norway / NOAA / ECMWF."""
        fallback_order = ["open_meteo", "met_norway", "ecmwf_ifs", "noaa_gfs", "dwd_icon", "wttr_in"]
        last_snap: Optional[ProviderSnapshot] = None
        for pid in fallback_order:
            snap = await self.fetch_single(pid, location, forecast_days=forecast_days)
            if snap.status == "ok" and snap.current is not None:
                return snap
            last_snap = snap
        assert last_snap is not None
        return last_snap

    def _persist_live_records(self, location: LocationModel, snapshots: list[ProviderSnapshot]) -> None:
        """Guarda pronósticos futuros y observaciones reales actuales para verificación prospectiva."""
        if location.id is None:
            return
        now_dt = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        now_iso = now_dt.strftime("%Y-%m-%dT%H:00:00Z")

        forecast_rows: list[dict] = []
        obs_rows: list[dict] = []
        live_temps: list[float] = []
        live_winds: list[float] = []
        live_hum: list[float] = []
        live_pres: list[float] = []
        live_prec: list[float] = []

        for snap in snapshots:
            if snap.status != "ok" or snap.current is None:
                continue
            cur = snap.current
            if cur.temperature_c is not None:
                live_temps.append(cur.temperature_c)
            if cur.wind_speed_kmh is not None:
                live_winds.append(cur.wind_speed_kmh)
            if cur.humidity_pct is not None:
                live_hum.append(cur.humidity_pct)
            if cur.pressure_hpa is not None:
                live_pres.append(cur.pressure_hpa)
            if cur.precipitation_mm is not None:
                live_prec.append(cur.precipitation_mm)

            if snap.provider_id in ("open_meteo", "met_norway"):
                obs_rows.append(
                    {
                        "location_id": location.id,
                        "observed_at": now_iso,
                        "source_provider": f"{snap.provider_id}_current",
                        "temp_c": cur.temperature_c,
                        "feels_like_c": cur.feels_like_c,
                        "humidity_pct": cur.humidity_pct,
                        "pressure_hpa": cur.pressure_hpa,
                        "wind_kmh": cur.wind_speed_kmh,
                        "precip_mm": cur.precipitation_mm,
                        "weather_code": cur.weather_code,
                    }
                )

            # Guardar pronósticos con horizontes 1h, 3h, 6h, 12h, 24h, 48h
            for hp in snap.hourly[:54]:
                target_iso = normalize_hour_utc(hp.time)
                try:
                    t_dt = datetime.fromisoformat(target_iso.replace("Z", "+00:00"))
                except ValueError:
                    continue
                lead_h = int(round((t_dt - now_dt).total_seconds() / 3600.0))
                if lead_h in (1, 3, 6, 12, 24, 48) and hp.temperature_c is not None:
                    forecast_rows.append(
                        {
                            "location_id": location.id,
                            "provider_id": snap.provider_id,
                            "source_type": "live_forecast",
                            "issued_at": now_iso,
                            "target_time": target_iso,
                            "lead_hours": lead_h,
                            "temp_c": hp.temperature_c,
                            "feels_like_c": hp.feels_like_c,
                            "humidity_pct": hp.humidity_pct,
                            "pressure_hpa": hp.pressure_hpa,
                            "wind_kmh": hp.wind_speed_kmh,
                            "precip_mm": hp.precipitation_mm,
                            "precip_prob_pct": hp.precip_probability_pct,
                            "weather_code": hp.weather_code,
                        }
                    )

        if live_temps:
            obs_rows.append(
                {
                    "location_id": location.id,
                    "observed_at": now_iso,
                    "source_provider": "consensus_live",
                    "temp_c": round(sum(live_temps) / len(live_temps), 1),
                    "humidity_pct": round(sum(live_hum) / len(live_hum), 0) if live_hum else None,
                    "pressure_hpa": round(sum(live_pres) / len(live_pres), 1) if live_pres else None,
                    "wind_kmh": round(sum(live_winds) / len(live_winds), 1) if live_winds else None,
                    "precip_mm": round(sum(live_prec) / len(live_prec), 2) if live_prec else 0.0,
                }
            )

        self.repo.save_actual_observations(obs_rows)
        self.repo.save_forecast_records(forecast_rows)
