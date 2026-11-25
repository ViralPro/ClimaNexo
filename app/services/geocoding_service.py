"""Servicio de geocodificación en español, geocodificación inversa y geolocalización por IP."""

from __future__ import annotations

from typing import Optional

import httpx

from app.config import DEFAULT_CITIES, settings
from app.models.schemas import LocationModel
from app.services.cache_service import CacheService


class GeocodingService:
    OPEN_METEO_GEO = "https://geocoding-api.open-meteo.com/v1/search"
    NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"

    def __init__(self, cache: CacheService | None = None) -> None:
        self.cache = cache or CacheService()

    async def search_cities(self, query: str, count: int = 8) -> list[LocationModel]:
        q = query.strip()
        if len(q) < 2:
            return []
        cache_key = self.cache.build_key("geocode", q=q.lower(), count=count)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return [LocationModel(**item) for item in cached]

        results: list[LocationModel] = []
        try:
            async with httpx.AsyncClient(
                timeout=settings.http_timeout_seconds,
                headers={"User-Agent": settings.user_agent},
            ) as client:
                resp = await client.get(
                    self.OPEN_METEO_GEO,
                    params={"name": q, "count": count, "language": "es", "format": "json"},
                )
                resp.raise_for_status()
                data = resp.json()
            for item in data.get("results") or []:
                loc = LocationModel(
                    name=str(item.get("name") or q),
                    admin1=str(item.get("admin1") or ""),
                    country=str(item.get("country") or ""),
                    country_code=str(item.get("country_code") or ""),
                    latitude=round(float(item["latitude"]), 4),
                    longitude=round(float(item["longitude"]), 4),
                    timezone=str(item.get("timezone") or "auto"),
                    elevation=float(item["elevation"]) if item.get("elevation") is not None else None,
                )
                loc.display_label = loc.formatted_label()
                results.append(loc)
        except Exception:
            # Fallback a ciudades conocidas si falla temporalmente la red
            ql = q.lower()
            for c in DEFAULT_CITIES:
                if ql in c["name"].lower() or ql in c["country"].lower():
                    loc = LocationModel(**c)
                    loc.display_label = loc.formatted_label()
                    results.append(loc)

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
