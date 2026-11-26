"""Pruebas de base de datos SQLite WAL, integridad referencial, CRUD y caché."""

from __future__ import annotations

import time

from app.db.database import DatabaseManager
from app.db.repository import WeatherRepository
from app.models.schemas import LocationModel, WeatherAlertModel
from app.services.cache_service import CacheService


def test_sqlite_wal_and_integrity(tmp_path):
    db = DatabaseManager(tmp_path / "test_weather.db")
    info = db.check_integrity()
    assert info["ok"] is True
    assert str(info["journal_mode"]).lower() == "wal"
    assert info["foreign_keys"] is True
    assert info["table_counts"]["provider_settings"] == 10


def test_location_upsert_favorites_and_cascade(tmp_path):
    db = DatabaseManager(tmp_path / "test_locs.db")
    repo = WeatherRepository(db)

    loc1 = repo.upsert_location(
        LocationModel(name="Madrid", country="España", latitude=40.4165, longitude=-3.7026)
    )
    assert loc1.id is not None
    assert loc1.is_favorite is False

    # Upsert con coordenadas equivalentes no duplica fila
    loc1_again = repo.upsert_location(
        LocationModel(name="Madrid", admin1="Comunidad de Madrid", country="España", latitude=40.4166, longitude=-3.7025),
        set_favorite=True,
    )
    assert loc1_again.id == loc1.id
    assert loc1_again.is_favorite is True

    # Guardar alerta vinculada y verificar borrado en cascada
    repo.sync_location_alerts(
        loc1.id,
        [
            WeatherAlertModel(
                alert_key="test_alert",
                category="viento",
                severity="alerta",
                title="Viento fuerte",
                description="Ráfagas elevadas",
                recommendation="Precaución",
                metric_value="70 km/h",
                source="Test",
                starts_at="2026-11-25T00:00:00Z",
                ends_at="2026-11-26T00:00:00Z",
            )
        ],
    )
    assert len(repo.list_alerts(location_id=loc1.id, active_only=True)) == 1
    assert repo.delete_location(loc1.id) is True
    assert len(repo.list_alerts(location_id=loc1.id)) == 0


def test_two_tier_cache_ttl_and_stale(tmp_path):
    db = DatabaseManager(tmp_path / "test_cache.db")
    repo = WeatherRepository(db)
    cache = CacheService(repo)

    key = cache.build_key("weather", city="Córdoba")
    cache.set(key, "weather", {"temp": 24.5}, ttl_seconds=1)
    assert cache.get(key) == {"temp": 24.5}

    cache.clear_memory()
    # Debe recuperar desde SQLite persistente
    assert cache.get(key) == {"temp": 24.5}

    time.sleep(1.15)
    assert cache.get(key, allow_stale=False) is None
    assert cache.get(key, allow_stale=True) == {"temp": 24.5}
