/* Cliente REST y utilidades de formato en español para Weather Dashboard Pro */

window.WeatherAPI = (() => {
  const BASE = "/api/v1";

  async function request(path, options = {}) {
    const resp = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const contentType = resp.headers.get("content-type") || "";
    const data = contentType.includes("application/json") ? await resp.json() : await resp.text();
    if (!resp.ok) {
      const msg = (data && data.detail) || `Error HTTP ${resp.status}`;
      throw new Error(msg);
    }
    return data;
  }

  function qs(params) {
    const sp = new URLSearchParams();
    Object.entries(params || {}).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
    });
    const s = sp.toString();
    return s ? `?${s}` : "";
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function convertTemp(tempC, unitSystem) {
    if (tempC === null || tempC === undefined || Number.isNaN(Number(tempC))) return null;
    const n = Number(tempC);
    return unitSystem === "imperial" ? Number(((n * 9) / 5 + 32).toFixed(1)) : Number(n.toFixed(1));
  }

  function fmtTemp(tempC, unitSystem, decimals = 0) {
    const val = convertTemp(tempC, unitSystem);
    if (val === null) return "—";
    const fixed = val.toFixed(decimals);
    const clean = Number(fixed) === 0 ? (0).toFixed(decimals) : fixed;
    return `${clean}${unitSystem === "imperial" ? " °F" : " °C"}`;
  }

  function convertWind(kmh, unitSystem) {
    if (kmh === null || kmh === undefined || Number.isNaN(Number(kmh))) return null;
    const n = Number(kmh);
    return unitSystem === "imperial" ? Number((n * 0.621371).toFixed(1)) : Number(n.toFixed(1));
  }

  function fmtWind(kmh, unitSystem) {
    const val = convertWind(kmh, unitSystem);
    if (val === null) return "—";
    return `${Math.round(val)} ${unitSystem === "imperial" ? "mph" : "km/h"}`;
  }

  function fmtPrecip(mm, unitSystem) {
    if (mm === null || mm === undefined || Number.isNaN(Number(mm))) return "0 mm";
    const n = Number(mm);
    if (unitSystem === "imperial") return `${(n / 25.4).toFixed(2)} in`;
    return `${n.toFixed(1)} mm`;
  }

  const MONTHS_SHORT = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  const DAYS_SHORT = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];

  function fmtShortDate(isoDate) {
    if (!isoDate) return "—";
    const m = String(isoDate).match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return isoDate;
    const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    return `${DAYS_SHORT[d.getDay()]} ${d.getDate()} ${MONTHS_SHORT[d.getMonth()]}`;
  }

  function fmtChartDate(isoDate) {
    if (!isoDate) return "";
    const m = String(isoDate).match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return isoDate;
    return `${Number(m[3])} ${MONTHS_SHORT[Number(m[2]) - 1]}`;
  }

  function fmtHour(isoTime) {
    if (!isoTime) return "—";
    const m = String(isoTime).match(/T(\d{2}:\d{2})/);
    return m ? m[1] : String(isoTime).slice(-5);
  }

  function fmtDayHour(isoTime) {
    if (!isoTime) return "—";
    const m = String(isoTime).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2})/);
    if (!m) return isoTime;
    return `${Number(m[3])} ${MONTHS_SHORT[Number(m[2]) - 1]} ${m[4]}`;
  }

  function fmtAgeSeconds(sec) {
    if (sec === null || sec === undefined) return "recién actualizado";
    const s = Math.max(0, Number(sec));
    if (s < 60) return "hace menos de 1 min";
    const m = Math.round(s / 60);
    if (m < 60) return `hace ${m} min`;
    const h = Math.round(m / 60);
    return `hace ${h} h`;
  }

  return {
    escapeHtml,
    convertTemp,
    fmtTemp,
    convertWind,
    fmtWind,
    fmtPrecip,
    fmtShortDate,
    fmtChartDate,
    fmtHour,
    fmtDayHour,
    fmtAgeSeconds,

    health: () => request("/health"),
    geolocateIp: () => request("/geolocate"),
    geocode: (q, count = 7) => request(`/geocode${qs({ q, count })}`),
    reverseGeocode: (lat, lon) => request(`/reverse-geocode${qs({ lat, lon })}`),
    getWeather: (params) => request(`/weather${qs(params)}`),
    postQuery: (body) => request("/query", { method: "POST", body: JSON.stringify(body) }),
    compareProviders: (params) => request(`/compare/providers${qs(params)}`),
    compareCities: (params) => request(`/compare/cities${qs(params)}`),
    getAccuracy: (params) => request(`/accuracy${qs(params)}`),
    runBacktest: (params) => request(`/accuracy/backtest${qs(params)}`, { method: "POST" }),
    getHistory: (params) => request(`/history${qs(params)}`),
    historyExportUrl: (params) => `${BASE}/history/export${qs(params)}`,
    getRadar: () => request("/radar"),
    getLocations: () => request("/locations"),
    toggleFavorite: (id, is_favorite) =>
      request(`/locations/${id}/favorite${qs({ is_favorite })}`, { method: "POST" }),
    deleteLocation: (id) => request(`/locations/${id}`, { method: "DELETE" }),
    getProviders: () => request("/providers"),
  };
})();
