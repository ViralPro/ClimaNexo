"""Analizador determinístico de consultas meteorológicas en español (sin IA ni LLM)."""

from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

MONTHS_ES: dict[str, int] = {
    "enero": 1, "ene": 1, "january": 1,
    "febrero": 2, "feb": 2, "february": 2,
    "marzo": 3, "mar": 3, "march": 3,
    "abril": 4, "abr": 4, "april": 4,
    "mayo": 5, "may": 5,
    "junio": 6, "jun": 6, "june": 6,
    "julio": 7, "jul": 7, "july": 7,
    "agosto": 8, "ago": 8, "august": 8,
    "septiembre": 9, "setiembre": 9, "sep": 9, "sept": 9, "september": 9,
    "octubre": 10, "oct": 10, "october": 10,
    "noviembre": 11, "nov": 11, "november": 11,
    "diciembre": 12, "dic": 12, "december": 12,
}

MONTH_NAMES_ES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


@dataclass
class ParsedWeatherQuery:
    raw_query: str
    intent: str = "forecast"  # forecast | history | multi_city
    cities: list[str] = field(default_factory=list)
    focus_metric: str = "general"  # general | rain | wind | temperature | humidity | pressure | snow
    forecast_days: int = 7
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    date_focus_label: str = "Próximos 7 días"
    is_historical: bool = False


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def format_date_range_es(start_iso: str, end_iso: str) -> str:
    try:
        d1 = date.fromisoformat(start_iso)
        d2 = date.fromisoformat(end_iso)
    except ValueError:
        return f"{start_iso} — {end_iso}"
    if d1 == d2:
        return f"{d1.day} de {MONTH_NAMES_ES[d1.month]} de {d1.year}"
    if d1.day == 1 and d2.day == calendar.monthrange(d2.year, d2.month)[1] and d1.month == d2.month and d1.year == d2.year:
        return f"{MONTH_NAMES_ES[d1.month].capitalize()} {d1.year}"
    if d1.year == d2.year:
        return f"{d1.day} {MONTH_NAMES_ES[d1.month][:3]} — {d2.day} {MONTH_NAMES_ES[d2.month][:3]} {d2.year}"
    return f"{d1.day} {MONTH_NAMES_ES[d1.month][:3]} {d1.year} — {d2.day} {MONTH_NAMES_ES[d2.month][:3]} {d2.year}"


def parse_weather_query(query: str, today: Optional[date] = None) -> ParsedWeatherQuery:
    """Interpreta consultas en español mediante reglas determinísticas (regex y fechas)."""
    ref_today = today or date.today()
    raw = query.strip()
    norm = _strip_accents(raw.lower())
    result = ParsedWeatherQuery(raw_query=raw)

    # 1. Detectar métrica de enfoque
    if any(w in norm for w in ("lluvia", "llovio", "llover", "precipitacion", "aguacero", "rain", "mm")):
        result.focus_metric = "rain"
    elif any(w in norm for w in ("viento", "ventoso", "rafaga", "wind", "brisa")):
        result.focus_metric = "wind"
    elif any(w in norm for w in ("nieve", "nevada", "snow", "helada")):
        result.focus_metric = "snow"
    elif any(w in norm for w in ("humedad", "rocio", "humidity")):
        result.focus_metric = "humidity"
    elif any(w in norm for w in ("presion", "barometr", "hpa", "pressure")):
        result.focus_metric = "pressure"
    elif any(w in norm for w in ("temperatura", "calor", "frio", "maxima", "minima", "temp")):
        result.focus_metric = "temperature"

    # 2. Detectar rango de fechas (histórico o pronóstico)
    cleaned_for_cities = raw

    # Rango explícito YYYY-MM-DD a YYYY-MM-DD
    m_iso = re.search(r"(\d{4}-\d{2}-\d{2})\s*(?:a|al|hasta|to|-|—)\s*(\d{4}-\d{2}-\d{2})", norm)
    if m_iso:
        d1, d2 = m_iso.group(1), m_iso.group(2)
        result.start_date, result.end_date = d1, d2
        result.date_focus_label = format_date_range_es(d1, d2)
        result.is_historical = date.fromisoformat(d2) < ref_today
        cleaned_for_cities = re.sub(r"\d{4}-\d{2}-\d{2}\s*(?:a|al|hasta|to|-|—)\s*\d{4}-\d{2}-\d{2}", " ", cleaned_for_cities, flags=re.I)
    elif any(p in norm for p in ("mes pasado", "ultimo mes", "last month", "el mes anterior")):
        first_this = ref_today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        first_prev = last_prev.replace(day=1)
        result.start_date = first_prev.isoformat()
        result.end_date = last_prev.isoformat()
        result.is_historical = True
        result.date_focus_label = format_date_range_es(result.start_date, result.end_date)
        cleaned_for_cities = re.sub(r"\b(el\s+)?(mes\s+pasado|ultimo\s+mes|último\s+mes|last\s+month|el\s+mes\s+anterior)\b", " ", cleaned_for_cities, flags=re.I)
    elif any(p in norm for p in ("ultima semana", "semana pasada", "last week")):
        d2 = ref_today - timedelta(days=1)
        d1 = d2 - timedelta(days=6)
        result.start_date, result.end_date = d1.isoformat(), d2.isoformat()
        result.is_historical = True
        result.date_focus_label = format_date_range_es(result.start_date, result.end_date)
        cleaned_for_cities = re.sub(r"\b(la\s+)?(ultima\s+semana|última\s+semana|semana\s+pasada|last\s+week)\b", " ", cleaned_for_cities, flags=re.I)
    elif any(p in norm for p in ("este ano", "en lo que va del ano", "this year")):
        d1 = date(ref_today.year, 1, 1)
        d2 = max(d1, ref_today - timedelta(days=1))
        result.start_date, result.end_date = d1.isoformat(), d2.isoformat()
        result.is_historical = True
        result.date_focus_label = format_date_range_es(result.start_date, result.end_date)
        cleaned_for_cities = re.sub(r"\b(este\s+año|este\s+ano|en\s+lo\s+que\s+va\s+del\s+año|this\s+year)\b", " ", cleaned_for_cities, flags=re.I)
    elif m_past := re.search(r"(?:ultimos?|pasados?)\s+(\d{1,3})\s+dias?", norm):
        n = max(1, min(365, int(m_past.group(1))))
        d2 = ref_today - timedelta(days=1)
        d1 = d2 - timedelta(days=n - 1)
        result.start_date, result.end_date = d1.isoformat(), d2.isoformat()
        result.is_historical = True
        result.date_focus_label = format_date_range_es(result.start_date, result.end_date)
        cleaned_for_cities = re.sub(r"\b(?:en\s+los\s+)?(?:ultimos?|últimos?|pasados?)\s+\d{1,3}\s+d[ií]as?\b", " ", cleaned_for_cities, flags=re.I)
    elif m_fc := re.search(r"(?:proximos?|siguientes?|en|de|a)?\s*(\d{1,2})\s+dias?", norm):
        n = max(1, min(16, int(m_fc.group(1))))
        result.forecast_days = n
        d1 = ref_today
        d2 = ref_today + timedelta(days=n - 1)
        result.start_date, result.end_date = d1.isoformat(), d2.isoformat()
        result.date_focus_label = format_date_range_es(result.start_date, result.end_date)
        cleaned_for_cities = re.sub(r"\b(?:para\s+los\s+)?(?:proximos?|próximos?|siguientes?|de|a)?\s*\d{1,2}\s+d[ií]as?\b", " ", cleaned_for_cities, flags=re.I)
    elif re.search(r"\b(hoy|ahora|actual|tiempo\s+real)\b", norm):
        result.forecast_days = 7
        result.start_date = ref_today.isoformat()
        result.end_date = (ref_today + timedelta(days=6)).isoformat()
        result.date_focus_label = f"Hoy · {format_date_range_es(result.start_date, result.end_date)}"
        cleaned_for_cities = re.sub(r"\b(hoy|ahora|actual|en\s+tiempo\s+real)\b", " ", cleaned_for_cities, flags=re.I)
    else:
        # Detectar nombre de mes (ej. "octubre 2026" o "en septiembre")
        month_match = None
        for m_name, m_num in MONTHS_ES.items():
            pat = rf"\b(?:en\s+|durante\s+|de\s+)?{m_name}(?:\s+(?:de\s+)?(20\d{{2}}))?\b"
            mm = re.search(pat, norm)
            if mm:
                year = int(mm.group(1)) if mm.group(1) else (
                    ref_today.year if m_num <= ref_today.month else ref_today.year - 1
                )
                last_day = calendar.monthrange(year, m_num)[1]
                d1 = date(year, m_num, 1)
                d2 = min(date(year, m_num, last_day), ref_today - timedelta(days=1))
                if d2 >= d1:
                    result.start_date = d1.isoformat()
                    result.end_date = d2.isoformat()
                    result.is_historical = True
                    result.date_focus_label = format_date_range_es(result.start_date, result.end_date)
                    cleaned_for_cities = re.sub(pat, " ", _strip_accents(cleaned_for_cities), flags=re.I)
                    month_match = True
                    break
        if not month_match:
            result.forecast_days = 7
            d1 = ref_today
            d2 = ref_today + timedelta(days=6)
            result.start_date, result.end_date = d1.isoformat(), d2.isoformat()
            result.date_focus_label = format_date_range_es(result.start_date, result.end_date)

    # 3. Extraer ciudades (una o varias para comparación)
    is_compare = any(
        w in norm for w in ("comparar", "compara", "comparacion", "compare", " vs ", "contra", "entre ")
    )
    cities = _extract_cities(cleaned_for_cities, is_compare=is_compare)
    result.cities = cities

    if len(cities) >= 2:
        result.intent = "multi_city"
    elif result.is_historical:
        result.intent = "history"
    else:
        result.intent = "forecast"

    return result


def _extract_cities(text: str, is_compare: bool) -> list[str]:
    """Extrae uno o múltiples nombres de ciudades de una frase en español."""
    t = re.sub(r"[¿?¡!]", " ", text).strip()

    # Remover frases introductorias comunes
    prefix_patterns = [
        r"^(?:comparar|compara|comparaci[oó]n|compare)\s+(?:el\s+)?(?:clima|tiempo|temperatura|lluvia|viento|pron[oó]stico|weather)?\s*(?:en|de|entre)?\s*",
        r"^(?:cu[aá]nto|cuanta|cu[aá]nta)\s+(?:llovi[oó]|lluvia|nieve|nev[oó]|viento)\s+(?:hubo|cay[oó]|tuvo|recibi[oó]|en)?\s*(?:en|de)?\s*",
        r"^(?:cu[aá]l\s+fue\s+el\s+d[ií]a\s+m[aá]s\s+\w+\s+(?:en|de)\s+)",
        r"^(?:c[oó]mo\s+(?:est[aá]|estar[aá]|va\s+a\s+estar)\s+(?:el\s+)?(?:clima|tiempo|lluvia|viento)?\s*(?:en|de)?\s*)",
        r"^(?:ver|mostrar|mu[eé]strame|dame|consultar|consulta|buscar)\s+(?:el\s+)?(?:clima|tiempo|pron[oó]stico|historial|viento|lluvia|temperatura|humedad|presi[oó]n)?\s*(?:extendido|por\s+horas|actual|hist[oó]rico)?\s*(?:en|de)?\s*",
        r"^(?:el\s+)?(?:clima|tiempo|pron[oó]stico|historial|lluvia|viento|temperatura|humedad|presi[oó]n|nieve|weather)\s*(?:extendido|actual|por\s+horas|hist[oó]rico)?\s*(?:en|de|para)\s+",
    ]
    for pat in prefix_patterns:
        t = re.sub(pat, "", t, flags=re.I).strip()

    # Remover sufijos temporales o descriptivos residuales
    suffix_patterns = [
        r"\b(?:durante|para|en\s+los|en\s+la|con\s+lluvia|y\s+pron[oó]stico.*|y\s+viento.*|y\s+lluvia.*)\s*$",
        r"\b(?:clima|tiempo|pron[oó]stico|historial|extendido|por\s+horas)\s*$",
    ]
    for pat in suffix_patterns:
        t = re.sub(pat, "", t, flags=re.I).strip()

    # Separar múltiples ciudades si hay comas, " y ", " vs ", " and ", " / "
    if is_compare or re.search(r",|\s+vs\.?\s+|\s+contra\s+|\s+y\s+|\s+and\s+", t, flags=re.I):
        parts = re.split(r"\s*(?:,|;|\bvs\.?\b|\bcontra\b|\by\b|\band\b|/)\s*", t, flags=re.I)
    else:
        # También detectar patrón "en <Ciudad>" dentro de una frase más larga
        m_en = re.search(r"\b(?:en|de)\s+([A-ZÁÉÍÓÚÑa-záéíóúñ][A-ZÁÉÍÓÚÑa-záéíóúñ\s\.-]{1,45})$", t)
        if m_en and len(t.split()) > 3:
            parts = [m_en.group(1)]
        else:
            parts = [t]

    stop_words = {
        "clima", "tiempo", "pronostico", "historial", "lluvia", "viento", "temperatura",
        "humedad", "presion", "nieve", "hoy", "manana", "ayer", "semana", "mes", "ano",
        "el", "la", "los", "las", "en", "de", "del", "para", "actual", "extendido",
        "comparar", "compara", "weather", "forecast", "mi ubicacion", "aqui", "aca",
    }

    cities: list[str] = []
    for p in parts:
        clean = re.sub(r"^(?:en|de|para|el|la)\s+", "", p.strip(" .,-"), flags=re.I).strip()
        clean = re.sub(r"\s+", " ", clean)
        if len(clean) < 2:
            continue
        if _strip_accents(clean.lower()) in stop_words:
            continue
        if clean not in cities:
            cities.append(clean)

    return cities[:5]
