"""Pruebas de integración FastAPI contra APIs meteorológicas reales en vivo."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db_file = tmp_path_factory.mktemp("api_int") / "integration.db"
    app = create_app(db_path=db_file, enable_scheduler=False)
    with TestClient(app) as tc:
        yield tc


def test_frontend_and_static_assets(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200
    assert "Consultá el clima" in r.text
    assert 'id="landing-input"' in r.text
    assert 'id="landing-submit-btn"' in r.text
    assert "WEATHER DASHBOARD PRO" not in r.text

    for asset in (
        "/static/css/styles.css",
        "/static/js/api.js",
        "/static/js/charts.js",
        "/static/js/map.js",
        "/static/js/app.js",
        "/static/vendor/chart.umd.min.js",
        "/static/vendor/leaflet.js",
        "/static/vendor/lucide.min.js",
        "/static/manifest.json",
    ):
        ra = client.get(asset)
        assert ra.status_code == 200, f"Fallo al servir {asset}"


def test_health_and_geocoding_live(client: TestClient):
    rh = client.get("/api/v1/health")
    assert rh.status_code == 200
    dh = rh.json()
    assert dh["status"] == "ok"
    assert dh["database"]["ok"] is True
    assert dh["providers_ready"] >= 6

    rg = client.get("/api/v1/geocode", params={"q": "Madrid"})
    assert rg.status_code == 200
    results = rg.json()["results"]
    assert len(results) >= 1
    assert any("Madrid" in r["name"] for r in results)

    rip = client.get("/api/v1/geolocate")
    assert rip.status_code == 200
    assert rip.json()["location"]["latitude"] is not None


def test_weather_bundle_and_queries_live(client: TestClient):
    rw = client.get("/api/v1/weather", params={"city": "Madrid", "days": 7})
    assert rw.status_code == 200
    dw = rw.json()
    assert dw["location"]["name"] == "Madrid"
    assert dw["current"]["temperature_c"] is not None
    assert len(dw["hourly"]) >= 24
    assert len(dw["daily"]) == 7
    assert len(dw["narrative"]) > 20

    # Consulta determinística multi-ciudad
    rq_mc = client.post(
        "/api/v1/query",
        json={"query": "Comparar el clima en Madrid, Buenos Aires y Bogotá"},
    )
    assert rq_mc.status_code == 200
    dmc = rq_mc.json()
    assert dmc["mode"] == "multi_city"
    assert len(dmc["multi_city"]["cities"]) == 3
    assert len(dmc["multi_city"]["temp_series"]) == 3

    # Consulta histórica de lluvia
    rq_h = client.post(
        "/api/v1/query",
        json={"query": "¿Cuánto llovió en Madrid el mes pasado?"},
    )
    assert rq_h.status_code == 200
    dh = rq_h.json()
    assert dh["mode"] == "single_history"
    assert dh["history"]["days_count"] >= 28


def test_multiprovider_and_real_accuracy_backtest_live(client: TestClient):
    rc = client.get("/api/v1/compare/providers", params={"city": "Madrid", "days": 7})
    assert rc.status_code == 200
    dc = rc.json()
    assert dc["active_providers"] >= 5
    assert dc["consensus"]["temp_mean_c"] is not None
    assert len(dc["hourly_chart"]["datasets"]) >= 4

    # Ejecutar backtest histórico real en ciudad fresca (Lima) y comprobar idempotencia y cero invención
    rb = client.post("/api/v1/accuracy/backtest", params={"city": "Lima", "past_days": 5})
    assert rb.status_code == 200
    db_res = rb.json()
    assert db_res["ingested"]["newly_verified"] >= 50
    dash = db_res["dashboard"]
    assert dash["total_verified_pairs"] >= 50
    assert len(dash["recent_audit"]) >= 10

    # Repetir el mismo backtest debe ser idempotente (sin duplicar registros en SQLite)
    rb_again = client.post("/api/v1/accuracy/backtest", params={"city": "Lima", "past_days": 5})
    assert rb_again.status_code == 200
    assert rb_again.json()["ingested"]["newly_verified"] == 0

    by_pid = {r["provider_id"]: r for r in dash["ranking"]}
    for verified_pid in ("open_meteo", "ecmwf_ifs", "noaa_gfs", "dwd_icon"):
        assert by_pid[verified_pid]["has_data"] is True
        assert by_pid[verified_pid]["sample_count"] > 0
        assert 0.0 <= by_pid[verified_pid]["composite_accuracy_pct"] <= 100.0
    # Proveedores sin muestras maduras deben permanecer explícitamente en None
    for unverified_pid in ("weatherapi", "openweather"):
        assert by_pid[unverified_pid]["has_data"] is False
        assert by_pid[unverified_pid]["composite_accuracy_pct"] is None


def test_history_csv_radar_providers_and_settings(client: TestClient):
    rh = client.get("/api/v1/history", params={"city": "Madrid"})
    assert rh.status_code == 200
    hdata = rh.json()
    assert hdata["days_count"] >= 25

    rcsv = client.get(
        "/api/v1/history/export",
        params={"lat": 40.4165, "lon": -3.7026, "start_date": hdata["start_date"], "end_date": hdata["end_date"]},
    )
    assert rcsv.status_code == 200
    assert "Temp Max" in rcsv.text

    rr = client.get("/api/v1/radar")
    assert rr.status_code == 200
    assert len(rr.json()["radar_past"]) >= 1

    rp = client.get("/api/v1/providers")
    assert rp.status_code == 200
    assert sum(1 for p in rp.json()["providers"] if p["active"]) >= 6

    # Verificar que endpoints de mutación técnica fueron eliminados
    assert client.put("/api/v1/settings", json={"auto_refresh_seconds": 600}).status_code in (404, 405)
    assert client.put("/api/v1/providers", json={"provider_id": "open_meteo", "enabled": False}).status_code in (404, 405)
