"""Generador determinístico de resúmenes narrativos en español y temas visuales (sin IA)."""

from __future__ import annotations

from typing import Optional

from app.models.schemas import (
    CityComparisonItem,
    CurrentWeather,
    DailyPoint,
    HistoryDayPoint,
    LocationModel,
)


def build_forecast_narrative(
    location: LocationModel,
    current: CurrentWeather,
    daily: list[DailyPoint],
    focus_metric: str = "general",
) -> str:
    city = location.name
    temp_now = current.temperature_c
    cond_now = current.condition_text.lower()

    if not daily:
        t_str = f"{temp_now:.1f} °C" if temp_now is not None else "sin lectura térmica"
        return f"{city} registra actualmente {t_str} con {cond_now}."

    maxs = [d.temp_max_c for d in daily if d.temp_max_c is not None]
    mins = [d.temp_min_c for d in daily if d.temp_min_c is not None]
    precips = [(d.date, d.precipitation_sum_mm or 0.0) for d in daily]
    winds = [(d.date, d.wind_speed_max_kmh or 0.0) for d in daily]

    peak_max = max(maxs) if maxs else None
    lowest_min = min(mins) if mins else None
    total_rain = round(sum(p for _, p in precips), 1)
    wettest_day, wettest_mm = max(precips, key=lambda x: x[1]) if precips else ("", 0.0)
    windiest_day, windiest_kmh = max(winds, key=lambda x: x[1]) if winds else ("", 0.0)

    days_n = len(daily)
    period_txt = f"los próximos {days_n} días" if days_n > 1 else "la jornada de hoy"

    if focus_metric == "rain":
        if total_rain >= 1.0:
            return (
                f"{city} acumulará cerca de {total_rain} mm de precipitación durante {period_txt}, "
                f"con el mayor episodio previsto para el {wettest_day} ({wettest_mm:.1f} mm). "
                f"Actualmente se registran {temp_now:.1f} °C y {cond_now}."
            )
        return (
            f"Se proyectan condiciones mayormente secas en {city} durante {period_txt} "
            f"(acumulado estimado de {total_rain} mm), con temperaturas entre {lowest_min:.0f} °C y {peak_max:.0f} °C."
        )

    if focus_metric == "wind":
        return (
            f"El viento en {city} sopla actualmente a {current.wind_speed_kmh or 0:.0f} km/h ({current.wind_direction_cardinal}) "
            f"y alcanzará picos de hasta {windiest_kmh:.0f} km/h hacia el {windiest_day} durante {period_txt}."
        )

    parts = [f"{city} presenta actualmente {temp_now:.1f} °C ({cond_now})" if temp_now is not None else f"{city} presenta {cond_now}"]
    if peak_max is not None and lowest_min is not None:
        if lowest_min <= 2.0:
            parts.append(f"para {period_txt} se esperan mínimas frías de hasta {lowest_min:.0f} °C y máximas de {peak_max:.0f} °C")
        elif peak_max >= 32.0:
            parts.append(f"para {period_txt} se prevé ambiente caluroso con máximas de hasta {peak_max:.0f} °C y mínimas de {lowest_min:.0f} °C")
        else:
            parts.append(f"para {period_txt} se proyecta un rango térmico estable entre {lowest_min:.0f} °C y {peak_max:.0f} °C")

    if total_rain >= 10.0:
        parts.append(f"con precipitaciones acumuladas de {total_rain} mm (pico el {wettest_day})")
    elif total_rain >= 1.0:
        parts.append(f"con lluvias aisladas que sumarán {total_rain} mm")
    else:
        parts.append("sin lluvias significativas en el horizonte")

    return ", ".join(parts) + "."


def build_history_narrative(
    location: LocationModel,
    days: list[HistoryDayPoint],
    start_date: str,
    end_date: str,
    focus_metric: str = "general",
) -> str:
    if not days:
        return f"No se encontraron registros históricos para {location.name} entre {start_date} y {end_date}."

    maxs = [(d.date, d.temp_max_c) for d in days if d.temp_max_c is not None]
    mins = [(d.date, d.temp_min_c) for d in days if d.temp_min_c is not None]
    rains = [(d.date, d.precipitation_sum_mm or 0.0) for d in days]
    winds = [(d.date, d.wind_speed_max_kmh or 0.0) for d in days]

    total_rain = round(sum(v for _, v in rains), 1)
    wet_day, wet_val = max(rains, key=lambda x: x[1])
    hot_day, hot_val = max(maxs, key=lambda x: x[1]) if maxs else ("", 0.0)
    cold_day, cold_val = min(mins, key=lambda x: x[1]) if mins else ("", 0.0)
    wind_day, wind_val = max(winds, key=lambda x: x[1]) if winds else ("", 0.0)

    if focus_metric == "rain":
        return (
            f"{location.name} registró un total de {total_rain} mm de lluvia entre el {start_date} y el {end_date}, "
            f"con la jornada más lluviosa el {wet_day} ({wet_val:.1f} mm)."
        )
    if focus_metric == "wind":
        return (
            f"En el período {start_date} a {end_date}, el día más ventoso en {location.name} fue el {wind_day} "
            f"con rachas/viento máximo de {wind_val:.1f} km/h."
        )
    return (
        f"Entre el {start_date} y el {end_date}, {location.name} alcanzó un pico térmico de {hot_val:.1f} °C ({hot_day}) "
        f"y una mínima de {cold_val:.1f} °C ({cold_day}), acumulando {total_rain} mm de precipitación en {len(days)} días."
    )


def build_multicity_narrative(
    cities: list[CityComparisonItem],
    date_focus_label: str,
    mode: str,
) -> str:
    if not cities:
        return "Sin ciudades disponibles para comparar."
    valid_max = [c for c in cities if c.temp_max_c is not None]
    valid_min = [c for c in cities if c.temp_min_c is not None]
    wettest = max(cities, key=lambda c: c.precip_total_mm)
    warmest = max(valid_max, key=lambda c: c.temp_max_c or -999) if valid_max else None
    coolest = min(valid_min, key=lambda c: c.temp_min_c or 999) if valid_min else None

    names = ", ".join(c.location.name for c in cities[:-1]) + f" y {cities[-1].location.name}" if len(cities) > 1 else cities[0].location.name
    verb = "registraron" if mode == "historical" else "proyectan"
    parts = [f"Comparativa para {names} ({date_focus_label}):"]
    if warmest:
        parts.append(f"{warmest.location.name} lidera la temperatura máxima con {warmest.temp_max_c:.1f} °C")
    if coolest and (not warmest or coolest.location.name != warmest.location.name):
        parts.append(f"mientras {coolest.location.name} presenta la mínima más baja ({coolest.temp_min_c:.1f} °C)")
    if wettest.precip_total_mm > 0:
        parts.append(f"y {wettest.location.name} concentra el mayor volumen de lluvia con {wettest.precip_total_mm:.1f} mm")
    else:
        parts.append(f"todas las ciudades {verb} tiempo mayormente seco en el período")
    return " ".join(parts) + "."


def resolve_theme(
    weather_code: Optional[int] = None,
    temp_c: Optional[float] = None,
    precip_total: float = 0.0,
    focus_metric: str = "general",
) -> str:
    if focus_metric == "rain" and precip_total >= 10.0:
        return "rain"
    if focus_metric == "snow":
        return "snow"
    if weather_code is not None:
        if weather_code in (95, 96, 99, 82):
            return "storm"
        if weather_code in (71, 73, 75, 77, 85, 86):
            return "snow"
        if weather_code in (51, 53, 55, 61, 63, 65, 80, 81):
            return "rain"
        if weather_code in (45, 48):
            return "fog"
        if weather_code in (56, 57, 66, 67):
            return "cold"
    if temp_c is not None:
        if temp_c <= 1.0:
            return "cold"
        if temp_c >= 30.0:
            return "sun"
    return "clear"
