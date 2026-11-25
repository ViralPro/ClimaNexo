"""Pruebas matemáticas del motor de precisión, consenso multiproveedor y alertas."""

from __future__ import annotations

from app.db.database import DatabaseManager
from app.db.repository import WeatherRepository
from app.models.schemas import CurrentWeather, DailyPoint, HourlyPoint, LocationModel, ProviderSnapshot
from app.services.accuracy_engine import AccuracyEngine, compute_pair_verification
from app.services.alerts_service import evaluate_weather_alerts
from app.services.comparison_service import ComparisonService


def test_accuracy_math_exact_and_no_fabricated_data(tmp_path):
    db = DatabaseManager(tmp_path / "test_acc.db")
    repo = WeatherRepository(db)
    loc = repo.upsert_location(LocationModel(name="Córdoba", country="Argentina", latitude=-31.42, longitude=-64.18))
    engine = AccuracyEngine(repo)

    # 1. En una base limpia, NINGÚN proveedor puede tener porcentaje inventado
    empty_dash = engine.get_dashboard(loc)
    assert empty_dash.total_verified_pairs == 0
    for stat in empty_dash.ranking:
        assert stat.has_data is False
        assert stat.sample_count == 0
        assert stat.composite_accuracy_pct is None

    # 2. Insertar 2 pronósticos y 2 observaciones reales conocidas
    repo.save_actual_observations([
        {
            "location_id": loc.id,
            "observed_at": "2026-11-24T12:00:00Z",
            "source_provider": "open_meteo_archive",
            "temp_c": 20.0,
            "wind_kmh": 10.0,
            "precip_mm": 0.0,
        },
        {
            "location_id": loc.id,
            "observed_at": "2026-11-24T15:00:00Z",
            "source_provider": "open_meteo_archive",
            "temp_c": 24.0,
            "wind_kmh": 15.0,
            "precip_mm": 2.0,
        },
    ])
    repo.save_forecast_records([
        # Proveedor A: exacto en ambos registros -> 100% precisión
        {
            "location_id": loc.id,
            "provider_id": "open_meteo",
            "source_type": "live_forecast",
            "issued_at": "2026-11-23T12:00:00Z",
            "target_time": "2026-11-24T12:00:00Z",
            "lead_hours": 24,
            "temp_c": 20.0,
            "wind_kmh": 10.0,
            "precip_mm": 0.0,
        },
        {
            "location_id": loc.id,
            "provider_id": "open_meteo",
            "source_type": "live_forecast",
            "issued_at": "2026-11-23T15:00:00Z",
            "target_time": "2026-11-24T15:00:00Z",
            "lead_hours": 24,
            "temp_c": 24.0,
            "wind_kmh": 15.0,
            "precip_mm": 2.0,
        },
        # Proveedor B: error de +2 °C, +10 km/h viento y falló lluvia en 15h
        {
            "location_id": loc.id,
            "provider_id": "met_norway",
            "source_type": "live_forecast",
            "issued_at": "2026-11-23T15:00:00Z",
            "target_time": "2026-11-24T15:00:00Z",
            "lead_hours": 24,
            "temp_c": 26.0,
            "wind_kmh": 25.0,
            "precip_mm": 0.0,
        },
    ])

    verified = engine.evaluate_pending(location_id=loc.id)
    assert verified == 3

    dash = engine.get_dashboard(loc)
    by_id = {r.provider_id: r for r in dash.ranking}

    assert by_id["open_meteo"].has_data is True
    assert by_id["open_meteo"].sample_count == 2
    assert by_id["open_meteo"].composite_accuracy_pct == 100.0
    assert by_id["open_meteo"].temp_mae_c == 0.0
    assert by_id["open_meteo"].rain_hit_rate_pct == 100.0

    assert by_id["met_norway"].has_data is True
    assert by_id["met_norway"].sample_count == 1
    assert by_id["met_norway"].temp_mae_c == 2.0
    assert by_id["met_norway"].temp_bias_c == 2.0
    assert by_id["met_norway"].rain_hit_rate_pct == 0.0
    assert by_id["met_norway"].composite_accuracy_pct < 80.0

    # Los demás proveedores siguen en has_data=False (jamás inventados)
    assert by_id["noaa_gfs"].has_data is False
    assert by_id["noaa_gfs"].composite_accuracy_pct is None


def test_alerts_threshold_engine():
    cur = CurrentWeather(
        observed_at="2026-11-25T15:00",
        temperature_c=37.5,
        feels_like_c=40.2,
        humidity_pct=65.0,
        pressure_hpa=1012.0,
        wind_speed_kmh=58.0,
        wind_gusts_kmh=78.0,
        precipitation_mm=12.0,
        uv_index=9.5,
        weather_code=96,
        condition_text="Tormenta con granizo ligero",
    )
    hourly = [
        HourlyPoint(time=f"2026-11-25T{h:02d}:00", temperature_c=36.0, pressure_hpa=1014.0 - h * 1.2, precipitation_mm=4.0, wind_speed_kmh=55.0, wind_gusts_kmh=76.0, weather_code=96)
        for h in range(8)
    ]
    daily = [DailyPoint(date="2026-11-25", temp_max_c=37.5, temp_min_c=22.0, uv_index_max=9.5, precipitation_sum_mm=32.0)]

    alerts = evaluate_weather_alerts("Córdoba", cur, hourly, daily)
    categories = {a.category for a in alerts}
    assert {"tormenta", "lluvia", "viento", "temperatura", "uv", "presion"}.issubset(categories)


def test_consensus_calculation(tmp_path):
    db = DatabaseManager(tmp_path / "test_comp.db")
    repo = WeatherRepository(db)
    svc = ComparisonService(repo)
    loc = LocationModel(name="Lima", latitude=-12.04, longitude=-77.04)

    snaps = [
        ProviderSnapshot(
            provider_id="open_meteo",
            display_name="Open-Meteo",
            status="ok",
            latency_ms=100,
            fetched_at="2026-11-25T00:00:00Z",
            current=CurrentWeather(observed_at="2026-11-25T00:00", temperature_c=20.0, humidity_pct=70, pressure_hpa=1013, wind_speed_kmh=10),
        ),
        ProviderSnapshot(
            provider_id="met_norway",
            display_name="MET Norway",
            status="ok",
            latency_ms=120,
            fetched_at="2026-11-25T00:00:00Z",
            current=CurrentWeather(observed_at="2026-11-25T00:00", temperature_c=22.0, humidity_pct=74, pressure_hpa=1015, wind_speed_kmh=14),
        ),
    ]
    comp = svc.build_comparison_payload(loc, snaps)
    assert comp.active_providers == 2
    assert comp.consensus["temp_mean_c"] == 21.0
    assert comp.consensus["temp_spread_c"] == 2.0
    assert comp.providers[0].temp_deviation_c == -1.0
    assert comp.providers[1].temp_deviation_c == 1.0
