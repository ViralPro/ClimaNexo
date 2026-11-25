"""Motor real de alertas climáticas basado en umbrales meteorológicos WMO/SMN y avisos oficiales."""

from __future__ import annotations

from typing import Optional

from app.db.database import utc_now_iso
from app.models.schemas import CurrentWeather, DailyPoint, HourlyPoint, WeatherAlertModel


def evaluate_weather_alerts(
    city_name: str,
    current: CurrentWeather,
    hourly: list[HourlyPoint],
    daily: list[DailyPoint],
    official_alerts: Optional[list[WeatherAlertModel]] = None,
) -> list[WeatherAlertModel]:
    """Analiza datos reales actuales y de las próximas 48 h para generar alertas accionables."""
    alerts: list[WeatherAlertModel] = list(official_alerts or [])
    next_48h = hourly[:48]
    now_iso = current.observed_at or utc_now_iso()
    end_48h = next_48h[-1].time if next_48h else now_iso

    # 1. Tormenta eléctrica (WMO 95, 96, 99)
    storm_hours = [h for h in next_48h if h.weather_code in (95, 96, 99)]
    if current.weather_code in (95, 96, 99) or storm_hours:
        severe = any(h.weather_code in (96, 99) for h in storm_hours) or current.weather_code in (96, 99)
        alerts.append(
            WeatherAlertModel(
                alert_key="storm_activity",
                category="tormenta",
                severity="extrema" if severe else "alerta",
                title=f"Alerta de tormenta eléctrica{' con granizo' if severe else ''} en {city_name}",
                description=(
                    f"Se detecta actividad convectiva con tormenta eléctrica en {len(storm_hours) or 1} franja(s) horaria(s) "
                    f"dentro de las próximas 48 horas."
                ),
                recommendation="Evitá permanecer en espacios abiertos o bajo árboles, desconectá equipos sensibles y resguardá vehículos.",
                metric_value=f"Código WMO {storm_hours[0].weather_code if storm_hours else current.weather_code}",
                source="Análisis Meteorológico WMO",
                starts_at=storm_hours[0].time if storm_hours else now_iso,
                ends_at=storm_hours[-1].time if storm_hours else end_48h,
            )
        )

    # 2. Lluvia intensa (acumulado 24h >= 20 mm o pico horario >= 8 mm/h)
    rain_24h = round(sum((h.precipitation_mm or 0.0) for h in next_48h[:24]), 1)
    peak_rain_h = max(((h.precipitation_mm or 0.0, h.time) for h in next_48h), default=(0.0, now_iso))
    if rain_24h >= 20.0 or peak_rain_h[0] >= 8.0:
        sev = "extrema" if (rain_24h >= 50.0 or peak_rain_h[0] >= 20.0) else "alerta"
        alerts.append(
            WeatherAlertModel(
                alert_key="heavy_rain",
                category="lluvia",
                severity=sev,
                title=f"Precipitaciones intensas previstas en {city_name}",
                description=(
                    f"Acumulado proyectado de {rain_24h} mm en las próximas 24 horas "
                    f"(pico horario de {peak_rain_h[0]:.1f} mm/h alrededor de {peak_rain_h[1]})."
                ),
                recommendation="Mantené desagües despejados, evitá calles anegables y conducí con distancia de frenado ampliada.",
                metric_value=f"{rain_24h} mm / 24h",
                source="Análisis Hidrometeorológico",
                starts_at=now_iso,
                ends_at=end_48h,
            )
        )

    # 3. Vientos fuertes / ráfagas (viento >= 45 km/h o ráfagas >= 65 km/h)
    max_wind = max(
        [current.wind_speed_kmh or 0.0] + [h.wind_speed_kmh or 0.0 for h in next_48h]
    )
    max_gust = max(
        [current.wind_gusts_kmh or 0.0] + [h.wind_gusts_kmh or 0.0 for h in next_48h]
    )
    if max_wind >= 45.0 or max_gust >= 65.0:
        sev = "extrema" if (max_gust >= 90.0 or max_wind >= 70.0) else ("alerta" if max_gust >= 75.0 or max_wind >= 55.0 else "precaucion")
        alerts.append(
            WeatherAlertModel(
                alert_key="strong_wind",
                category="viento",
                severity=sev,
                title=f"Aviso por vientos intensos en {city_name}",
                description=(
                    f"Se proyectan vientos sostenidos de hasta {max_wind:.0f} km/h "
                    f"{f'y ráfagas de {max_gust:.0f} km/h ' if max_gust >= 50 else ''}en las próximas 48 horas."
                ),
                recommendation="Asegurá objetos sueltos en balcones o patios y extremá precauciones al circular en rutas abiertas.",
                metric_value=f"Ráfagas {max_gust:.0f} km/h" if max_gust >= max_wind else f"Viento {max_wind:.0f} km/h",
                source="Análisis Anemométrico",
                starts_at=now_iso,
                ends_at=end_48h,
            )
        )

    # 4. Calor extremo (>= 35 °C) o sensación térmica >= 38 °C
    max_temp = max(
        [current.temperature_c if current.temperature_c is not None else -99.0]
        + [h.temperature_c for h in next_48h if h.temperature_c is not None]
    )
    max_feels = max(
        [current.feels_like_c if current.feels_like_c is not None else -99.0]
        + [h.feels_like_c for h in next_48h if h.feels_like_c is not None]
    )
    if max_temp >= 35.0 or max_feels >= 38.0:
        sev = "extrema" if (max_temp >= 40.0 or max_feels >= 43.0) else "alerta"
        alerts.append(
            WeatherAlertModel(
                alert_key="extreme_heat",
                category="temperatura",
                severity=sev,
                title=f"Alerta por altas temperaturas en {city_name}",
                description=(
                    f"La temperatura alcanzará hasta {max_temp:.1f} °C "
                    f"(sensación térmica estimada de {max_feels:.1f} °C) en las próximas 48 horas."
                ),
                recommendation="Hidratate con frecuencia, evitá actividad física al sol entre las 11:00 y 17:00 y cuidá a niños y adultos mayores.",
                metric_value=f"{max_temp:.1f} °C (ST {max_feels:.1f} °C)",
                source="Análisis Térmico",
                starts_at=now_iso,
                ends_at=end_48h,
            )
        )

    # 5. Heladas / Frío extremo (<= 0 °C)
    min_temp = min(
        [current.temperature_c if current.temperature_c is not None else 99.0]
        + [h.temperature_c for h in next_48h if h.temperature_c is not None]
    )
    if min_temp <= 0.0:
        sev = "extrema" if min_temp <= -8.0 else ("alerta" if min_temp <= -3.0 else "precaucion")
        alerts.append(
            WeatherAlertModel(
                alert_key="frost_cold",
                category="temperatura",
                severity=sev,
                title=f"Aviso por heladas y bajas temperaturas en {city_name}",
                description=f"Se esperan temperaturas mínimas de hasta {min_temp:.1f} °C dentro de las próximas 48 horas.",
                recommendation="Protegé cañerías expuestas, plantas sensibles y calefaccioná ambientes con ventilación adecuada.",
                metric_value=f"Mínima {min_temp:.1f} °C",
                source="Análisis Térmico",
                starts_at=now_iso,
                ends_at=end_48h,
            )
        )

    # 6. Radiación UV muy alta / extrema (>= 8)
    uv_vals = [current.uv_index or 0.0] + [d.uv_index_max or 0.0 for d in daily[:2]]
    max_uv = max(uv_vals)
    if max_uv >= 8.0:
        sev = "alerta" if max_uv >= 11.0 else "precaucion"
        alerts.append(
            WeatherAlertModel(
                alert_key="high_uv",
                category="uv",
                severity=sev,
                title=f"Índice UV {'extremo' if max_uv >= 11 else 'muy alto'} ({max_uv:.1f})",
                description=f"Radiación solar elevada prevista en {city_name}, con pico de índice UV {max_uv:.1f}.",
                recommendation="Utilizá protector solar FPS 50+, anteojos con filtro UV y buscá sombra al mediodía.",
                metric_value=f"UV {max_uv:.1f}",
                source="Índice Solar UV",
                starts_at=now_iso,
                ends_at=end_48h,
            )
        )

    # 7. Caída barométrica rápida (>= 5 hPa en 6 horas -> señal de frente inestable)
    press_series = [h.pressure_hpa for h in next_48h[:24] if h.pressure_hpa is not None]
    max_drop = 0.0
    for i in range(max(0, len(press_series) - 6)):
        drop = press_series[i] - press_series[i + 6]
        if drop > max_drop:
            max_drop = drop
    if max_drop >= 5.0:
        alerts.append(
            WeatherAlertModel(
                alert_key="barometric_drop",
                category="presion",
                severity="precaucion",
                title="Descenso rápido de presión atmosférica",
                description=(
                    f"Se proyecta una caída barométrica de {max_drop:.1f} hPa en 6 horas en {city_name}, "
                    "indicador típico de aproximación de frente inestable o cambio brusco del tiempo."
                ),
                recommendation="Seguí la evolución del radar meteorológico y las actualizaciones horarias.",
                metric_value=f"-{max_drop:.1f} hPa / 6h",
                source="Análisis Barométrico",
                starts_at=now_iso,
                ends_at=end_48h,
            )
        )

    # 8. Niebla densa / baja visibilidad
    if current.weather_code in (45, 48) or (current.visibility_km is not None and current.visibility_km <= 1.5):
        alerts.append(
            WeatherAlertModel(
                alert_key="low_visibility_fog",
                category="niebla",
                severity="precaucion",
                title=f"Visibilidad reducida por niebla en {city_name}",
                description=(
                    f"Condición actual de {current.condition_text.lower()} "
                    f"{f'(visibilidad {current.visibility_km:.1f} km)' if current.visibility_km is not None else ''}."
                ),
                recommendation="Usá luces bajas/antiniebla al conducir y reducí la velocidad en tramos de ruta.",
                metric_value=f"{current.visibility_km:.1f} km" if current.visibility_km is not None else "Niebla",
                source="Análisis de Visibilidad",
                starts_at=now_iso,
                ends_at=end_48h,
            )
        )

    return alerts
