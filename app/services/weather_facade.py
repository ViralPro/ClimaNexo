"""Fachada principal orientada a base de datos (lectura rápida + refresco no bloqueante)."""

from __future__ import annotations

import asyncio
from typing import Optional

from app.config import DEFAULT_CITIES
from app.db.repository import WeatherRepository
from app.models.schemas import (
    DashboardQueryRequest,
    DashboardQueryResponse,
    LocationModel,
    WeatherBundleResponse,
)
from app.providers.registry import ProviderRegistry
from app.services.accuracy_engine import AccuracyEngine
from app.services.cache_service import CacheService
from app.services.collector_service import CollectorService
from app.services.comparison_service import ComparisonService
from app.services.geocoding_service import GeocodingService
from app.services.history_service import HistoryService
from app.services.narrative_service import build_forecast_narrative, resolve_theme
from app.services.query_parser import parse_weather_query
from app.services.radar_service import RadarService


class WeatherFacade:
    def __init__(self, repo: WeatherRepository | None = None) -> None:
        self.repo = repo or WeatherRepository()
        self.cache = CacheService(self.repo)
        self.registry = ProviderRegistry(self.repo)
        self.accuracy = AccuracyEngine(self.repo)
        self.collector = CollectorService(self.repo, self.registry, self.accuracy)
        self.geocoding = GeocodingService(self.cache)
        self.history = HistoryService(self.repo, self.cache)
        self.comparison = ComparisonService(
            self.repo, self.registry, self.cache, self.history, self.collector
        )
        self.radar = RadarService(self.cache)

    async def resolve_or_upsert_location(
        self,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        city: Optional[str] = None,
        name: Optional[str] = None,
        country: Optional[str] = None,
        admin1: Optional[str] = None,
    ) -> LocationModel:
        if lat is not None and lon is not None:
            existing = self.repo.find_location_by_coords(lat, lon)
            if existing and not name:
                return self.repo.upsert_location(existing)
            if name:
                loc = LocationModel(
                    name=name,
                    admin1=admin1 or "",
                    country=country or "",
                    latitude=round(lat, 4),
                    longitude=round(lon, 4),
                )
            else:
                loc = await self.geocoding.reverse_geocode(lat, lon)
            return self.repo.upsert_location(loc)

        if city:
            local_match = self.repo.find_location_by_name(city)
            if local_match is not None:
                return local_match
            resolved = await self.geocoding.resolve_city(city)
            if resolved:
                return self.repo.upsert_location(resolved)

        recent = self.repo.list_locations(limit=1)
        if recent:
            return recent[0]
        return self.repo.upsert_location(LocationModel(**DEFAULT_CITIES[0]))

    async def get_weather_bundle(
        self,
        location: LocationModel,
        forecast_days: int = 7,
        focus_metric: str = "general",
    ) -> WeatherBundleResponse:
        """
        Consulta rápida orientada a base de datos:
        1. Busca snapshot vigente en SQLite -> responde al instante (< 5 ms).
        2. Si existe snapshot vencido -> responde al instante sin bloquear y dispara
           actualización multiproveedor en segundo plano.
        3. Si es una ubicación nueva -> recopila, valida, compara y almacena automáticamente.
        """
        if location.id is None:
            location = self.repo.upsert_location(location)
        assert location.id is not None

        days = max(1, min(16, int(forecast_days)))

        # 1. Lectura ultrarrápida desde SQLite
        stored = self.repo.get_weather_snapshot(location.id, forecast_days=days)
        if stored is not None:
            bundle = WeatherBundleResponse(**stored["payload"])
            bundle.location = location  # Refleja estado actualizado de favorito
            bundle.daily = bundle.daily[:days]
            bundle.from_database = True
            bundle.from_cache = True
            bundle.is_stale = stored["is_stale"]
            bundle.data_age_seconds = stored["age_seconds"]

            if focus_metric != "general":
                bundle.narrative = build_forecast_narrative(
                    bundle.location, bundle.current, bundle.daily, focus_metric
                )
                precip_total = sum((d.precipitation_sum_mm or 0.0) for d in bundle.daily)
                bundle.theme = resolve_theme(
                    weather_code=bundle.current.weather_code,
                    temp_c=bundle.current.temperature_c,
                    precip_total=precip_total,
                    focus_metric=focus_metric,
                )

            # Si el registro venció, actualizar en segundo plano SIN bloquear al usuario
            if stored["is_stale"]:
                self.collector.trigger_background_refresh(location, forecast_days=max(days, 7))
                bundle.refreshing_in_background = True

            return bundle

        # 2. Ubicación aún no recopilada -> ejecutar pipeline automático completo una vez
        res = await self.collector.collect_location(
            location, forecast_days=days, focus_metric=focus_metric
        )
        bundle: WeatherBundleResponse = res["bundle"]

        # Verificación histórica real en segundo plano si esta ubicación aún no tiene historial
        agg = self.repo.get_accuracy_aggregates(location_id=location.id)
        if agg["historical_verified"] == 0:
            asyncio.create_task(self._safe_backtest(location))

        return bundle

    async def execute_query(self, req: DashboardQueryRequest) -> DashboardQueryResponse:
        parsed = parse_weather_query(req.query)
        if req.days:
            parsed.forecast_days = max(1, min(16, int(req.days)))

        resolved_locs: list[LocationModel] = []
        if parsed.cities:
            res_list = await asyncio.gather(*[self.geocoding.resolve_city(c) for c in parsed.cities])
            for loc in res_list:
                if loc:
                    resolved_locs.append(self.repo.upsert_location(loc))

        if not resolved_locs:
            fallback_loc = await self.resolve_or_upsert_location(lat=req.lat, lon=req.lon)
            resolved_locs = [fallback_loc]

        self.repo.log_query(
            query_text=req.query,
            intent=parsed.intent,
            locations=[l.name for l in resolved_locs],
            date_range=parsed.date_focus_label,
        )

        # Caso 1: Comparación multi-ciudad
        if len(resolved_locs) >= 2:
            mc = await self.comparison.compare_cities(
                resolved_locs,
                start_date=parsed.start_date,
                end_date=parsed.end_date,
                forecast_days=parsed.forecast_days,
                is_historical=parsed.is_historical,
            )
            return DashboardQueryResponse(
                mode="multi_city",
                parsed={
                    "intent": "multi_city",
                    "cities": [l.name for l in resolved_locs],
                    "focus_metric": parsed.focus_metric,
                    "is_historical": parsed.is_historical,
                    "start_date": mc.start_date,
                    "end_date": mc.end_date,
                },
                theme=mc.theme,
                narrative=mc.narrative,
                date_focus_label=mc.date_focus_label,
                multi_city=mc,
            )

        loc = resolved_locs[0]

        # Caso 2: Consulta histórica de una ciudad
        if parsed.is_historical and parsed.start_date and parsed.end_date:
            hist = await self.history.get_history(
                loc,
                start_date=parsed.start_date,
                end_date=parsed.end_date,
                focus_metric=parsed.focus_metric,
            )
            rain_total = float(hist.summary.get("precip_total_mm") or 0.0)
            avg_t = hist.summary.get("temp_avg_c")
            theme = resolve_theme(temp_c=avg_t, precip_total=rain_total, focus_metric=parsed.focus_metric)
            return DashboardQueryResponse(
                mode="single_history",
                parsed={
                    "intent": "history",
                    "cities": [loc.name],
                    "focus_metric": parsed.focus_metric,
                    "is_historical": True,
                    "start_date": hist.start_date,
                    "end_date": hist.end_date,
                },
                theme=theme,
                narrative=hist.narrative,
                date_focus_label=parsed.date_focus_label,
                history=hist,
            )

        # Caso 3: Pronóstico actual y extendido desde BD inteligente
        bundle = await self.get_weather_bundle(
            loc,
            forecast_days=parsed.forecast_days,
            focus_metric=parsed.focus_metric,
        )
        return DashboardQueryResponse(
            mode="single_forecast",
            parsed={
                "intent": "forecast",
                "cities": [loc.name],
                "focus_metric": parsed.focus_metric,
                "is_historical": False,
                "forecast_days": parsed.forecast_days,
            },
            theme=bundle.theme,
            narrative=bundle.narrative,
            date_focus_label=bundle.date_focus_label,
            weather=bundle,
        )

    async def _safe_backtest(self, location: LocationModel) -> None:
        try:
            await self.accuracy.ingest_historical_verification(location, past_days=7)
        except Exception:
            pass
