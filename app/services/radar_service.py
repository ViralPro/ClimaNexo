"""Servicio de metadatos de radar meteorológico en vivo (RainViewer API)."""

from __future__ import annotations

import httpx

from app.config import settings
from app.models.schemas import RadarMetadataResponse
from app.services.cache_service import CacheService


class RadarService:
    RAINVIEWER_URL = "https://api.rainviewer.com/public/weather-maps.json"

    def __init__(self, cache: CacheService | None = None) -> None:
        self.cache = cache or CacheService()

    async def get_radar_frames(self) -> RadarMetadataResponse:
        cache_key = "radar:rainviewer:v2"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return RadarMetadataResponse(**cached)

        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                headers={"User-Agent": settings.user_agent},
            ) as client:
                r = await client.get(self.RAINVIEWER_URL)
                r.raise_for_status()
                d = r.json()
            host = str(d.get("host") or "https://tilecache.rainviewer.com")
            radar = d.get("radar") or {}
            sat = d.get("satellite") or {}
            res = RadarMetadataResponse(
                host=host,
                generated=int(d.get("generated") or 0),
                radar_past=list(radar.get("past") or []),
                radar_nowcast=list(radar.get("nowcast") or []),
                satellite_infrared=list(sat.get("infrared") or []),
            )
            self.cache.set(cache_key, "radar", res.model_dump(), settings.ttl_radar)
            return res
        except Exception:
            stale = self.cache.get(cache_key, allow_stale=True)
            if stale is not None:
                return RadarMetadataResponse(**stale)
            return RadarMetadataResponse(
                host="https://tilecache.rainviewer.com",
                generated=0,
                radar_past=[],
                radar_nowcast=[],
                satellite_infrared=[],
            )
