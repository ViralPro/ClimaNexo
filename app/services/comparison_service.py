"""Servicio de comparación multiproveedor (BD primero + refresco no bloqueante) y comparativa multi-ciudad."""

from __future__ import annotations

import asyncio
from typing import Optional

from app.db.repository import WeatherRepository
from app.models.schemas import (
    CityComparisonItem,
    LocationModel,
    MultiCityComparisonResponse,
    ProviderComparisonResponse,
    ProviderSnapshot,
)
from app.providers.registry import ProviderRegistry
from app.services.cache_service import CacheService
from app.services.collector_service import CollectorService
from app.services.history_service import HistoryService
from app.services.narrative_service import build_multicity_narrative, resolve_theme
from app.services.query_parser import format_date_range_es
from app.services.smart_engine import SmartMeteorologicalEngine


class ComparisonService:
    def __init__(
        self,
        repo: WeatherRepository | None = None,
        registry: ProviderRegistry | None = None,
        cache: CacheService | None = None,
        history_service: HistoryService | None = None,
        collector: CollectorService | None = None,
    ) -> None:
        self.repo = repo or WeatherRepository()
        self.registry = registry or ProviderRegistry(self.repo)
        self.cache = cache or CacheService(self.repo)
        self.history_service = history_service or HistoryService(self.repo, self.cache)
        self.collector = collector or CollectorService(self.repo, self.registry)
        self.smart_engine = SmartMeteorologicalEngine(self.repo)

    async def compare_providers(
        self,
        location: LocationModel,
        forecast_days: int = 7,
        force_refresh: bool = False,
    ) -> ProviderComparisonResponse:
        """
        Consulta rápida desde SQLite (`comparison_runs`):
        - Si hay comparación vigente -> responde al instante desde BD.
        - Si está vencida -> responde al instante sin bloquear y dispara actualización en segundo plano.
        - Si no existe aún -> ejecuta recopilación + comparación + almacenamiento una vez.
        """
        if location.id is None:
            location = self.repo.upsert_location(location)
        assert location.id is not None

        if not force_refresh:
            stored = self.repo.get_latest_comparison_run(location.id)
            if stored is not None:
                comp = ProviderComparisonResponse(**stored["payload"])
                comp.from_database = True
                comp.is_stale = stored["is_stale"]
                if stored["is_stale"]:
                    self.collector.trigger_background_refresh(location, forecast_days=forecast_days)
                    comp.refreshing_in_background = True
                return comp

        res = await self.collector.collect_location(location, forecast_days=forecast_days)
        return res["comparison"]

    def build_comparison_payload(
        self,
        location: LocationModel,
        snapshots: list[ProviderSnapshot],
    ) -> ProviderComparisonResponse:
        """Construye comparación inteligente directamente sobre una lista de snapshots (útil en tests)."""
        eval_res = self.smart_engine.evaluate(location, snapshots)
        return self.collector._build_comparison_response(location, snapshots, eval_res)

    async def compare_cities(
        self,
        locations: list[LocationModel],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        forecast_days: int = 7,
        is_historical: bool = False,
    ) -> MultiCityComparisonResponse:
        """Compara 2 a 5 ciudades en modo pronóstico (BD primero) o histórico."""
        locs = locations[:5]
        if is_historical and start_date and end_date:
            hist_results = await asyncio.gather(
                *[self.history_service.get_history(loc, start_date, end_date) for loc in locs]
            )
            city_items: list[CityComparisonItem] = []
            all_dates_set: set[str] = set()
            for loc, h in zip(locs, hist_results):
                daily_dicts = [
                    {
                        "date": d.date,
                        "temp_max_c": d.temp_max_c,
                        "temp_min_c": d.temp_min_c,
                        "precip_mm": d.precipitation_sum_mm or 0.0,
                        "wind_kmh": d.wind_speed_max_kmh,
                    }
                    for d in h.daily
                ]
                for d in daily_dicts:
                    all_dates_set.add(d["date"])
                city_items.append(
                    CityComparisonItem(
                        location=loc,
                        temp_max_c=h.summary.get("temp_max_c"),
                        temp_min_c=h.summary.get("temp_min_c"),
                        temp_avg_c=h.summary.get("temp_avg_c"),
                        precip_total_mm=float(h.summary.get("precip_total_mm") or 0.0),
                        wind_max_kmh=h.summary.get("wind_max_kmh"),
                        daily=daily_dicts,
                    )
                )
            dates = sorted(all_dates_set)
            s_date, e_date = start_date, end_date
            mode = "historical"
        else:
            snaps = await asyncio.gather(
                *[self.registry.fetch_primary_with_fallback(loc, forecast_days=forecast_days) for loc in locs]
            )
            city_items = []
            all_dates_set = set()
            for loc, s in zip(locs, snaps):
                daily_dicts = [
                    {
                        "date": d.date,
                        "temp_max_c": d.temp_max_c,
                        "temp_min_c": d.temp_min_c,
                        "precip_mm": d.precipitation_sum_mm or 0.0,
                        "wind_kmh": d.wind_speed_max_kmh,
                    }
                    for d in s.daily
                ]
                for d in daily_dicts:
                    all_dates_set.add(d["date"])
                maxs = [d["temp_max_c"] for d in daily_dicts if d["temp_max_c"] is not None]
                mins = [d["temp_min_c"] for d in daily_dicts if d["temp_min_c"] is not None]
                winds = [d["wind_kmh"] for d in daily_dicts if d["wind_kmh"] is not None]
                total_p = round(sum(d["precip_mm"] for d in daily_dicts), 1)
                city_items.append(
                    CityComparisonItem(
                        location=loc,
                        current_temp_c=s.current.temperature_c if s.current else None,
                        temp_max_c=max(maxs) if maxs else None,
                        temp_min_c=min(mins) if mins else None,
                        temp_avg_c=round(sum(maxs + mins) / len(maxs + mins), 1) if (maxs and mins) else None,
                        precip_total_mm=total_p,
                        wind_max_kmh=max(winds) if winds else None,
                        daily=daily_dicts,
                    )
                )
            dates = sorted(all_dates_set)
            s_date = dates[0] if dates else (start_date or "")
            e_date = dates[-1] if dates else (end_date or "")
            mode = "forecast"

        temp_series = []
        precip_series = []
        wind_series = []
        for ci in city_items:
            by_d = {d["date"]: d for d in ci.daily}
            temp_series.append(
                {"label": ci.location.name, "values": [by_d.get(dt, {}).get("temp_max_c") for dt in dates]}
            )
            precip_series.append(
                {"label": ci.location.name, "values": [by_d.get(dt, {}).get("precip_mm", 0.0) for dt in dates]}
            )
            wind_series.append(
                {"label": ci.location.name, "values": [by_d.get(dt, {}).get("wind_kmh") for dt in dates]}
            )

        label = format_date_range_es(s_date, e_date) if s_date and e_date else f"Próximos {forecast_days} días"
        narrative = build_multicity_narrative(city_items, label, mode)
        max_precip = max((c.precip_total_mm for c in city_items), default=0.0)
        theme = resolve_theme(precip_total=max_precip, focus_metric="general")

        return MultiCityComparisonResponse(
            date_focus_label=label,
            start_date=s_date,
            end_date=e_date,
            mode=mode,
            theme=theme,
            narrative=narrative,
            cities=city_items,
            dates=dates,
            temp_series=temp_series,
            precip_series=precip_series,
            wind_series=wind_series,
        )
