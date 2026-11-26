"""Servicio de geocodificación en español, geocodificación inversa y geolocalización por IP."""

from __future__ import annotations

import unicodedata
from typing import Optional

import httpx

from app.config import DEFAULT_CITIES, settings
from app.models.schemas import LocationModel
from app.providers.base import get_ssl_context
from app.services.cache_service import CacheService


def _norm_geo(text: str) -> str:
    nfd = unicodedata.normalize("NFD", (text or "").strip().lower())
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")


class GeocodingService:
    OPEN_METEO_GEO = "https://geocoding-api.open-meteo.com/v1/search"
    PHOTON_GEO = "https://photon.komoot.io/api/"
    NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"

    def __init__(self, cache: CacheService | None = None) -> None:
        self.cache = cache or CacheService()

    async def search_cities(self, query: str, count: int = 8) -> list[LocationModel]:
        q = query.strip()
        if len(q) < 2:
            return []
        cache_key = self.cache.build_key("geocode_v2", q=_norm_geo(q), count=count)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return [LocationModel(**item) for item in cached]

        parts = [p.strip() for p in q.split(",") if p.strip()]
        primary_q = parts[0] if parts else q
        qualifiers = [_norm_geo(p) for p in parts[1:]]

        candidates: list[LocationModel] = []
        async with httpx.AsyncClient(
            timeout=settings.http_timeout_seconds,
            headers={"User-Agent": settings.user_agent},
            verify=get_ssl_context(),
        ) as client:
            # 1. Open-Meteo Geocoding
            try:
                resp = await client.get(
                    self.OPEN_METEO_GEO,
                    params={"name": primary_q, "count": 15, "language": "es", "format": "json"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("results") or []:
                        loc = LocationModel(
                            name=str(item.get("name") or primary_q),
                            admin1=str(item.get("admin1") or ""),
                            country=str(item.get("country") or ""),
                            country_code=str(item.get("country_code") or ""),
                            latitude=round(float(item["latitude"]), 4),
                            longitude=round(float(item["longitude"]), 4),
                            timezone=str(item.get("timezone") or "auto"),
                            elevation=float(item["elevation"]) if item.get("elevation") is not None else None,
                        )
                        loc.display_label = loc.formatted_label()
                        candidates.append(loc)
            except Exception:
                pass

            # 2. Photon Komoot (soporta búsquedas multi-palabra "Localidad Provincia País" y localidades pequeñas)
            try:
                presp = await client.get(
                    self.PHOTON_GEO,
                    params=[
                        ("q", q),
                        ("limit", "10"),
                        ("layer", "city"),
                        ("layer", "district"),
                        ("layer", "locality"),
                        ("layer", "county"),
                        ("layer", "state"),
                        ("layer", "country"),
                    ],
                )
                if presp.status_code == 200:
                    pdata = presp.json()
                    for feat in pdata.get("features") or []:
                        props = feat.get("properties") or {}
                        coords = (feat.get("geometry") or {}).get("coordinates") or []
                        if len(coords) < 2:
                            continue
                        name = str(props.get("name") or props.get("city") or "").strip()
                        if not name:
                            continue
                        loc = LocationModel(
                            name=name,
                            admin1=str(props.get("state") or props.get("county") or ""),
                            country=str(props.get("country") or ""),
                            country_code=str(props.get("countrycode") or "").upper(),
                            latitude=round(float(coords[1]), 4),
                            longitude=round(float(coords[0]), 4),
                            timezone="auto",
                        )
                        loc.display_label = loc.formatted_label()
                        candidates.append(loc)
            except Exception:
                pass

        # Filtrar por calificadores explícitos (provincia / país), puntuar relevancia y deduplicar geográficamente
        primary_norm = _norm_geo(primary_q)
        scored_pairs: list[tuple[int, LocationModel]] = []
        for c in candidates:
            name_norm = _norm_geo(c.name)
            region_hay = _norm_geo(f"{c.admin1} {c.country} {c.country_code}")
            if qualifiers and not all(qf in region_hay for qf in qualifiers):
                continue

            if name_norm == primary_norm:
                rank_score = 1500
            elif name_norm.startswith(primary_norm + " ") or name_norm.startswith(primary_norm):
                rank_score = 900
            elif primary_norm.startswith(name_norm + " ") and len(name_norm) >= 3:
                rank_score = 1200
            elif all(tok in _norm_geo(f"{c.name} {c.admin1} {c.country}") for tok in primary_norm.split() if len(tok) >= 2):
                rank_score = 650
            else:
                continue

            if qualifiers:
                rank_score += len(qualifiers) * 300
            scored_pairs.append((rank_score, c))

        scored_pairs.sort(key=lambda pair: pair[0], reverse=True)
        filtered: list[LocationModel] = []
        for _, c in scored_pairs:
            is_dup = any(
                _norm_geo(ex.name) == _norm_geo(c.name)
                and _norm_geo(ex.country) == _norm_geo(c.country)
                and abs(ex.latitude - c.latitude) < 0.09
                and abs(ex.longitude - c.longitude) < 0.09
                for ex in filtered
            )
            if not is_dup:
                filtered.append(c)

        results = filtered[:count]
        if results:
            self.cache.set(
                cache_key, "geocode", [r.model_dump() for r in results], settings.ttl_geocoding
            )
        return results

    async def resolve_city(self, city_name: str) -> Optional[LocationModel]:
        matches = await self.search_cities(city_name, count=5)
        if not matches:
            return None
        target = city_name.strip().lower()
        for m in matches:
            if m.name.lower() == target:
                return m
        return matches[0]

    async def reverse_geocode(self, lat: float, lon: float) -> LocationModel:
        rlat, rlon = round(lat, 3), round(lon, 3)
        cache_key = self.cache.build_key("rev_geo", lat=rlat, lon=rlon)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return LocationModel(**cached)

        loc = LocationModel(
            name=f"Ubicación ({lat:.2f}, {lon:.2f})",
            latitude=round(lat, 4),
            longitude=round(lon, 4),
        )
        try:
            async with httpx.AsyncClient(
                timeout=8.0,
                headers={"User-Agent": settings.user_agent, "Accept-Language": "es"},
            ) as client:
                resp = await client.get(
                    self.NOMINATIM_REVERSE,
                    params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 10},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    addr = data.get("address") or {}
                    city = (
                        addr.get("city")
                        or addr.get("town")
                        or addr.get("village")
                        or addr.get("municipality")
                        or addr.get("county")
                        or data.get("name")
                    )
                    if city:
                        loc = LocationModel(
                            name=str(city),
                            admin1=str(addr.get("state") or addr.get("region") or ""),
                            country=str(addr.get("country") or ""),
                            country_code=str(addr.get("country_code") or "").upper(),
                            latitude=round(lat, 4),
                            longitude=round(lon, 4),
                        )
        except Exception:
            pass

        loc.display_label = loc.formatted_label()
        self.cache.set(cache_key, "rev_geo", loc.model_dump(), settings.ttl_geocoding)
        return loc

    async def geolocate_by_ip(self, client_ip: Optional[str] = None) -> LocationModel:
        """Geolocalización automática por IP como respaldo cuando el navegador no otorga GPS."""
        ip_key = client_ip or "auto"
        if ip_key in ("127.0.0.1", "::1", "localhost"):
            ip_key = "auto"
        cache_key = self.cache.build_key("ip_geo", ip=ip_key)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return LocationModel(**cached)

        endpoints = [
            f"https://ipwho.is/{'' if ip_key == 'auto' else ip_key}",
            f"https://ipapi.co/{'' if ip_key == 'auto' else ip_key + '/'}json/",
        ]
        async with httpx.AsyncClient(
            timeout=7.0,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        ) as client:
            for url in endpoints:
                try:
                    r = await client.get(url)
                    if r.status_code != 200:
                        continue
                    d = r.json()
                    if d.get("success") is False or d.get("error"):
                        continue
                    lat = d.get("latitude")
                    lon = d.get("longitude")
                    city = d.get("city")
                    if lat is not None and lon is not None and city:
                        tz = d.get("timezone")
                        tz_id = tz.get("id") if isinstance(tz, dict) else (tz or "auto")
                        loc = LocationModel(
                            name=str(city),
                            admin1=str(d.get("region") or ""),
                            country=str(d.get("country") or d.get("country_name") or ""),
                            country_code=str(d.get("country_code") or ""),
                            latitude=round(float(lat), 4),
                            longitude=round(float(lon), 4),
                            timezone=str(tz_id),
                        )
                        loc.display_label = loc.formatted_label()
                        self.cache.set(cache_key, "ip_geo", loc.model_dump(), settings.ttl_ip_geo)
                        return loc
                except Exception:
                    continue

        # Fallback determinístico seguro si no hay conexión a servicios IP
        fallback = LocationModel(**DEFAULT_CITIES[0])
        fallback.display_label = fallback.formatted_label()
        return fallback
