"""Configuración global de Weather Dashboard Pro."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    app_name: str = "Weather Dashboard Pro"
    app_version: str = "2.0.0"
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8050"))
    database_path: Path = BASE_DIR / os.getenv("DATABASE_PATH", "data/weather_dashboard.db")
    weatherapi_key: str = os.getenv("WEATHERAPI_KEY", "").strip()
    openweather_api_key: str = os.getenv("OPENWEATHER_API_KEY", "").strip()

    # Administración 100% automática en servidor (no configurable por el usuario final)
    auto_refresh_seconds: int = int(os.getenv("AUTO_REFRESH_SECONDS", "600"))
    collector_interval_seconds: int = int(os.getenv("COLLECTOR_INTERVAL_SECONDS", "300"))
    snapshot_ttl_seconds: int = int(os.getenv("SNAPSHOT_TTL_SECONDS", "600"))
    comparison_ttl_seconds: int = int(os.getenv("COMPARISON_TTL_SECONDS", "600"))
    collector_max_retries: int = 2
    collector_backoff_seconds: float = 1.5
    discrepancy_temp_threshold_c: float = 2.0
    discrepancy_wind_threshold_kmh: float = 15.0

    http_timeout_seconds: float = 12.0
    user_agent: str = "WeatherDashboardPro/2.0 (+https://localhost/weather-dashboard-pro)"

    # TTLs de caché en segundos
    ttl_forecast: int = 600          # 10 min
    ttl_comparison: int = 600        # 10 min
    ttl_geocoding: int = 86400       # 24 h
    ttl_history: int = 21600         # 6 h
    ttl_radar: int = 300             # 5 min
    ttl_ip_geo: int = 1800           # 30 min
    ttl_prev_runs: int = 3600        # 1 h


settings = Settings()

DEFAULT_PROVIDERS: list[dict] = [
    {
        "provider_id": "open_meteo",
        "display_name": "Open-Meteo (Best Match)",
        "enabled": 1,
        "requires_api_key": 0,
        "base_url": "https://api.open-meteo.com/v1/forecast",
        "priority": 1,
        "description": "Agregador de alta resolución que selecciona automáticamente el mejor modelo meteorológico local.",
        "supports_historical_backtest": True,
        "open_meteo_model": "best_match",
    },
    {
        "provider_id": "met_norway",
        "display_name": "MET Norway",
        "enabled": 1,
        "requires_api_key": 0,
        "base_url": "https://api.met.no/weatherapi/locationforecast/2.0/compact",
        "priority": 2,
        "description": "Instituto Meteorológico de Noruega (YR.no), pronóstico global de alta precisión.",
        "supports_historical_backtest": False,
        "open_meteo_model": None,
    },
    {
        "provider_id": "ecmwf_ifs",
        "display_name": "ECMWF IFS (Europa)",
        "enabled": 1,
        "requires_api_key": 0,
        "base_url": "https://api.open-meteo.com/v1/ecmwf",
        "priority": 3,
        "description": "Centro Europeo de Previsiones Meteorológicas a Plazo Medio (modelo IFS).",
        "supports_historical_backtest": True,
        "open_meteo_model": "ecmwf_ifs025",
    },
    {
        "provider_id": "noaa_gfs",
        "display_name": "NOAA GFS (EE. UU.)",
        "enabled": 1,
        "requires_api_key": 0,
        "base_url": "https://api.open-meteo.com/v1/gfs",
        "priority": 4,
        "description": "Sistema de Pronóstico Global (GFS) de la Administración Nacional Oceánica y Atmosférica.",
        "supports_historical_backtest": True,
        "open_meteo_model": "gfs_seamless",
    },
    {
        "provider_id": "dwd_icon",
        "display_name": "DWD ICON (Alemania)",
        "enabled": 1,
        "requires_api_key": 0,
        "base_url": "https://api.open-meteo.com/v1/dwd-icon",
        "priority": 5,
        "description": "Servicio Meteorológico Alemán (Deutscher Wetterdienst) con malla global ICON.",
        "supports_historical_backtest": True,
        "open_meteo_model": "icon_seamless",
    },
    {
        "provider_id": "wttr_in",
        "display_name": "wttr.in (WorldWeather)",
        "enabled": 1,
        "requires_api_key": 0,
        "base_url": "https://wttr.in",
        "priority": 6,
        "description": "Servicio meteorológico independiente con observaciones y pronóstico multi-día.",
        "supports_historical_backtest": False,
        "open_meteo_model": None,
    },
    {
        "provider_id": "weatherapi",
        "display_name": "WeatherAPI.com",
        "enabled": 1,
        "requires_api_key": 1,
        "base_url": "https://api.weatherapi.com/v1/forecast.json",
        "priority": 7,
        "description": "API comercial global con alertas oficiales y pronóstico horario (requiere API Key).",
        "supports_historical_backtest": False,
        "open_meteo_model": None,
    },
    {
        "provider_id": "openweather",
        "display_name": "OpenWeatherMap",
        "enabled": 1,
        "requires_api_key": 1,
        "base_url": "https://api.openweathermap.org/data/2.5",
        "priority": 8,
        "description": "Plataforma meteorológica global con observaciones y pronóstico 5 días/3h (requiere API Key).",
        "supports_historical_backtest": False,
        "open_meteo_model": None,
    },
]

PROVIDER_META_MAP: dict[str, dict] = {p["provider_id"]: p for p in DEFAULT_PROVIDERS}

DEFAULT_CITIES: list[dict] = [
    {"name": "Buenos Aires", "admin1": "Ciudad Autónoma de Buenos Aires", "country": "Argentina", "country_code": "AR", "latitude": -34.6037, "longitude": -58.3816, "timezone": "America/Argentina/Buenos_Aires"},
    {"name": "Madrid", "admin1": "Comunidad de Madrid", "country": "España", "country_code": "ES", "latitude": 40.4165, "longitude": -3.7026, "timezone": "Europe/Madrid"},
    {"name": "Ciudad de México", "admin1": "Ciudad de México", "country": "México", "country_code": "MX", "latitude": 19.4326, "longitude": -99.1332, "timezone": "America/Mexico_City"},
    {"name": "Bogotá", "admin1": "Bogotá D.C.", "country": "Colombia", "country_code": "CO", "latitude": 4.7110, "longitude": -74.0721, "timezone": "America/Bogota"},
    {"name": "Santiago", "admin1": "Región Metropolitana", "country": "Chile", "country_code": "CL", "latitude": -33.4489, "longitude": -70.6693, "timezone": "America/Santiago"},
]
