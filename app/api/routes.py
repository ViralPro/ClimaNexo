"""Endpoints REST v1 de Weather Dashboard Pro (sin controles técnicos modificables por el usuario)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.config import DEFAULT_CITIES, settings
from app.models.schemas import DashboardQueryRequest, LocationModel

router = APIRouter(prefix="/api/v1", tags=["weather"])


def _facade(request: Request):
    return request.app.state.facade


def _scheduler(request: Request):
    return request.app.state.scheduler


@router.get("/health")
async def health_check(request: Request) -> dict[str, Any]:
    """Estado de solo lectura del motor meteorológico administrado automáticamente por el servidor."""
    facade = _facade(request)
    scheduler = _scheduler(request)
    providers = facade.repo.list_providers()
    active_ready = sum(
        1 for p in providers if p["enabled"] and (not p["requires_api_key"] or p["has_api_key"])
    )
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "ai_engines": "none (100% deterministic Python)",
        "server_managed_automation": True,
        "database": facade.repo.db.check_integrity(),
        "cache": facade.cache.stats(),
        "scheduler": scheduler.status(),
        "providers_ready": active_ready,
        "providers_total": len(providers),
    }


@router.get("/geolocate")
async def geolocate_ip(request: Request) -> dict[str, Any]:
    facade = _facade(request)
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    client_ip = forwarded or (request.client.host if request.client else None)
    loc = await facade.geocoding.geolocate_by_ip(client_ip)
    saved = facade.repo.upsert_location(loc)
    return {"location": saved.model_dump(), "method": "ip_geolocation"}


@router.get("/geocode")
async def geocode_cities(
    request: Request,
    q: str = Query(..., min_length=2, max_length=120),
    count: int = Query(8, ge=1, le=15),
) -> dict[str, Any]:
    facade = _facade(request)
    matches = await facade.geocoding.search_cities(q, count=count)
    return {"query": q, "results": [m.model_dump() for m in matches]}


@router.get("/reverse-geocode")
async def reverse_geocode_coords(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> dict[str, Any]:
    facade = _facade(request)
    loc = await facade.geocoding.reverse_geocode(lat, lon)
    saved = facade.repo.upsert_location(loc)
    return {"location": saved.model_dump()}


@router.get("/weather")
async def get_weather(
    request: Request,
    lat: Optional[float] = Query(None, ge=-90, le=90),
    lon: Optional[float] = Query(None, ge=-180, le=180),
    city: Optional[str] = Query(None, max_length=120),
    name: Optional[str] = Query(None, max_length=120),
    country: Optional[str] = Query(None, max_length=120),
    admin1: Optional[str] = Query(None, max_length=120),
    days: int = Query(7, ge=1, le=16),
) -> dict[str, Any]:
    """Consulta rápida desde BD con selección inteligente multiproveedor y refresco no bloqueante."""
    facade = _facade(request)
    loc = await facade.resolve_or_upsert_location(
        lat=lat, lon=lon, city=city, name=name, country=country, admin1=admin1
    )
    try:
        bundle = await facade.get_weather_bundle(loc, forecast_days=days)
        return bundle.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/query")
async def execute_dashboard_query(request: Request, body: DashboardQueryRequest) -> dict[str, Any]:
    facade = _facade(request)
    try:
        res = await facade.execute_query(body)
        return res.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error al procesar la consulta: {exc}") from exc


@router.get("/compare/providers")
async def compare_providers(
    request: Request,
    lat: Optional[float] = Query(None, ge=-90, le=90),
    lon: Optional[float] = Query(None, ge=-180, le=180),
    city: Optional[str] = Query(None, max_length=120),
    days: int = Query(7, ge=1, le=10),
) -> dict[str, Any]:
    """Lectura rápida de comparación multiproveedor, procedencia y discrepancias desde BD."""
    facade = _facade(request)
    loc = await facade.resolve_or_upsert_location(lat=lat, lon=lon, city=city)
    comp = await facade.comparison.compare_providers(loc, forecast_days=days, force_refresh=False)
    return comp.model_dump()


@router.get("/compare/cities")
async def compare_cities(
    request: Request,
    cities: str = Query(..., min_length=3, max_length=250, description="Ciudades separadas por coma"),
    days: int = Query(7, ge=1, le=16),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
) -> dict[str, Any]:
    facade = _facade(request)
    raw_names = [c.strip() for c in cities.split(",") if c.strip()][:5]
    if len(raw_names) < 2:
        raise HTTPException(status_code=400, detail="Indicá al menos 2 ciudades separadas por coma.")

    resolved: list[LocationModel] = []
    for cname in raw_names:
        loc = await facade.geocoding.resolve_city(cname)
        if loc:
            resolved.append(facade.repo.upsert_location(loc))

    if len(resolved) < 2:
        raise HTTPException(status_code=404, detail="No se pudieron localizar al menos 2 de las ciudades solicitadas.")

    is_hist = bool(start_date and end_date)
    comp = await facade.comparison.compare_cities(
        resolved,
        start_date=start_date,
        end_date=end_date,
        forecast_days=days,
        is_historical=is_hist,
    )
    return comp.model_dump()


@router.get("/accuracy")
async def get_accuracy_dashboard(
    request: Request,
    lat: Optional[float] = Query(None, ge=-90, le=90),
    lon: Optional[float] = Query(None, ge=-180, le=180),
    scope: str = Query("location", pattern="^(location|global)$"),
    source_type: Optional[str] = Query(None, pattern="^(live_forecast|historical_run)$"),
) -> dict[str, Any]:
    facade = _facade(request)
    loc: Optional[LocationModel] = None
    if scope == "location" and lat is not None and lon is not None:
        loc = facade.repo.find_location_by_coords(lat, lon)
    dash = facade.accuracy.get_dashboard(location=loc, source_type=source_type)
    return dash.model_dump()


@router.post("/accuracy/backtest")
async def run_accuracy_backtest(
    request: Request,
    lat: Optional[float] = Query(None, ge=-90, le=90),
    lon: Optional[float] = Query(None, ge=-180, le=180),
    city: Optional[str] = Query(None, max_length=120),
    past_days: int = Query(7, ge=3, le=14),
) -> dict[str, Any]:
    facade = _facade(request)
    loc = await facade.resolve_or_upsert_location(lat=lat, lon=lon, city=city)
    try:
        summary = await facade.accuracy.ingest_historical_verification(loc, past_days=past_days)
        dash = facade.accuracy.get_dashboard(location=loc)
        return {"ingested": summary, "dashboard": dash.model_dump()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error ejecutando verificación histórica: {exc}") from exc


@router.get("/history")
async def get_weather_history(
    request: Request,
    lat: Optional[float] = Query(None, ge=-90, le=90),
    lon: Optional[float] = Query(None, ge=-180, le=180),
    city: Optional[str] = Query(None, max_length=120),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
) -> dict[str, Any]:
    facade = _facade(request)
    loc = await facade.resolve_or_upsert_location(lat=lat, lon=lon, city=city)
    try:
        hist = await facade.history.get_history(loc, start_date=start_date, end_date=end_date)
        return hist.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error consultando historial: {exc}") from exc


@router.get("/history/export")
async def export_weather_history_csv(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
) -> PlainTextResponse:
    facade = _facade(request)
    loc = await facade.resolve_or_upsert_location(lat=lat, lon=lon)
    hist = await facade.history.get_history(loc, start_date=start_date, end_date=end_date)
    csv_text = facade.history.to_csv(hist)
    safe_city = "".join(ch if ch.isalnum() else "_" for ch in loc.name).strip("_") or "clima"
    headers = {
        "Content-Disposition": f'attachment; filename="historial_{safe_city}_{hist.start_date}_{hist.end_date}.csv"'
    }
    return PlainTextResponse(content=csv_text, media_type="text/csv; charset=utf-8", headers=headers)


@router.get("/alerts")
async def get_alerts(
    request: Request,
    lat: Optional[float] = Query(None, ge=-90, le=90),
    lon: Optional[float] = Query(None, ge=-180, le=180),
    active_only: bool = Query(False),
) -> dict[str, Any]:
    facade = _facade(request)
    loc_id: Optional[int] = None
    if lat is not None and lon is not None:
        loc = facade.repo.find_location_by_coords(lat, lon)
        if loc:
            loc_id = loc.id
    alerts = facade.repo.list_alerts(location_id=loc_id, active_only=active_only)
    return {"count": len(alerts), "alerts": [a.model_dump() for a in alerts]}


@router.get("/radar")
async def get_radar(request: Request) -> dict[str, Any]:
    facade = _facade(request)
    res = await facade.radar.get_radar_frames()
    return res.model_dump()


@router.get("/locations")
async def list_saved_locations(request: Request, favorites_only: bool = Query(False)) -> dict[str, Any]:
    facade = _facade(request)
    locs = facade.repo.list_locations(favorites_only=favorites_only)
    return {
        "locations": [l.model_dump() for l in locs],
        "suggested_cities": DEFAULT_CITIES,
    }


@router.post("/locations/{location_id}/favorite")
async def toggle_favorite_location(
    request: Request,
    location_id: int,
    is_favorite: bool = Query(True),
) -> dict[str, Any]:
    facade = _facade(request)
    updated = facade.repo.set_location_favorite(location_id, is_favorite)
    if not updated:
        raise HTTPException(status_code=404, detail="Ubicación no encontrada.")
    return {"location": updated.model_dump()}


@router.delete("/locations/{location_id}")
async def delete_saved_location(request: Request, location_id: int) -> dict[str, Any]:
    facade = _facade(request)
    ok = facade.repo.delete_location(location_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Ubicación no encontrada.")
    return {"deleted": True, "id": location_id}


@router.get("/providers")
async def list_providers_readonly(request: Request) -> dict[str, Any]:
    """Listado de solo lectura de fuentes meteorológicas administradas por el servidor."""
    facade = _facade(request)
    providers = facade.repo.list_providers()
    sanitized = []
    for p in providers:
        sanitized.append(
            {
                "provider_id": p["provider_id"],
                "display_name": p["display_name"],
                "description": p["description"],
                "status": p["status"],
                "active": bool(p["enabled"] and (not p["requires_api_key"] or p["has_api_key"])),
                "avg_latency_ms": p["avg_latency_ms"],
                "last_success_at": p["last_success_at"],
            }
        )
    return {"providers": sanitized}
