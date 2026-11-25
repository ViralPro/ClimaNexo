"""Interfaz base de proveedores meteorológicos, mapeo WMO en español y cálculos auxiliares."""

from __future__ import annotations

import math
import ssl
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from app.models.schemas import LocationModel, ProviderSnapshot


@lru_cache(maxsize=1)
def get_ssl_context() -> ssl.SSLContext:
    """Reutiliza un único contexto TLS para evitar recargas síncronas del almacén de certificados."""
    return ssl.create_default_context()

# Códigos WMO estándar traducidos al español + ícono Lucide + tema cromático
WMO_DESCRIPTIONS: dict[int, tuple[str, str, str]] = {
    0: ("Cielo despejado", "sun", "sun"),
    1: ("Mayormente despejado", "sun", "clear"),
    2: ("Parcialmente nublado", "cloud-sun", "clear"),
    3: ("Nublado", "cloud", "cloudy"),
    45: ("Niebla", "cloud-fog", "fog"),
    48: ("Niebla con escarcha", "cloud-fog", "fog"),
    51: ("Llovizna ligera", "cloud-drizzle", "rain"),
    53: ("Llovizna moderada", "cloud-drizzle", "rain"),
    55: ("Llovizna intensa", "cloud-drizzle", "rain"),
    56: ("Llovizna helada ligera", "cloud-drizzle", "cold"),
    57: ("Llovizna helada intensa", "cloud-drizzle", "cold"),
    61: ("Lluvia ligera", "cloud-rain", "rain"),
    63: ("Lluvia moderada", "cloud-rain", "rain"),
    65: ("Lluvia fuerte", "cloud-rain", "rain"),
    66: ("Lluvia helada ligera", "cloud-rain", "cold"),
    67: ("Lluvia helada fuerte", "cloud-rain", "cold"),
    71: ("Nevada ligera", "snowflake", "snow"),
    73: ("Nevada moderada", "snowflake", "snow"),
    75: ("Nevada intensa", "snowflake", "snow"),
    77: ("Granos de nieve", "snowflake", "snow"),
    80: ("Chubascos ligeros", "cloud-rain", "rain"),
    81: ("Chubascos moderados", "cloud-rain", "rain"),
    82: ("Chubascos violentos", "cloud-rain", "storm"),
    85: ("Chubascos de nieve ligeros", "snowflake", "snow"),
    86: ("Chubascos de nieve intensos", "snowflake", "snow"),
    95: ("Tormenta eléctrica", "cloud-lightning", "storm"),
    96: ("Tormenta con granizo ligero", "cloud-lightning", "storm"),
    99: ("Tormenta con granizo fuerte", "cloud-lightning", "storm"),
}


def decode_wmo(code: Optional[int], temp_c: Optional[float] = None) -> tuple[str, str, str]:
    """Devuelve (texto_español, icono_lucide, tema_visual)."""
    c = int(code) if code is not None else 0
    text, icon, theme = WMO_DESCRIPTIONS.get(c, ("Condiciones variables", "cloud", "clear"))
    if theme in ("clear", "sun") and temp_c is not None:
        if temp_c <= 2.0:
            theme = "cold"
        elif temp_c >= 29.0:
            theme = "sun"
    return text, icon, theme


def wind_deg_to_cardinal(deg: Optional[float]) -> str:
    if deg is None:
        return "—"
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"]
    idx = int((deg + 11.25) // 22.5) % 16
    return dirs[idx]


def compute_dew_point_c(temp_c: Optional[float], humidity_pct: Optional[float]) -> Optional[float]:
    """Fórmula de Magnus-Tetens para punto de rocío (°C)."""
    if temp_c is None or humidity_pct is None or humidity_pct <= 0:
        return None
    a, b = 17.27, 237.7
    rh = max(1.0, min(100.0, float(humidity_pct)))
    alpha = ((a * temp_c) / (b + temp_c)) + math.log(rh / 100.0)
    return round((b * alpha) / (a - alpha), 1)


def compute_apparent_temp_c(
    temp_c: Optional[float],
    humidity_pct: Optional[float],
    wind_kmh: Optional[float],
) -> Optional[float]:
    """Sensación térmica convencional (Steadman / Wind Chill / Heat Index)."""
    if temp_c is None:
        return None
    rh = float(humidity_pct) if humidity_pct is not None else 50.0
    v_kmh = float(wind_kmh) if wind_kmh is not None else 5.0
    if temp_c <= 10.0 and v_kmh >= 4.8:
        # Wind Chill (NOAA/Environment Canada)
        wc = 13.12 + 0.6215 * temp_c - 11.37 * (v_kmh ** 0.16) + 0.3965 * temp_c * (v_kmh ** 0.16)
        return round(wc, 1)
    # Australian Apparent Temperature (Steadman)
    v_ms = v_kmh / 3.6
    e = (rh / 100.0) * 6.105 * math.exp((17.27 * temp_c) / (237.7 + temp_c))
    at = temp_c + 0.33 * e - 0.70 * v_ms - 4.0
    return round(at, 1)


def compute_pressure_trend(hourly_pressures: list[Optional[float]]) -> str:
    vals = [p for p in hourly_pressures[:6] if p is not None]
    if len(vals) < 3:
        return "estable"
    delta = vals[-1] - vals[0]
    if delta >= 1.5:
        return "en ascenso"
    if delta <= -1.5:
        return "en descenso"
    return "estable"


def normalize_hour_utc(iso_str: str) -> str:
    """Normaliza una marca temporal ISO a formato hora UTC 'YYYY-MM-DDTHH:00:00Z'."""
    s = iso_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return iso_str
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:00:00Z")


def safe_float(val: object, digits: int = 1) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return round(float(val), digits)
    except (TypeError, ValueError):
        return None


class BaseWeatherProvider(ABC):
    provider_id: str = ""
    display_name: str = ""
    requires_api_key: bool = False

    @abstractmethod
    async def fetch(
        self,
        location: LocationModel,
        forecast_days: int = 7,
        api_key: str = "",
    ) -> ProviderSnapshot:
        """Obtiene datos meteorológicos reales normalizados."""
