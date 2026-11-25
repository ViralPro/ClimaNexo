"""Pruebas del motor inteligente, almacenamiento masivo sin duplicados, velocidad BD y recolector."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.db.database import DatabaseManager
from app.db.repository import WeatherRepository
from app.main import create_app
from app.models.schemas import CurrentWeather, DailyPoint, HourlyPoint, LocationModel, ProviderSnapshot
from app.services.accuracy_engine import AccuracyEngine
from app.services.collector_service import CollectorService
from app.services.smart_engine import SmartMeteorologicalEngine, validate_current_reading


def test_physical_reading_validator():
    ok, err = validate_current_reading(
        CurrentWeather(observed_at="2026-11-25T12:00:00Z", temperature_c=21.5, humidity_pct=60, pressure_hpa=1015, wind_speed_kmh=14, precipitation_mm=0.2)
    )
    assert ok is True and err is None

    for bad in (
        CurrentWeather(observed_at="2026-11-25T12:00:00Z", temperature_c=95.0),
        CurrentWeather(observed_at="2026-11-25T12:00:00Z", temperature_c=20.0, humidity_pct=130.0),
        CurrentWeather(observed_at="2026-11-25T12:00:00Z", temperature_c=20.0, pressure_hpa=500.0),
        CurrentWeather(observed_at="2026-11-25T12:00:00Z", temperature_c=20.0, wind_speed_kmh=-10.0),
        CurrentWeather(observed_at="2026-11-25T12:00:00Z", temperature_c=20.0, precipitation_mm=-1.0),
    ):
        ok_bad, reason = validate_current_reading(bad)
        assert ok_bad is False and reason is not None


def test_smart_engine_weights_accuracy_freshness_and_flags_discrepancy(tmp_path):
    db = DatabaseManager(tmp_path / "smart_test.db")
    repo = WeatherRepository(db)
    loc = repo.upsert_location(LocationModel(name="Mendoza", country="Argentina", latitude=-32.89, longitude=-68.83))

    # Sembrar 8 verificaciones reales: dwd_icon muy preciso (0.1°C error) vs noaa_gfs impreciso (4.0°C error)
    obs = []
    fcs = []
    base_dt = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
    for i in range(8):
        t_iso = (base_dt + timedelta(hours=i * 3)).strftime("%Y-%m-%dT%H:00:00Z")
        iss_iso = (base_dt + timedelta(hours=i * 3 - 24)).strftime("%Y-%m-%dT%H:00:00Z")
        obs.append({"location_id": loc.id, "observed_at": t_iso, "source_provider": "open_meteo_archive", "temp_c": 20.0, "wind_kmh": 10.0, "precip_mm": 0.0})
        fcs.append({"location_id": loc.id, "provider_id": "dwd_icon", "source_type": "historical_run", "issued_at": iss_iso, "target_time": t_iso, "lead_hours": 24, "temp_c": 20.1, "wind_kmh": 10.5, "precip_mm": 0.0})
        fcs.append({"location_id": loc.id, "provider_id": "noaa_gfs", "source_type": "historical_run", "issued_at": iss_iso, "target_time": t_iso, "lead_hours": 24, "temp_c": 24.5, "wind_kmh": 25.0, "precip_mm": 0.0})

    repo.save_actual_observations(obs)
    repo.save_forecast_records(fcs)
    AccuracyEngine(repo).evaluate_pending(loc.id)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_iso = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    snaps = [
        ProviderSnapshot(
            provider_id="dwd_icon",
            display_name="DWD ICON (Alemania)",
            status="ok",
            latency_ms=110,
            fetched_at=now_iso,
            current=CurrentWeather(observed_at=now_iso, temperature_c=18.0, humidity_pct=50, pressure_hpa=1016, wind_speed_kmh=12, precipitation_mm=0.0),
            hourly=[HourlyPoint(time="2026-11-25T12:00", temperature_c=18.0)],
            daily=[DailyPoint(date="2026-11-25", temp_min_c=12.0, temp_max_c=23.0)],
        ),
        ProviderSnapshot(
            provider_id="noaa_gfs",
            display_name="NOAA GFS (EE. UU.)",
            status="ok",
            latency_ms=140,
            fetched_at=now_iso,
            current=CurrentWeather(observed_at=old_iso, temperature_c=21.5, humidity_pct=45, pressure_hpa=1012, wind_speed_kmh=18, precipitation_mm=1.2),
            hourly=[HourlyPoint(time="2026-11-25T12:00", temperature_c=21.5)],
            daily=[DailyPoint(date="2026-11-25", temp_min_c=14.0, temp_max_c=26.0)],
        ),
        # Lectura corrupta que debe ser rechazada automáticamente
        ProviderSnapshot(
            provider_id="wttr_in",
            display_name="wttr.in",
            status="ok",
            latency_ms=90,
            fetched_at=now_iso,
            current=CurrentWeather(observed_at=now_iso, temperature_c=150.0),
        ),
    ]

    engine = SmartMeteorologicalEngine(repo)
    res = engine.evaluate(loc, snaps)

    # 1. La lectura corrupta de 150 °C fue descartada
    assert res.rejected_count == 1
    # 2. DWD ICON (fresco + 99% precisión real) supera ampliamente a NOAA GFS (viejo + baja precisión)
    assert res.primary_provider_id == "dwd_icon"
    # 3. Temperatura seleccionada sesgada hacia DWD ICON (18.0) en lugar de promedio simple (19.75)
    assert res.selected_current.temperature_c < 19.3
    # 4. Discrepancia de 3.5 °C + lluvia vs seco claramente reportada
    assert res.discrepancy["has_significant_diff"] is True
    assert res.discrepancy["severity"] == "alta"
    assert res.discrepancy["precip_disagreement"] is True
    assert "DWD ICON" in res.discrepancy["summary"]


def test_collector_deduplication_and_fast_db_first_with_nonblocking_stale_refresh(tmp_path):
    db_file = tmp_path / "collector_fast.db"
    app = create_app(db_path=db_file, enable_scheduler=False)

    with TestClient(app) as client:
        # 1. Primera consulta (ciudad nueva): recopila, compara y almacena todo en SQLite
        r1 = client.get("/api/v1/weather", params={"city": "Montevideo", "days": 7})
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["from_database"] is False
        assert d1["provenance"]["sources_count"] >= 4
        assert "discrepancy" in d1

        counts_after_first = app.state.facade.repo.db.check_integrity()["table_counts"]
        assert counts_after_first["weather_snapshots"] >= 1
        assert counts_after_first["provider_readings"] >= 4
        assert counts_after_first["hourly_forecasts_store"] >= 24
        assert counts_after_first["daily_forecasts_store"] >= 7
        assert counts_after_first["comparison_runs"] >= 1

        # 2. Segunda consulta: lectura ultrarrápida desde BD (< 150 ms) sin llamar APIs externas
        t0 = time.perf_counter()
        r2 = client.get("/api/v1/weather", params={"city": "Montevideo", "days": 7})
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["from_database"] is True
        assert d2["is_stale"] is False
        assert d2["refreshing_in_background"] is False
        assert elapsed_ms < 250.0, f"La consulta desde BD tardó demasiado: {elapsed_ms:.1f} ms"

        # 3. Consulta de comparación multiproveedor también sale al instante desde BD
        t_comp = time.perf_counter()
        rc = client.get("/api/v1/compare/providers", params={"city": "Montevideo", "days": 7})
        comp_ms = (time.perf_counter() - t_comp) * 1000
        assert rc.status_code == 200
        assert rc.json()["from_database"] is True
        assert comp_ms < 250.0

        # 4. Forzar vencimiento del snapshot en SQLite y verificar respuesta inmediata NO bloqueante
        with app.state.facade.repo.db.connection() as conn:
            conn.execute("UPDATE weather_snapshots SET expires_at = ?", (time.time() - 120,))

        t_stale = time.perf_counter()
        r3 = client.get("/api/v1/weather", params={"city": "Montevideo", "days": 7})
        stale_ms = (time.perf_counter() - t_stale) * 1000
        assert r3.status_code == 200
        d3 = r3.json()
        assert d3["from_database"] is True
        assert d3["is_stale"] is True
        assert d3["refreshing_in_background"] is True
        assert stale_ms < 250.0, f"La consulta vencida bloqueó al usuario ({stale_ms:.1f} ms)"


def test_collector_cycle_updates_only_stale_locations(tmp_path):
    db = DatabaseManager(tmp_path / "cycle_test.db")
    repo = WeatherRepository(db)
    loc = repo.upsert_location(LocationModel(name="Salta", country="Argentina", latitude=-24.78, longitude=-65.41))
    collector = CollectorService(repo)

    # Primer ciclo: actualiza Salta (sin snapshot aún)
    s1 = asyncio.run(collector.run_cycle(worker_name="test_worker", force_all=False))
    assert s1["locations_updated"] == 1
    assert s1["readings_stored"] >= 4
    assert s1["forecasts_stored"] >= 20

    # Segundo ciclo inmediato: Salta está vigente, así que actualiza 0 ubicaciones (cero trabajo redundante)
    s2 = asyncio.run(collector.run_cycle(worker_name="test_worker", force_all=False))
    assert s2["locations_checked"] == 0
    assert s2["locations_updated"] == 0
    assert len(repo.list_collection_runs(limit=5)) == 2


def test_technical_controls_removed_from_ui_and_api(tmp_path):
    app = create_app(db_path=tmp_path / "lockdown.db", enable_scheduler=False)
    with TestClient(app) as client:
        html = client.get("/").text
        for forbidden_ui in (
            "Ajustes, Proveedores y Base de Datos",
            "setting-refresh-interval",
            "js-api-key-input",
            "btn-run-scheduler-now",
            "btn-settings",
            "Reconsultar proveedores",
            'data-tab="alerts"',
            'id="tab-alerts"',
            'id="overview-alerts-banner"',
            'id="nav-alerts-badge"',
            "Alertas Climáticas",
            "WEATHER DASHBOARD PRO",
            'id="btn-brand-home"',
            'id="btn-geolocate"',
            'id="btn-unit-toggle"',
            'id="btn-saved-cities"',
            'id="cities-modal"',
            'id="landing-geo-btn"',
            'class="quick-chips"',
            'id="landing-favorites"',
            "Comparar 3 ciudades",
            "Lluvia mes pasado",
            "Datos meteorológicos reales: Open-Meteo",
            'id="followup-form"',
            'id="followup-input"',
            'class="search-pill-light"',
        ):
            assert forbidden_ui not in html, f"Elemento eliminado aún presente en HTML: {forbidden_ui}"

        app_js = client.get("/static/js/app.js").text
        for forbidden_js in (
            "loadAlertsTab",
            "renderAlertsTab",
            "renderAlertsBadgeAndBanner",
            "overview-alerts-banner",
            "openCitiesModal",
            "btn-brand-home",
            "btn-unit-toggle",
            "btn-saved-cities",
            "followup-form",
            "followup-input",
        ):
            assert forbidden_js not in app_js, f"Referencia residual en app.js: {forbidden_js}"

        for required_tab in ("overview", "hourly", "extended", "map", "providers", "accuracy", "history", "search"):
            assert f'data-tab="{required_tab}"' in html, f"Falta pestaña requerida: {required_tab}"

        assert 'id="tab-search"' in html
        assert 'id="search-tab-input"' in html
        assert 'id="search-tab-submit"' in html
        assert 'id="landing-input"' in html
        assert 'id="landing-submit-btn"' in html

        # Endpoints administrativos bloqueados
        assert client.put("/api/v1/providers", json={"provider_id": "open_meteo", "enabled": False}).status_code in (404, 405)
        assert client.put("/api/v1/settings", json={"auto_refresh_seconds": 9999}).status_code in (404, 405)
        assert client.post("/api/v1/scheduler/run").status_code in (404, 405)
        assert client.post("/api/v1/providers/met_norway/test").status_code in (404, 405)
