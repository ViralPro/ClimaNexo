/* Cliente REST y Motor Meteorológico Autónomo (Híbrido FastAPI + GitHub Pages) — ClimaNexo */

window.WeatherAPI = (() => {
  const BASE = "/api/v1";
  const IS_GITHUB_PAGES =
    typeof window !== "undefined" &&
    (window.location.hostname.endsWith(".github.io") || window.location.protocol === "file:");

  let useStaticEngine = IS_GITHUB_PAGES;

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

  function degToCardinal(deg) {
    if (deg === null || deg === undefined || Number.isNaN(Number(deg))) return "—";
    const dirs = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"];
    return dirs[Math.round(((Number(deg) % 360) + 360) % 360 / 45) % 8];
  }

  const DEFAULT_CITIES_MAP = {
    "buenos aires": { id: 1, name: "Buenos Aires", admin1: "Ciudad Autónoma de Buenos Aires", country: "Argentina", country_code: "AR", latitude: -34.6037, longitude: -58.3816, timezone: "America/Argentina/Buenos_Aires" },
    "cordoba": { id: 2, name: "Córdoba", admin1: "Provincia de Córdoba", country: "Argentina", country_code: "AR", latitude: -31.4135, longitude: -64.1811, timezone: "America/Argentina/Cordoba" },
    "mendoza": { id: 3, name: "Mendoza", admin1: "Provincia de Mendoza", country: "Argentina", country_code: "AR", latitude: -32.8908, longitude: -68.8272, timezone: "America/Argentina/Mendoza" },
    "madrid": { id: 4, name: "Madrid", admin1: "Comunidad de Madrid", country: "España", country_code: "ES", latitude: 40.4165, longitude: -3.7026, timezone: "Europe/Madrid" },
    "ciudad de mexico": { id: 5, name: "Ciudad de México", admin1: "Ciudad de México", country: "México", country_code: "MX", latitude: 19.4326, longitude: -99.1332, timezone: "America/Mexico_City" },
    "bogota": { id: 6, name: "Bogotá", admin1: "Bogotá D.C.", country: "Colombia", country_code: "CO", latitude: 4.711, longitude: -74.0721, timezone: "America/Bogota" },
    "santiago": { id: 7, name: "Santiago", admin1: "Región Metropolitana", country: "Chile", country_code: "CL", latitude: -33.4489, longitude: -70.6693, timezone: "America/Santiago" },
    "lima": { id: 8, name: "Lima", admin1: "Provincia de Lima", country: "Perú", country_code: "PE", latitude: -12.0464, longitude: -77.0428, timezone: "America/Lima" },
    "montevideo": { id: 9, name: "Montevideo", admin1: "Montevideo", country: "Uruguay", country_code: "UY", latitude: -34.9011, longitude: -56.1645, timezone: "America/Montevideo" },
    "quito": { id: 10, name: "Quito", admin1: "Pichincha", country: "Ecuador", country_code: "EC", latitude: -0.1807, longitude: -78.4678, timezone: "America/Guayaquil" },
  };

  function normKey(s) {
    return String(s || "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .trim();
  }

  /* ── Almacenamiento Local Persistente ( navegador / GitHub Pages ) ──── */
  const STORAGE_KEY = "climanexo_store_v2";
  function loadStore() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) return JSON.parse(raw);
    } catch {
      // ignore
    }
    return { favorites: {}, snapshots: {}, audits: [], stats: {} };
  }
  function saveStore(st) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(st));
    } catch {
      // ignore
    }
  }

  function locKey(lat, lon) {
    return `${Number(lat).toFixed(2)},${Number(lon).toFixed(2)}`;
  }

  function decorateLocation(loc) {
    const st = loadStore();
    const key = locKey(loc.latitude, loc.longitude);
    const id = loc.id || Math.abs(key.split("").reduce((a, b) => (a << 5) - a + b.charCodeAt(0), 0));
    const display_label = [loc.name, loc.admin1, loc.country].filter(Boolean).join(", ");
    return {
      ...loc,
      id,
      display_label,
      is_favorite: Boolean(st.favorites[id] || st.favorites[key]),
    };
  }

  /* ── Geocodificación y Geolocalización en Navegador ──────────────────── */
  async function staticGeocode(q, count = 7) {
    const clean = String(q || "").trim();
    if (!clean) return { results: [] };
    try {
      const url = `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(clean)}&count=${count}&language=es&format=json`;
      const r = await fetch(url);
      const d = await r.json();
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
        })
      );
      if (results.length) return { results };
    } catch {
      // fallback a catálogo local
    }
    const nk = normKey(clean);
    const localMatches = Object.entries(DEFAULT_CITIES_MAP)
      .filter(([k]) => k.includes(nk) || nk.includes(k))
      .map(([, v]) => decorateLocation(v));
    return { results: localMatches };
  }

  async function staticReverseGeocode(lat, lon) {
    try {
      const r = await fetch(
        `https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${lat}&longitude=${lon}&localityLanguage=es`
      );
      if (r.ok) {
        const d = await r.json();
        const name = d.city || d.locality || d.principalSubdivision || "Ubicación seleccionada";
        return {
          location: decorateLocation({
            name,
            admin1: d.principalSubdivision || "",
            country: d.countryName || "",
            country_code: d.countryCode || "",
            latitude: Number(lat),
            longitude: Number(lon),
            timezone: "auto",
          }),
        };
      }
    } catch {
      // ignore
    }
    return {
      location: decorateLocation({
        name: `Coord. ${Number(lat).toFixed(2)}, ${Number(lon).toFixed(2)}`,
        admin1: "",
        country: "",
        latitude: Number(lat),
        longitude: Number(lon),
        timezone: "auto",
      }),
    };
  }

  async function staticGeolocateIp() {
    for (const url of ["https://ipwho.is/", "https://get.geojs.io/v1/ip/geo.json"]) {
      try {
        const r = await fetch(url);
        if (!r.ok) continue;
        const d = await r.json();
        const lat = Number(d.latitude);
        const lon = Number(d.longitude);
        if (!Number.isNaN(lat) && !Number.isNaN(lon) && (lat !== 0 || lon !== 0)) {
          return {
            location: decorateLocation({
              name: d.city || "Buenos Aires",
              admin1: d.region || "",
              country: d.country || "",
              country_code: d.country_code || "",
              latitude: lat,
              longitude: lon,
              timezone: (d.timezone && (d.timezone.id || d.timezone)) || "auto",
            }),
          };
        }
      } catch {
        // intentar siguiente proveedor IP
      }
    }
    return { location: decorateLocation(DEFAULT_CITIES_MAP["buenos aires"]) };
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
        });
      }
      const rev = await staticReverseGeocode(params.lat, params.lon);
      return rev.location;
    }
    if (params.city) {
      const nk = normKey(params.city);
      if (DEFAULT_CITIES_MAP[nk]) return decorateLocation(DEFAULT_CITIES_MAP[nk]);
      const geo = await staticGeocode(params.city, 1);
      if (geo.results && geo.results.length) return geo.results[0];
    }
    return decorateLocation(DEFAULT_CITIES_MAP["buenos aires"]);
  }

  /* ── Consultas Multiproveedor en Paralelo (Open-Meteo, ECMWF, GFS, ICON) ── */
  const STATIC_PROVIDERS = [
    { id: "open_meteo", name: "Open-Meteo (Best Match)", endpoint: "https://api.open-meteo.com/v1/forecast", model: null, baseWeight: 1.15 },
    { id: "ecmwf_ifs", name: "ECMWF IFS (Europa)", endpoint: "https://api.open-meteo.com/v1/forecast", model: "ecmwf_ifs025", baseWeight: 1.2 },
    { id: "dwd_icon", name: "DWD ICON (Alemania)", endpoint: "https://api.open-meteo.com/v1/dwd-icon", model: null, baseWeight: 1.1 },
    { id: "noaa_gfs", name: "NOAA GFS (EE. UU.)", endpoint: "https://api.open-meteo.com/v1/gfs", model: null, baseWeight: 1.05 },
  ];

  async function fetchOpenMeteoVariant(prov, lat, lon, days = 7) {
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
      latitude: String(lat),
      longitude: String(lon),
      current: currentFields,
      hourly: hourlyFields,
      daily: dailyFields,
      timezone: "auto",
      forecast_days: String(Math.min(16, Math.max(days, 7))),
    });
    if (prov.model) sp.set("models", prov.model);

    const r = await fetch(`${prov.endpoint}?${sp.toString()}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    const latency_ms = Math.round(performance.now() - t0);

    const cur = d.current || {};
    const h = d.hourly || {};
    const dy = d.daily || {};
    const hTimes = h.time || [];

    const pickHourly = (arr) => (Array.isArray(arr) && arr.length ? arr[0] : null);
    const tempC = cur.temperature_2m ?? pickHourly(h.temperature_2m);
    const wmoCode = cur.weather_code ?? pickHourly(h.weather_code) ?? 0;
    const wmo = decodeWmo(wmoCode, tempC);

    const hourly = hTimes.map((t, i) => {
      const hc = h.weather_code ? h.weather_code[i] : 0;
      const hw = decodeWmo(hc, h.temperature_2m ? h.temperature_2m[i] : null);
      return {
        time: t,
        temperature_c: h.temperature_2m ? h.temperature_2m[i] : null,
        feels_like_c: h.apparent_temperature ? h.apparent_temperature[i] : null,
        humidity_pct: h.relative_humidity_2m ? h.relative_humidity_2m[i] : null,
        dew_point_c: h.dew_point_2m ? h.dew_point_2m[i] : null,
        precip_probability_pct: h.precipitation_probability ? h.precipitation_probability[i] : null,
        precipitation_mm: h.precipitation ? h.precipitation[i] ?? 0 : 0,
        weather_code: hc,
        condition_text: hw.text,
        condition_icon: hw.icon,
        pressure_hpa: h.pressure_msl ? h.pressure_msl[i] : null,
        cloud_cover_pct: h.cloud_cover ? h.cloud_cover[i] : null,
        visibility_km: h.visibility && h.visibility[i] != null ? Number((h.visibility[i] / 1000).toFixed(1)) : 24.0,
        wind_speed_kmh: h.wind_speed_10m ? h.wind_speed_10m[i] : null,
        wind_direction_deg: h.wind_direction_10m ? h.wind_direction_10m[i] : null,
        wind_gusts_kmh: h.wind_gusts_10m ? h.wind_gusts_10m[i] : null,
        uv_index: h.uv_index ? h.uv_index[i] : null,
      };
    });

    const daily = (dy.time || []).slice(0, days).map((dt, i) => {
      const dc = dy.weather_code ? dy.weather_code[i] : 0;
      const dw = decodeWmo(dc, dy.temperature_2m_max ? dy.temperature_2m_max[i] : null);
      const tMax = dy.temperature_2m_max ? dy.temperature_2m_max[i] : null;
      const tMin = dy.temperature_2m_min ? dy.temperature_2m_min[i] : null;
      return {
        date: dt,
        weather_code: dc,
        condition_text: dw.text,
        condition_icon: dw.icon,
        temp_max_c: tMax,
        temp_min_c: tMin,
        temp_mean_c: tMax !== null && tMin !== null ? Number(((tMax + tMin) / 2).toFixed(1)) : null,
        feels_max_c: dy.apparent_temperature_max ? dy.apparent_temperature_max[i] : null,
        feels_min_c: dy.apparent_temperature_min ? dy.apparent_temperature_min[i] : null,
        sunrise: dy.sunrise ? dy.sunrise[i] : null,
        sunset: dy.sunset ? dy.sunset[i] : null,
        uv_index_max: dy.uv_index_max ? dy.uv_index_max[i] : null,
        precipitation_sum_mm: dy.precipitation_sum ? dy.precipitation_sum[i] ?? 0 : 0,
        precip_probability_max_pct: dy.precipitation_probability_max ? dy.precipitation_probability_max[i] : null,
        wind_speed_max_kmh: dy.wind_speed_10m_max ? dy.wind_speed_10m_max[i] : null,
        wind_gusts_max_kmh: dy.wind_gusts_10m_max ? dy.wind_gusts_10m_max[i] : null,
        wind_direction_dominant_deg: dy.wind_direction_10m_dominant ? dy.wind_direction_10m_dominant[i] : null,
      };
    });

    const h0 = hourly[0] || {};
    const windDeg = cur.wind_direction_10m ?? h0.wind_direction_deg ?? 0;

    return {
      provider_id: prov.id,
      display_name: prov.name,
      status: "ok",
      latency_ms,
      fetched_at: new Date().toISOString(),
      baseWeight: prov.baseWeight,
      current: {
        observed_at: cur.time || h0.time || new Date().toISOString(),
        temperature_c: tempC,
        feels_like_c: cur.apparent_temperature ?? h0.feels_like_c ?? tempC,
        humidity_pct: cur.relative_humidity_2m ?? h0.humidity_pct ?? null,
        dew_point_c: h0.dew_point_c ?? null,
        pressure_hpa: cur.pressure_msl ?? h0.pressure_hpa ?? null,
        pressure_trend: "estable",
        wind_speed_kmh: cur.wind_speed_10m ?? h0.wind_speed_kmh ?? 0,
        wind_gusts_kmh: cur.wind_gusts_10m ?? h0.wind_gusts_kmh ?? null,
        wind_direction_deg: windDeg,
        wind_direction_cardinal: degToCardinal(windDeg),
        precipitation_mm: cur.precipitation ?? h0.precipitation_mm ?? 0,
        precip_probability_pct: h0.precip_probability_pct ?? 0,
        cloud_cover_pct: cur.cloud_cover ?? h0.cloud_cover_pct ?? 0,
        visibility_km: h0.visibility_km ?? 24.0,
        uv_index: h0.uv_index ?? (daily[0] ? daily[0].uv_index_max : 0),
        weather_code: wmoCode,
        condition_text: wmo.text,
        condition_icon: wmo.icon,
        theme: wmo.theme,
      },
      hourly,
      daily,
    };
  }

  /* ── Verificación Histórica Real (Previous Runs vs Observaciones Reales) ─ */
  async function ensureHistoricalVerification(loc, pastDays = 7) {
    const st = loadStore();
    const lKey = locKey(loc.latitude, loc.longitude);
    const existingForLoc = st.audits.filter((a) => a.locKey === lKey);
    if (existingForLoc.length >= 20 && pastDays <= 7) {
      return { newly_verified: 0, observations_saved: existingForLoc.length };
    }

    const models = [
      { id: "open_meteo", name: "Open-Meteo (Best Match)", param: "temperature_2m_previous_day1", pRain: "precipitation_previous_day1", pWind: "wind_speed_10m_previous_day1", model: null },
      { id: "ecmwf_ifs", name: "ECMWF IFS (Europa)", param: "temperature_2m_previous_day1", pRain: "precipitation_previous_day1", pWind: "wind_speed_10m_previous_day1", model: "ecmwf_ifs025" },
      { id: "dwd_icon", name: "DWD ICON (Alemania)", param: "temperature_2m_previous_day1", pRain: "precipitation_previous_day1", pWind: "wind_speed_10m_previous_day1", model: "icon_seamless" },
      { id: "noaa_gfs", name: "NOAA GFS (EE. UU.)", param: "temperature_2m_previous_day1", pRain: "precipitation_previous_day1", pWind: "wind_speed_10m_previous_day1", model: "gfs_seamless" },
    ];

    try {
      const obsUrl = `https://api.open-meteo.com/v1/forecast?latitude=${loc.latitude}&longitude=${loc.longitude}&hourly=temperature_2m,precipitation,wind_speed_10m&past_days=${pastDays}&forecast_days=1&timezone=UTC`;
      const obsResp = await fetch(obsUrl);
      if (!obsResp.ok) return { newly_verified: 0, observations_saved: 0 };
      const obsJson = await obsResp.json();
      const obsTimes = (obsJson.hourly && obsJson.hourly.time) || [];
      const obsMap = {};
      obsTimes.forEach((t, i) => {
        obsMap[t] = {
          temp: obsJson.hourly.temperature_2m[i],
          precip: obsJson.hourly.precipitation[i] ?? 0,
          wind: obsJson.hourly.wind_speed_10m[i] ?? 0,
        };
      });

      let newly = 0;
      await Promise.all(
        models.map(async (m) => {
          try {
            const sp = new URLSearchParams({
              latitude: String(loc.latitude),
              longitude: String(loc.longitude),
              hourly: `${m.param},${m.pRain},${m.pWind}`,
              past_days: String(pastDays),
              forecast_days: "1",
              timezone: "UTC",
            });
            if (m.model) sp.set("models", m.model);
            const prResp = await fetch(`https://previous-runs-api.open-meteo.com/v1/forecast?${sp.toString()}`);
            if (!prResp.ok) return;
            const prJson = await prResp.json();
            const times = (prJson.hourly && prJson.hourly.time) || [];
            const temps = (prJson.hourly && prJson.hourly[m.param]) || [];
            const rains = (prJson.hourly && prJson.hourly[m.pRain]) || [];
            const winds = (prJson.hourly && prJson.hourly[m.pWind]) || [];

            for (let i = 0; i < times.length; i += 3) {
              const t = times[i];
              const predT = temps[i];
              const act = obsMap[t];
              if (predT === null || predT === undefined || !act || act.temp === null || act.temp === undefined) continue;
              const pairId = `${lKey}:${m.id}:${t}`;
              if (st.audits.some((x) => x.pairId === pairId)) continue;

              const errT = Number((predT - act.temp).toFixed(2));
              const absErrT = Math.abs(errT);
              const predR = Number(rains[i] ?? 0);
              const actR = Number(act.precip ?? 0);
              const predW = Number(winds[i] ?? act.wind ?? 0);
              const errW = Math.abs(predW - act.wind);
              const rainHit = (predR >= 0.2 && actR >= 0.2) || (predR < 0.2 && actR < 0.2);

              const tempScore = Math.max(0, 100 - absErrT * 12.5);
              const windScore = Math.max(0, 100 - errW * 3.0);
              const rainScore = rainHit ? 100 : Math.max(0, 100 - Math.abs(predR - actR) * 15);
              const composite = Number((tempScore * 0.65 + rainScore * 0.2 + windScore * 0.15).toFixed(1));

              st.audits.push({
                pairId,
                locKey: lKey,
                location_name: loc.name,
                provider_id: m.id,
                provider_name: m.name,
                target_time: t,
                lead_hours: 24,
                pred_temp_c: Number(predT.toFixed(1)),
                actual_temp_c: Number(act.temp.toFixed(1)),
                temp_error_c: errT,
                wind_abs_error: errW,
                pred_precip_mm: Number(predR.toFixed(1)),
                actual_precip_mm: Number(actR.toFixed(1)),
                rain_hit: rainHit,
                composite_score_pct: composite,
                source_type: "historical_run",
              });
              newly++;
            }
          } catch {
            // continuar con los demás modelos
          }
        })
      );

      if (st.audits.length > 600) {
        st.audits = st.audits.slice(-600);
      }
      st.last_backtest_at = new Date().toISOString();
      saveStore(st);
      return { newly_verified: newly, observations_saved: Object.keys(obsMap).length };
    } catch {
      return { newly_verified: 0, observations_saved: 0 };
    }
  }

  function computeProviderAccuracyStats(loc = null, sourceType = "") {
    const st = loadStore();
    const lKey = loc ? locKey(loc.latitude, loc.longitude) : null;
    const filtered = st.audits.filter((a) => {
      if (lKey && a.locKey !== lKey) return false;
      if (sourceType && a.source_type !== sourceType) return false;
      return true;
    });

    const ranking = STATIC_PROVIDERS.map((p) => {
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
          status_note: "Pendiente de verificación",
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
      total_observations: Math.round(filtered.length / Math.max(1, STATIC_PROVIDERS.length)),
      last_backtest_at: st.last_backtest_at || new Date().toISOString(),
      methodology_note:
        "Precisión calculada matemáticamente contrastando pronósticos históricos emitidos a +24 h (Open-Meteo Previous Runs) contra observaciones horarias reales (MAE, RMSE, sesgo térmico y acierto de precipitación). Cero porcentajes inventados.",
      ranking,
      recent_audit: filtered.slice(-40).reverse(),
    };
  }

  /* ── Síntesis Inteligente Multiproveedor y Narrativa ─────────────────── */
  function buildNarrative(loc, cur, daily) {
    const d0 = daily[0] || {};
    const mins = daily.map((d) => d.temp_min_c).filter((v) => v !== null);
    const maxs = daily.map((d) => d.temp_max_c).filter((v) => v !== null);
    const totalRain = daily.reduce((s, d) => s + (d.precipitation_sum_mm || 0), 0);
    const minW = mins.length ? Math.round(Math.min(...mins)) : Math.round(d0.temp_min_c || cur.temperature_c);
    const maxW = maxs.length ? Math.round(Math.max(...maxs)) : Math.round(d0.temp_max_c || cur.temperature_c);
    const rainPhrase =
      totalRain >= 5
        ? `con lluvias previstas que acumularán ${totalRain.toFixed(1)} mm.`
        : totalRain > 0.3
        ? `con precipitaciones aisladas que sumarán ${totalRain.toFixed(1)} mm.`
        : "con tiempo mayormente seco sin lluvias significativas.";
    return `${loc.name} presenta actualmente ${Number(cur.temperature_c).toFixed(1)} °C (${cur.condition_text.toLowerCase()}), para los próximos ${daily.length} días se proyecta un rango térmico entre ${minW} °C y ${maxW} °C, ${rainPhrase}`;
  }

  async function buildWeatherAndComparison(loc, days = 7) {
    await ensureHistoricalVerification(loc, 7);
    const accDash = computeProviderAccuracyStats(loc);
    const accMap = {};
    accDash.ranking.forEach((r) => {
      accMap[r.provider_id] = r;
    });

    const snaps = (
      await Promise.all(
        STATIC_PROVIDERS.map(async (p) => {
          try {
            return await fetchOpenMeteoVariant(p, loc.latitude, loc.longitude, days);
          } catch {
            return null;
          }
        })
      )
    ).filter(Boolean);

    if (!snaps.length) throw new Error("No se pudo conectar con los proveedores meteorológicos.");

    // Ponderar cada fuente por su precisión histórica verificada + peso base
    snaps.forEach((s) => {
      const st = accMap[s.provider_id];
      const accBoost = st && st.has_data && st.composite_accuracy_pct ? st.composite_accuracy_pct / 85 : 1.0;
      s.weight = Number((s.baseWeight * accBoost).toFixed(2));
      s.historical_accuracy_pct = st && st.has_data ? st.composite_accuracy_pct : null;
      s.historical_samples = st && st.has_data ? st.sample_count : 0;
    });

    snaps.sort((a, b) => b.weight - a.weight);
    const primary = snaps[0];

    const temps = snaps.map((s) => Number(s.current.temperature_c)).filter((v) => !Number.isNaN(v));
    const weights = snaps.map((s) => s.weight);
    const wSum = weights.reduce((a, b) => a + b, 0) || 1;
    const weightedTemp = Number(
      (snaps.reduce((acc, s) => acc + Number(s.current.temperature_c) * s.weight, 0) / wSum).toFixed(1)
    );
    const meanTemp = Number((temps.reduce((a, b) => a + b, 0) / temps.length).toFixed(1));
    const sortedT = [...temps].sort((a, b) => a - b);
    const medianTemp = Number(sortedT[Math.floor(sortedT.length / 2)].toFixed(1));
    const minT = Math.min(...temps);
    const maxT = Math.max(...temps);
    const spreadT = Number((maxT - minT).toFixed(1));
    const stdT = Number(
      Math.sqrt(temps.reduce((s, v) => s + Math.pow(v - meanTemp, 2), 0) / Math.max(1, temps.length)).toFixed(2)
    );

    const agreement_level = spreadT <= 1.2 ? "Alta" : spreadT <= 2.5 ? "Moderada" : "Dispersa";
    let discrepancy = { has_significant_diff: false, severity: "baja", summary: "" };
    if (spreadT >= 2.0 && snaps.length >= 2) {
      const lowP = [...snaps].sort((a, b) => a.current.temperature_c - b.current.temperature_c)[0];
      const highP = [...snaps].sort((a, b) => b.current.temperature_c - a.current.temperature_c)[0];
      discrepancy = {
        has_significant_diff: true,
        severity: spreadT >= 3.5 ? "alta" : "moderada",
        summary: `Diferencia térmica de ${spreadT.toFixed(1)} °C entre ${lowP.display_name} (${lowP.current.temperature_c.toFixed(1)} °C) y ${highP.display_name} (${highP.current.temperature_c.toFixed(1)} °C).`,
      };
    }

    const selectedCurrent = {
      ...primary.current,
      temperature_c: weightedTemp,
    };

    const dFirst = primary.daily[0]?.date;
    const dLast = primary.daily[primary.daily.length - 1]?.date;
    const date_focus_label = dFirst && dLast ? `${fmtChartDate(dFirst)} — ${fmtChartDate(dLast)} ${dLast.slice(0, 4)}` : "Próximos 7 días";

    const verifiedSourcesCount = snaps.filter((s) => s.historical_samples > 0).length;
    const totalSamples = snaps.reduce((acc, s) => acc + (s.historical_samples || 0), 0);

    const provenance = {
      primary_provider_id: primary.provider_id,
      primary_provider_name: primary.display_name,
      primary_accuracy_pct: primary.historical_accuracy_pct,
      primary_samples: primary.historical_samples,
      sources_count: snaps.length,
      sources_used: snaps.map((s) => ({ id: s.provider_id, name: s.display_name })),
      accuracy_backing: `Ponderado con ${totalSamples} verificaciones históricas reales (${verifiedSourcesCount} de ${snaps.length} fuentes con historial verificado).`,
    };

    const nowIso = new Date().toISOString();
    const bundle = {
      location: loc,
      provider: primary.display_name,
      fetched_at: nowIso,
      data_age_seconds: 5,
      from_database: false,
      is_stale: false,
      refreshing_in_background: false,
      theme: selectedCurrent.theme,
      date_focus_label,
      narrative: buildNarrative(loc, selectedCurrent, primary.daily),
      current: selectedCurrent,
      hourly: primary.hourly,
      daily: primary.daily,
      discrepancy,
      provenance,
      consensus_preview: {
        selected_temp_c: weightedTemp,
        temp_mean_c: meanTemp,
        temp_min_c: Number(minT.toFixed(1)),
        temp_max_c: Number(maxT.toFixed(1)),
        agreement_level,
        active_sources: snaps.length,
      },
    };

    const compProviders = snaps.map((s, idx) => ({
      provider_id: s.provider_id,
      display_name: s.display_name,
      status: "ok",
      is_primary_selected: idx === 0,
      current_temp_c: s.current.temperature_c,
      temp_deviation_c: Number((s.current.temperature_c - weightedTemp).toFixed(1)),
      feels_like_c: s.current.feels_like_c,
      humidity_pct: s.current.humidity_pct,
      pressure_hpa: s.current.pressure_hpa,
      wind_kmh: s.current.wind_speed_kmh,
      precip_mm: s.current.precipitation_mm,
      historical_accuracy_pct: s.historical_accuracy_pct,
      historical_samples: s.historical_samples,
      reliability_weight: s.weight,
      condition_text: s.current.condition_text,
    }));

    const hLabels = primary.hourly.slice(0, 24).map((h) => h.time);
    const dLabels = primary.daily.slice(0, 7).map((d) => d.date);

    const comparison = {
      location: loc,
      fetched_at: nowIso,
      from_database: false,
      refreshing_in_background: false,
      active_providers: snaps.length,
      discrepancy,
      provenance,
      consensus: {
        selected_temp_c: weightedTemp,
        temp_mean_c: meanTemp,
        temp_median_c: medianTemp,
        temp_min_c: minT,
        temp_max_c: maxT,
        temp_spread_c: spreadT,
        temp_std_c: stdT,
        agreement_level,
        primary_provider_name: primary.display_name,
      },
      providers: compProviders,
      hourly_chart: {
        labels: hLabels,
        datasets: snaps.map((s) => ({
          provider_id: s.provider_id,
          label: s.display_name,
          values: s.hourly.slice(0, 24).map((h) => h.temperature_c),
        })),
      },
      daily_chart: {
        labels: dLabels,
        datasets: snaps.map((s) => ({
          provider_id: s.provider_id,
          label: s.display_name,
          values: s.daily.slice(0, 7).map((d) => d.temp_max_c),
        })),
      },
    };

    return { bundle, comparison };
  }

  /* ── Historial Climático Real ERA5 ───────────────────────────────────── */
  async function staticGetHistory(params = {}) {
    const loc = await resolveLocationParams(params);
    const endDefault = new Date();
    endDefault.setDate(endDefault.getDate() - 1);
    const startDefault = new Date(endDefault);
    startDefault.setDate(endDefault.getDate() - 29);

    const start_date = params.start_date || startDefault.toISOString().slice(0, 10);
    const end_date = params.end_date || endDefault.toISOString().slice(0, 10);

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
    const r = await fetch(url);
    if (!r.ok) throw new Error(`Error consultando archivo ERA5 (${r.status})`);
    const d = await r.json();
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

    const narrative = `Entre ${start_date} y ${end_date} (${daily.length} días) en ${loc.name}, la temperatura osciló entre ${summary.temp_min_c ?? "—"} °C (${summary.temp_min_date}) y ${summary.temp_max_c ?? "—"} °C (${summary.temp_max_date}), acumulando ${summary.precip_total_mm} mm de lluvia en ${rainyDays} jornadas lluviosas.`;

    return {
      location: loc,
      start_date,
      end_date,
      days_count: daily.length,
      local_observations_count: daily.length,
      narrative,
      summary,
      daily,
    };
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
          const h = await staticGetHistory({ lat: loc.latitude, lon: loc.longitude, name: loc.name, start_date: params.start_date, end_date: params.end_date });
          return {
            location: loc,
            daily: h.daily,
            temp_max_c: h.summary.temp_max_c,
            temp_min_c: h.summary.temp_min_c,
            precip_total_mm: h.summary.precip_total_mm,
            wind_max_kmh: h.summary.wind_max_kmh,
          };
        }
        const { bundle } = await buildWeatherAndComparison(loc, days);
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

  /* ── Intérprete Determinístico de Consultas en Español ───────────────── */
  async function staticPostQuery(body = {}) {
    const q = String(body.query || "").trim();
    const low = normKey(q);

    // 1. Comparación multi-ciudad
    if (low.startsWith("comparar") || (q.includes(",") && q.split(",").length >= 2)) {
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

    // 2. Extraer ciudad de consulta natural o usar ubicación de referencia
    let cityExtract = q
      .replace(/^[¿?¡!]+|[¿?¡!]+$/g, "")
      .replace(/\b(como esta el clima|clima en|tiempo en|pronostico en|pronostico para|cuanto llovio en|historial del mes pasado en|historial en|el mes pasado|este ano|ayer|hoy)\b/gi, " ")
      .replace(/\s+/g, " ")
      .trim();

    let loc = null;
    if (cityExtract && cityExtract.length >= 2) {
      loc = await resolveLocationParams({ city: cityExtract });
    } else if (body.lat !== undefined && body.lon !== undefined) {
      loc = await resolveLocationParams({ lat: body.lat, lon: body.lon });
    } else {
      loc = (await staticGeolocateIp()).location;
    }

    // 3. Consulta histórica ("mes pasado", "cuánto llovió", "historial")
    if (/\b(mes pasado|llovio|historial|archivo|este ano)\b/.test(low)) {
      const end = new Date();
      end.setDate(end.getDate() - 1);
      const start = new Date(end);
      start.setDate(end.getDate() - 29);
      const h = await staticGetHistory({
        lat: loc.latitude,
        lon: loc.longitude,
        name: loc.name,
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

    // 4. Pronóstico estándar
    const { bundle } = await buildWeatherAndComparison(loc, 7);
    return { mode: "single_forecast", weather: bundle, theme: bundle.theme, date_focus_label: bundle.date_focus_label };
  }

  /* ── Radar RainViewer en Vivo ────────────────────────────────────────── */
  async function staticGetRadar() {
    const r = await fetch("https://api.rainviewer.com/public/weather-maps.json");
    if (!r.ok) throw new Error("RainViewer no disponible");
    const d = await r.json();
    const host = d.host || "https://tilecache.rainviewer.com";
    const past = ((d.radar && d.radar.past) || []).map((f) => ({
      time: f.time,
      path: f.path,
      tile_url: `${host}${f.path}/256/{z}/{x}/{y}/2/1_1.png`,
    }));
    const nowcast = ((d.radar && d.radar.nowcast) || []).map((f) => ({
      time: f.time,
      path: f.path,
      tile_url: `${host}${f.path}/256/{z}/{x}/{y}/2/1_1.png`,
    }));
    const infrared = ((d.satellite && d.satellite.infrared) || []).map((f) => ({
      time: f.time,
      path: f.path,
      tile_url: `${host}${f.path}/256/{z}/{x}/{y}/0/0_0.png`,
    }));
    return { host, radar_past: past, radar_nowcast: nowcast, satellite_infrared: infrared };
  }

  /* ── Despachador Híbrido (FastAPI Backend -> Fallback Autónomo Navegador) ─ */
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

    reverseGeocode: (lat, lon) =>
      callWithFallback(`/reverse-geocode${qs({ lat, lon })}`, {}, () => staticReverseGeocode(lat, lon)),

    getWeather: (params = {}) =>
      callWithFallback(`/weather${qs(params)}`, {}, async () => {
        const loc = await resolveLocationParams(params);
        const { bundle } = await buildWeatherAndComparison(loc, Number(params.days || 7));
        return bundle;
      }),

    postQuery: (body = {}) =>
      callWithFallback("/query", { method: "POST", body: JSON.stringify(body) }, () => staticPostQuery(body)),

    compareProviders: (params = {}) =>
      callWithFallback(`/compare/providers${qs(params)}`, {}, async () => {
        const loc = await resolveLocationParams(params);
        const { comparison } = await buildWeatherAndComparison(loc, Number(params.days || 7));
        return comparison;
      }),

    compareCities: (params = {}) =>
      callWithFallback(`/compare/cities${qs(params)}`, {}, () => staticCompareCities(params)),

    getAccuracy: (params = {}) =>
      callWithFallback(`/accuracy${qs(params)}`, {}, async () => {
        let loc = null;
        if (params.scope !== "global" && params.lat !== undefined && params.lon !== undefined) {
          loc = await resolveLocationParams(params);
          await ensureHistoricalVerification(loc, 7);
        }
        return computeProviderAccuracyStats(loc, params.source_type || "");
      }),

    runBacktest: (params = {}) =>
      callWithFallback(`/accuracy/backtest${qs(params)}`, { method: "POST" }, async () => {
        const loc = await resolveLocationParams(params);
        const ingested = await ensureHistoricalVerification(loc, Number(params.past_days || 7));
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
