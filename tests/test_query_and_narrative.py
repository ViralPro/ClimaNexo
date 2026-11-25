"""Pruebas del analizador determinístico en español, narrativa y ausencia total de IA."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.services.narrative_service import resolve_theme
from app.services.query_parser import parse_weather_query


def test_parse_single_city_forecast():
    p = parse_weather_query("Clima en Buenos Aires próximos 14 días", today=date(2026, 11, 25))
    assert p.intent == "forecast"
    assert p.is_historical is False
    assert p.forecast_days == 14
    assert p.cities == ["Buenos Aires"]


def test_parse_multicity_comparison():
    p = parse_weather_query(
        "Comparar el clima en Madrid, Buenos Aires y Bogotá el mes pasado",
        today=date(2026, 11, 25),
    )
    assert p.intent == "multi_city"
    assert p.is_historical is True
    assert p.start_date == "2026-10-01"
    assert p.end_date == "2026-10-31"
    assert p.cities == ["Madrid", "Buenos Aires", "Bogotá"]


def test_parse_historical_rain_query():
    p = parse_weather_query(
        "¿Cuánto llovió en Los Ángeles en octubre 2026?",
        today=date(2026, 11, 25),
    )
    assert p.intent == "history"
    assert p.is_historical is True
    assert p.focus_metric == "rain"
    assert p.start_date == "2026-10-01"
    assert p.end_date == "2026-10-31"
    assert p.cities == ["Los Ángeles"] or p.cities == ["Los Angeles"]


def test_parse_wind_this_year():
    p = parse_weather_query(
        "¿Cuál fue el día más ventoso en Córdoba este año?",
        today=date(2026, 11, 25),
    )
    assert p.intent == "history"
    assert p.focus_metric == "wind"
    assert p.start_date == "2026-01-01"
    assert p.end_date == "2026-11-24"
    assert p.cities == ["Córdoba"]


def test_theme_resolution():
    assert resolve_theme(weather_code=95, temp_c=22.0) == "storm"
    assert resolve_theme(weather_code=63, temp_c=14.0) == "rain"
    assert resolve_theme(weather_code=73, temp_c=-2.0) == "snow"
    assert resolve_theme(weather_code=45, temp_c=10.0) == "fog"
    assert resolve_theme(weather_code=0, temp_c=33.0) == "sun"
    assert resolve_theme(weather_code=0, temp_c=-1.0) == "cold"


def test_zero_ai_or_gemini_dependencies_in_app_code():
    """Garantiza que no exista Gemini, OpenAI ni ningún agente de IA dentro de app/."""
    app_root = Path(__file__).resolve().parent.parent / "app"
    forbidden = ("google.genai", "google-genai", "generativeai", "openai", "anthropic", "langchain")
    for py_file in app_root.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in content, f"Token prohibido '{token}' encontrado en {py_file}"
