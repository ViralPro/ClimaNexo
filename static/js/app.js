/* Controlador principal de interfaz — Weather Dashboard Pro (sencillo, rápido y sin controles técnicos) */

(() => {
  const API = window.WeatherAPI;
  const Charts = window.WeatherCharts;
  const MapCtrl = window.WeatherMap;

  const state = {
    view: "landing", // landing | dashboard
    activeTab: "overview",
    overviewMode: "single_forecast", // single_forecast | single_history | multi_city
    unitSystem: "metric", // Celsius (°C) predeterminado
    hourlyWindow: 24,
    extendedDays: 7,
    location: null,
    detectedLocation: null,
    bundle: null,
    historyData: null,
    multiCityData: null,
    providerComp: null,
    accuracyData: null,
    radarMeta: null,
    refreshTimer: null,
    lastCompactState: window.innerWidth < 560,
  };

  /* ── Utilidades UI ───────────────────────────────────────────────────── */
  function toast(msg, isError = false) {
    const region = document.getElementById("toast-region");
    if (!region) return;
    const item = document.createElement("div");
    item.className = `toast-item${isError ? " toast-error" : ""}`;
    item.textContent = msg;
    region.appendChild(item);
    setTimeout(() => item.remove(), 4200);
  }

  function setLoading(isLoading, text = "Consultando base meteorológica...") {
    const el = document.getElementById("loading-overlay");
    const txt = document.getElementById("loading-text");
    if (txt) txt.textContent = text;
    if (el) el.classList.toggle("hidden", !isLoading);
  }

  function setTheme(theme) {
    document.body.dataset.theme = theme || "clear";
  }

  function refreshIcons() {
    if (window.lucide) window.lucide.createIcons();
  }

  function updateNavScrollCue() {
    const shell = document.getElementById("main-nav-shell");
    const nav = document.getElementById("main-nav");
    if (!shell || !nav) return;
    const canScrollLeft = nav.scrollLeft > 8;
    const canScrollRight = nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 8;
    shell.classList.toggle("can-scroll-left", canScrollLeft);
    shell.classList.toggle("can-scroll-right", canScrollRight);
  }

  function switchView(viewName) {
    state.view = viewName;
    document.body.dataset.view = viewName;
    document.getElementById("view-landing").classList.toggle("hidden", viewName !== "landing");
    document.getElementById("view-dashboard").classList.toggle("hidden", viewName !== "dashboard");
    const navShell = document.getElementById("main-nav-shell");
    if (navShell) navShell.classList.toggle("hidden", viewName !== "dashboard");
    if (viewName === "dashboard") {
      setTimeout(updateNavScrollCue, 60);
    }
    if (viewName === "landing") {
      setTheme("clear");
      MapCtrl.stopPlayback();
      const lInp = document.getElementById("landing-input");
      if (lInp) lInp.value = "";
    }
  }

  async function switchTab(tabName) {
    state.activeTab = tabName;
    let activeBtn = null;
    document.querySelectorAll(".nav-pill").forEach((btn) => {
      const isMatch = btn.dataset.tab === tabName;
      btn.classList.toggle("active", isMatch);
      if (isMatch) {
        btn.setAttribute("aria-current", "page");
        activeBtn = btn;
      } else {
        btn.removeAttribute("aria-current");
      }
    });
    if (activeBtn && window.innerWidth < 768) {
      activeBtn.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
      setTimeout(updateNavScrollCue, 260);
    }
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.classList.toggle("hidden", panel.id !== `tab-${tabName}`);
    });
    if (tabName !== "map") MapCtrl.stopPlayback();
    if (window.innerWidth < 768) {
      window.scrollTo({ top: 0, behavior: "auto" });
    }

    if (!state.location && tabName !== "overview" && tabName !== "search") {
      toast("Primero seleccioná una ciudad para explorar esta sección.");
      return;
    }

    if (tabName === "search") {
      initSearchTab();
    } else if (tabName === "hourly") {
      renderHourlyTab();
    } else if (tabName === "extended") {
      await ensureExtendedDays(state.extendedDays);
    } else if (tabName === "map") {
      await loadRadarIfNeeded();
      MapCtrl.initFullMap(
        state.location.latitude,
        state.location.longitude,
        state.location.display_label || state.location.name,
        handleMapPointClick
      );
    } else if (tabName === "providers") {
      await loadProvidersComparison();
    } else if (tabName === "accuracy") {
      await loadAccuracyDashboard();
    } else if (tabName === "history") {
      await loadHistoryTab();
    } else if (tabName === "overview") {
      MapCtrl.invalidateAll();
    }
  }

  function updateActiveCityLabels() {
    const label = state.location ? state.location.name : "—";
    document.querySelectorAll(".js-active-city").forEach((el) => {
      el.textContent = label;
    });
  }

  /* ── Carga rápida orientada a caché multinivel ───────────────────────── */
  async function loadCityByCoords(lat, lon, meta = {}, days = 7, forceOverview = false) {
    switchView("dashboard");
    const shouldGoOverview = forceOverview || state.activeTab === "search";
    if (shouldGoOverview && state.activeTab !== "overview") {
      state.activeTab = "overview";
      document.querySelectorAll(".nav-pill").forEach((btn) => {
        const isMatch = btn.dataset.tab === "overview";
        btn.classList.toggle("active", isMatch);
        if (isMatch) btn.setAttribute("aria-current", "page");
        else btn.removeAttribute("aria-current");
      });
      document.querySelectorAll(".tab-panel").forEach((panel) => {
        panel.classList.toggle("hidden", panel.id !== "tab-overview");
      });
    }

    setLoading(true, `Consultando clima${meta.name ? ` para ${meta.name}` : ""}...`);
    try {
      const bundle = await API.getWeather({
        lat,
        lon,
        name: meta.name,
        country: meta.country,
        admin1: meta.admin1,
        location_source: meta.location_source,
        days,
      });
      if (meta.location_source && bundle.location && !bundle.location.location_source) {
        bundle.location.location_source = meta.location_source;
      }
      state.location = bundle.location;
      state.bundle = bundle;
      state.overviewMode = "single_forecast";
      state.extendedDays = Math.max(days, 7);
      renderOverviewForecast(bundle);
      updateActiveCityLabels();
      updateSearchActiveCard();
      loadRadarIfNeeded().then(() => {
        MapCtrl.initMiniMap(
          bundle.location.latitude,
          bundle.location.longitude,
          bundle.location.display_label || bundle.location.name
        );
      });
      if (state.activeTab !== "overview") {
        await switchTab(state.activeTab);
      } else {
        MapCtrl.invalidateAll();
      }
      if (bundle.refreshing_in_background) {
        setTimeout(() => silentSyncActiveCity(), 6000);
      }
    } catch (err) {
      toast(`Error cargando el clima: ${err.message}`, true);
    } finally {
      setLoading(false);
    }
  }

  async function silentSyncActiveCity() {
    if (state.view !== "dashboard" || !state.location || state.overviewMode !== "single_forecast") return;
    try {
      const bundle = await API.getWeather({
        lat: state.location.latitude,
        lon: state.location.longitude,
        days: state.extendedDays,
      });
      if (!bundle.is_stale) {
        state.bundle = bundle;
        state.location = bundle.location;
        if (state.activeTab === "overview") renderOverviewForecast(bundle);
        if (state.activeTab === "hourly") renderHourlyTab();
        if (state.activeTab === "extended") renderExtendedTab();
      }
    } catch {
      // Actualización pasiva silenciosa
    }
  }

  async function submitNaturalQuery(queryText) {
    const q = (queryText || "").trim();
    if (!q) {
      // Si el usuario envía vacío, mostrar automáticamente el clima de su ubicación detectada
      const detected = state.location || state.detectedLocation || (await detectLocationInBackground());
      if (detected) {
        await loadCityByCoords(detected.latitude, detected.longitude, detected, 7);
      }
      return;
    }
    switchView("dashboard");
    switchTab("overview");
    setLoading(true, "Procesando consulta meteorológica...");
    try {
      const refLoc = state.location || state.detectedLocation;
      const res = await API.postQuery({
        query: q,
        lat: refLoc ? refLoc.latitude : undefined,
        lon: refLoc ? refLoc.longitude : undefined,
        name: refLoc ? refLoc.name : undefined,
        admin1: refLoc ? refLoc.admin1 : undefined,
        country: refLoc ? refLoc.country : undefined,
        location_source: refLoc ? refLoc.location_source : undefined,
      });

      const sTabInp = document.getElementById("search-tab-input");
      if (sTabInp) sTabInp.value = q;

      if (res.mode === "multi_city" && res.multi_city) {
        state.overviewMode = "multi_city";
        state.multiCityData = res.multi_city;
        if (res.multi_city.cities && res.multi_city.cities.length) {
          state.location = res.multi_city.cities[0].location;
          updateActiveCityLabels();
          updateSearchActiveCard();
        }
        renderOverviewMultiCity(res.multi_city);
      } else if (res.mode === "single_history" && res.history) {
        state.overviewMode = "single_history";
        state.historyData = res.history;
        state.location = res.history.location;
        updateActiveCityLabels();
        updateSearchActiveCard();
        renderOverviewHistory(res.history, res.theme, res.date_focus_label);
        loadRadarIfNeeded().then(() => {
          MapCtrl.initMiniMap(
            res.history.location.latitude,
            res.history.location.longitude,
            res.history.location.display_label || res.history.location.name
          );
        });
      } else if (res.weather) {
        state.overviewMode = "single_forecast";
        state.bundle = res.weather;
        state.location = res.weather.location;
        updateActiveCityLabels();
        updateSearchActiveCard();
        renderOverviewForecast(res.weather);
        loadRadarIfNeeded().then(() => {
          MapCtrl.initMiniMap(
            res.weather.location.latitude,
            res.weather.location.longitude,
            res.weather.location.display_label || res.weather.location.name
          );
        });
        if (res.weather.refreshing_in_background) {
          setTimeout(() => silentSyncActiveCity(), 6000);
        }
      }
    } catch (err) {
      toast(`No se pudo completar la consulta: ${err.message}`, true);
    } finally {
      setLoading(false);
    }
  }

  /* ── Geolocalización automática silenciosa en segundo plano ──────────── */
  async function detectLocationInBackground() {
    if (state.detectedLocation) return state.detectedLocation;

    const resolveViaIp = async () => {
      try {
        const res = await API.geolocateIp();
        if (res && res.location) {
          state.detectedLocation = res.location;
          return res.location;
        }
      } catch {
        // Silencioso en segundo plano
      }
      return null;
    };

    // Si el permiso GPS ya fue concedido anteriormente, usarlo silenciosamente sin bloquear ni mostrar prompts molestos
    if (navigator.geolocation && navigator.permissions && navigator.permissions.query) {
      try {
        const perm = await navigator.permissions.query({ name: "geolocation" });
        if (perm.state === "granted") {
          const gpsLoc = await new Promise((resolve) => {
            navigator.geolocation.getCurrentPosition(
              async (pos) => {
                try {
                  const rev = await API.reverseGeocode(
                    Number(pos.coords.latitude.toFixed(4)),
                    Number(pos.coords.longitude.toFixed(4))
                  );
                  resolve(rev.location || null);
                } catch {
                  resolve({
                    name: "Mi ubicación",
                    latitude: Number(pos.coords.latitude.toFixed(4)),
                    longitude: Number(pos.coords.longitude.toFixed(4)),
                  });
                }
              },
              () => resolve(null),
              { enableHighAccuracy: false, timeout: 4500, maximumAge: 300000 }
            );
          });
          if (gpsLoc) {
            state.detectedLocation = gpsLoc;
            return gpsLoc;
          }
        }
      } catch {
        // Continuar con geolocalización por IP
      }
    }

    return resolveViaIp();
  }

  /* ── Geolocalización GPS exacta a pedido del usuario ─────────────────── */
  async function requestExactGpsLocation() {
    if (!navigator.geolocation) {
      toast("Este navegador no soporta geolocalización GPS. Usando ubicación aproximada por red.", true);
      const ipRes = await detectLocationInBackground();
      if (ipRes) await loadCityByCoords(ipRes.latitude, ipRes.longitude, { ...ipRes, location_source: "ip" }, 7, true);
      return;
    }
    setLoading(true, "Detectando tu ubicación exacta mediante GPS...");
    try {
      const pos = await new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, {
          enableHighAccuracy: true,
          timeout: 10000,
          maximumAge: 60000,
        });
      });
      const lat = Number(pos.coords.latitude.toFixed(4));
      const lon = Number(pos.coords.longitude.toFixed(4));
      const rev = await API.reverseGeocode(lat, lon, "gps");
      const exactLoc = {
        ...(rev && rev.location ? rev.location : { name: "Mi ubicación", latitude: lat, longitude: lon }),
        latitude: lat,
        longitude: lon,
        location_source: "gps",
      };
      state.detectedLocation = exactLoc;
      await loadCityByCoords(lat, lon, exactLoc, 7, true);
      toast(`Ubicación exacta detectada: ${exactLoc.display_label || exactLoc.name}`);
    } catch (err) {
      setLoading(false);
      const ipLoc = await detectLocationInBackground();
      if (ipLoc) {
        toast(`Sin permiso GPS (${err.message || "denegado"}). Mostrando ubicación aproximada: ${ipLoc.name}.`);
        await loadCityByCoords(ipLoc.latitude, ipLoc.longitude, { ...ipLoc, location_source: "ip" }, 7, true);
      } else {
        toast("No se pudo obtener la ubicación exacta. Buscá tu localidad manualmente.", true);
      }
    }
  }

  /* ── Renderizado de Confianza Meteorológica Real ─────────────────────── */
  function renderConfidenceIndicator(conf) {
    const strip = document.getElementById("hero-confidence-strip");
    const factorsEl = document.getElementById("confidence-factors-row");
    if (!strip) return;

    if (!conf || conf.available === undefined) {
      strip.classList.add("hidden");
      if (factorsEl) factorsEl.classList.add("hidden");
      return;
    }

    strip.classList.remove("hidden");
    if (!conf.available || conf.score_pct === null || conf.score_pct === undefined) {
      strip.className = "confidence-strip conf-na";
      strip.innerHTML = `
        <div class="confidence-top-row">
          <span class="confidence-label"><i data-lucide="shield-alert"></i> Confianza no disponible</span>
          <span class="confidence-pct tabular">—</span>
        </div>
        <p class="confidence-note">${API.escapeHtml(conf.reason || "Insuficientes fuentes independientes para calcular confianza.")}</p>
      `;
      if (factorsEl) factorsEl.classList.add("hidden");
      return;
    }

    const score = Number(conf.score_pct);
    const tier = score >= 85 ? "conf-high" : score >= 70 ? "conf-good" : score >= 55 ? "conf-mod" : "conf-low";
    strip.className = `confidence-strip ${tier}`;
    strip.innerHTML = `
      <div class="confidence-top-row">
        <span class="confidence-label"><i data-lucide="shield-check"></i> Confianza del pronóstico</span>
        <span class="confidence-pct tabular">${score}% · ${API.escapeHtml(conf.level || "")}</span>
      </div>
      <div class="confidence-meter-track" aria-hidden="true">
        <div class="confidence-meter-fill" style="width:${Math.max(8, Math.min(100, score))}%"></div>
      </div>
      <p class="confidence-note">${API.escapeHtml(conf.summary || "")}</p>
    `;

    if (factorsEl && conf.factors) {
      const f = conf.factors;
      const chips = [
        { lbl: "Modelos", val: f.model_agreement_pct },
        { lbl: "Lluvia y viento", val: f.precip_consensus_pct },
        { lbl: "Frescura", val: f.freshness_pct },
        { lbl: "Fuentes", val: f.availability_pct },
      ];
      if (f.historical_backing_pct !== null && f.historical_backing_pct !== undefined) {
        chips.push({ lbl: "Historial real", val: f.historical_backing_pct });
      }
      factorsEl.innerHTML = chips
        .filter((c) => c.val !== null && c.val !== undefined)
        .map(
          (c) => `
        <div class="conf-factor-pill">
          <span>${API.escapeHtml(c.lbl)}</span>
          <strong class="tabular">${Math.round(c.val)}%</strong>
        </div>`
        )
        .join("");
      factorsEl.classList.remove("hidden");
    }
  }

  /* ── Renderizado TAB 1: RESUMEN ──────────────────────────────────────── */
  function renderOverviewForecast(b) {
    setTheme(b.theme);
    document.getElementById("single-city-stack").classList.remove("hidden");
    document.getElementById("multi-city-stack").classList.add("hidden");
    document.getElementById("overview-hourly-card").classList.remove("hidden");
    document.getElementById("consensus-mini-card").classList.remove("hidden");

    document.getElementById("overview-narrative").textContent = b.narrative;
    document.getElementById("date-focus-value").textContent = b.date_focus_label;

    const ageTxt = b.refreshing_in_background
      ? `${API.fmtAgeSeconds(b.data_age_seconds)} · actualizando automáticamente...`
      : API.fmtAgeSeconds(b.data_age_seconds);
    const updatedLabel = document.getElementById("last-updated-label");
    if (updatedLabel) {
      updatedLabel.textContent = `${API.fmtDayHour(b.fetched_at)} (${ageTxt})`;
    }

    document.querySelectorAll(".range-quick-btns .mini-pill").forEach((btn) => {
      btn.classList.toggle(
        "active",
        (btn.dataset.range === "7d" && b.daily.length <= 7) ||
          (btn.dataset.range === "14d" && b.daily.length > 7)
      );
    });

    renderDiscrepancyBanner("overview-discrepancy-banner", b.discrepancy, true);

    const cur = b.current;
    document.getElementById("hero-city-name").textContent = b.location.name;
    const regionText = [b.location.admin1, b.location.country].filter(Boolean).join(", ");
    const coordsText = `${Number(b.location.latitude).toFixed(2)}°, ${Number(b.location.longitude).toFixed(2)}°`;
    document.getElementById("hero-city-sub").textContent = regionText ? `${regionText} · ${coordsText}` : coordsText;

    // Transparencia de precisión de ubicación (GPS exacto vs IP aproximada)
    const locBadge = document.getElementById("hero-location-badge");
    if (locBadge) {
      const src = b.location.location_source;
      if (src === "gps") {
        locBadge.className = "location-precision-row is-exact";
        locBadge.innerHTML = `<i data-lucide="locate-fixed"></i><span>Ubicación exacta (GPS)</span>`;
      } else if (src === "ip" || src === "fallback") {
        locBadge.className = "location-precision-row is-approx";
        locBadge.innerHTML = `
          <i data-lucide="navigation"></i>
          <span>Ubicación aproximada por red</span>
          <button type="button" class="loc-upgrade-btn" id="btn-upgrade-gps">Usar GPS exacto</button>
        `;
        const upBtn = document.getElementById("btn-upgrade-gps");
        if (upBtn) upBtn.addEventListener("click", requestExactGpsLocation);
      } else {
        locBadge.className = "location-precision-row hidden";
        locBadge.innerHTML = "";
      }
    }

    renderConfidenceIndicator(b.confidence);

    const favBtn = document.getElementById("btn-favorite-toggle");
    favBtn.classList.toggle("is-favorite", Boolean(b.location.is_favorite));

    const tVal = API.convertTemp(cur.temperature_c, state.unitSystem);
    const roundedHero = tVal !== null ? (Math.round(tVal) === 0 ? 0 : Math.round(tVal)) : "--";
    document.getElementById("hero-temp-value").textContent = roundedHero;
    document.getElementById("hero-temp-unit").textContent = state.unitSystem === "imperial" ? "°F" : "°C";
    document.getElementById("hero-condition-icon").innerHTML = `<i data-lucide="${API.escapeHtml(cur.condition_icon || "sun")}"></i>`;
    document.getElementById("hero-condition-text").textContent = cur.condition_text;
    document.getElementById("hero-feels-text").textContent = `Sensación ${API.fmtTemp(cur.feels_like_c, state.unitSystem, 1)}`;

    const today = b.daily[0] || {};
    const tiles = [
      {
        icon: "thermometer",
        label: "Mín / Máx hoy",
        value:
          today.temp_min_c !== undefined
            ? `${API.fmtTemp(today.temp_min_c, state.unitSystem, 0).replace(/ °[CF]/, "°")} / ${API.fmtTemp(
                today.temp_max_c,
                state.unitSystem,
                0
              ).replace(/ °[CF]/, "°")}`
            : "—",
        unit: "",
        note: cur.dew_point_c !== null ? `Rocío ${API.fmtTemp(cur.dew_point_c, state.unitSystem, 0)}` : "",
      },
      {
        icon: "wind",
        label: "Viento",
        value: cur.wind_speed_kmh !== null ? Math.round(API.convertWind(cur.wind_speed_kmh, state.unitSystem)) : "—",
        unit: state.unitSystem === "imperial" ? "mph" : "km/h",
        note: `${cur.wind_direction_cardinal}${cur.wind_gusts_kmh ? ` · Ráf. ${Math.round(API.convertWind(cur.wind_gusts_kmh, state.unitSystem))}` : ""}`,
      },
      {
        icon: "droplets",
        label: "Humedad",
        value: cur.humidity_pct !== null ? Math.round(cur.humidity_pct) : "—",
        unit: "%",
        note: cur.precip_probability_pct !== null ? `Prob. lluvia ${Math.round(cur.precip_probability_pct)}%` : `Nubes ${cur.cloud_cover_pct ?? "—"}%`,
      },
      {
        icon: "gauge",
        label: "Presión",
        value: cur.pressure_hpa !== null ? Math.round(cur.pressure_hpa) : "—",
        unit: "hPa",
        note: `Tendencia ${cur.pressure_trend || "estable"}`,
      },
      {
        icon: "cloud-rain",
        label: "Precipitación",
        value: (cur.precipitation_mm || 0).toFixed(1),
        unit: "mm",
        note: today.precipitation_sum_mm !== undefined ? `Hoy total ${today.precipitation_sum_mm.toFixed(1)} mm` : "",
      },
      {
        icon: "sun",
        label: "Índice UV / Vis.",
        value: cur.uv_index !== null ? cur.uv_index.toFixed(1) : "—",
        unit: "UV",
        note: cur.visibility_km !== null ? `Visibilidad ${cur.visibility_km} km` : "Cielo observado",
      },
    ];

    document.getElementById("hero-submetrics").innerHTML = tiles
      .map(
        (t) => `
      <div class="submetric-tile">
        <div class="submetric-label"><i data-lucide="${t.icon}"></i><span>${API.escapeHtml(t.label)}</span></div>
        <div class="submetric-val tabular">${API.escapeHtml(t.value)}<span class="submetric-unit">${API.escapeHtml(t.unit)}</span></div>
        <div class="submetric-note">${API.escapeHtml(t.note)}</div>
      </div>`
      )
      .join("");

    // Tira horaria 24h
    const nowHourPrefix = (cur.observed_at || "").slice(0, 13);
    let startIdx = b.hourly.findIndex((h) => h.time.startsWith(nowHourPrefix));
    if (startIdx < 0) startIdx = 0;
    const next24 = b.hourly.slice(startIdx, startIdx + 24);
    document.getElementById("overview-hourly-strip").innerHTML = next24
      .map(
        (h, i) => `
      <div class="hourly-chip" role="listitem">
        <span class="hourly-chip-time tabular">${i === 0 ? "Ahora" : API.fmtHour(h.time)}</span>
        <span class="hourly-chip-icon"><i data-lucide="${API.escapeHtml(h.condition_icon || "sun")}"></i></span>
        <span class="hourly-chip-temp tabular">${API.fmtTemp(h.temperature_c, state.unitSystem, 0)}</span>
        <span class="hourly-chip-rain tabular">${h.precip_probability_pct !== null ? `${Math.round(h.precip_probability_pct)}%` : `${(h.precipitation_mm || 0).toFixed(1)}mm`}</span>
      </div>`
      )
      .join("");

    // Gráficos principales
    document.getElementById("chart-primary-title").textContent = "Pronóstico de Temperatura y Precipitación";
    document.getElementById("chart-primary-sub").textContent = `Máximas diarias y acumulado previsto (${b.daily.length} días)`;
    const dLabels = b.daily.map((d) => API.fmtChartDate(d.date));
    const dMaxs = b.daily.map((d) => API.convertTemp(d.temp_max_c, state.unitSystem));
    const dPrecips = b.daily.map((d) => Number((d.precipitation_sum_mm || 0).toFixed(1)));
    Charts.renderTempPrecipChart("chart-temp-precip", dLabels, dMaxs, dPrecips, state.unitSystem);

    document.getElementById("chart-secondary-title").textContent = "Tendencia de Viento";
    document.getElementById("chart-secondary-sub").textContent = "Velocidad máxima diaria estimada";
    const dWinds = b.daily.map((d) => API.convertWind(d.wind_speed_max_kmh, state.unitSystem));
    Charts.renderWindLineChart("chart-wind-trend", dLabels, dWinds, state.unitSystem);

    // Procedencia y respaldo verificable
    const cp = b.consensus_preview || {};
    const prov = b.provenance || {};
    const primaryName = prov.primary_provider_name || b.provider;
    const sourcesCount = prov.sources_count || cp.active_sources || 1;
    document.getElementById("consensus-mini-sub").textContent =
      `Selección automática de ${sourcesCount} fuentes reales · Primaria: ${primaryName}`;

    document.getElementById("consensus-mini-metrics").innerHTML = `
      <div class="mini-kpi-box"><span>Selección final</span><strong class="tabular">${API.fmtTemp(cp.selected_temp_c ?? cur.temperature_c, state.unitSystem, 1)}</strong></div>
      <div class="mini-kpi-box"><span>Confianza</span><strong class="tabular">${b.confidence && b.confidence.available ? `${b.confidence.score_pct}% (${API.escapeHtml(b.confidence.level)})` : "No disponible"}</strong></div>
      <div class="mini-kpi-box"><span>Rango fuentes</span><strong class="tabular">${cp.temp_min_c !== undefined && cp.temp_max_c !== undefined ? `${API.convertTemp(cp.temp_min_c, state.unitSystem)}° – ${API.convertTemp(cp.temp_max_c, state.unitSystem)}°` : "—"}</strong></div>
      <div class="mini-kpi-box"><span>Fuentes usadas</span><strong class="tabular">${sourcesCount} reales</strong></div>
    `;

    const provNames = (prov.sources_used || []).map((s) => s.name).join(" · ");
    document.getElementById("provenance-detail-line").textContent =
      `${prov.accuracy_backing || "Datos reales comparados automáticamente."}${provNames ? ` Fuentes: ${provNames}.` : ""}`;

    refreshIcons();
  }

  function renderDiscrepancyBanner(elementId, disc, showLink = false) {
    const el = document.getElementById(elementId);
    if (!el) return;
    if (!disc || !disc.has_significant_diff) {
      el.classList.add("hidden");
      return;
    }
    el.className = `discrepancy-banner sev-${disc.severity || "moderada"}`;
    el.innerHTML = `
      <div>
        <strong>Discrepancia ${API.escapeHtml(disc.severity || "moderada")} detectada entre fuentes</strong>
        <div style="font-size:12px;opacity:0.92;margin-top:2px;">${API.escapeHtml(disc.summary || "")}</div>
      </div>
      ${showLink ? `<button type="button" class="link-pill js-jump-providers">Ver comparativa</button>` : ""}
    `;
    const btn = el.querySelector(".js-jump-providers");
    if (btn) btn.addEventListener("click", () => switchTab("providers"));
  }

  function renderOverviewHistory(h, theme, dateLabel) {
    setTheme(theme || "rain");
    document.getElementById("single-city-stack").classList.remove("hidden");
    document.getElementById("multi-city-stack").classList.add("hidden");
    document.getElementById("overview-hourly-card").classList.add("hidden");
    document.getElementById("consensus-mini-card").classList.add("hidden");
    document.getElementById("overview-discrepancy-banner").classList.add("hidden");

    document.getElementById("overview-narrative").textContent = h.narrative;
    document.getElementById("date-focus-value").textContent = dateLabel || `${h.start_date} — ${h.end_date}`;
    document.querySelectorAll(".range-quick-btns .mini-pill").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.range === "last_month");
    });

    const sum = h.summary || {};
    document.getElementById("hero-city-name").textContent = h.location.name;
    document.getElementById("hero-city-sub").textContent = `Archivo histórico · ${h.days_count} días`;
    document.getElementById("hero-temp-value").textContent =
      sum.precip_total_mm !== undefined ? Math.round(sum.precip_total_mm) : "--";
    document.getElementById("hero-temp-unit").textContent = "mm";
    document.getElementById("hero-condition-icon").innerHTML = `<i data-lucide="cloud-rain"></i>`;
    document.getElementById("hero-condition-text").textContent = "Lluvia acumulada";
    document.getElementById("hero-feels-text").textContent = `Media térmica ${API.fmtTemp(sum.temp_avg_c, state.unitSystem, 1)}`;

    document.getElementById("hero-submetrics").innerHTML = [
      { icon: "thermometer", label: "Máxima período", value: API.convertTemp(sum.temp_max_c, state.unitSystem) ?? "—", unit: state.unitSystem === "imperial" ? "°F" : "°C", note: sum.temp_max_date || "" },
      { icon: "snowflake", label: "Mínima período", value: API.convertTemp(sum.temp_min_c, state.unitSystem) ?? "—", unit: state.unitSystem === "imperial" ? "°F" : "°C", note: sum.temp_min_date || "" },
      { icon: "droplets", label: "Día más lluvioso", value: sum.wettest_day_mm ?? 0, unit: "mm", note: sum.wettest_day || "" },
      { icon: "wind", label: "Viento pico", value: API.convertWind(sum.wind_max_kmh, state.unitSystem) ?? "—", unit: state.unitSystem === "imperial" ? "mph" : "km/h", note: sum.windiest_day || "" },
    ]
      .map(
        (t) => `
      <div class="submetric-tile">
        <div class="submetric-label"><i data-lucide="${t.icon}"></i><span>${API.escapeHtml(t.label)}</span></div>
        <div class="submetric-val tabular">${API.escapeHtml(t.value)}<span class="submetric-unit">${API.escapeHtml(t.unit)}</span></div>
        <div class="submetric-note">${API.escapeHtml(t.note)}</div>
      </div>`
      )
      .join("");

    const labels = h.daily.map((d) => API.fmtChartDate(d.date));
    document.getElementById("chart-primary-title").textContent = "Tendencia Diaria de Precipitaciones";
    document.getElementById("chart-primary-sub").textContent = `${h.start_date} a ${h.end_date} · Fuente ERA5`;
    Charts.renderRainBarsChart(
      "chart-temp-precip",
      labels,
      h.daily.map((d) => d.precipitation_sum_mm || 0),
      "Lluvia Diaria (mm)"
    );

    document.getElementById("chart-secondary-title").textContent = "Evolución Térmica Histórica";
    document.getElementById("chart-secondary-sub").textContent = "Máxima y mínima diaria registrada";
    Charts.renderMultiLineChart(
      "chart-wind-trend",
      labels,
      [
        { label: "Temp. Máx.", values: h.daily.map((d) => API.convertTemp(d.temp_max_c, state.unitSystem)) },
        { label: "Temp. Mín.", values: h.daily.map((d) => API.convertTemp(d.temp_min_c, state.unitSystem)) },
      ],
      state.unitSystem === "imperial" ? "°F" : "°C"
    );
    refreshIcons();
  }

  function renderOverviewMultiCity(mc) {
    setTheme(mc.theme || "clear");
    document.getElementById("single-city-stack").classList.add("hidden");
    document.getElementById("multi-city-stack").classList.remove("hidden");
    document.getElementById("overview-discrepancy-banner").classList.add("hidden");

    document.getElementById("overview-narrative").textContent = mc.narrative;
    document.getElementById("date-focus-value").textContent = mc.date_focus_label;

    document.getElementById("mc-city-cards").innerHTML = mc.cities
      .map((c, idx) => {
        const col = Charts.PALETTE[idx % Charts.PALETTE.length];
        return `
        <div class="mc-city-card">
          <h4><span class="mc-dot" style="background:${col}"></span>${API.escapeHtml(c.location.name)}</h4>
          <div class="mc-stat-line"><span>Máxima</span><strong class="tabular">${API.fmtTemp(c.temp_max_c, state.unitSystem, 1)}</strong></div>
          <div class="mc-stat-line"><span>Mínima</span><strong class="tabular">${API.fmtTemp(c.temp_min_c, state.unitSystem, 1)}</strong></div>
          <div class="mc-stat-line"><span>Lluvia total</span><strong class="tabular">${c.precip_total_mm.toFixed(1)} mm</strong></div>
          <div class="mc-stat-line"><span>Viento máx.</span><strong class="tabular">${API.fmtWind(c.wind_max_kmh, state.unitSystem)}</strong></div>
        </div>`;
      })
      .join("");

    const labels = mc.dates.map((d) => API.fmtChartDate(d));
    const tUnit = state.unitSystem === "imperial" ? "°F" : "°C";
    const wUnit = state.unitSystem === "imperial" ? "mph" : "km/h";

    Charts.renderMultiLineChart(
      "chart-mc-temp",
      labels,
      mc.temp_series.map((s) => ({
        label: s.label,
        values: s.values.map((v) => API.convertTemp(v, state.unitSystem)),
      })),
      tUnit
    );
    Charts.renderGroupedBarChart("chart-mc-precip", labels, mc.precip_series, " mm");
    Charts.renderMultiLineChart(
      "chart-mc-wind",
      labels,
      mc.wind_series.map((s) => ({
        label: s.label,
        values: s.values.map((v) => API.convertWind(v, state.unitSystem)),
      })),
      wUnit
    );
  }

  /* ── TAB 2: POR HORAS ────────────────────────────────────────────────── */
  function renderHourlyTab() {
    if (!state.bundle) return;
    const cur = state.bundle.current;
    const nowPrefix = (cur.observed_at || "").slice(0, 13);
    let idx = state.bundle.hourly.findIndex((h) => h.time.startsWith(nowPrefix));
    if (idx < 0) idx = 0;
    const slice = state.bundle.hourly.slice(idx, idx + state.hourlyWindow);

    const labels = slice.map((h) => (state.hourlyWindow > 24 ? API.fmtDayHour(h.time) : API.fmtHour(h.time)));
    Charts.renderHourlyDetailChart(
      "chart-hourly-detail",
      labels,
      slice.map((h) => API.convertTemp(h.temperature_c, state.unitSystem)),
      slice.map((h) => API.convertTemp(h.feels_like_c, state.unitSystem)),
      slice.map((h) => h.precip_probability_pct ?? 0),
      state.unitSystem
    );

    document.getElementById("hourly-table-body").innerHTML = slice
      .map(
        (h) => `
      <tr>
        <td class="tabular">${API.escapeHtml(API.fmtDayHour(h.time))}</td>
        <td>${API.escapeHtml(h.condition_text)}</td>
        <td class="num">${API.fmtTemp(h.temperature_c, state.unitSystem, 1)}</td>
        <td class="num">${API.fmtTemp(h.feels_like_c, state.unitSystem, 1)}</td>
        <td class="num">${h.precip_probability_pct !== null ? `${Math.round(h.precip_probability_pct)}%` : "—"}</td>
        <td class="num">${API.fmtPrecip(h.precipitation_mm, state.unitSystem)}</td>
        <td class="num">${API.fmtWind(h.wind_speed_kmh, state.unitSystem)}</td>
        <td class="num">${h.humidity_pct !== null ? `${Math.round(h.humidity_pct)}%` : "—"}</td>
        <td class="num">${h.pressure_hpa !== null ? `${h.pressure_hpa.toFixed(1)} hPa` : "—"}</td>
        <td class="num">${h.uv_index !== null ? h.uv_index.toFixed(1) : "—"}</td>
      </tr>`
      )
      .join("");
  }

  /* ── TAB 3: EXTENDIDO ────────────────────────────────────────────────── */
  async function ensureExtendedDays(days) {
    state.extendedDays = days;
    if (!state.location) return;
    if (!state.bundle || state.bundle.daily.length < days) {
      setLoading(true, `Cargando pronóstico extendido (${days} días)...`);
      try {
        const b = await API.getWeather({
          lat: state.location.latitude,
          lon: state.location.longitude,
          days,
        });
        state.bundle = b;
      } catch (err) {
        toast(`Error cargando pronóstico extendido: ${err.message}`, true);
      } finally {
        setLoading(false);
      }
    }
    renderExtendedTab();
  }

  function renderExtendedTab() {
    if (!state.bundle) return;
    const list = state.bundle.daily.slice(0, state.extendedDays);
    const allMins = list.map((d) => d.temp_min_c).filter((v) => v !== null);
    const allMaxs = list.map((d) => d.temp_max_c).filter((v) => v !== null);
    const gMin = allMins.length ? Math.min(...allMins) : 0;
    const gMax = allMaxs.length ? Math.max(...allMaxs) : 30;
    const span = Math.max(1, gMax - gMin);

    document.getElementById("extended-days-list").innerHTML = list
      .map((d) => {
        const leftPct = d.temp_min_c !== null ? ((d.temp_min_c - gMin) / span) * 100 : 0;
        const widthPct =
          d.temp_max_c !== null && d.temp_min_c !== null
            ? Math.max(8, ((d.temp_max_c - d.temp_min_c) / span) * 100)
            : 20;
        const sunInfo = d.sunrise && d.sunset ? `${API.fmtHour(d.sunrise)}–${API.fmtHour(d.sunset)}` : "";
        const gustsTxt = d.wind_gusts_max_kmh ? ` · Ráf. ${Math.round(API.convertWind(d.wind_gusts_max_kmh, state.unitSystem))}` : "";
        const uvTxt = d.uv_index_max !== null && d.uv_index_max !== undefined ? `UV ${d.uv_index_max.toFixed(1)}` : "";
        const metaExtra = [uvTxt, sunInfo].filter(Boolean).join(" · ") || "—";
        return `
        <div class="daily-row">
          <div class="daily-date">${API.escapeHtml(API.fmtShortDate(d.date))}</div>
          <div class="daily-cond">
            <i data-lucide="${API.escapeHtml(d.condition_icon || "sun")}"></i>
            <span>${API.escapeHtml(d.condition_text)}</span>
          </div>
          <div class="daily-rain tabular">
            ${d.precip_probability_max_pct !== null ? `${Math.round(d.precip_probability_max_pct)}% · ` : ""}${(d.precipitation_sum_mm || 0).toFixed(1)} mm
          </div>
          <div class="temp-range-wrap tabular">
            <span>${API.fmtTemp(d.temp_min_c, state.unitSystem, 0)}</span>
            <div class="temp-bar-track"><div class="temp-bar-fill" style="left:${leftPct.toFixed(1)}%;width:${widthPct.toFixed(1)}%"></div></div>
            <strong>${API.fmtTemp(d.temp_max_c, state.unitSystem, 0)}</strong>
          </div>
          <div class="daily-wind tabular">${API.fmtWind(d.wind_speed_max_kmh, state.unitSystem)}${gustsTxt ? `<span class="daily-sub-meta">${API.escapeHtml(gustsTxt)}</span>` : ""}</div>
          <div class="daily-uv tabular">${API.escapeHtml(metaExtra)}</div>
        </div>`;
      })
      .join("");
    refreshIcons();
  }

  /* ── TAB 4: MAPA RADAR ───────────────────────────────────────────────── */
  async function loadRadarIfNeeded() {
    if (state.radarMeta) return;
    try {
      state.radarMeta = await API.getRadar();
      MapCtrl.setRadarMetadata(state.radarMeta);
    } catch {
      // Silencioso si RainViewer falla momentáneamente
    }
  }

  async function handleMapPointClick(lat, lon) {
    const box = document.getElementById("map-click-inspector");
    box.classList.remove("hidden");
    box.innerHTML = `<p class="card-subtitle">Consultando condiciones reales en (${lat}, ${lon})...</p>`;
    try {
      const b = await API.getWeather({ lat, lon, days: 3 });
      box.innerHTML = `
        <div class="card-header-row" style="margin-bottom:0">
          <div>
            <h3 class="card-title">${API.escapeHtml(b.location.display_label || b.location.name)} (${lat}, ${lon})</h3>
            <p class="card-subtitle">
              ${API.fmtTemp(b.current.temperature_c, state.unitSystem, 1)} · ${API.escapeHtml(b.current.condition_text)} ·
              Humedad ${b.current.humidity_pct ?? "—"}% · Viento ${API.fmtWind(b.current.wind_speed_kmh, state.unitSystem)} ·
              Presión ${b.current.pressure_hpa ?? "—"} hPa
            </p>
          </div>
          <button type="button" class="primary-btn" id="btn-load-clicked-point">Cargar en panel</button>
        </div>
      `;
      document.getElementById("btn-load-clicked-point").addEventListener("click", () => {
        state.location = b.location;
        state.bundle = b;
        switchTab("overview");
        loadCityByCoords(lat, lon, b.location, 7);
      });
    } catch (err) {
      box.innerHTML = `<p class="card-subtitle">Error consultando punto: ${API.escapeHtml(err.message)}</p>`;
    }
  }

  /* ── TAB 5: FUENTES Y COMPARATIVA (CACHÉ INSTANTÁNEA SIN DUPLICADOS) ── */
  async function loadProvidersComparison() {
    if (!state.location) return;
    let loadingShown = false;
    const spinnerTimer = setTimeout(() => {
      loadingShown = true;
      setLoading(true, "Consolidando comparativa multiproveedor...");
    }, 120);
    try {
      const comp = await API.compareProviders({
        lat: state.location.latitude,
        lon: state.location.longitude,
        name: state.location.name,
        admin1: state.location.admin1,
        country: state.location.country,
        location_source: state.location.location_source,
        days: 7,
      });
      state.providerComp = comp;
      renderProvidersTab(comp);
    } catch (err) {
      toast(`Error cargando comparativa: ${err.message}`, true);
    } finally {
      clearTimeout(spinnerTimer);
      if (loadingShown) setLoading(false);
    }
  }

  function renderProvidersTab(comp) {
    const c = comp.consensus || {};
    const prov = comp.provenance || {};
    const conf = comp.confidence || {};
    const badge = document.getElementById("providers-freshness-badge");
    if (badge) {
      badge.textContent = comp.refreshing_in_background
        ? "Actualizando en segundo plano..."
        : comp.from_database
        ? `Caché vigente · ${API.fmtHour(comp.fetched_at)} UTC`
        : `Recién verificado · ${API.fmtHour(comp.fetched_at)} UTC`;
    }

    renderDiscrepancyBanner("providers-discrepancy-box", comp.discrepancy, false);

    document.getElementById("providers-consensus-grid").innerHTML = `
      <div class="kpi-tile">
        <span class="kpi-tile-label">Temperatura Consolidada</span>
        <span class="kpi-tile-value tabular">${API.fmtTemp(c.selected_temp_c ?? c.temp_mean_c, state.unitSystem, 1)}</span>
        <span class="kpi-tile-sub tabular">Media ${API.fmtTemp(c.temp_mean_c, state.unitSystem, 1)} · Mediana ${API.fmtTemp(c.temp_median_c, state.unitSystem, 1)}</span>
      </div>
      <div class="kpi-tile">
        <span class="kpi-tile-label">Confianza Meteorológica</span>
        <span class="kpi-tile-value tabular">${conf.available && conf.score_pct !== null ? `${conf.score_pct}%` : "N/D"}</span>
        <span class="kpi-tile-sub">${API.escapeHtml(conf.available ? `${conf.level} · Dispersión ±${c.temp_std_c ?? 0} °C` : conf.reason || "Fuentes insuficientes")}</span>
      </div>
      <div class="kpi-tile">
        <span class="kpi-tile-label">Fuente Primaria</span>
        <span class="kpi-tile-value" style="font-size:17px">${API.escapeHtml(prov.primary_provider_name || c.primary_provider_name || "—")}</span>
        <span class="kpi-tile-sub">${prov.primary_accuracy_pct ? `Precisión real ${prov.primary_accuracy_pct}% (N=${prov.primary_samples})` : "Seleccionada por frescura y concordancia"}</span>
      </div>
      <div class="kpi-tile">
        <span class="kpi-tile-label">Agencias Evaluadas</span>
        <span class="kpi-tile-value tabular">${comp.active_providers} activas</span>
        <span class="kpi-tile-sub">Rango térmico ${c.temp_spread_c ?? 0} °C (${API.escapeHtml(c.agreement_level || "—")})</span>
      </div>
    `;

    const noteEl = document.getElementById("providers-provenance-note");
    if (noteEl) {
      noteEl.textContent = prov.accuracy_backing || "Comparativa ponderada por frescura, concordancia y precisión histórica.";
    }

    // Mostrar únicamente las fuentes conectadas en servidor (sin exponer controles técnicos de API keys)
    const connectedProviders = comp.providers.filter((p) => p.status === "ok" || p.status === "error");
    document.getElementById("providers-table-body").innerHTML = connectedProviders
      .map((p) => {
        const devStr =
          p.temp_deviation_c !== null && p.temp_deviation_c !== undefined
            ? `${p.temp_deviation_c > 0 ? "+" : ""}${p.temp_deviation_c.toFixed(1)} °C`
            : "—";
        const roleBadge = p.is_primary_selected
          ? `<span class="status-badge status-ok">Primaria</span>`
          : p.status === "ok"
          ? `<span class="status-badge" style="background:rgba(255,255,255,0.12)">Contraste</span>`
          : `<span class="status-badge status-error">Sin respuesta</span>`;
        const accText =
          p.historical_accuracy_pct !== null && p.historical_accuracy_pct !== undefined
            ? `${p.historical_accuracy_pct.toFixed(1)}% (N=${p.historical_samples})`
            : "En recopilación";
        const weightText =
          p.reliability_weight !== null && p.reliability_weight !== undefined
            ? `${p.reliability_weight.toFixed(2)}×`
            : "—";

        return `
        <tr>
          <td><strong>${API.escapeHtml(p.display_name)}</strong></td>
          <td>${roleBadge}</td>
          <td class="num">${API.fmtTemp(p.current_temp_c, state.unitSystem, 1)}</td>
          <td class="num">${devStr}</td>
          <td class="num">${API.fmtTemp(p.feels_like_c, state.unitSystem, 1)}</td>
          <td class="num">${p.humidity_pct !== null ? `${Math.round(p.humidity_pct)}%` : "—"}</td>
          <td class="num">${p.pressure_hpa !== null ? `${p.pressure_hpa.toFixed(1)} hPa` : "—"}</td>
          <td class="num">${API.fmtWind(p.wind_kmh, state.unitSystem)}</td>
          <td class="num">${p.precip_mm !== null ? API.fmtPrecip(p.precip_mm, state.unitSystem) : "—"}</td>
          <td class="num">${API.escapeHtml(accText)}</td>
          <td class="num">${API.escapeHtml(weightText)}</td>
          <td>${API.escapeHtml(p.status === "ok" ? p.condition_text : "Recuperando conexión")}</td>
        </tr>`;
      })
      .join("");

    const tUnit = state.unitSystem === "imperial" ? "°F" : "°C";
    const h24 = comp.hourly_chart || { labels: [], datasets: [] };
    Charts.renderMultiLineChart(
      "chart-providers-24h",
      h24.labels.map((l) => API.fmtHour(l)),
      h24.datasets.map((ds) => ({
        label: ds.label,
        values: ds.values.map((v) => API.convertTemp(v, state.unitSystem)),
      })),
      tUnit
    );

    const d7 = comp.daily_chart || { labels: [], datasets: [] };
    Charts.renderMultiLineChart(
      "chart-providers-7d",
      d7.labels.map((l) => API.fmtChartDate(l)),
      d7.datasets.map((ds) => ({
        label: ds.label,
        values: ds.values.map((v) => API.convertTemp(v, state.unitSystem)),
      })),
      tUnit
    );
  }

  /* ── TAB 6: PRECISIÓN REAL Y AUDITORÍA SQLITE ────────────────────────── */
  async function loadAccuracyDashboard() {
    const scope = document.getElementById("accuracy-scope-select").value;
    const sourceType = document.getElementById("accuracy-source-select").value;
    setLoading(true, "Cargando estadísticas reales de precisión...");
    try {
      const data = await API.getAccuracy({
        lat: scope === "location" && state.location ? state.location.latitude : undefined,
        lon: scope === "location" && state.location ? state.location.longitude : undefined,
        name: scope === "location" && state.location ? state.location.name : undefined,
        admin1: scope === "location" && state.location ? state.location.admin1 : undefined,
        country: scope === "location" && state.location ? state.location.country : undefined,
        scope,
        source_type: sourceType || undefined,
      });
      state.accuracyData = data;
      renderAccuracyTab(data);
    } catch (err) {
      toast(`Error cargando precisión: ${err.message}`, true);
    } finally {
      setLoading(false);
    }
  }

  async function runHistoricalBacktest() {
    if (!state.location) return;
    const btn = document.getElementById("btn-run-backtest");
    btn.disabled = true;
    setLoading(true, `Verificando pronósticos reales de los últimos 7 días en ${state.location.name}...`);
    try {
      const res = await API.runBacktest({
        lat: state.location.latitude,
        lon: state.location.longitude,
        name: state.location.name,
        admin1: state.location.admin1,
        country: state.location.country,
        past_days: 7,
      });
      toast(
        `Verificación real completada: ${res.ingested.newly_verified} nuevos pares contrastados (${res.ingested.observations_saved} observaciones reales).`
      );
      state.accuracyData = res.dashboard;
      renderAccuracyTab(res.dashboard);
    } catch (err) {
      toast(`Error en verificación histórica: ${err.message}`, true);
    } finally {
      btn.disabled = false;
      setLoading(false);
    }
  }

  function renderAccuracyTab(d) {
    document.getElementById("accuracy-methodology-text").textContent = d.methodology_note;

    const topLeader = d.ranking.find((r) => r.has_data);
    document.getElementById("accuracy-kpi-grid").innerHTML = `
      <div class="kpi-tile">
        <span class="kpi-tile-label">Pares Verificados Reales</span>
        <span class="kpi-tile-value tabular">${d.total_verified_pairs}</span>
        <span class="kpi-tile-sub tabular">Históricos: ${d.historical_verified_pairs} · En vivo: ${d.live_verified_pairs}</span>
      </div>
      <div class="kpi-tile">
        <span class="kpi-tile-label">Pronósticos Registrados</span>
        <span class="kpi-tile-value tabular">${d.total_forecast_records}</span>
        <span class="kpi-tile-sub tabular">${d.total_observations} observaciones reales</span>
      </div>
      <div class="kpi-tile">
        <span class="kpi-tile-label">Fuente Más Precisa</span>
        <span class="kpi-tile-value" style="font-size:18px">${topLeader ? API.escapeHtml(topLeader.display_name) : "En recopilación"}</span>
        <span class="kpi-tile-sub tabular">${topLeader ? `${topLeader.composite_accuracy_pct}% (MAE ${topLeader.temp_mae_c} °C)` : "Esperando muestras maduras"}</span>
      </div>
      <div class="kpi-tile">
        <span class="kpi-tile-label">Última Verificación</span>
        <span class="kpi-tile-value tabular" style="font-size:17px">${d.last_backtest_at ? API.fmtDayHour(d.last_backtest_at) : "Automática"}</span>
        <span class="kpi-tile-sub">Porcentajes 100% respaldados en registros</span>
      </div>
    `;

    const verifiedOnly = d.ranking.filter((r) => r.has_data && r.composite_accuracy_pct !== null);
    if (verifiedOnly.length) {
      Charts.renderAccuracyBarChart("chart-accuracy-ranking", verifiedOnly);
    } else {
      Charts.destroy("chart-accuracy-ranking");
    }

    // Mostrar fuentes activas (ocultando mensajes técnicos de API key)
    const visibleRanking = d.ranking.filter(
      (r) => !String(r.status_note || "").toLowerCase().includes("api key")
    );
    document.getElementById("accuracy-ranking-body").innerHTML = visibleRanking
      .map(
        (r) => `
      <tr>
        <td><strong>${API.escapeHtml(r.display_name)}</strong></td>
        <td class="num">${r.sample_count}</td>
        <td class="num"><strong>${r.has_data && r.composite_accuracy_pct !== null ? `${r.composite_accuracy_pct.toFixed(1)}%` : "Sin datos"}</strong></td>
        <td class="num">${r.temp_mae_c !== null ? `${r.temp_mae_c.toFixed(2)} °C` : "—"}</td>
        <td class="num">${r.temp_rmse_c !== null ? `${r.temp_rmse_c.toFixed(2)} °C` : "—"}</td>
        <td class="num">${r.temp_bias_c !== null ? `${r.temp_bias_c > 0 ? "+" : ""}${r.temp_bias_c.toFixed(2)} °C` : "—"}</td>
        <td class="num">${r.wind_mae_kmh !== null ? `${r.wind_mae_kmh.toFixed(1)} km/h` : "—"}</td>
        <td class="num">${r.rain_hit_rate_pct !== null ? `${r.rain_hit_rate_pct.toFixed(1)}%` : "—"}</td>
        <td><span class="status-badge ${r.has_data ? "status-ok" : "status-needs_key"}">${API.escapeHtml(r.status_note)}</span></td>
      </tr>`
      )
      .join("");

    const auditBody = document.getElementById("accuracy-audit-body");
    if (!d.recent_audit.length) {
      auditBody.innerHTML = `<tr><td colspan="10" style="text-align:center;padding:18px;opacity:0.8">Todavía no hay pares verificados para este filtro. Hacé clic en <strong>"Verificar últimos 7 días"</strong> o aguardá el ciclo automático del servidor.</td></tr>`;
    } else {
      auditBody.innerHTML = d.recent_audit
        .map(
          (a) => `
        <tr>
          <td class="tabular">${API.escapeHtml(API.fmtDayHour(a.target_time))}</td>
          <td>${API.escapeHtml(a.location_name)}</td>
          <td>${API.escapeHtml(a.provider_name)}</td>
          <td class="num">+${a.lead_hours} h</td>
          <td class="num">${a.pred_temp_c !== null ? `${a.pred_temp_c.toFixed(1)} °C` : "—"}</td>
          <td class="num">${a.actual_temp_c !== null ? `${a.actual_temp_c.toFixed(1)} °C` : "—"}</td>
          <td class="num">${a.temp_error_c !== null ? `${a.temp_error_c > 0 ? "+" : ""}${a.temp_error_c.toFixed(2)} °C` : "—"}</td>
          <td class="num">${a.pred_precip_mm !== null && a.actual_precip_mm !== null ? `${a.pred_precip_mm.toFixed(1)} / ${a.actual_precip_mm.toFixed(1)} mm` : "—"}</td>
          <td class="num"><strong>${a.composite_score_pct.toFixed(1)}%</strong></td>
          <td>${a.source_type === "historical_run" ? "Corrida histórica" : "En vivo BD"}</td>
        </tr>`
        )
        .join("");
    }
  }

  /* ── TAB 7: HISTORIAL METEOROLÓGICO ──────────────────────────────────── */
  function setHistoryPresetDates(daysBack) {
    const end = new Date();
    end.setDate(end.getDate() - 1);
    const start = new Date(end);
    start.setDate(end.getDate() - (daysBack - 1));
    document.getElementById("history-start-input").value = start.toISOString().slice(0, 10);
    document.getElementById("history-end-input").value = end.toISOString().slice(0, 10);
  }

  async function loadHistoryTab(startOverride = null, endOverride = null) {
    if (!state.location) return;
    const sInput = document.getElementById("history-start-input");
    const eInput = document.getElementById("history-end-input");
    if (!sInput.value || !eInput.value) setHistoryPresetDates(30);
    const start_date = startOverride || sInput.value;
    const end_date = endOverride || eInput.value;
    sInput.value = start_date;
    eInput.value = end_date;

    setLoading(true, `Consultando archivo histórico de ${state.location.name}...`);
    try {
      const h = await API.getHistory({
        lat: state.location.latitude,
        lon: state.location.longitude,
        name: state.location.name,
        admin1: state.location.admin1,
        country: state.location.country,
        start_date,
        end_date,
      });
      state.historyData = h;
      renderHistoryTab(h);
    } catch (err) {
      toast(`Error cargando historial: ${err.message}`, true);
    } finally {
      setLoading(false);
    }
  }

  function renderHistoryTab(h) {
    document.getElementById("history-narrative").textContent = h.narrative;
    const s = h.summary || {};
    document.getElementById("history-kpi-grid").innerHTML = `
      <div class="kpi-tile">
        <span class="kpi-tile-label">Máxima del Período</span>
        <span class="kpi-tile-value tabular">${API.fmtTemp(s.temp_max_c, state.unitSystem, 1)}</span>
        <span class="kpi-tile-sub tabular">${API.escapeHtml(s.temp_max_date || "—")} · ${s.hot_days || 0} días ≥30°C</span>
      </div>
      <div class="kpi-tile">
        <span class="kpi-tile-label">Mínima del Período</span>
        <span class="kpi-tile-value tabular">${API.fmtTemp(s.temp_min_c, state.unitSystem, 1)}</span>
        <span class="kpi-tile-sub tabular">${API.escapeHtml(s.temp_min_date || "—")} · ${s.frost_days || 0} heladas</span>
      </div>
      <div class="kpi-tile">
        <span class="kpi-tile-label">Lluvia Acumulada</span>
        <span class="kpi-tile-value tabular">${API.fmtPrecip(s.precip_total_mm, state.unitSystem)}</span>
        <span class="kpi-tile-sub tabular">${s.rainy_days || 0} días con lluvia · Pico ${s.wettest_day_mm ?? 0} mm</span>
      </div>
      <div class="kpi-tile">
        <span class="kpi-tile-label">Viento Máximo</span>
        <span class="kpi-tile-value tabular">${API.fmtWind(s.wind_max_kmh, state.unitSystem)}</span>
        <span class="kpi-tile-sub tabular">${API.escapeHtml(s.windiest_day || "—")} · ${h.days_count} días ERA5</span>
      </div>
    `;

    const labels = h.daily.map((d) => API.fmtChartDate(d.date));
    const tUnit = state.unitSystem === "imperial" ? "°F" : "°C";
    Charts.renderMultiLineChart(
      "chart-history-temp",
      labels,
      [
        { label: "Máxima", values: h.daily.map((d) => API.convertTemp(d.temp_max_c, state.unitSystem)) },
        { label: "Media", values: h.daily.map((d) => API.convertTemp(d.temp_mean_c, state.unitSystem)) },
        { label: "Mínima", values: h.daily.map((d) => API.convertTemp(d.temp_min_c, state.unitSystem)) },
      ],
      tUnit
    );
    Charts.renderRainBarsChart(
      "chart-history-rain",
      labels,
      h.daily.map((d) => d.precipitation_sum_mm || 0),
      "Precipitación diaria (mm)"
    );

    document.getElementById("history-table-body").innerHTML = [...h.daily]
      .reverse()
      .map(
        (d) => `
      <tr>
        <td class="tabular">${API.escapeHtml(d.date)}</td>
        <td>${API.escapeHtml(d.condition_text)}</td>
        <td class="num">${API.fmtTemp(d.temp_max_c, state.unitSystem, 1)}</td>
        <td class="num">${API.fmtTemp(d.temp_min_c, state.unitSystem, 1)}</td>
        <td class="num">${API.fmtTemp(d.temp_mean_c, state.unitSystem, 1)}</td>
        <td class="num">${API.fmtPrecip(d.precipitation_sum_mm, state.unitSystem)}</td>
        <td class="num">${API.fmtWind(d.wind_speed_max_kmh, state.unitSystem)}</td>
        <td class="num">${API.fmtWind(d.wind_gusts_max_kmh, state.unitSystem)}</td>
      </tr>`
      )
      .join("");
  }

  /* ── TAB 8: BUSCAR UBICACIONES ───────────────────────────────────────── */
  function updateSearchActiveCard() {
    const card = document.getElementById("search-active-card");
    const nameEl = document.getElementById("search-active-name");
    const metaEl = document.getElementById("search-active-meta");
    if (!card || !nameEl || !metaEl) return;
    if (state.location) {
      nameEl.textContent = state.location.name;
      metaEl.textContent =
        [state.location.admin1, state.location.country].filter(Boolean).join(", ") ||
        `${state.location.latitude.toFixed(2)}, ${state.location.longitude.toFixed(2)}`;
      card.classList.remove("hidden");
    } else {
      card.classList.add("hidden");
    }
  }

  function initSearchTab() {
    updateSearchActiveCard();
    const input = document.getElementById("search-tab-input");
    if (input) {
      if (state.location && !input.value) {
        input.value = state.location.name;
      }
      setTimeout(() => {
        input.focus();
        input.select();
      }, 100);
    }
  }

  function renderSearchTabResults(results) {
    const list = document.getElementById("search-tab-results");
    const status = document.getElementById("search-tab-status");
    if (!list) return;

    if (!results || !results.length) {
      list.classList.add("hidden");
      if (status) {
        status.textContent = "No se encontraron ubicaciones coincidentes.";
        status.classList.remove("hidden");
      }
      return;
    }

    if (status) {
      status.textContent = `${results.length} ubicaciones encontradas. Seleccioná una para consultar su clima:`;
      status.classList.remove("hidden");
    }

    list.innerHTML = results
      .map(
        (loc, idx) => `
      <button type="button" class="search-result-row" data-idx="${idx}">
        <div class="search-result-left">
          <span class="search-result-pin"><i data-lucide="map-pin"></i></span>
          <div class="search-result-title">
            <strong>${API.escapeHtml(loc.name)}</strong>
            <span class="search-result-meta">${API.escapeHtml([loc.admin1, loc.country].filter(Boolean).join(", ") || "Ubicación geográfica")} · <span class="tabular">${loc.latitude.toFixed(2)}, ${loc.longitude.toFixed(2)}</span></span>
          </div>
        </div>
        <span class="search-result-action">Ver clima <i data-lucide="arrow-right"></i></span>
      </button>`
      )
      .join("");
    list.classList.remove("hidden");
    refreshIcons();

    list.querySelectorAll(".search-result-row").forEach((btn) => {
      btn.addEventListener("click", () => {
        const chosen = results[Number(btn.dataset.idx)];
        list.classList.add("hidden");
        if (status) status.classList.add("hidden");
        const sTabInp = document.getElementById("search-tab-input");
        if (sTabInp) sTabInp.value = chosen.display_label || chosen.name;
        loadCityByCoords(chosen.latitude, chosen.longitude, { ...chosen, location_source: "search" }, 7, true);
      });
    });
  }

  async function executeTabSearch(rawQuery) {
    const q = (rawQuery || "").trim();
    const status = document.getElementById("search-tab-status");
    const list = document.getElementById("search-tab-results");
    if (!q) {
      if (status) status.classList.add("hidden");
      if (list) list.classList.add("hidden");
      return;
    }

    // Si es una consulta compleja (comparativa, historial o coordenadas directas), resolverla directamente
    if (
      q.includes(",") ||
      /^-?\d{1,2}(\.\d+)?\s*[,;\s]\s*-?\d{1,3}(\.\d+)?$/.test(q) ||
      /\b(comparar|cu[aá]nto|mes pasado|este a[nñ]o|ayer|historial)\b/i.test(q)
    ) {
      if (status) {
        status.textContent = "Procesando consulta meteorológica...";
        status.classList.remove("hidden");
      }
      if (list) list.classList.add("hidden");
      await submitNaturalQuery(q);
      return;
    }

    // Búsqueda de ubicación: geocodificar sin descargar pronósticos especulativos innecesarios
    if (status) {
      status.textContent = `Buscando "${q}"...`;
      status.classList.remove("hidden");
    }
    try {
      const res = await API.geocode(q, 7);
      const items = res.results || [];
      if (!items.length) {
        await submitNaturalQuery(q);
        return;
      }
      if (items.length === 1) {
        if (list) list.classList.add("hidden");
        if (status) status.classList.add("hidden");
        await loadCityByCoords(items[0].latitude, items[0].longitude, { ...items[0], location_source: "search" }, 7, true);
        return;
      }
      renderSearchTabResults(items);
    } catch (err) {
      if (status) {
        status.textContent = `Error buscando ubicación: ${err.message}`;
        status.classList.remove("hidden");
      }
    }
  }

  /* ── Autocomplete de ciudades ────────────────────────────────────────── */
  function attachAutocomplete(inputId, menuId) {
    const input = document.getElementById(inputId);
    const menu = document.getElementById(menuId);
    if (!input || !menu) return;
    let timer = null;

    input.addEventListener("input", () => {
      clearTimeout(timer);
      const val = input.value.trim();
      if (val.length < 2 || val.includes(",") || /\b(comparar|cu[aá]nto|mes pasado|este a[nñ]o)\b/i.test(val)) {
        menu.classList.add("hidden");
        return;
      }
      timer = setTimeout(async () => {
        try {
          const res = await API.geocode(val, 6);
          const list = res.results || [];
          if (!list.length) {
            menu.classList.add("hidden");
            return;
          }
          menu.innerHTML = list
            .map(
              (c, idx) => `
            <button type="button" class="autocomplete-item" role="option" data-idx="${idx}">
              <span><strong>${API.escapeHtml(c.name)}</strong> <span class="autocomplete-sub">${API.escapeHtml([c.admin1, c.country].filter(Boolean).join(", "))}</span></span>
              <span class="autocomplete-sub tabular">${c.latitude.toFixed(2)}, ${c.longitude.toFixed(2)}</span>
            </button>`
            )
            .join("");
          menu.classList.remove("hidden");
          menu.querySelectorAll(".autocomplete-item").forEach((btn) => {
            btn.addEventListener("click", () => {
              const chosen = list[Number(btn.dataset.idx)];
              menu.classList.add("hidden");
              input.value = chosen.display_label || chosen.name;
              loadCityByCoords(chosen.latitude, chosen.longitude, chosen, 7);
            });
          });
        } catch {
          menu.classList.add("hidden");
        }
      }, 240);
    });

    document.addEventListener("click", (ev) => {
      if (!menu.contains(ev.target) && ev.target !== input) {
        menu.classList.add("hidden");
      }
    });
  }

  /* ── Sincronización pasiva contra la base local cada 5 min ───────────── */
  function startPassiveDisplaySync() {
    if (state.refreshTimer) clearInterval(state.refreshTimer);
    state.refreshTimer = setInterval(() => silentSyncActiveCity(), 300000);
  }

  /* ── Enlace de eventos e inicialización ──────────────────────────────── */
  function bindEvents() {
    // Formulario de búsqueda en pantalla de inicio
    document.getElementById("landing-form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      const inp = document.getElementById("landing-input");
      submitNaturalQuery(inp.value.trim());
    });

    // Formulario de búsqueda en apartado Buscar (navegación superior)
    const searchTabForm = document.getElementById("search-tab-form");
    const searchTabInp = document.getElementById("search-tab-input");
    const searchTabClr = document.getElementById("search-tab-clear");
    if (searchTabForm && searchTabInp) {
      searchTabForm.addEventListener("submit", (ev) => {
        ev.preventDefault();
        executeTabSearch(searchTabInp.value.trim());
      });
      searchTabInp.addEventListener("input", () => {
        if (searchTabClr) {
          searchTabClr.classList.toggle("hidden", !searchTabInp.value.trim());
        }
      });
      if (searchTabClr) {
        searchTabClr.addEventListener("click", () => {
          searchTabInp.value = "";
          searchTabClr.classList.add("hidden");
          const list = document.getElementById("search-tab-results");
          const status = document.getElementById("search-tab-status");
          if (list) list.classList.add("hidden");
          if (status) status.classList.add("hidden");
          searchTabInp.focus();
        });
      }
    }

    const searchGpsBtn = document.getElementById("btn-search-gps");
    if (searchGpsBtn) {
      searchGpsBtn.addEventListener("click", requestExactGpsLocation);
    }

    const mainNav = document.getElementById("main-nav");
    if (mainNav) {
      mainNav.addEventListener("scroll", updateNavScrollCue, { passive: true });
    }

    document.querySelectorAll(".nav-pill").forEach((btn) => {
      btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });
    document.querySelectorAll("[data-jump-tab]").forEach((btn) => {
      btn.addEventListener("click", () => switchTab(btn.dataset.jumpTab));
    });
    document.getElementById("btn-expand-map").addEventListener("click", () => switchTab("map"));

    document.querySelectorAll(".range-quick-btns .mini-pill").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (!state.location) return;
        const r = btn.dataset.range;
        if (r === "7d") loadCityByCoords(state.location.latitude, state.location.longitude, state.location, 7);
        else if (r === "14d") loadCityByCoords(state.location.latitude, state.location.longitude, state.location, 14);
        else if (r === "last_month") submitNaturalQuery(`Historial del mes pasado en ${state.location.name}`);
      });
    });

    document.getElementById("btn-favorite-toggle").addEventListener("click", async () => {
      if (!state.location || !state.location.id) return;
      const nextFav = !state.location.is_favorite;
      try {
        const res = await API.toggleFavorite(state.location.id, nextFav);
        state.location = res.location;
        if (state.bundle) state.bundle.location = res.location;
        document.getElementById("btn-favorite-toggle").classList.toggle("is-favorite", nextFav);
        toast(nextFav ? `${state.location.name} agregada a favoritos.` : `${state.location.name} quitada de favoritos.`);
      } catch (err) {
        toast(`Error actualizando favorito: ${err.message}`, true);
      }
    });

    document.querySelectorAll("#hourly-window-group .seg-btn").forEach((b) => {
      b.addEventListener("click", () => {
        document.querySelectorAll("#hourly-window-group .seg-btn").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        state.hourlyWindow = Number(b.dataset.hours);
        renderHourlyTab();
      });
    });

    document.querySelectorAll("#extended-days-group .seg-btn").forEach((b) => {
      b.addEventListener("click", () => {
        document.querySelectorAll("#extended-days-group .seg-btn").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        ensureExtendedDays(Number(b.dataset.days));
      });
    });

    document.getElementById("city-compare-form").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const cities = document.getElementById("compare-cities-input").value.trim();
      const modeVal = document.getElementById("compare-mode-select").value;
      const params = { cities };
      if (modeVal === "hist_30") {
        const end = new Date();
        end.setDate(end.getDate() - 1);
        const start = new Date(end);
        start.setDate(end.getDate() - 29);
        params.start_date = start.toISOString().slice(0, 10);
        params.end_date = end.toISOString().slice(0, 10);
      } else {
        params.days = Number(modeVal);
      }
      setLoading(true, "Comparando ciudades...");
      try {
        const mc = await API.compareCities(params);
        state.overviewMode = "multi_city";
        state.multiCityData = mc;
        switchTab("overview");
        renderOverviewMultiCity(mc);
      } catch (err) {
        toast(`Error comparando ciudades: ${err.message}`, true);
      } finally {
        setLoading(false);
      }
    });

    document.querySelectorAll("#map-layer-group .seg-btn").forEach((b) => {
      b.addEventListener("click", () => {
        document.querySelectorAll("#map-layer-group .seg-btn").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        MapCtrl.setLayerType(b.dataset.layer);
      });
    });
    document.getElementById("radar-play-btn").addEventListener("click", () => MapCtrl.togglePlayback());
    document.getElementById("radar-frame-slider").addEventListener("input", (ev) => MapCtrl.setFrameIndex(ev.target.value));
    document.getElementById("radar-opacity-slider").addEventListener("input", (ev) => MapCtrl.setOpacity(ev.target.value));

    document.getElementById("accuracy-scope-select").addEventListener("change", loadAccuracyDashboard);
    document.getElementById("accuracy-source-select").addEventListener("change", loadAccuracyDashboard);
    document.getElementById("btn-run-backtest").addEventListener("click", runHistoricalBacktest);

    document.querySelectorAll("[data-hist-preset]").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll("[data-hist-preset]").forEach((x) => x.classList.remove("active"));
        btn.classList.add("active");
        setHistoryPresetDates(Number(btn.dataset.histPreset));
        loadHistoryTab();
      });
    });
    document.getElementById("history-range-form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      loadHistoryTab();
    });
    document.getElementById("btn-export-csv").addEventListener("click", () => {
      if (!state.location) return;
      API.exportHistoryCsv(
        {
          lat: state.location.latitude,
          lon: state.location.longitude,
          start_date: document.getElementById("history-start-input").value,
          end_date: document.getElementById("history-end-input").value,
        },
        state.historyData
      );
    });

    attachAutocomplete("landing-input", "landing-autocomplete");

    // Adaptación fluida al rotar el teléfono o cambiar tamaño de pantalla
    let resizeTimer = null;
    const handleViewportChange = () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        MapCtrl.invalidateAll();
        updateNavScrollCue();
        const nowCompact = window.innerWidth < 560;
        if (nowCompact !== state.lastCompactState) {
          state.lastCompactState = nowCompact;
          rerenderActiveState();
          if (state.activeTab === "accuracy" && state.accuracyData) {
            renderAccuracyTab(state.accuracyData);
          }
        }
      }, 180);
    };
    window.addEventListener("resize", handleViewportChange, { passive: true });
    window.addEventListener("orientationchange", handleViewportChange, { passive: true });
  }

  function rerenderActiveState() {
    if (state.overviewMode === "single_forecast" && state.bundle) {
      renderOverviewForecast(state.bundle);
    } else if (state.overviewMode === "single_history" && state.historyData) {
      renderOverviewHistory(state.historyData, document.body.dataset.theme, document.getElementById("date-focus-value").textContent);
    } else if (state.overviewMode === "multi_city" && state.multiCityData) {
      renderOverviewMultiCity(state.multiCityData);
    }
    if (state.activeTab === "hourly" && state.bundle) renderHourlyTab();
    if (state.activeTab === "extended" && state.bundle) renderExtendedTab();
    if (state.activeTab === "providers" && state.providerComp) renderProvidersTab(state.providerComp);
    if (state.activeTab === "history" && state.historyData) renderHistoryTab(state.historyData);
  }

  async function init() {
    bindEvents();
    refreshIcons();
    setHistoryPresetDates(30);
    // Geolocalización automática silenciosa en segundo plano (sin bloquear la vista principal)
    detectLocationInBackground();
    startPassiveDisplaySync();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
