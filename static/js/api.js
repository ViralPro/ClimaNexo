/* ClimaNexo — Motor Meteorológico Multiproveedor Híbrido (FastAPI + GitHub Pages Autónomo)
 * Arquitectura v3.0:
 * - 7 fuentes internacionales independientes (Open-Meteo Best Match, ECMWF IFS, DWD ICON,
 *   NOAA GFS, ECCC GEM Canadá, Météo-France ARPEGE y wttr.in WorldWeather).
 * - Batched Multi-Model Fetch (reduce 11 llamadas HTTP a 2 en paralelo) con fallback por adaptador.
 * - Caché multinivel RAM + localStorage con TTL diferenciado, deduplicación in-flight y SWR.
 * - Validación física meteorológica, motor de confianza real (5 criterios objetivos) y
 *   verificación histórica empírica no bloqueante (Previous Runs vs Observaciones reales).
 */

window.WeatherAPI = (() => {
  const BASE = "/api/v1";
  const IS_GITHUB_PAGES =
    typeof window !== "undefined" &&
    (window.location.hostname.endsWith(".github.io") ||
      window.location.protocol === "file:" ||
      window.location.hostname === "climanexo.app");

  let useStaticEngine = IS_GITHUB_PAGES;

  /* ── TTLs de caché inteligente (segundos) ────────────────────────────── */
  const TTL = {
    WEATHER: 600,        // 10 min vigente
    WEATHER_STALE: 2700, // 45 min stale-while-revalidate
    GEOCODE: 86400,      // 24 h
    HISTORY: 21600,      // 6 h
    RADAR: 300,          // 5 min
    IP_GEO: 1800,        // 30 min
    BACKTEST: 21600,     // 6 h
  };

  /* ── Caché en memoria + localStorage + deduplicación in-flight ───────── */
  const memCache = new Map();
  const inFlight = new Map();
  const CACHE_PREFIX = "climanexo_c3_";

  function cacheGet(key, allowStaleSec = 0) {
    const now = Date.now() / 1000;
    let entry = memCache.get(key);
    if (!entry) {
      try {
        const raw = localStorage.getItem(CACHE_PREFIX + key);
        if (raw) {
          entry = JSON.parse(raw);
          memCache.set(key, entry);
        }
      } catch {
        // Quota o privacidad de almacenamiento
      }
    }
    if (!entry) return null;
    const age = Math.max(0, Math.round(now - (entry.ts || 0)));
    if (now <= entry.exp) {
      return { value: entry.val, isStale: false, ageSeconds: age };
    }
    if (allowStaleSec > 0 && now <= entry.ts + allowStaleSec) {
      return { value: entry.val, isStale: true, ageSeconds: age };
    }
    return null;
  }

  function cacheSet(key, val, ttlSec, persist = true) {
    const now = Date.now() / 1000;
    const entry = { ts: now, exp: now + ttlSec, val };
    memCache.set(key, entry);
    if (persist) {
      try {
        localStorage.setItem(CACHE_PREFIX + key, JSON.stringify(entry));
      } catch {
        pruneOldLocalCache();
        try {
          localStorage.setItem(CACHE_PREFIX + key, JSON.stringify(entry));
        } catch {
          // Seguir solo en RAM si localStorage está lleno
        }
      }
    }
  }

  function pruneOldLocalCache() {
    try {
      const now = Date.now() / 1000;
      const keys = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith(CACHE_PREFIX)) keys.push(k);
      }
      keys.forEach((k) => {
        try {
          const parsed = JSON.parse(localStorage.getItem(k) || "{}");
          if (!parsed.exp || now > parsed.exp + 1800) localStorage.removeItem(k);
        } catch {
          localStorage.removeItem(k);
        }
      });
    } catch {
      // ignore
    }
  }

  function dedupe(key, fn) {
    if (inFlight.has(key)) return inFlight.get(key);
    const p = Promise.resolve()
      .then(fn)
      .finally(() => inFlight.delete(key));
    inFlight.set(key, p);
    return p;
  }

  async function fetchJsonWithTimeout(url, timeoutMs = 9500) {
    const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
    try {
      const resp = await fetch(url, ctrl ? { signal: ctrl.signal } : undefined);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return await resp.json();
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  /* ── Tablas WMO y utilidades meteorológicas ──────────────────────────── */
  const WMO_MAP = {
    0: ["Cielo despejado", "sun", "clear"],
    1: ["Mayormente despejado", "sun", "clear"],
    2: ["Parcialmente nublado", "cloud-sun", "cloudy"],
    3: ["Nublado", "cloud", "cloudy"],
    45: ["Niebla", "cloud-fog", "fog"],
    48: ["Niebla escarchada", "cloud-fog", "fog"],
    51: ["Llovizna ligera", "cloud-drizzle", "rain"],
    53: ["Llovizna moderada", "cloud-drizzle", "rain"],
    55: ["Llovizna intensa", "cloud-drizzle", "rain"],
    56: ["Llovizna helada", "cloud-drizzle", "rain"],
    57: ["Llovizna helada densa", "cloud-drizzle", "rain"],
    61: ["Lluvia ligera", "cloud-rain", "rain"],
    63: ["Lluvia moderada", "cloud-rain", "rain"],
    65: ["Lluvia fuerte", "cloud-rain", "rain"],
    66: ["Lluvia helada", "cloud-rain", "rain"],
    67: ["Lluvia helada fuerte", "cloud-rain", "rain"],
    71: ["Nevada ligera", "snowflake", "snow"],
    73: ["Nevada moderada", "snowflake", "snow"],
    75: ["Nevada intensa", "snowflake", "snow"],
    77: ["Granos de nieve", "snowflake", "snow"],
    80: ["Chubascos ligeros", "cloud-rain", "rain"],
    81: ["Chubascos moderados", "cloud-rain", "rain"],
    82: ["Chubascos violentos", "cloud-rain-wind", "storm"],
    85: ["Chubascos de nieve", "snowflake", "snow"],
    86: ["Chubascos de nieve fuertes", "snowflake", "snow"],
    95: ["Tormenta eléctrica", "cloud-lightning", "storm"],
    96: ["Tormenta con granizo", "cloud-lightning", "storm"],
    99: ["Tormenta fuerte con granizo", "cloud-lightning", "storm"],
  };

  function decodeWmo(code, tempC = null) {
    const c = Number(code ?? 0);
    const entry = WMO_MAP[c] || ["Condición variable", "cloud-sun", "clear"];
    let theme = entry[2];
    if (theme === "clear" && tempC !== null && Number(tempC) >= 30) theme = "sun";
    if (theme === "clear" && tempC !== null && Number(tempC) <= 4) theme = "cold";
    return { text: entry[0], icon: entry[1], theme };
  }

  function decodeWttrCode(code) {
    const c = Number(code || 113);
    if (c === 113) return 0;
    if (c === 116) return 2;
    if (c === 119 || c === 122) return 3;
    if (c === 143 || c === 248 || c === 260) return 45;
    if ([176, 263, 266, 293, 296, 353].includes(c)) return 61;
    if ([299, 302, 305, 308, 356, 359].includes(c)) return 63;
    if ([200, 386, 389, 392, 395].includes(c)) return 95;
    if ([179, 227, 230, 323, 326, 329, 332, 335, 338].includes(c)) return 71;
    return 2;
  }

  function degToCardinal(deg) {
    if (deg === null || deg === undefined || Number.isNaN(Number(deg))) return "—";
    const dirs = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"];
    return dirs[Math.round((((Number(deg) % 360) + 360) % 360) / 45) % 8];
  }

  function computeApparentTemp(tempC, rhPct, windKmh) {
    if (tempC === null || tempC === undefined) return null;
    const t = Number(tempC);
    const rh = rhPct !== null && rhPct !== undefined ? Number(rhPct) : 50;
    const v = windKmh !== null && windKmh !== undefined ? Number(windKmh) / 3.6 : 2;
    const e = (rh / 100) * 6.105 * Math.exp((17.27 * t) / (237.7 + t));
    return Number((t + 0.33 * e - 0.7 * v - 4.0).toFixed(1));
  }

  function computeDewPoint(tempC, rhPct) {
    if (tempC === null || tempC === undefined || rhPct === null || rhPct === undefined || rhPct <= 0) return null;
    const a = 17.27;
    const b = 237.7;
    const alpha = (a * Number(tempC)) / (b + Number(tempC)) + Math.log(Number(rhPct) / 100);
    return Number(((b * alpha) / (a - alpha)).toFixed(1));
  }

  function computePressureTrend(pressures) {
    const valid = (pressures || []).filter((p) => p !== null && p !== undefined && !Number.isNaN(Number(p)));
    if (valid.length < 3) return "estable";
    const diff = Number(valid[valid.length - 1]) - Number(valid[0]);
    if (diff >= 1.5) return "en ascenso";
    if (diff <= -1.5) return "en descenso";
    return "estable";
  }

  function validateCurrentReading(cur) {
    if (!cur || cur.temperature_c === null || cur.temperature_c === undefined || Number.isNaN(Number(cur.temperature_c))) {
      return { ok: false, reason: "Sin temperatura" };
    }
    const t = Number(cur.temperature_c);
    if (t < -90 || t > 65) return { ok: false, reason: `Temperatura fuera de rango (${t} °C)` };
    if (cur.humidity_pct !== null && cur.humidity_pct !== undefined && (cur.humidity_pct < 0 || cur.humidity_pct > 100)) {
      return { ok: false, reason: "Humedad inválida" };
    }
    if (cur.pressure_hpa !== null && cur.pressure_hpa !== undefined && (cur.pressure_hpa < 850 || cur.pressure_hpa > 1090)) {
      return { ok: false, reason: "Presión inválida" };
    }
    if (cur.wind_speed_kmh !== null && cur.wind_speed_kmh !== undefined && (cur.wind_speed_kmh < 0 || cur.wind_speed_kmh > 400)) {
      return { ok: false, reason: "Viento inválido" };
    }
    if (cur.precipitation_mm !== null && cur.precipitation_mm !== undefined && cur.precipitation_mm < 0) {
      return { ok: false, reason: "Precipitación negativa" };
    }
    return { ok: true, reason: null };
  }

  function computeFreshness(observedAt) {
    if (!observedAt) return { ageMin: 20, factor: 0.96 };
    const s = String(observedAt).trim();
    // Si tiene offset/Z explícito, medimos antigüedad real
    if (s.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(s)) {
      const dt = new Date(s);
      if (!Number.isNaN(dt.getTime())) {
        const ageMin = Math.round(Math.min(1440, Math.abs(Date.now() - dt.getTime()) / 60000));
        const factor = ageMin <= 30 ? 1.0 : ageMin <= 75 ? 0.92 : ageMin <= 150 ? 0.8 : ageMin <= 360 ? 0.62 : 0.4;
        return { ageMin, factor };
      }
    }
    return { ageMin: 15, factor: 1.0 };
  }

  const DEFAULT_CITIES_MAP = {
    "buenos aires": { id: 1, name: "Buenos Aires", admin1: "Ciudad Autónoma de Buenos Aires", country: "Argentina", country_code: "AR", latitude: -34.6037, longitude: -58.3816, timezone: "America/Argentina/Buenos_Aires" },
    cordoba: { id: 2, name: "Córdoba", admin1: "Provincia de Córdoba", country: "Argentina", country_code: "AR", latitude: -31.4135, longitude: -64.1811, timezone: "America/Argentina/Cordoba" },
    mendoza: { id: 3, name: "Mendoza", admin1: "Provincia de Mendoza", country: "Argentina", country_code: "AR", latitude: -32.8908, longitude: -68.8272, timezone: "America/Argentina/Mendoza" },
    madrid: { id: 4, name: "Madrid", admin1: "Comunidad de Madrid", country: "España", country_code: "ES", latitude: 40.4165, longitude: -3.7026, timezone: "Europe/Madrid" },
    "ciudad de mexico": { id: 5, name: "Ciudad de México", admin1: "Ciudad de México", country: "México", country_code: "MX", latitude: 19.4326, longitude: -99.1332, timezone: "America/Mexico_City" },
    bogota: { id: 6, name: "Bogotá", admin1: "Bogotá D.C.", country: "Colombia", country_code: "CO", latitude: 4.711, longitude: -74.0721, timezone: "America/Bogota" },
    santiago: { id: 7, name: "Santiago", admin1: "Región Metropolitana", country: "Chile", country_code: "CL", latitude: -33.4489, longitude: -70.6693, timezone: "America/Santiago" },
    lima: { id: 8, name: "Lima", admin1: "Provincia de Lima", country: "Perú", country_code: "PE", latitude: -12.0464, longitude: -77.0428, timezone: "America/Lima" },
    montevideo: { id: 9, name: "Montevideo", admin1: "Montevideo", country: "Uruguay", country_code: "UY", latitude: -34.9011, longitude: -56.1645, timezone: "America/Montevideo" },
    quito: { id: 10, name: "Quito", admin1: "Pichincha", country: "Ecuador", country_code: "EC", latitude: -0.1807, longitude: -78.4678, timezone: "America/Guayaquil" },
  };

  function normKey(s) {
    return String(s || "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .trim();
  }

  /* ── Almacenamiento persistente de favoritos y auditoría de precisión ─── */
  const STORAGE_KEY = "climanexo_store_v3";
  function loadStore() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) return JSON.parse(raw);
    } catch {
      // ignore
    }
    return { favorites: {}, audits: [], backtestTs: {}, last_backtest_at: null };
  }
  function saveStore(st) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(st));
    } catch {
      // ignore
    }
  }

  function locKey(lat, lon) {
    // 3 decimales (~110 m) preserva localidades específicas sin mezclar ciudades vecinas
    return `${Number(lat).toFixed(3)},${Number(lon).toFixed(3)}`;
  }

  function decorateLocation(loc) {
    const st = loadStore();
    const key = locKey(loc.latitude, loc.longitude);
    const id = loc.id || Math.abs(key.split("").reduce((a, b) => (a << 5) - a + b.charCodeAt(0), 0));
    const parts = [loc.name];
    if (loc.admin1 && loc.admin1.toLowerCase() !== String(loc.name || "").toLowerCase()) parts.push(loc.admin1);
    if (loc.country) parts.push(loc.country);
    const display_label = loc.display_label || parts.filter(Boolean).join(", ");
    return {
      ...loc,
      latitude: Number(Number(loc.latitude).toFixed(4)),
      longitude: Number(Number(loc.longitude).toFixed(4)),
      id,
      display_label,
      is_favorite: Boolean(st.favorites[id] || st.favorites[key]),
    };
  }

  /* ── Geocodificación y Geolocalización precisa con caché 24h ─────────── */
  async function staticGeocode(q, count = 7) {
    const clean = String(q || "").trim();
    if (!clean) return { results: [] };
    const ck = `geo_${normKey(clean)}_${count}`;
    const cached = cacheGet(ck);
    if (cached) return cached.value;

    return dedupe(ck, async () => {
      try {
        const url = `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(clean)}&count=${count}&language=es&format=json`;
        const d = await fetchJsonWithTimeout(url, 8000);
        const results = (d.results || []).map((item) =>
          decorateLocation({
            id: item.id,
            name: item.name,
            admin1: item.admin1 || "",
            country: item.country || "",
            country_code: item.country_code || "",
            latitude: Number(item.latitude),
            longitude: Number(item.longitude),
            timezone: item.timezone || "auto",
            location_source: "search",
          })
        );
        if (results.length) {
          const out = { results };
          cacheSet(ck, out, TTL.GEOCODE);
          return out;
        }
      } catch {
        // fallback al catálogo base
      }
      const nk = normKey(clean);
      const localMatches = Object.entries(DEFAULT_CITIES_MAP)
        .filter(([k]) => k.includes(nk) || nk.includes(k))
        .map(([, v]) => decorateLocation({ ...v, location_source: "search" }));
      return { results: localMatches };
    });
  }

  async function staticReverseGeocode(lat, lon, sourceTag = "gps") {
    const lk = locKey(lat, lon);
    const ck = `rev_${lk}`;
    const cached = cacheGet(ck);
    if (cached) {
      return { location: decorateLocation({ ...cached.value.location, location_source: sourceTag || cached.value.location.location_source }) };
    }

    return dedupe(ck, async () => {
      try {
        const d = await fetchJsonWithTimeout(
          `https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${lat}&longitude=${lon}&localityLanguage=es`,
          7000
        );
        // Priorizar localidad exacta (locality) antes que cabecera o provincia distante
        const exactLocality = (d.locality || "").trim();
        const city = (d.city || "").trim();
        const admin = (d.principalSubdivision || "").trim();
        const chosenName = exactLocality || city || admin || "Ubicación seleccionada";
        const subAdmin =
          city && city.toLowerCase() !== chosenName.toLowerCase()
            ? [city, admin].filter(Boolean).join(", ")
            : admin;

        const out = {
          location: decorateLocation({
            name: chosenName,
            locality: exactLocality || chosenName,
            admin1: subAdmin,
            country: d.countryName || "",
            country_code: d.countryCode || "",
            latitude: Number(lat),
            longitude: Number(lon),
            timezone: "auto",
            location_source: sourceTag,
          }),
        };
        cacheSet(ck, out, TTL.GEOCODE);
        return out;
      } catch {
        return {
          location: decorateLocation({
            name: `Ubicación (${Number(lat).toFixed(2)}°, ${Number(lon).toFixed(2)}°)`,
            admin1: "",
            country: "",
            latitude: Number(lat),
            longitude: Number(lon),
            timezone: "auto",
            location_source: sourceTag,
          }),
        };
      }
    });
  }

  async function staticGeolocateIp() {
    const ck = "ip_geo_current";
    const cached = cacheGet(ck);
    if (cached) return cached.value;

    return dedupe(ck, async () => {
      for (const url of ["https://ipwho.is/", "https://get.geojs.io/v1/ip/geo.json"]) {
        try {
          const d = await fetchJsonWithTimeout(url, 5500);
          const lat = Number(d.latitude);
          const lon = Number(d.longitude);
          if (!Number.isNaN(lat) && !Number.isNaN(lon) && (lat !== 0 || lon !== 0)) {
            const out = {
              location: decorateLocation({
                name: d.city || "Buenos Aires",
                admin1: d.region || "",
                country: d.country || "",
                country_code: d.country_code || "",
                latitude: lat,
                longitude: lon,
                timezone: (d.timezone && (d.timezone.id || d.timezone)) || "auto",
                location_source: "ip",
              }),
              method: "ip_geolocation",
            };
            cacheSet(ck, out, TTL.IP_GEO);
            return out;
          }
        } catch {
          // probar siguiente proveedor IP
        }
      }
      return {
        location: decorateLocation({ ...DEFAULT_CITIES_MAP["buenos aires"], location_source: "fallback" }),
        method: "fallback",
      };
    });
  }

  async function resolveLocationParams(params = {}) {
    if (params.lat !== undefined && params.lon !== undefined && params.lat !== "" && params.lon !== "") {
      if (params.name) {
        return decorateLocation({
          name: params.name,
          admin1: params.admin1 || "",
          country: params.country || "",
          latitude: Number(params.lat),
          longitude: Number(params.lon),
          timezone: "auto",
          location_source: params.location_source || "search",
        });
      }
      const rev = await staticReverseGeocode(params.lat, params.lon, params.location_source || "search");
      return rev.location;
    }
    if (params.city) {
      const geo = await staticGeocode(params.city, 1);
      if (geo.results && geo.results.length) return geo.results[0];
      const nk = normKey(params.city);
      if (DEFAULT_CITIES_MAP[nk]) return decorateLocation({ ...DEFAULT_CITIES_MAP[nk], location_source: "search" });
    }
    return decorateLocation({ ...DEFAULT_CITIES_MAP["buenos aires"], location_source: "fallback" });
  }

  /* ── Adaptadores Multiproveedor (7 Agencias Internacionales) ─────────── */
  const OM_MODELS = [
    { id: "open_meteo", modelKey: "best_match", name: "Open-Meteo (Best Match)", baseWeight: 1.12 },
    { id: "ecmwf_ifs", modelKey: "ecmwf_ifs025", name: "ECMWF IFS (Europa)", baseWeight: 1.18 },
    { id: "dwd_icon", modelKey: "icon_seamless", name: "DWD ICON (Alemania)", baseWeight: 1.08 },
    { id: "noaa_gfs", modelKey: "gfs_seamless", name: "NOAA GFS (EE. UU.)", baseWeight: 1.04 },
    { id: "eccc_gem", modelKey: "gem_seamless", name: "ECCC GEM (Canadá)", baseWeight: 1.04 },
    { id: "meteofrance", modelKey: "meteofrance_seamless", name: "Météo-France ARPEGE", baseWeight: 1.05 },
  ];

  const ALL_PROVIDERS_META = [
    ...OM_MODELS.map((m) => ({ id: m.id, name: m.name, supportsBacktest: true })),
    { id: "wttr_in", name: "wttr.in (WorldWeather)", supportsBacktest: false },
  ];

  function extractOpenMeteoModelSnap(rawData, modelMeta, days, latencyMs) {
    const suffix = `_${modelMeta.modelKey}`;
    const h = rawData.hourly || {};
    const dy = rawData.daily || {};
    const cur = rawData.current || {};
    const hTimes = h.time || [];

    const getH = (field, i) => {
      const arr = h[`${field}${suffix}`] || h[field];
      return Array.isArray(arr) && i < arr.length ? arr[i] : null;
    };
    const getD = (field, i) => {
      const arr = dy[`${field}${suffix}`] || dy[field];
      return Array.isArray(arr) && i < arr.length ? arr[i] : null;
    };
    const getBestH = (field, i) => {
      const arr = h[`${field}_best_match`] || h[field];
      return Array.isArray(arr) && i < arr.length ? arr[i] : null;
    };
    const getBestD = (field, i) => {
      const arr = dy[`${field}_best_match`] || dy[field];
      return Array.isArray(arr) && i < arr.length ? arr[i] : null;
    };

    const curTime = cur.time || hTimes[0] || new Date().toISOString();
    const curPrefix = String(curTime).slice(0, 13);
    let nowIdx = hTimes.findIndex((t) => String(t).startsWith(curPrefix));
    if (nowIdx < 0) nowIdx = 0;

    const hourly = hTimes.map((t, i) => {
      const tempC = getH("temperature_2m", i);
      const rh = getH("relative_humidity_2m", i);
      const wind = getH("wind_speed_10m", i);
      const feels = getH("apparent_temperature", i) ?? computeApparentTemp(tempC, rh, wind);
      const hc = getH("weather_code", i) ?? getBestH("weather_code", i) ?? 0;
      const hw = decodeWmo(hc, tempC);
      const visM = getH("visibility", i) ?? getBestH("visibility", i);
      const uvVal = getH("uv_index", i) ?? getBestH("uv_index", i);
      const dewVal = getH("dew_point_2m", i) ?? getBestH("dew_point_2m", i) ?? computeDewPoint(tempC, rh);
      const probRain = getH("precipitation_probability", i) ?? getBestH("precipitation_probability", i);
      return {
        time: t,
        temperature_c: tempC,
        feels_like_c: feels,
        humidity_pct: rh,
        dew_point_c: dewVal,
        precip_probability_pct: probRain,
        precipitation_mm: getH("precipitation", i) ?? 0,
        weather_code: hc,
        condition_text: hw.text,
        condition_icon: hw.icon,
        pressure_hpa: getH("pressure_msl", i),
        cloud_cover_pct: getH("cloud_cover", i),
        visibility_km: visM !== null && visM !== undefined ? Number((Number(visM) / 1000).toFixed(1)) : null,
        wind_speed_kmh: wind,
        wind_direction_deg: getH("wind_direction_10m", i),
        wind_gusts_kmh: getH("wind_gusts_10m", i) ?? getBestH("wind_gusts_10m", i),
        uv_index: uvVal !== null && uvVal !== undefined ? Number(Number(uvVal).toFixed(1)) : null,
      };
    });

    const daily = (dy.time || []).slice(0, days).map((dt, i) => {
      const tMax = getD("temperature_2m_max", i);
      const tMin = getD("temperature_2m_min", i);
      const dc = getD("weather_code", i) ?? getBestD("weather_code", i) ?? 0;
      const dw = decodeWmo(dc, tMax);
      const uvMax = getD("uv_index_max", i) ?? getBestD("uv_index_max", i);
      return {
        date: dt,
        weather_code: dc,
        condition_text: dw.text,
        condition_icon: dw.icon,
        temp_max_c: tMax,
        temp_min_c: tMin,
        temp_mean_c: tMax !== null && tMin !== null ? Number(((tMax + tMin) / 2).toFixed(1)) : null,
        feels_max_c: getD("apparent_temperature_max", i) ?? getBestD("apparent_temperature_max", i),
        feels_min_c: getD("apparent_temperature_min", i) ?? getBestD("apparent_temperature_min", i),
        sunrise: getD("sunrise", i) ?? getBestD("sunrise", i),
        sunset: getD("sunset", i) ?? getBestD("sunset", i),
        uv_index_max: uvMax !== null && uvMax !== undefined ? Number(Number(uvMax).toFixed(1)) : null,
        precipitation_sum_mm: getD("precipitation_sum", i) ?? 0,
        precip_probability_max_pct: getD("precipitation_probability_max", i) ?? getBestD("precipitation_probability_max", i),
        wind_speed_max_kmh: getD("wind_speed_10m_max", i) ?? getBestD("wind_speed_10m_max", i),
        wind_gusts_max_kmh: getD("wind_gusts_10m_max", i) ?? getBestD("wind_gusts_10m_max", i),
        wind_direction_dominant_deg: getD("wind_direction_10m_dominant", i) ?? getBestD("wind_direction_10m_dominant", i),
      };
    });

    const hNow = hourly[nowIdx] || hourly[0] || {};
    const isBestMatch = modelMeta.id === "open_meteo";
    const tempC = isBestMatch ? (cur.temperature_2m ?? hNow.temperature_c) : (hNow.temperature_c ?? cur.temperature_2m);
    if (tempC === null || tempC === undefined) return null;

    const rhNow = isBestMatch ? (cur.relative_humidity_2m ?? hNow.humidity_pct) : (hNow.humidity_pct ?? cur.relative_humidity_2m);
    const windNow = isBestMatch ? (cur.wind_speed_10m ?? hNow.wind_speed_kmh) : (hNow.wind_speed_kmh ?? cur.wind_speed_10m);
    const gustsNow = isBestMatch ? (cur.wind_gusts_10m ?? hNow.wind_gusts_kmh) : (hNow.wind_gusts_kmh ?? cur.wind_gusts_10m);
    const presNow = isBestMatch ? (cur.pressure_msl ?? hNow.pressure_hpa) : (hNow.pressure_hpa ?? cur.pressure_msl);
    const precNow = isBestMatch ? (cur.precipitation ?? hNow.precipitation_mm ?? 0) : (hNow.precipitation_mm ?? cur.precipitation ?? 0);
    const feelsNow =
      (isBestMatch ? cur.apparent_temperature : hNow.feels_like_c) ?? computeApparentTemp(tempC, rhNow, windNow);
    const windDeg = isBestMatch ? (cur.wind_direction_10m ?? hNow.wind_direction_deg ?? 0) : (hNow.wind_direction_deg ?? cur.wind_direction_10m ?? 0);
    const wmoCode = isBestMatch ? (cur.weather_code ?? hNow.weather_code ?? 0) : (hNow.weather_code ?? cur.weather_code ?? 0);
    const wmo = decodeWmo(wmoCode, tempC);
    const presWindow = hourly.slice(nowIdx, nowIdx + 6).map((x) => x.pressure_hpa);

    return {
      provider_id: modelMeta.id,
      display_name: modelMeta.name,
      status: "ok",
      latency_ms: latencyMs,
      fetched_at: new Date().toISOString(),
      baseWeight: modelMeta.baseWeight,
      current: {
        observed_at: curTime,
        temperature_c: Number(Number(tempC).toFixed(1)),
        feels_like_c: feelsNow !== null ? Number(Number(feelsNow).toFixed(1)) : Number(Number(tempC).toFixed(1)),
        humidity_pct: rhNow !== null && rhNow !== undefined ? Math.round(Number(rhNow)) : null,
        dew_point_c: hNow.dew_point_c ?? computeDewPoint(tempC, rhNow),
        pressure_hpa: presNow !== null && presNow !== undefined ? Number(Number(presNow).toFixed(1)) : null,
        pressure_trend: computePressureTrend(presWindow),
        wind_speed_kmh: windNow !== null && windNow !== undefined ? Number(Number(windNow).toFixed(1)) : 0,
        wind_gusts_kmh: gustsNow !== null && gustsNow !== undefined ? Number(Number(gustsNow).toFixed(1)) : null,
        wind_direction_deg: windDeg,
        wind_direction_cardinal: degToCardinal(windDeg),
        precipitation_mm: Number(Number(precNow || 0).toFixed(2)),
        precip_probability_pct: hNow.precip_probability_pct ?? 0,
        cloud_cover_pct: (isBestMatch ? cur.cloud_cover : hNow.cloud_cover_pct) ?? 0,
        visibility_km: hNow.visibility_km ?? 24.0,
        uv_index: hNow.uv_index ?? (daily[0] ? daily[0].uv_index_max : 0),
        weather_code: wmoCode,
        condition_text: wmo.text,
        condition_icon: wmo.icon,
        theme: wmo.theme,
      },
      hourly,
      daily,
    };
  }

  async function fetchBatchedOpenMeteoAdapters(lat, lon, days = 7) {
    const t0 = performance.now();
    const currentFields = [
      "temperature_2m",
      "relative_humidity_2m",
      "apparent_temperature",
      "precipitation",
      "weather_code",
      "cloud_cover",
      "pressure_msl",
      "wind_speed_10m",
      "wind_direction_10m",
      "wind_gusts_10m",
    ].join(",");
    const hourlyFields = [
      "temperature_2m",
      "apparent_temperature",
      "relative_humidity_2m",
      "dew_point_2m",
      "precipitation_probability",
      "precipitation",
      "weather_code",
      "pressure_msl",
      "cloud_cover",
      "visibility",
      "wind_speed_10m",
      "wind_direction_10m",
      "wind_gusts_10m",
      "uv_index",
    ].join(",");
    const dailyFields = [
      "weather_code",
      "temperature_2m_max",
      "temperature_2m_min",
      "apparent_temperature_max",
      "apparent_temperature_min",
      "sunrise",
      "sunset",
      "uv_index_max",
      "precipitation_sum",
      "precipitation_probability_max",
      "wind_speed_10m_max",
      "wind_gusts_10m_max",
      "wind_direction_10m_dominant",
    ].join(",");

    const sp = new URLSearchParams({
      latitude: String(Number(lat).toFixed(4)),
      longitude: String(Number(lon).toFixed(4)),
      current: currentFields,
      hourly: hourlyFields,
      daily: dailyFields,
      timezone: "auto",
      forecast_days: String(Math.min(16, Math.max(days, 7))),
      models: OM_MODELS.map((m) => m.modelKey).join(","),
    });

    try {
      const data = await fetchJsonWithTimeout(`https://api.open-meteo.com/v1/forecast?${sp.toString()}`, 9500);
      const latency = Math.round(performance.now() - t0);
      const snaps = OM_MODELS.map((m) => extractOpenMeteoModelSnap(data, m, days, latency)).filter(Boolean);
      if (snaps.length >= 2) return snaps;
    } catch {
      // Fallback automático a consulta individual del modelo principal si el lote falla
    }

    // Fallback de resiliencia: consultar Best Match + ECMWF por separado
    const fallbackModels = OM_MODELS.slice(0, 2);
    const settled = await Promise.all(
      fallbackModels.map(async (m) => {
        try {
          const sp1 = new URLSearchParams({
            latitude: String(Number(lat).toFixed(4)),
            longitude: String(Number(lon).toFixed(4)),
            current: currentFields,
            hourly: hourlyFields,
            daily: dailyFields,
            timezone: "auto",
            forecast_days: String(Math.min(16, Math.max(days, 7))),
          });
          if (m.modelKey !== "best_match") sp1.set("models", m.modelKey);
          const d1 = await fetchJsonWithTimeout(`https://api.open-meteo.com/v1/forecast?${sp1.toString()}`, 8000);
          return extractOpenMeteoModelSnap(d1, m, days, Math.round(performance.now() - t0));
        } catch {
          return null;
        }
      })
    );
    return settled.filter(Boolean);
  }

  async function fetchWttrInAdapter(lat, lon) {
    const t0 = performance.now();
    try {
      const url = `https://wttr.in/${Number(lat).toFixed(3)},${Number(lon).toFixed(3)}?format=j1`;
      const d = await fetchJsonWithTimeout(url, 4200);
      const curArr = d.current_condition || [];
      if (!curArr.length) return null;
      const c = curArr[0];
      const tempC = Number(c.temp_C);
      if (Number.isNaN(tempC)) return null;
      const wmoCode = decodeWttrCode(c.weatherCode);
      const wmo = decodeWmo(wmoCode, tempC);
      const latency = Math.round(performance.now() - t0);
      const daily = (d.weather || []).map((w) => ({
        date: w.date,
        temp_max_c: Number(w.maxtempC),
        temp_min_c: Number(w.mintempC),
        precipitation_sum_mm: 0,
        wind_speed_max_kmh: Number(c.windspeedKmph || 0),
        weather_code: wmoCode,
        condition_text: wmo.text,
        condition_icon: wmo.icon,
      }));
      return {
        provider_id: "wttr_in",
        display_name: "wttr.in (WorldWeather)",
        status: "ok",
        latency_ms: latency,
        fetched_at: new Date().toISOString(),
        baseWeight: 0.92,
        current: {
          observed_at: new Date().toISOString(),
          temperature_c: Number(tempC.toFixed(1)),
          feels_like_c: Number(c.FeelsLikeC ?? tempC),
          humidity_pct: c.humidity ? Number(c.humidity) : null,
          dew_point_c: computeDewPoint(tempC, c.humidity ? Number(c.humidity) : null),
          pressure_hpa: c.pressure ? Number(c.pressure) : null,
          pressure_trend: "estable",
          wind_speed_kmh: c.windspeedKmph ? Number(c.windspeedKmph) : 0,
          wind_gusts_kmh: null,
          wind_direction_deg: c.winddirDegree ? Number(c.winddirDegree) : 0,
          wind_direction_cardinal: degToCardinal(c.winddirDegree ? Number(c.winddirDegree) : 0),
          precipitation_mm: c.precipMM ? Number(c.precipMM) : 0,
          precip_probability_pct: null,
          cloud_cover_pct: c.cloudcover ? Number(c.cloudcover) : 0,
          visibility_km: c.visibility ? Number(c.visibility) : 16.0,
          uv_index: c.uvIndex ? Number(c.uvIndex) : null,
          weather_code: wmoCode,
          condition_text: wmo.text,
          condition_icon: wmo.icon,
          theme: wmo.theme,
        },
        hourly: [],
        daily,
      };
    } catch {
      // Fallo silencioso y seguro cuando wttr.in no responde rápido
      return null;
    }
  }

  /* ── Verificación Histórica Real en 1 SOLA llamada agrupada (No bloqueante) ─ */
  async function ensureHistoricalVerification(loc, pastDays = 7, force = false) {
    const lKey = locKey(loc.latitude, loc.longitude);
    const st = loadStore();
    const lastTs = (st.backtestTs && st.backtestTs[lKey]) || 0;
    const nowSec = Date.now() / 1000;
    const existingForLoc = st.audits.filter((a) => a.locKey === lKey);
    if (!force && existingForLoc.length >= 24 && nowSec - lastTs < TTL.BACKTEST) {
      return { newly_verified: 0, observations_saved: existingForLoc.length };
    }

    const dk = `bt_${lKey}_${pastDays}`;
    return dedupe(dk, async () => {
      try {
        const sp = new URLSearchParams({
          latitude: String(Number(loc.latitude).toFixed(4)),
          longitude: String(Number(loc.longitude).toFixed(4)),
          hourly: "temperature_2m,temperature_2m_previous_day1,precipitation,precipitation_previous_day1,wind_speed_10m,wind_speed_10m_previous_day1",
          past_days: String(pastDays),
          forecast_days: "1",
          timezone: "UTC",
          models: OM_MODELS.map((m) => m.modelKey).join(","),
        });
        const prJson = await fetchJsonWithTimeout(`https://previous-runs-api.open-meteo.com/v1/forecast?${sp.toString()}`, 10000);
        const h = prJson.hourly || {};
        const times = h.time || [];
        const actTemps = h.temperature_2m_best_match || h.temperature_2m || [];
        const actPrecs = h.precipitation_best_match || h.precipitation || [];
        const actWinds = h.wind_speed_10m_best_match || h.wind_speed_10m || [];

        const store = loadStore();
        const existingSet = new Set(store.audits.map((x) => x.pairId));
        let newly = 0;

        OM_MODELS.forEach((m) => {
          const predTemps = h[`temperature_2m_previous_day1_${m.modelKey}`] || [];
          const predPrecs = h[`precipitation_previous_day1_${m.modelKey}`] || [];
          const predWinds = h[`wind_speed_10m_previous_day1_${m.modelKey}`] || [];

          for (let i = 0; i < times.length; i += 3) {
            const t = times[i];
            const predT = predTemps[i];
            const actT = actTemps[i];
            if (predT === null || predT === undefined || actT === null || actT === undefined) continue;
            const pairId = `${lKey}:${m.id}:${t}`;
            if (existingSet.has(pairId)) continue;

            const errT = Number((predT - actT).toFixed(2));
            const absErrT = Math.abs(errT);
            const predR = Number(predPrecs[i] ?? 0);
            const actR = Number(actPrecs[i] ?? 0);
            const predW = Number(predWinds[i] ?? actWinds[i] ?? 0);
            const actW = Number(actWinds[i] ?? 0);
            const errW = Math.abs(predW - actW);
            const rainHit = (predR >= 0.2 && actR >= 0.2) || (predR < 0.2 && actR < 0.2);

            const tempScore = Math.max(0, 100 - absErrT * 12.5);
            const windScore = Math.max(0, 100 - errW * 3.0);
            const rainScore = rainHit ? 100 : Math.max(0, 100 - Math.abs(predR - actR) * 15);
            const composite = Number((tempScore * 0.65 + rainScore * 0.2 + windScore * 0.15).toFixed(1));

            store.audits.push({
              pairId,
              locKey: lKey,
              location_name: loc.name,
              provider_id: m.id,
              provider_name: m.name,
              target_time: t,
              lead_hours: 24,
              pred_temp_c: Number(predT.toFixed(1)),
              actual_temp_c: Number(actT.toFixed(1)),
              temp_error_c: errT,
              wind_abs_error: errW,
              pred_precip_mm: Number(predR.toFixed(1)),
              actual_precip_mm: Number(actR.toFixed(1)),
              rain_hit: rainHit,
              composite_score_pct: composite,
              source_type: "historical_run",
            });
            existingSet.add(pairId);
            newly++;
          }
        });

        if (store.audits.length > 900) store.audits = store.audits.slice(-900);
        store.backtestTs = store.backtestTs || {};
        store.backtestTs[lKey] = Date.now() / 1000;
        store.last_backtest_at = new Date().toISOString();
        saveStore(store);
        return { newly_verified: newly, observations_saved: times.length };
      } catch {
        return { newly_verified: 0, observations_saved: 0 };
      }
    });
  }

  function computeProviderAccuracyStats(loc = null, sourceType = "") {
    const st = loadStore();
    const lKey = loc ? locKey(loc.latitude, loc.longitude) : null;
    const filtered = st.audits.filter((a) => {
      if (lKey && a.locKey !== lKey) return false;
      if (sourceType && a.source_type !== sourceType) return false;
      return true;
    });

    const ranking = ALL_PROVIDERS_META.map((p) => {
      const rows = filtered.filter((a) => a.provider_id === p.id);
      if (!rows.length) {
        return {
          provider_id: p.id,
          display_name: p.name,
          has_data: false,
          sample_count: 0,
          composite_accuracy_pct: null,
          temp_mae_c: null,
          temp_rmse_c: null,
          temp_bias_c: null,
          wind_mae_kmh: null,
          rain_hit_rate_pct: null,
          status_note: p.supportsBacktest ? "Verificando historial..." : "Solo en vivo (sin archivo +24 h)",
        };
      }
      const n = rows.length;
      const mae = rows.reduce((s, r) => s + Math.abs(r.temp_error_c), 0) / n;
      const rmse = Math.sqrt(rows.reduce((s, r) => s + r.temp_error_c * r.temp_error_c, 0) / n);
      const bias = rows.reduce((s, r) => s + r.temp_error_c, 0) / n;
      const wMae = rows.reduce((s, r) => s + (r.wind_abs_error || 0), 0) / n;
      const rHits = (rows.filter((r) => r.rain_hit).length / n) * 100;
      const comp = rows.reduce((s, r) => s + r.composite_score_pct, 0) / n;

      return {
        provider_id: p.id,
        display_name: p.name,
        has_data: true,
        sample_count: n,
        composite_accuracy_pct: Number(comp.toFixed(1)),
        temp_mae_c: Number(mae.toFixed(2)),
        temp_rmse_c: Number(rmse.toFixed(2)),
        temp_bias_c: Number(bias.toFixed(2)),
        wind_mae_kmh: Number(wMae.toFixed(1)),
        rain_hit_rate_pct: Number(rHits.toFixed(1)),
        status_note: `Verificado (${n} muestras reales)`,
      };
    }).sort((a, b) => (b.composite_accuracy_pct || -1) - (a.composite_accuracy_pct || -1));

    return {
      total_verified_pairs: filtered.length,
      historical_verified_pairs: filtered.filter((a) => a.source_type === "historical_run").length,
      live_verified_pairs: filtered.filter((a) => a.source_type !== "historical_run").length,
      total_forecast_records: filtered.length,
      total_observations: Math.round(filtered.length / Math.max(1, OM_MODELS.length)),
      last_backtest_at: st.last_backtest_at || null,
      methodology_note:
        "Precisión calculada matemáticamente contrastando pronósticos históricos emitidos a +24 h contra observaciones horarias reales (MAE, RMSE, sesgo térmico y acierto de lluvia). Cero porcentajes inventados.",
      ranking,
      recent_audit: filtered.slice(-45).reverse(),
    };
  }

  /* ── Motor de Porcentaje REAL de Confianza Meteorológica ─────────────── */
  function computeForecastConfidence(validSnaps, totalExpected, spreadT, stdT, windSpread, precipDisagreement) {
    const nValid = validSnaps.length;
    if (nValid < 2) {
      return {
        available: false,
        score_pct: null,
        level: "No disponible",
        reason: "Se requieren al menos 2 fuentes meteorológicas válidas para calcular la confianza.",
        summary: "Confianza no disponible por insuficiencia de fuentes activas.",
        factors: {},
      };
    }

    const thermalAgreement = Math.max(25, Math.min(100, 100 - (spreadT * 8.5 + stdT * 9.5)));
    const rainPenalty = precipDisagreement ? 24 : 0;
    const windPenalty = Math.min(22, Math.max(0, (windSpread - 8) * 1.1));
    const precipWindConsensus = Math.max(35, Math.min(100, 100 - rainPenalty - windPenalty));
    const freshnessPct = (validSnaps.reduce((s, x) => s + (x.freshnessFactor || 1), 0) / nValid) * 100;
    const targetSources = Math.max(4, Math.min(7, totalExpected));
    const availabilityPct = Math.min(100, (nValid / targetSources) * 100);

    const verified = validSnaps.filter((s) => s.historical_accuracy_pct !== null && s.historical_samples >= 6);
    const totalSamples = verified.reduce((acc, s) => acc + s.historical_samples, 0);
    const hasHist = totalSamples >= 12 && verified.length >= 1;

    let histPct = null;
    let rawScore;
    if (hasHist) {
      histPct = verified.reduce((acc, s) => acc + s.historical_accuracy_pct * s.historical_samples, 0) / totalSamples;
      rawScore =
        thermalAgreement * 0.36 +
        precipWindConsensus * 0.18 +
        freshnessPct * 0.16 +
        availabilityPct * 0.14 +
        histPct * 0.16;
    } else {
      rawScore =
        thermalAgreement * 0.44 +
        precipWindConsensus * 0.22 +
        freshnessPct * 0.18 +
        availabilityPct * 0.16;
    }

    const score = Math.round(Math.max(20, Math.min(98, rawScore)));
    const level = score >= 85 ? "Alta" : score >= 70 ? "Buena" : score >= 55 ? "Moderada" : "Baja";
    const summary =
      `Confianza ${level.toLowerCase()} (${score} %) basada en ${nValid} fuentes independientes ` +
      `(dispersión ±${stdT.toFixed(1)} °C, rango ${spreadT.toFixed(1)} °C` +
      (hasHist ? `, respaldo de ${totalSamples} verificaciones reales).` : ").");

    return {
      available: true,
      score_pct: score,
      level,
      summary,
      sources_used: nValid,
      verified_samples: totalSamples,
      factors: {
        model_agreement_pct: Number(thermalAgreement.toFixed(1)),
        precip_consensus_pct: Number(precipWindConsensus.toFixed(1)),
        freshness_pct: Number(freshnessPct.toFixed(1)),
        availability_pct: Number(availabilityPct.toFixed(1)),
        historical_backing_pct: histPct !== null ? Number(histPct.toFixed(1)) : null,
      },
    };
  }

  /* ── Síntesis Inteligente Multiproveedor + Caché SWR (0 duplicados) ──── */
  function buildNarrative(loc, cur, daily) {
    const d0 = daily[0] || {};
    const mins = daily.map((d) => d.temp_min_c).filter((v) => v !== null && v !== undefined);
    const maxs = daily.map((d) => d.temp_max_c).filter((v) => v !== null && v !== undefined);
    const totalRain = daily.reduce((s, d) => s + (d.precipitation_sum_mm || 0), 0);
    const minW = mins.length ? Math.round(Math.min(...mins)) : Math.round(d0.temp_min_c ?? cur.temperature_c);
    const maxW = maxs.length ? Math.round(Math.max(...maxs)) : Math.round(d0.temp_max_c ?? cur.temperature_c);
    const rainPhrase =
      totalRain >= 5
        ? `con lluvias previstas que acumularán ${totalRain.toFixed(1)} mm.`
        : totalRain > 0.3
        ? `con precipitaciones aisladas que sumarán ${totalRain.toFixed(1)} mm.`
        : "con tiempo mayormente seco sin lluvias significativas.";
    return `${loc.name} registra ${Number(cur.temperature_c).toFixed(1)} °C (${cur.condition_text.toLowerCase()}). Para los próximos ${daily.length} días se estima un rango entre ${minW} °C y ${maxW} °C, ${rainPhrase}`;
  }

  function weightedAvg(pairs, decimals = 1) {
    let num = 0;
    let den = 0;
    pairs.forEach(([v, w]) => {
      if (v === null || v === undefined || Number.isNaN(Number(v)) || w <= 0) return;
      num += Number(v) * w;
      den += w;
    });
    if (den <= 0) return null;
    return Number((num / den).toFixed(decimals));
  }

  async function fetchAndSynthesize(loc, days = 7) {
    const fetchDays = Math.max(days, 7);
    const accDash = computeProviderAccuracyStats(loc);
    const accMap = {};
    accDash.ranking.forEach((r) => {
      accMap[r.provider_id] = r;
    });

    // Ejecutar únicamente 2 llamadas HTTP paralelas: lote 6 modelos + wttr.in independiente
    const [omSnaps, wttrSnap] = await Promise.all([
      fetchBatchedOpenMeteoAdapters(loc.latitude, loc.longitude, fetchDays),
      fetchWttrInAdapter(loc.latitude, loc.longitude),
    ]);

    const rawSnaps = [...omSnaps, ...(wttrSnap ? [wttrSnap] : [])];
    const validSnaps = [];
    let rejectedCount = 0;

    rawSnaps.forEach((s) => {
      const check = validateCurrentReading(s.current);
      if (!check.ok) {
        rejectedCount++;
        return;
      }
      const fr = computeFreshness(s.current.observed_at);
      const st = accMap[s.provider_id];
      const hasAcc = Boolean(st && st.has_data && st.composite_accuracy_pct !== null && st.sample_count >= 6);
      const accFactor = hasAcc ? Math.max(0.55, Math.min(1.5, 0.5 + st.composite_accuracy_pct / 100)) : 1.0;

      s.freshnessMinutes = fr.ageMin;
      s.freshnessFactor = fr.factor;
      s.historical_accuracy_pct = hasAcc ? st.composite_accuracy_pct : null;
      s.historical_samples = hasAcc ? st.sample_count : 0;
      s.prelimWeight = s.baseWeight * fr.factor * accFactor;
      validSnaps.push(s);
    });

    if (!validSnaps.length) {
      throw new Error("Ninguna fuente meteorológica devolvió lecturas válidas en este momento.");
    }

    // Resistencia a valores atípicos basada en la mediana real
    const temps = validSnaps.map((s) => Number(s.current.temperature_c));
    const sortedT = [...temps].sort((a, b) => a - b);
    const medianTemp = Number(sortedT[Math.floor(sortedT.length / 2)].toFixed(1));
    const meanTemp = Number((temps.reduce((a, b) => a + b, 0) / temps.length).toFixed(1));
    const minT = Number(Math.min(...temps).toFixed(1));
    const maxT = Number(Math.max(...temps).toFixed(1));
    const spreadT = Number((maxT - minT).toFixed(1));
    const stdT = Number(
      Math.sqrt(temps.reduce((acc, v) => acc + Math.pow(v - meanTemp, 2), 0) / Math.max(1, temps.length)).toFixed(2)
    );

    validSnaps.forEach((s) => {
      const dev = Math.abs(Number(s.current.temperature_c) - medianTemp);
      const agreeFactor = validSnaps.length >= 4 && dev >= 4.0 ? 0.55 : validSnaps.length >= 3 && dev >= 2.5 ? 0.8 : 1.0;
      s.weight = Number((s.prelimWeight * agreeFactor).toFixed(2));
    });

    validSnaps.sort((a, b) => b.weight - a.weight);
    const primary = validSnaps[0];

    // Serie completa enriquecida (garantiza UV, visibilidad, amanecer, ráfagas y prob. lluvia sin vacíos)
    const richSnap =
      validSnaps.find((s) => s.provider_id === "open_meteo" && s.hourly.length >= 24) ||
      [...validSnaps].sort((a, b) => b.daily.length + b.hourly.length - (a.daily.length + a.hourly.length))[0];

    const selectedTemp = weightedAvg(validSnaps.map((s) => [s.current.temperature_c, s.weight]), 1);
    const selectedHum = weightedAvg(validSnaps.map((s) => [s.current.humidity_pct, s.weight]), 0);
    const selectedPres = weightedAvg(validSnaps.map((s) => [s.current.pressure_hpa, s.weight]), 1);
    const selectedWind = weightedAvg(validSnaps.map((s) => [s.current.wind_speed_kmh, s.weight]), 1);
    const selectedPrec = weightedAvg(validSnaps.map((s) => [s.current.precipitation_mm, s.weight]), 2);
    const selectedFeels =
      weightedAvg(validSnaps.map((s) => [s.current.feels_like_c, s.weight]), 1) ??
      computeApparentTemp(selectedTemp, selectedHum, selectedWind);

    const baseCur = richSnap.current || primary.current;
    const selectedCurrent = {
      ...baseCur,
      temperature_c: selectedTemp ?? baseCur.temperature_c,
      feels_like_c: selectedFeels ?? baseCur.feels_like_c,
      humidity_pct: selectedHum ?? baseCur.humidity_pct,
      dew_point_c: computeDewPoint(selectedTemp, selectedHum) ?? baseCur.dew_point_c,
      pressure_hpa: selectedPres ?? baseCur.pressure_hpa,
      wind_speed_kmh: selectedWind ?? baseCur.wind_speed_kmh,
      precipitation_mm: selectedPrec ?? baseCur.precipitation_mm,
      uv_index: baseCur.uv_index ?? (richSnap.daily[0] ? richSnap.daily[0].uv_index_max : 0),
      visibility_km: baseCur.visibility_km ?? 24.0,
    };

    // Análisis de discrepancias (temperatura, lluvia y viento)
    const winds = validSnaps.map((s) => s.current.wind_speed_kmh).filter((w) => w !== null && w !== undefined);
    const windSpread = winds.length >= 2 ? Number((Math.max(...winds) - Math.min(...winds)).toFixed(1)) : 0;
    const rainSrcs = validSnaps.filter((s) => (s.current.precipitation_mm || 0) >= 0.2).map((s) => s.display_name);
    const drySrcs = validSnaps.filter((s) => (s.current.precipitation_mm || 0) < 0.1).map((s) => s.display_name);
    const precipDisagreement = Boolean(rainSrcs.length && drySrcs.length);
    const hasTempDisc = spreadT >= 2.0;
    const hasWindDisc = windSpread >= 15.0;
    const hasSig = hasTempDisc || precipDisagreement || hasWindDisc;
    const severity = spreadT >= 3.5 || (hasTempDisc && precipDisagreement) ? "alta" : hasSig ? "moderada" : "baja";

    const discNotes = [];
    if (hasTempDisc && validSnaps.length >= 2) {
      const lowP = [...validSnaps].sort((a, b) => a.current.temperature_c - b.current.temperature_c)[0];
      const highP = [...validSnaps].sort((a, b) => b.current.temperature_c - a.current.temperature_c)[0];
      discNotes.push(
        `Diferencia térmica de ${spreadT.toFixed(1)} °C entre ${lowP.display_name} (${lowP.current.temperature_c.toFixed(1)} °C) y ${highP.display_name} (${highP.current.temperature_c.toFixed(1)} °C)`
      );
    }
    if (precipDisagreement) {
      discNotes.push(`Divergencia de lluvia entre ${rainSrcs[0]} y ${drySrcs[0]}`);
    }
    if (hasWindDisc) {
      discNotes.push(`Diferencia de viento de ${Math.round(windSpread)} km/h entre modelos`);
    }

    const discrepancy = {
      has_significant_diff: hasSig,
      severity,
      temp_spread_c: spreadT,
      wind_spread_kmh: windSpread,
      precip_disagreement: precipDisagreement,
      summary: discNotes.length
        ? `${discNotes.join(". ")}.`
        : `Alta concordancia entre las ${validSnaps.length} fuentes activas (diferencia térmica de ${spreadT.toFixed(1)} °C).`,
    };

    const confidence = computeForecastConfidence(
      validSnaps,
      ALL_PROVIDERS_META.length,
      spreadT,
      stdT,
      windSpread,
      precipDisagreement
    );

    const agreement_level = severity === "baja" ? "Alto" : severity === "moderada" ? "Moderado" : "Divergente";
    const dailyOut = richSnap.daily.slice(0, days);
    const dFirst = dailyOut[0]?.date;
    const dLast = dailyOut[dailyOut.length - 1]?.date;
    const date_focus_label = dFirst && dLast ? `${fmtChartDate(dFirst)} — ${fmtChartDate(dLast)} ${dLast.slice(0, 4)}` : "Próximos 7 días";

    const verifiedCount = validSnaps.filter((s) => s.historical_samples > 0).length;
    const totalSamples = validSnaps.reduce((acc, s) => acc + (s.historical_samples || 0), 0);

    const provenance = {
      primary_provider_id: primary.provider_id,
      primary_provider_name: primary.display_name,
      primary_accuracy_pct: primary.historical_accuracy_pct,
      primary_samples: primary.historical_samples,
      sources_count: validSnaps.length,
      rejected_readings: rejectedCount,
      sources_used: validSnaps.map((s) => ({ id: s.provider_id, name: s.display_name })),
      accuracy_backing:
        verifiedCount > 0
          ? `Ponderado con ${totalSamples} verificaciones históricas reales (${verifiedCount} de ${validSnaps.length} fuentes verificadas).`
          : `Consolidado en tiempo real entre ${validSnaps.length} agencias internacionales (verificación histórica en curso).`,
    };

    const nowIso = new Date().toISOString();
    const bundle = {
      location: loc,
      provider: primary.display_name,
      fetched_at: nowIso,
      data_age_seconds: 0,
      from_database: false,
      is_stale: false,
      refreshing_in_background: false,
      theme: selectedCurrent.theme,
      date_focus_label,
      narrative: buildNarrative(loc, selectedCurrent, dailyOut),
      current: selectedCurrent,
      hourly: richSnap.hourly.slice(0, 72),
      daily: dailyOut,
      discrepancy,
      confidence,
      provenance,
      consensus_preview: {
        selected_temp_c: selectedCurrent.temperature_c,
        temp_mean_c: meanTemp,
        temp_min_c: minT,
        temp_max_c: maxT,
        agreement_level,
        active_sources: validSnaps.length,
      },
    };

    // Alinear series horarias 24h desde la hora actual para los gráficos de Fuentes
    const curHourPrefix = String(selectedCurrent.observed_at || "").slice(0, 13);
    let hStart = richSnap.hourly.findIndex((x) => String(x.time).startsWith(curHourPrefix));
    if (hStart < 0) hStart = 0;
    const hLabels = richSnap.hourly.slice(hStart, hStart + 24).map((x) => x.time);
    const dLabels = richSnap.daily.slice(0, 7).map((x) => x.date);

    const comparison = {
      location: loc,
      fetched_at: nowIso,
      from_database: false,
      refreshing_in_background: false,
      active_providers: validSnaps.length,
      total_providers: ALL_PROVIDERS_META.length,
      discrepancy,
      confidence,
      provenance,
      consensus: {
        selected_temp_c: selectedCurrent.temperature_c,
        temp_mean_c: meanTemp,
        temp_median_c: medianTemp,
        temp_min_c: minT,
        temp_max_c: maxT,
        temp_spread_c: spreadT,
        temp_std_c: stdT,
        agreement_level,
        primary_provider_name: primary.display_name,
      },
      providers: validSnaps.map((s, idx) => ({
        provider_id: s.provider_id,
        display_name: s.display_name,
        status: "ok",
        is_primary_selected: idx === 0,
        current_temp_c: s.current.temperature_c,
        temp_deviation_c: Number((s.current.temperature_c - selectedCurrent.temperature_c).toFixed(1)),
        feels_like_c: s.current.feels_like_c,
        humidity_pct: s.current.humidity_pct,
        pressure_hpa: s.current.pressure_hpa,
        wind_kmh: s.current.wind_speed_kmh,
        precip_mm: s.current.precipitation_mm,
        historical_accuracy_pct: s.historical_accuracy_pct,
        historical_samples: s.historical_samples,
        reliability_weight: s.weight,
        condition_text: s.current.condition_text,
      })),
      hourly_chart: {
        labels: hLabels,
        datasets: validSnaps
          .filter((s) => s.hourly.length >= 12)
          .map((s) => ({
            provider_id: s.provider_id,
            label: s.display_name,
            values: s.hourly.slice(hStart, hStart + 24).map((x) => x.temperature_c),
          })),
      },
      daily_chart: {
        labels: dLabels,
        datasets: validSnaps
          .filter((s) => s.daily.length >= 3)
          .map((s) => ({
            provider_id: s.provider_id,
            label: s.display_name,
            values: dLabels.map((dt) => {
              const found = s.daily.find((d) => d.date === dt);
              return found ? found.temp_max_c : null;
            }),
          })),
      },
    };

    // Lanzar verificación histórica real en segundo plano SIN bloquear la pintura inicial
    ensureHistoricalVerification(loc, 7, false).then((btRes) => {
      if (btRes && btRes.newly_verified > 0) {
        // Actualizar caché en memoria con los nuevos respaldos reales para próximas lecturas
        const cachedObj = cacheGet(`wx_${locKey(loc.latitude, loc.longitude)}_${fetchDays}`, TTL.WEATHER_STALE);
        if (cachedObj && cachedObj.value) {
          const updatedAcc = computeProviderAccuracyStats(loc);
          const uMap = {};
          updatedAcc.ranking.forEach((r) => (uMap[r.provider_id] = r));
          cachedObj.value.comparison.providers.forEach((p) => {
            if (uMap[p.provider_id] && uMap[p.provider_id].has_data) {
              p.historical_accuracy_pct = uMap[p.provider_id].composite_accuracy_pct;
              p.historical_samples = uMap[p.provider_id].sample_count;
            }
          });
          const totalVerified = cachedObj.value.comparison.providers.reduce((a, p) => a + (p.historical_samples || 0), 0);
          if (totalVerified > 0) {
            cachedObj.value.bundle.provenance.accuracy_backing = `Ponderado con ${totalVerified} verificaciones históricas reales.`;
            cachedObj.value.comparison.provenance.accuracy_backing = cachedObj.value.bundle.provenance.accuracy_backing;
          }
          cacheSet(`wx_${locKey(loc.latitude, loc.longitude)}_${fetchDays}`, cachedObj.value, TTL.WEATHER);
        }
      }
    });

    return { bundle, comparison };
  }

  async function getCachedWeatherAndComparison(loc, days = 7) {
    const fetchDays = Math.max(days, 7);
    const lKey = locKey(loc.latitude, loc.longitude);
    const ck = `wx_${lKey}_${fetchDays}`;
    const hit = cacheGet(ck, TTL.WEATHER_STALE);

    if (hit) {
      const clonedBundle = {
        ...hit.value.bundle,
        location: { ...hit.value.bundle.location, ...loc },
        daily: hit.value.bundle.daily.slice(0, days),
        from_database: true,
        is_stale: hit.isStale,
        data_age_seconds: hit.ageSeconds,
        refreshing_in_background: hit.isStale,
      };
      const clonedComp = {
        ...hit.value.comparison,
        location: { ...hit.value.comparison.location, ...loc },
        from_database: true,
        refreshing_in_background: hit.isStale,
      };
      if (hit.isStale) {
        // Stale-While-Revalidate: refrescar en segundo plano sin bloquear la UI
        dedupe(`swr_${ck}`, async () => {
          const fresh = await fetchAndSynthesize(loc, fetchDays);
          cacheSet(ck, fresh, TTL.WEATHER);
          return fresh;
        }).catch(() => {});
      }
      return { bundle: clonedBundle, comparison: clonedComp };
    }

    return dedupe(`fetch_${ck}`, async () => {
      const fresh = await fetchAndSynthesize(loc, fetchDays);
      cacheSet(ck, fresh, TTL.WEATHER);
      return {
        bundle: { ...fresh.bundle, daily: fresh.bundle.daily.slice(0, days) },
        comparison: fresh.comparison,
      };
    });
  }

  /* ── Historial Climático Real ERA5 con Caché 6 h ─────────────────────── */
  async function staticGetHistory(params = {}) {
    const loc = await resolveLocationParams(params);
    const endDefault = new Date();
    endDefault.setDate(endDefault.getDate() - 1);
    const startDefault = new Date(endDefault);
    startDefault.setDate(endDefault.getDate() - 29);

    const start_date = params.start_date || startDefault.toISOString().slice(0, 10);
    const end_date = params.end_date || endDefault.toISOString().slice(0, 10);
    const ck = `hist_${locKey(loc.latitude, loc.longitude)}_${start_date}_${end_date}`;
    const hit = cacheGet(ck);
    if (hit) return { ...hit.value, location: loc };

    return dedupe(ck, async () => {
      const dailyFields = [
        "weather_code",
        "temperature_2m_max",
        "temperature_2m_min",
        "temperature_2m_mean",
        "precipitation_sum",
        "wind_speed_10m_max",
        "wind_gusts_10m_max",
      ].join(",");

      const url = `https://archive-api.open-meteo.com/v1/archive?latitude=${loc.latitude}&longitude=${loc.longitude}&start_date=${start_date}&end_date=${end_date}&daily=${dailyFields}&timezone=auto`;
      const d = await fetchJsonWithTimeout(url, 10000);
      const dy = d.daily || {};

      const daily = (dy.time || [])
        .map((dt, i) => {
          const tMax = dy.temperature_2m_max ? dy.temperature_2m_max[i] : null;
          const tMin = dy.temperature_2m_min ? dy.temperature_2m_min[i] : null;
          if (tMax === null && tMin === null) return null;
          const tMean =
            dy.temperature_2m_mean && dy.temperature_2m_mean[i] !== null
              ? dy.temperature_2m_mean[i]
              : tMax !== null && tMin !== null
              ? Number(((tMax + tMin) / 2).toFixed(1))
              : null;
          const wc = dy.weather_code ? dy.weather_code[i] : 0;
          const wmo = decodeWmo(wc, tMax);
          return {
            date: dt,
            weather_code: wc,
            condition_text: wmo.text,
            condition_icon: wmo.icon,
            temp_max_c: tMax,
            temp_min_c: tMin,
            temp_mean_c: tMean,
            precipitation_sum_mm: dy.precipitation_sum ? dy.precipitation_sum[i] ?? 0 : 0,
            wind_speed_max_kmh: dy.wind_speed_10m_max ? dy.wind_speed_10m_max[i] ?? 0 : 0,
            wind_gusts_max_kmh: dy.wind_gusts_10m_max ? dy.wind_gusts_10m_max[i] ?? 0 : 0,
          };
        })
        .filter(Boolean);

      let maxRow = daily[0] || {};
      let minRow = daily[0] || {};
      let wetRow = daily[0] || {};
      let windRow = daily[0] || {};
      let precipTotal = 0;
      let tempSum = 0;
      let hotDays = 0;
      let frostDays = 0;
      let rainyDays = 0;

      daily.forEach((row) => {
        if ((row.temp_max_c ?? -999) > (maxRow.temp_max_c ?? -999)) maxRow = row;
        if ((row.temp_min_c ?? 999) < (minRow.temp_min_c ?? 999)) minRow = row;
        if ((row.precipitation_sum_mm ?? 0) > (wetRow.precipitation_sum_mm ?? 0)) wetRow = row;
        if ((row.wind_speed_max_kmh ?? 0) > (windRow.wind_speed_max_kmh ?? 0)) windRow = row;
        precipTotal += row.precipitation_sum_mm || 0;
        tempSum += row.temp_mean_c ?? row.temp_max_c ?? 0;
        if ((row.temp_max_c ?? 0) >= 30) hotDays++;
        if ((row.temp_min_c ?? 99) <= 0) frostDays++;
        if ((row.precipitation_sum_mm ?? 0) >= 1.0) rainyDays++;
      });

      const summary = {
        temp_max_c: maxRow.temp_max_c ?? null,
        temp_max_date: maxRow.date || "—",
        temp_min_c: minRow.temp_min_c ?? null,
        temp_min_date: minRow.date || "—",
        temp_avg_c: daily.length ? Number((tempSum / daily.length).toFixed(1)) : null,
        precip_total_mm: Number(precipTotal.toFixed(1)),
        wettest_day_mm: Number((wetRow.precipitation_sum_mm || 0).toFixed(1)),
        wettest_day: wetRow.date || "—",
        wind_max_kmh: Number((windRow.wind_speed_max_kmh || 0).toFixed(1)),
        windiest_day: windRow.date || "—",
        hot_days: hotDays,
        frost_days: frostDays,
        rainy_days: rainyDays,
      };

      const out = {
        location: loc,
        start_date,
        end_date,
        days_count: daily.length,
        local_observations_count: daily.length,
        narrative: `Entre ${start_date} y ${end_date} (${daily.length} días) en ${loc.name}, la temperatura osciló entre ${summary.temp_min_c ?? "—"} °C (${summary.temp_min_date}) y ${summary.temp_max_c ?? "—"} °C (${summary.temp_max_date}), acumulando ${summary.precip_total_mm} mm de lluvia en ${rainyDays} días lluviosos.`,
        summary,
        daily,
      };
      cacheSet(ck, out, TTL.HISTORY);
      return out;
    });
  }

  /* ── Comparador Multi-Ciudad ─────────────────────────────────────────── */
  async function staticCompareCities(params = {}) {
    const rawCities = String(params.cities || "Madrid, Buenos Aires, Bogotá")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .slice(0, 5);

    const days = Number(params.days || 7);
    const isHist = Boolean(params.start_date && params.end_date);

    const cityResults = await Promise.all(
      rawCities.map(async (cName) => {
        const loc = await resolveLocationParams({ city: cName });
        if (isHist) {
          const h = await staticGetHistory({
            lat: loc.latitude,
            lon: loc.longitude,
            name: loc.name,
            admin1: loc.admin1,
            country: loc.country,
            start_date: params.start_date,
            end_date: params.end_date,
          });
          return {
            location: loc,
            daily: h.daily,
            temp_max_c: h.summary.temp_max_c,
            temp_min_c: h.summary.temp_min_c,
            precip_total_mm: h.summary.precip_total_mm,
            wind_max_kmh: h.summary.wind_max_kmh,
          };
        }
        const { bundle } = await getCachedWeatherAndComparison(loc, days);
        const dl = bundle.daily;
        return {
          location: loc,
          daily: dl,
          temp_max_c: Math.max(...dl.map((d) => d.temp_max_c ?? -99)),
          temp_min_c: Math.min(...dl.map((d) => d.temp_min_c ?? 99)),
          precip_total_mm: Number(dl.reduce((s, d) => s + (d.precipitation_sum_mm || 0), 0).toFixed(1)),
          wind_max_kmh: Math.max(...dl.map((d) => d.wind_speed_max_kmh ?? 0)),
        };
      })
    );

    const dates = (cityResults[0]?.daily || []).map((d) => d.date);
    const warmest = [...cityResults].sort((a, b) => b.temp_max_c - a.temp_max_c)[0];
    const wettest = [...cityResults].sort((a, b) => b.precip_total_mm - a.precip_total_mm)[0];

    return {
      mode: "multi_city",
      theme: "clear",
      date_focus_label: dates.length ? `${fmtChartDate(dates[0])} — ${fmtChartDate(dates[dates.length - 1])}` : "Comparativa",
      narrative: `Comparando ${cityResults.map((c) => c.location.name).join(", ")}: ${warmest.location.name} registra la máxima más elevada (${warmest.temp_max_c.toFixed(1)} °C) y ${wettest.location.name} encabeza el acumulado de precipitación (${wettest.precip_total_mm.toFixed(1)} mm).`,
      dates,
      cities: cityResults,
      temp_series: cityResults.map((c) => ({ label: c.location.name, values: c.daily.map((d) => d.temp_max_c) })),
      precip_series: cityResults.map((c) => ({ label: c.location.name, values: c.daily.map((d) => Number((d.precipitation_sum_mm || 0).toFixed(1))) })),
      wind_series: cityResults.map((c) => ({ label: c.location.name, values: c.daily.map((d) => d.wind_speed_max_kmh) })),
    };
  }

  /* ── Intérprete Determinístico de Consultas ──────────────────────────── */
  async function staticPostQuery(body = {}) {
    const q = String(body.query || "").trim();
    const low = normKey(q);

    // 1. Comparación multi-ciudad
    if (low.startsWith("comparar") || (q.includes(",") && q.split(",").length >= 2 && !/\d+\.\d+\s*,\s*-?\d+\.\d+/.test(q))) {
      const cleaned = q
        .replace(/^(comparar\s+(el\s+clima\s+en|clima\s+de|ciudades)?)/i, "")
        .replace(/\s+y\s+/gi, ", ")
        .trim();
      const cities = cleaned
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      if (cities.length >= 2) {
        const mc = await staticCompareCities({ cities: cities.join(", "), days: 7 });
        return { mode: "multi_city", multi_city: mc, theme: mc.theme, date_focus_label: mc.date_focus_label };
      }
    }

    // 2. Coordenadas directas ("-31.41, -64.18")
    const coordMatch = q.match(/^(-?\d{1,2}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)$/);
    let loc = null;
    if (coordMatch) {
      const lat = Number(coordMatch[1]);
      const lon = Number(coordMatch[2]);
      if (lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180) {
        loc = await resolveLocationParams({ lat, lon, location_source: "search" });
      }
    }

    // 3. Extraer ciudad o usar referencia geolocalizada
    if (!loc) {
      const cityExtract = q
        .replace(/^[¿?¡!]+|[¿?¡!]+$/g, "")
        .replace(
          /\b(como esta el clima|clima actual|clima en|tiempo en|pronostico en|pronostico para|cuanto llovio en|historial del mes pasado en|historial en|el mes pasado|este ano|ayer|hoy|ahora)\b/gi,
          " "
        )
        .replace(/\s+/g, " ")
        .trim();

      if (cityExtract && cityExtract.length >= 2) {
        loc = await resolveLocationParams({ city: cityExtract });
      } else if (body.lat !== undefined && body.lon !== undefined) {
        loc = await resolveLocationParams({
          lat: body.lat,
          lon: body.lon,
          name: body.name,
          admin1: body.admin1,
          country: body.country,
          location_source: body.location_source || "ip",
        });
      } else {
        loc = (await staticGeolocateIp()).location;
      }
    }

    // 4. Consulta histórica
    if (/\b(mes pasado|llovio|historial|archivo|este ano)\b/.test(low)) {
      const end = new Date();
      end.setDate(end.getDate() - 1);
      const start = new Date(end);
      start.setDate(end.getDate() - 29);
      const h = await staticGetHistory({
        lat: loc.latitude,
        lon: loc.longitude,
        name: loc.name,
        admin1: loc.admin1,
        country: loc.country,
        start_date: start.toISOString().slice(0, 10),
        end_date: end.toISOString().slice(0, 10),
      });
      return {
        mode: "single_history",
        history: h,
        theme: "rain",
        date_focus_label: `${fmtChartDate(h.start_date)} — ${fmtChartDate(h.end_date)}`,
      };
    }

    // 5. Pronóstico consolidado con caché SWR
    const { bundle } = await getCachedWeatherAndComparison(loc, 7);
    return { mode: "single_forecast", weather: bundle, theme: bundle.theme, date_focus_label: bundle.date_focus_label };
  }

  /* ── Radar RainViewer en Vivo con Caché 5 min ────────────────────────── */
  async function staticGetRadar() {
    const ck = "radar_rainviewer_meta";
    const hit = cacheGet(ck);
    if (hit) return hit.value;
    return dedupe(ck, async () => {
      const d = await fetchJsonWithTimeout("https://api.rainviewer.com/public/weather-maps.json", 7000);
      const host = d.host || "https://tilecache.rainviewer.com";
      const mapFrames = (arr, pathSuffix) =>
        (arr || []).map((f) => ({
          time: f.time,
          path: f.path,
          tile_url: `${host}${f.path}${pathSuffix}`,
        }));
      const out = {
        host,
        radar_past: mapFrames(d.radar && d.radar.past, "/256/{z}/{x}/{y}/2/1_1.png"),
        radar_nowcast: mapFrames(d.radar && d.radar.nowcast, "/256/{z}/{x}/{y}/2/1_1.png"),
        satellite_infrared: mapFrames(d.satellite && d.satellite.infrared, "/256/{z}/{x}/{y}/0/0_0.png"),
      };
      cacheSet(ck, out, TTL.RADAR, false);
      return out;
    });
  }

  /* ── Despachador Híbrido (FastAPI Backend vs Motor Autónomo Navegador) ── */
  async function request(path, options = {}) {
    if (!useStaticEngine) {
      try {
        const resp = await fetch(`${BASE}${path}`, {
          headers: { "Content-Type": "application/json", ...(options.headers || {}) },
          ...options,
        });
        const contentType = resp.headers.get("content-type") || "";
        if (resp.ok && contentType.includes("application/json")) {
          return await resp.json();
        }
        if (resp.status === 404 || resp.status === 405 || !contentType.includes("application/json")) {
          useStaticEngine = true;
        } else {
          const errData = contentType.includes("application/json") ? await resp.json() : await resp.text();
          throw new Error((errData && errData.detail) || `Error HTTP ${resp.status}`);
        }
      } catch (err) {
        if (err instanceof TypeError || String(err.message).includes("Failed to fetch")) {
          useStaticEngine = true;
        } else {
          throw err;
        }
      }
    }
    throw new Error("STATIC_FALLBACK");
  }

  async function callWithFallback(apiPath, options, staticFn) {
    if (!useStaticEngine) {
      try {
        return await request(apiPath, options);
      } catch (err) {
        if (err.message !== "STATIC_FALLBACK" && !useStaticEngine) throw err;
      }
    }
    return staticFn();
  }

  function qs(params) {
    const sp = new URLSearchParams();
    Object.entries(params || {}).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
    });
    const s = sp.toString();
    return s ? `?${s}` : "";
  }

  /* ── Formateadores de Presentación ───────────────────────────────────── */
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
    isStaticMode: () => useStaticEngine,
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

    health: () =>
      callWithFallback("/health", {}, async () => ({
        status: "ok",
        app: "ClimaNexo",
        mode: "github_pages_autonomous",
      })),

    geolocateIp: () => callWithFallback("/geolocate", {}, () => staticGeolocateIp()),

    geocode: (q, count = 7) => callWithFallback(`/geocode${qs({ q, count })}`, {}, () => staticGeocode(q, count)),

    reverseGeocode: (lat, lon, sourceTag = "gps") =>
      callWithFallback(`/reverse-geocode${qs({ lat, lon })}`, {}, () => staticReverseGeocode(lat, lon, sourceTag)),

    getWeather: (params = {}) =>
      callWithFallback(`/weather${qs(params)}`, {}, async () => {
        const loc = await resolveLocationParams(params);
        const { bundle } = await getCachedWeatherAndComparison(loc, Number(params.days || 7));
        return bundle;
      }),

    postQuery: (body = {}) =>
      callWithFallback("/query", { method: "POST", body: JSON.stringify(body) }, () => staticPostQuery(body)),

    compareProviders: (params = {}) =>
      callWithFallback(`/compare/providers${qs(params)}`, {}, async () => {
        const loc = await resolveLocationParams(params);
        const { comparison } = await getCachedWeatherAndComparison(loc, Number(params.days || 7));
        return comparison;
      }),

    compareCities: (params = {}) =>
      callWithFallback(`/compare/cities${qs(params)}`, {}, () => staticCompareCities(params)),

    getAccuracy: (params = {}) =>
      callWithFallback(`/accuracy${qs(params)}`, {}, async () => {
        let loc = null;
        if (params.scope !== "global" && params.lat !== undefined && params.lon !== undefined) {
          loc = await resolveLocationParams(params);
          await ensureHistoricalVerification(loc, 7, false);
        }
        return computeProviderAccuracyStats(loc, params.source_type || "");
      }),

    runBacktest: (params = {}) =>
      callWithFallback(`/accuracy/backtest${qs(params)}`, { method: "POST" }, async () => {
        const loc = await resolveLocationParams(params);
        const ingested = await ensureHistoricalVerification(loc, Number(params.past_days || 7), true);
        const dashboard = computeProviderAccuracyStats(loc);
        return { ingested, dashboard };
      }),

    getHistory: (params = {}) => callWithFallback(`/history${qs(params)}`, {}, () => staticGetHistory(params)),

    historyExportUrl: (params = {}) => `${BASE}/history/export${qs(params)}`,

    exportHistoryCsv: async (params = {}, historyData = null) => {
      if (!useStaticEngine) {
        window.open(`${BASE}/history/export${qs(params)}`, "_blank");
        return;
      }
      const h = historyData || (await staticGetHistory(params));
      const lines = ["Fecha,Condicion,Temp Max (C),Temp Min (C),Temp Media (C),Precipitacion (mm),Viento Max (km/h),Rafagas (km/h)"];
      (h.daily || []).forEach((d) => {
        lines.push(
          [
            d.date,
            `"${String(d.condition_text || "").replace(/"/g, '""')}"`,
            d.temp_max_c ?? "",
            d.temp_min_c ?? "",
            d.temp_mean_c ?? "",
            d.precipitation_sum_mm ?? 0,
            d.wind_speed_max_kmh ?? 0,
            d.wind_gusts_max_kmh ?? 0,
          ].join(",")
        );
      });
      const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `climanexo_historial_${h.location?.name || "ciudad"}_${h.start_date}_${h.end_date}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    },

    getRadar: () => callWithFallback("/radar", {}, () => staticGetRadar()),

    toggleFavorite: (id, is_favorite) =>
      callWithFallback(`/locations/${id}/favorite${qs({ is_favorite })}`, { method: "POST" }, async () => {
        const st = loadStore();
        st.favorites[id] = Boolean(is_favorite);
        saveStore(st);
        return { location: { id, is_favorite: Boolean(is_favorite) } };
      }),
  };
})();
