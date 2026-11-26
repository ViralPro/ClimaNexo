"""Motor Meteorológico Inteligente: validación física, ponderación por frescura/precisión real y detección de discrepancias."""

from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import settings
from app.db.repository import WeatherRepository
from app.models.schemas import CurrentWeather, DailyPoint, HourlyPoint, LocationModel, ProviderSnapshot
from app.providers.base import compute_apparent_temp_c, compute_dew_point_c


@dataclass
class EvaluatedSource:
    snapshot: ProviderSnapshot
    is_valid: bool
    rejection_reason: Optional[str] = None
    freshness_minutes: int = 0
    freshness_factor: float = 1.0
    health_factor: float = 0.95
    accuracy_factor: float = 1.0
    agreement_factor: float = 1.0
    total_weight: float = 1.0
    historical_accuracy_pct: Optional[float] = None
    historical_samples: int = 0
    accuracy_scope: str = "sin_historial"  # location | global | sin_historial


@dataclass
class SmartEvaluationResult:
    selected_current: CurrentWeather
    selected_hourly: list[HourlyPoint]
    selected_daily: list[DailyPoint]
    primary_provider_id: str
    primary_provider_name: str
    evaluated_sources: list[EvaluatedSource]
    consensus: dict[str, Any]
    discrepancy: dict[str, Any]
    provenance: dict[str, Any]
    confidence: dict[str, Any]
    content_hash: str
    rejected_count: int = 0


def validate_current_reading(cur: CurrentWeather) -> tuple[bool, Optional[str]]:
    """Valida rangos físicos meteorológicos reales para descartar lecturas corruptas."""
    if cur.temperature_c is None:
        return False, "Sin temperatura registrada"
    if not (-90.0 <= cur.temperature_c <= 65.0):
        return False, f"Temperatura físicamente inválida ({cur.temperature_c} °C)"
    if cur.humidity_pct is not None and not (0.0 <= cur.humidity_pct <= 100.0):
        return False, f"Humedad fuera de rango ({cur.humidity_pct}%)"
    if cur.pressure_hpa is not None and not (850.0 <= cur.pressure_hpa <= 1090.0):
        return False, f"Presión barométrica fuera de rango ({cur.pressure_hpa} hPa)"
    if cur.wind_speed_kmh is not None and not (0.0 <= cur.wind_speed_kmh <= 400.0):
        return False, f"Viento fuera de rango ({cur.wind_speed_kmh} km/h)"
    if cur.precipitation_mm is not None and cur.precipitation_mm < 0.0:
        return False, f"Precipitación negativa ({cur.precipitation_mm} mm)"
    return True, None


def compute_age_minutes(observed_at: str) -> int:
    """Calcula la antigüedad real de una lectura respecto a UTC actual."""
    if not observed_at:
        return 60
    s = observed_at.strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return 60
    now_utc = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        # Si viene en hora local sin offset (ej. Open-Meteo timezone=auto), acotamos conservadoramente
        # comparando sólo contra minutos recientes para no penalizar injustamente husos horarios
        return 15
    delta_min = abs((now_utc - dt.astimezone(timezone.utc)).total_seconds()) / 60.0
    return int(round(min(delta_min, 1440)))


def freshness_score_from_minutes(age_min: int) -> float:
    if age_min <= 30:
        return 1.0
    if age_min <= 75:
        return 0.92
    if age_min <= 150:
        return 0.80
    if age_min <= 360:
        return 0.62
    return 0.40


class SmartMeteorologicalEngine:
    """
    Motor determinístico de selección meteorológica confiable:
    - Valida rangos físicos reales.
    - Pondera cada proveedor según actualidad, historial real de precisión en SQLite (ubicación -> global),
      estabilidad operativa y consistencia entre fuentes independientes.
    - Detecta y explica discrepancias reales sin ocultar diferencias ni inventar niveles de confianza.
    """

    def __init__(self, repo: WeatherRepository) -> None:
        self.repo = repo

    def evaluate(
        self,
        location: LocationModel,
        snapshots: list[ProviderSnapshot],
    ) -> SmartEvaluationResult:
        # 1. Cargar estadísticas reales de precisión (por ubicación y global) + salud operativa desde SQLite
        loc_agg = (
            self.repo.get_accuracy_aggregates(location_id=location.id)["stats_by_provider"]
            if location.id is not None
            else {}
        )
        global_agg = self.repo.get_accuracy_aggregates(location_id=None)["stats_by_provider"]
        health_map = {p["provider_id"]: p for p in self.repo.list_providers()}

        evaluated: list[EvaluatedSource] = []
        rejected_count = 0

        for snap in snapshots:
            if snap.status != "ok" or snap.current is None:
                continue
            ok, reason = validate_current_reading(snap.current)
            if not ok:
                rejected_count += 1
                evaluated.append(EvaluatedSource(snapshot=snap, is_valid=False, rejection_reason=reason))
                continue

            age_min = compute_age_minutes(snap.current.observed_at)
            f_factor = freshness_score_from_minutes(age_min)

            # Calidad operativa real del proveedor (tasa de éxito histórica en SQLite)
            h_row = health_map.get(snap.provider_id, {})
            sc = int(h_row.get("success_count") or 0)
            ec = int(h_row.get("error_count") or 0)
            if sc + ec >= 3:
                h_factor = round(max(0.55, min(1.0, sc / (sc + ec))), 3)
            else:
                h_factor = 0.95

            # Precisión histórica REAL verificada en SQLite (ubicación prioritaria, luego global)
            loc_st = loc_agg.get(snap.provider_id)
            glob_st = global_agg.get(snap.provider_id)
            hist_acc: Optional[float] = None
            hist_n = 0
            acc_scope = "sin_historial"
            if loc_st and int(loc_st["sample_count"]) >= 6 and loc_st["avg_composite"] is not None:
                hist_acc = round(float(loc_st["avg_composite"]), 1)
                hist_n = int(loc_st["sample_count"])
                acc_scope = "location"
            elif glob_st and int(glob_st["sample_count"]) >= 6 and glob_st["avg_composite"] is not None:
                hist_acc = round(float(glob_st["avg_composite"]), 1)
                hist_n = int(glob_st["sample_count"])
                acc_scope = "global"

            if hist_acc is not None:
                # 95% -> 1.45, 85% -> 1.35, 60% -> 1.10, 40% -> 0.90
                a_factor = round(max(0.55, min(1.5, 0.50 + (hist_acc / 100.0))), 3)
            else:
                # Neutro y honesto cuando aún no hay muestras maduras
                a_factor = 1.0

            evaluated.append(
                EvaluatedSource(
                    snapshot=snap,
                    is_valid=True,
                    freshness_minutes=age_min,
                    freshness_factor=f_factor,
                    health_factor=h_factor,
                    accuracy_factor=a_factor,
                    historical_accuracy_pct=hist_acc,
                    historical_samples=hist_n,
                    accuracy_scope=acc_scope,
                )
            )

        valid_sources = [e for e in evaluated if e.is_valid]
        if not valid_sources:
            raise RuntimeError("Ningún proveedor devolvió lecturas meteorológicas válidas.")

        # 2. Resistencia a valores atípicos basada en la mediana (sin votar por mayoría simple)
        temps = [e.snapshot.current.temperature_c for e in valid_sources if e.snapshot.current and e.snapshot.current.temperature_c is not None]
        median_temp = statistics.median(temps)
        mean_temp = round(statistics.mean(temps), 1)
        std_temp = round(statistics.pstdev(temps), 2) if len(temps) >= 2 else 0.0
        min_temp = round(min(temps), 1)
        max_temp = round(max(temps), 1)
        spread_temp = round(max_temp - min_temp, 1) if len(temps) >= 2 else 0.0

        for e in valid_sources:
            t_val = e.snapshot.current.temperature_c
            assert t_val is not None
            dev = abs(t_val - median_temp)
            if len(valid_sources) >= 4 and dev >= 4.0:
                e.agreement_factor = 0.55
            elif len(valid_sources) >= 3 and dev >= 2.5:
                e.agreement_factor = 0.80
            else:
                e.agreement_factor = 1.0
            e.total_weight = round(
                e.freshness_factor * e.health_factor * e.accuracy_factor * e.agreement_factor,
                3,
            )

        # 3. Seleccionar fuente primaria mejor respaldada y sintetizar valores ponderados
        valid_sources.sort(
            key=lambda e: (
                e.total_weight,
                1 if e.snapshot.provider_id == "open_meteo" else 0,
                len(e.snapshot.hourly),
            ),
            reverse=True,
        )
        primary_eval = valid_sources[0]
        primary_snap = primary_eval.snapshot

        # Para series horarias y diarias completas (UV, amanecer, prob. lluvia, 14-16d), preferimos
        # la fuente más completa disponible (normalmente Open-Meteo) si la primaria no trae todos los campos
        rich_series_snap = max(
            [e.snapshot for e in valid_sources],
            key=lambda s: (len(s.daily), len(s.hourly), 1 if s.provider_id == "open_meteo" else 0),
        )

        selected_temp = _weighted_avg(
            [(e.snapshot.current.temperature_c, e.total_weight) for e in valid_sources]
        )
        selected_hum = _weighted_avg(
            [(e.snapshot.current.humidity_pct, e.total_weight) for e in valid_sources], digits=0
        )
        selected_pres = _weighted_avg(
            [(e.snapshot.current.pressure_hpa, e.total_weight) for e in valid_sources], digits=1
        )
        selected_wind = _weighted_avg(
            [(e.snapshot.current.wind_speed_kmh, e.total_weight) for e in valid_sources], digits=1
        )
        selected_precip = _weighted_avg(
            [(e.snapshot.current.precipitation_mm, e.total_weight) for e in valid_sources], digits=2
        )
        selected_feels = _weighted_avg(
            [(e.snapshot.current.feels_like_c, e.total_weight) for e in valid_sources], digits=1
        )
        if selected_feels is None:
            selected_feels = compute_apparent_temp_c(selected_temp, selected_hum, selected_wind)

        base_cur = rich_series_snap.current or primary_snap.current
        assert base_cur is not None
        selected_current = base_cur.model_copy(
            update={
                "temperature_c": selected_temp if selected_temp is not None else base_cur.temperature_c,
                "feels_like_c": selected_feels if selected_feels is not None else base_cur.feels_like_c,
                "humidity_pct": selected_hum if selected_hum is not None else base_cur.humidity_pct,
                "dew_point_c": compute_dew_point_c(selected_temp, selected_hum) or base_cur.dew_point_c,
                "pressure_hpa": selected_pres if selected_pres is not None else base_cur.pressure_hpa,
                "wind_speed_kmh": selected_wind if selected_wind is not None else base_cur.wind_speed_kmh,
                "precipitation_mm": selected_precip if selected_precip is not None else base_cur.precipitation_mm,
            }
        )

        # 4. Análisis transparente de discrepancias entre fuentes
        min_eval = min(valid_sources, key=lambda e: e.snapshot.current.temperature_c or 999)
        max_eval = max(valid_sources, key=lambda e: e.snapshot.current.temperature_c or -999)
        winds = [e.snapshot.current.wind_speed_kmh for e in valid_sources if e.snapshot.current.wind_speed_kmh is not None]
        wind_spread = round(max(winds) - min(winds), 1) if len(winds) >= 2 else 0.0
        rain_sources = [e.snapshot.display_name for e in valid_sources if (e.snapshot.current.precipitation_mm or 0.0) >= 0.2]
        dry_sources = [e.snapshot.display_name for e in valid_sources if (e.snapshot.current.precipitation_mm or 0.0) < 0.1]
        precip_disagreement = bool(rain_sources and dry_sources)

        has_temp_disc = spread_temp >= settings.discrepancy_temp_threshold_c
        has_wind_disc = wind_spread >= settings.discrepancy_wind_threshold_kmh
        has_significant = has_temp_disc or precip_disagreement or has_wind_disc

        if spread_temp >= 3.5 or (has_temp_disc and precip_disagreement):
            disc_severity = "alta"
        elif has_significant:
            disc_severity = "moderada"
        else:
            disc_severity = "baja"

        disc_notes: list[str] = []
        if has_temp_disc:
            disc_notes.append(
                f"Diferencia térmica de {spread_temp:.1f} °C entre {min_eval.snapshot.display_name} "
                f"({min_eval.snapshot.current.temperature_c:.1f} °C) y {max_eval.snapshot.display_name} "
                f"({max_eval.snapshot.current.temperature_c:.1f} °C)"
            )
        if precip_disagreement:
            r_list = rain_sources[:2]
            d_list = dry_sources[:2]
            r_verb = "reporta" if len(r_list) == 1 else "reportan"
            d_verb = "indica" if len(d_list) == 1 else "indican"
            disc_notes.append(
                f"Divergencia en lluvia actual: {', '.join(r_list)} {r_verb} precipitación "
                f"mientras {', '.join(d_list)} {d_verb} tiempo seco"
            )
        if has_wind_disc:
            disc_notes.append(f"Diferencia de viento entre fuentes de {wind_spread:.0f} km/h")

        disc_summary = (
            ". ".join(disc_notes) + "."
            if disc_notes
            else f"Alta concordancia entre las {len(valid_sources)} fuentes activas (diferencia térmica de {spread_temp:.1f} °C)."
        )

        discrepancy = {
            "has_significant_diff": has_significant,
            "severity": disc_severity,
            "temp_spread_c": spread_temp,
            "wind_spread_kmh": wind_spread,
            "precip_disagreement": precip_disagreement,
            "min_source": {
                "provider_id": min_eval.snapshot.provider_id,
                "name": min_eval.snapshot.display_name,
                "temp_c": min_eval.snapshot.current.temperature_c,
            },
            "max_source": {
                "provider_id": max_eval.snapshot.provider_id,
                "name": max_eval.snapshot.display_name,
                "temp_c": max_eval.snapshot.current.temperature_c,
            },
            "summary": disc_summary,
        }

        # 5. Procedencia transparente y respaldo real (sin inventar porcentajes)
        total_real_samples = sum(e.historical_samples for e in valid_sources)
        verified_providers_count = sum(1 for e in valid_sources if e.historical_samples > 0)
        if verified_providers_count > 0:
            backing_note = (
                f"Ponderado con {total_real_samples} verificaciones reales en SQLite "
                f"({verified_providers_count} de {len(valid_sources)} fuentes con historial verificado)"
            )
        else:
            backing_note = (
                f"Evaluado por frescura y concordancia entre {len(valid_sources)} fuentes reales "
                "(historial de precisión en recopilación)"
            )

        provenance = {
            "method": "Selección ponderada por frescura, precisión histórica real y concordancia",
            "primary_provider_id": primary_snap.provider_id,
            "primary_provider_name": primary_snap.display_name,
            "primary_accuracy_pct": primary_eval.historical_accuracy_pct,
            "primary_samples": primary_eval.historical_samples,
            "sources_used": [
                {
                    "provider_id": e.snapshot.provider_id,
                    "name": e.snapshot.display_name,
                    "temp_c": e.snapshot.current.temperature_c,
                    "observed_at": e.snapshot.current.observed_at,
                    "age_minutes": e.freshness_minutes,
                    "weight": e.total_weight,
                    "historical_accuracy_pct": e.historical_accuracy_pct,
                    "historical_samples": e.historical_samples,
                }
                for e in valid_sources
            ],
            "sources_count": len(valid_sources),
            "rejected_readings": rejected_count,
            "accuracy_backing": backing_note,
        }

        consensus = {
            "selected_temp_c": selected_current.temperature_c,
            "temp_mean_c": mean_temp,
            "temp_median_c": round(float(median_temp), 1),
            "temp_min_c": min_temp,
            "temp_max_c": max_temp,
            "temp_std_c": std_temp,
            "temp_spread_c": spread_temp,
            "feels_like_mean_c": selected_current.feels_like_c,
            "humidity_mean_pct": selected_current.humidity_pct,
            "pressure_mean_hpa": selected_current.pressure_hpa,
            "wind_mean_kmh": selected_current.wind_speed_kmh,
            "precip_mean_mm": selected_current.precipitation_mm,
            "agreement_level": (
                "Alto" if disc_severity == "baja" else ("Moderado" if disc_severity == "moderada" else "Divergente")
            ),
            "active_sources": len(valid_sources),
            "primary_provider_id": primary_snap.provider_id,
            "primary_provider_name": primary_snap.display_name,
        }

        confidence = compute_forecast_confidence(
            valid_sources=valid_sources,
            total_expected_sources=max(len(snapshots), len(valid_sources)),
            temp_spread_c=spread_temp,
            temp_std_c=std_temp,
            wind_spread_kmh=wind_spread,
            precip_disagreement=precip_disagreement,
        )

        # Huella criptográfica para detectar cambios meteorológicos reales y evitar escrituras redundantes
        fp_obj = {
            "t": selected_current.temperature_c,
            "h": selected_current.humidity_pct,
            "p": selected_current.pressure_hpa,
            "w": selected_current.wind_speed_kmh,
            "r": selected_current.precipitation_mm,
            "c": selected_current.weather_code,
            "d": [(d.date, d.temp_min_c, d.temp_max_c, d.precipitation_sum_mm) for d in rich_series_snap.daily[:7]],
        }
        content_hash = hashlib.sha256(json.dumps(fp_obj, sort_keys=True).encode("utf-8")).hexdigest()[:24]

        return SmartEvaluationResult(
            selected_current=selected_current,
            selected_hourly=rich_series_snap.hourly[:72],
            selected_daily=rich_series_snap.daily,
            primary_provider_id=primary_snap.provider_id,
            primary_provider_name=primary_snap.display_name,
            evaluated_sources=evaluated,
            consensus=consensus,
            discrepancy=discrepancy,
            provenance=provenance,
            confidence=confidence,
            content_hash=content_hash,
            rejected_count=rejected_count,
        )


def compute_forecast_confidence(
    valid_sources: list[EvaluatedSource],
    total_expected_sources: int,
    temp_spread_c: float,
    temp_std_c: float,
    wind_spread_kmh: float,
    precip_disagreement: bool,
) -> dict[str, Any]:
    """
    Calcula el porcentaje REAL de confianza del pronóstico combinando criterios objetivos:
    - Coincidencia térmica entre modelos (desviación estándar y dispersión real).
    - Consenso de precipitación y viento.
    - Antigüedad / frescura temporal de las observaciones.
    - Disponibilidad de fuentes válidas independientes.
    - Precisión histórica verificada real (solo si hay muestras reales suficientes; nunca inventada).
    """
    n_valid = len(valid_sources)
    if n_valid < 2:
        return {
            "available": False,
            "score_pct": None,
            "level": "No disponible",
            "reason": "Se requieren al menos 2 fuentes meteorológicas válidas para calcular la confianza estadística.",
            "summary": "Confianza no disponible por insuficiencia de fuentes independientes.",
            "factors": {},
        }

    # 1. Coincidencia térmica entre modelos (0–100)
    thermal_agreement = max(25.0, min(100.0, 100.0 - (temp_spread_c * 8.5 + temp_std_c * 9.5)))

    # 2. Consenso de precipitación y viento (0–100)
    rain_penalty = 24.0 if precip_disagreement else 0.0
    wind_penalty = min(22.0, max(0.0, (wind_spread_kmh - 8.0) * 1.1))
    precip_wind_consensus = max(35.0, min(100.0, 100.0 - rain_penalty - wind_penalty))

    # 3. Frescura real de los datos (0–100)
    freshness_pct = sum(e.freshness_factor for e in valid_sources) / n_valid * 100.0

    # 4. Disponibilidad de información (2 fuentes -> 65%, 4 -> 88%, 6+ -> 100%)
    target_sources = max(4, min(7, total_expected_sources))
    availability_pct = min(100.0, (n_valid / target_sources) * 100.0)

    # 5. Precisión histórica real (únicamente si hay >= 12 muestras verificadas)
    verified = [e for e in valid_sources if e.historical_accuracy_pct is not None and e.historical_samples >= 6]
    total_samples = sum(e.historical_samples for e in verified)
    has_historical = total_samples >= 12 and len(verified) >= 1

    if has_historical:
        hist_pct = sum(float(e.historical_accuracy_pct or 0.0) * e.historical_samples for e in verified) / total_samples
        raw_score = (
            thermal_agreement * 0.36
            + precip_wind_consensus * 0.18
            + freshness_pct * 0.16
            + availability_pct * 0.14
            + hist_pct * 0.16
        )
    else:
        hist_pct = None
        raw_score = (
            thermal_agreement * 0.44
            + precip_wind_consensus * 0.22
            + freshness_pct * 0.18
            + availability_pct * 0.16
        )

    score = int(round(max(20.0, min(98.0, raw_score))))
    if score >= 85:
        level = "Alta"
    elif score >= 70:
        level = "Buena"
    elif score >= 55:
        level = "Moderada"
    else:
        level = "Baja"

    summary = (
        f"Confianza {level.lower()} ({score} %) basada en {n_valid} fuentes independientes "
        f"(dispersión térmica ±{temp_std_c:.1f} °C, rango {temp_spread_c:.1f} °C"
        + (f", respaldo de {total_samples} verificaciones reales)." if has_historical else ").")
    )

    return {
        "available": True,
        "score_pct": score,
        "level": level,
        "summary": summary,
        "sources_used": n_valid,
        "verified_samples": total_samples,
        "factors": {
            "model_agreement_pct": round(thermal_agreement, 1),
            "precip_consensus_pct": round(precip_wind_consensus, 1),
            "freshness_pct": round(freshness_pct, 1),
            "availability_pct": round(availability_pct, 1),
            "historical_backing_pct": round(hist_pct, 1) if hist_pct is not None else None,
        },
    }


def _weighted_avg(pairs: list[tuple[Optional[float], float]], digits: int = 1) -> Optional[float]:
    num = 0.0
    den = 0.0
    for val, weight in pairs:
        if val is None or weight <= 0:
            continue
        num += float(val) * weight
        den += weight
    if den <= 0:
        return None
    return round(num / den, digits)
