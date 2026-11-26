"""Esquemas Pydantic v2 estrictos para contratos de datos y API REST."""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field


class LocationModel(BaseModel):
    id: Optional[int] = None
    name: str
    admin1: str = ""
    country: str = ""
    country_code: str = ""
    latitude: float
    longitude: float
    timezone: str = "auto"
    elevation: Optional[float] = None
    is_favorite: bool = False
    display_label: str = ""

    def formatted_label(self) -> str:
        if self.display_label:
            return self.display_label
        parts = [self.name]
        if self.admin1 and self.admin1.lower() != self.name.lower():
            parts.append(self.admin1)
        if self.country:
            parts.append(self.country)
        return ", ".join(parts)


class CurrentWeather(BaseModel):
    observed_at: str
    temperature_c: Optional[float] = None
    feels_like_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    dew_point_c: Optional[float] = None
    pressure_hpa: Optional[float] = None
    pressure_trend: str = "estable"
    wind_speed_kmh: Optional[float] = None
    wind_gusts_kmh: Optional[float] = None
    wind_direction_deg: Optional[float] = None
    wind_direction_cardinal: str = "—"
    precipitation_mm: Optional[float] = 0.0
    rain_mm: Optional[float] = 0.0
    snowfall_cm: Optional[float] = 0.0
    precip_probability_pct: Optional[float] = None
    cloud_cover_pct: Optional[float] = None
    visibility_km: Optional[float] = None
    uv_index: Optional[float] = None
    is_day: bool = True
    weather_code: int = 0
    condition_text: str = "Despejado"
    condition_icon: str = "sun"
    theme: str = "clear"


class HourlyPoint(BaseModel):
    time: str
    temperature_c: Optional[float] = None
    feels_like_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    pressure_hpa: Optional[float] = None
    wind_speed_kmh: Optional[float] = None
    wind_gusts_kmh: Optional[float] = None
    wind_direction_deg: Optional[float] = None
    precipitation_mm: Optional[float] = 0.0
    precip_probability_pct: Optional[float] = None
    cloud_cover_pct: Optional[float] = None
    uv_index: Optional[float] = None
    weather_code: int = 0
    condition_text: str = "Despejado"
    condition_icon: str = "sun"
    is_day: bool = True


class DailyPoint(BaseModel):
    date: str
    temp_max_c: Optional[float] = None
    temp_min_c: Optional[float] = None
    temp_mean_c: Optional[float] = None
    feels_like_max_c: Optional[float] = None
    feels_like_min_c: Optional[float] = None
    precipitation_sum_mm: Optional[float] = 0.0
    precip_probability_max_pct: Optional[float] = None
    wind_speed_max_kmh: Optional[float] = None
    wind_gusts_max_kmh: Optional[float] = None
    wind_direction_dominant_deg: Optional[float] = None
    uv_index_max: Optional[float] = None
    sunrise: Optional[str] = None
    sunset: Optional[str] = None
    weather_code: int = 0
    condition_text: str = "Despejado"
    condition_icon: str = "sun"


class WeatherAlertModel(BaseModel):
    id: Optional[int] = None
    alert_key: str
    category: str
    severity: str  # info | precaucion | alerta | extrema
    title: str
    description: str
    recommendation: str
    metric_value: str
    source: str
    starts_at: str
    ends_at: str
    is_active: bool = True


class ProviderSnapshot(BaseModel):
    provider_id: str
    display_name: str
    status: str  # ok | error | needs_key | disabled
    latency_ms: float = 0.0
    fetched_at: str
    error_message: Optional[str] = None
    current: Optional[CurrentWeather] = None
    hourly: list[HourlyPoint] = Field(default_factory=list)
    daily: list[DailyPoint] = Field(default_factory=list)
    raw_alerts: list[WeatherAlertModel] = Field(default_factory=list)


class WeatherBundleResponse(BaseModel):
    location: LocationModel
    provider: str
    fetched_at: str
    from_cache: bool = False
    from_database: bool = False
    is_stale: bool = False
    refreshing_in_background: bool = False
    data_age_seconds: int = 0
    theme: str = "clear"
    narrative: str
    date_focus_label: str
    current: CurrentWeather
    hourly: list[HourlyPoint]
    daily: list[DailyPoint]
    alerts: list[WeatherAlertModel] = Field(default_factory=list)
    consensus_preview: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    discrepancy: dict[str, Any] = Field(default_factory=dict)
    confidence: dict[str, Any] = Field(default_factory=dict)


class ComparisonProviderRow(BaseModel):
    provider_id: str
    display_name: str
    description: str = ""
    status: str
    requires_api_key: bool = False
    has_api_key: bool = False
    latency_ms: float = 0.0
    error_message: Optional[str] = None
    observed_at: Optional[str] = None
    freshness_minutes: Optional[int] = None
    reliability_weight: Optional[float] = None
    historical_accuracy_pct: Optional[float] = None
    historical_samples: int = 0
    is_primary_selected: bool = False
    current_temp_c: Optional[float] = None
    feels_like_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    pressure_hpa: Optional[float] = None
    wind_kmh: Optional[float] = None
    precip_mm: Optional[float] = None
    condition_text: str = "—"
    temp_deviation_c: Optional[float] = None
    hourly_24h: list[dict[str, Any]] = Field(default_factory=list)
    daily_7d: list[dict[str, Any]] = Field(default_factory=list)


class ProviderComparisonResponse(BaseModel):
    location: LocationModel
    fetched_at: str
    from_database: bool = False
    is_stale: bool = False
    refreshing_in_background: bool = False
    active_providers: int
    total_providers: int
    consensus: dict[str, Any]
    discrepancy: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    confidence: dict[str, Any] = Field(default_factory=dict)
    providers: list[ComparisonProviderRow]
    hourly_chart: dict[str, Any]
    daily_chart: dict[str, Any]


class AccuracyProviderStat(BaseModel):
    provider_id: str
    display_name: str
    sample_count: int
    live_sample_count: int = 0
    historical_sample_count: int = 0
    has_data: bool = False
    composite_accuracy_pct: Optional[float] = None
    temp_mae_c: Optional[float] = None
    temp_rmse_c: Optional[float] = None
    temp_bias_c: Optional[float] = None
    wind_mae_kmh: Optional[float] = None
    precip_mae_mm: Optional[float] = None
    rain_hit_rate_pct: Optional[float] = None
    humidity_mae_pct: Optional[float] = None
    pressure_mae_hpa: Optional[float] = None
    status_note: str = ""


class AccuracyAuditItem(BaseModel):
    id: int
    provider_id: str
    provider_name: str
    location_name: str
    source_type: str
    target_time: str
    lead_hours: int
    pred_temp_c: Optional[float] = None
    actual_temp_c: Optional[float] = None
    temp_error_c: Optional[float] = None
    temp_abs_error_c: Optional[float] = None
    pred_wind_kmh: Optional[float] = None
    actual_wind_kmh: Optional[float] = None
    pred_precip_mm: Optional[float] = None
    actual_precip_mm: Optional[float] = None
    rain_hit: Optional[int] = None
    composite_score_pct: float
    verified_at: str


class AccuracyDashboardResponse(BaseModel):
    location: Optional[LocationModel] = None
    total_forecast_records: int
    total_observations: int
    total_verified_pairs: int
    live_verified_pairs: int
    historical_verified_pairs: int
    methodology_note: str
    ranking: list[AccuracyProviderStat]
    recent_audit: list[AccuracyAuditItem]
    last_backtest_at: Optional[str] = None


class HistoryDayPoint(BaseModel):
    date: str
    temp_max_c: Optional[float] = None
    temp_min_c: Optional[float] = None
    temp_mean_c: Optional[float] = None
    precipitation_sum_mm: Optional[float] = 0.0
    rain_sum_mm: Optional[float] = 0.0
    snowfall_sum_cm: Optional[float] = 0.0
    wind_speed_max_kmh: Optional[float] = None
    wind_gusts_max_kmh: Optional[float] = None
    weather_code: int = 0
    condition_text: str = "—"


class HistoryResponse(BaseModel):
    location: LocationModel
    start_date: str
    end_date: str
    days_count: int
    source: str
    summary: dict[str, Any]
    narrative: str
    daily: list[HistoryDayPoint]
    local_observations_count: int = 0


class CityComparisonItem(BaseModel):
    location: LocationModel
    current_temp_c: Optional[float] = None
    temp_max_c: Optional[float] = None
    temp_min_c: Optional[float] = None
    temp_avg_c: Optional[float] = None
    precip_total_mm: float = 0.0
    wind_max_kmh: Optional[float] = None
    daily: list[dict[str, Any]] = Field(default_factory=list)


class MultiCityComparisonResponse(BaseModel):
    date_focus_label: str
    start_date: str
    end_date: str
    mode: str  # forecast | historical
    theme: str
    narrative: str
    cities: list[CityComparisonItem]
    dates: list[str]
    temp_series: list[dict[str, Any]]
    precip_series: list[dict[str, Any]]
    wind_series: list[dict[str, Any]]


class DashboardQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=300)
    lat: Optional[float] = None
    lon: Optional[float] = None
    days: Optional[int] = None


class DashboardQueryResponse(BaseModel):
    mode: str  # single_forecast | single_history | multi_city
    parsed: dict[str, Any]
    theme: str
    narrative: str
    date_focus_label: str
    weather: Optional[WeatherBundleResponse] = None
    history: Optional[HistoryResponse] = None
    multi_city: Optional[MultiCityComparisonResponse] = None


class RadarMetadataResponse(BaseModel):
    host: str
    generated: int
    radar_past: list[dict[str, Any]]
    radar_nowcast: list[dict[str, Any]]
    satellite_infrared: list[dict[str, Any]]


class ProviderConfigUpdate(BaseModel):
    provider_id: str
    enabled: Optional[bool] = None
    api_key: Optional[str] = None


class AppSettingsUpdate(BaseModel):
    unit_system: Optional[str] = None  # metric | imperial
    auto_refresh_seconds: Optional[int] = None
