/* Mapas meteorológicos interactivos con Leaflet + Radar RainViewer en vivo */

window.WeatherMap = (() => {
  let miniMap = null;
  let miniMarker = null;
  let miniRadarLayer = null;
  let miniResizeObs = null;
  let miniCenter = null;
  let miniResizeTimer = null;

  let fullMap = null;
  let fullMarker = null;
  let fullOverlayLayer = null;
  let fullResizeObs = null;
  let fullCenter = null;
  let fullResizeTimer = null;

  let radarMeta = null;
  let activeLayerType = "radar"; // radar | satellite | none
  let frames = [];
  let frameIndex = 0;
  let playTimer = null;
  let onPointClickCb = null;

  function makePinIcon() {
    return L.divIcon({
      className: "custom-weather-pin-wrap",
      html: '<div class="custom-weather-pin"></div>',
      iconSize: [18, 18],
      iconAnchor: [9, 9],
    });
  }

  function tileUrlForFrame(host, frame, layerType) {
    if (!frame || !frame.path) return null;
    if (layerType === "satellite") {
      return `${host}${frame.path}/256/{z}/{x}/{y}/0/0_0.png`;
    }
    return `${host}${frame.path}/256/{z}/{x}/{y}/2/1_1.png`;
  }

  function formatUnixLocal(ts) {
    if (!ts) return "—";
    const d = new Date(ts * 1000);
    return d.toLocaleString("es-ES", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function initMiniMap(lat, lon, label) {
    const el = document.getElementById("mini-map");
    if (!el || typeof L === "undefined") return;
    miniCenter = [Number(lat), Number(lon)];

    if (!miniMap) {
      miniMap = L.map(el, {
        center: miniCenter,
        zoom: 8,
        zoomControl: false,
        attributionControl: true,
      });
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 18,
        attribution: "&copy; OSM",
      }).addTo(miniMap);
      miniMarker = L.marker(miniCenter, { icon: makePinIcon() }).addTo(miniMap);
      if (typeof ResizeObserver !== "undefined" && !miniResizeObs) {
        miniResizeObs = new ResizeObserver(() => {
          clearTimeout(miniResizeTimer);
          miniResizeTimer = setTimeout(() => {
            if (miniMap && miniCenter) {
              miniMap.invalidateSize({ animate: false });
              miniMap.setView(miniCenter, miniMap.getZoom(), { animate: false });
            }
          }, 100);
        });
        miniResizeObs.observe(el);
      }
    } else {
      miniMap.setView(miniCenter, 8, { animate: false });
      miniMarker.setLatLng(miniCenter);
    }
    if (label) miniMarker.bindPopup(`<strong>${window.WeatherAPI.escapeHtml(label)}</strong>`);
    setTimeout(() => {
      if (miniMap && miniCenter) {
        miniMap.invalidateSize({ animate: false });
        miniMap.setView(miniCenter, miniMap.getZoom(), { animate: false });
      }
    }, 160);
    applyLatestRadarToMini();
  }

  function applyLatestRadarToMini() {
    if (!miniMap || !radarMeta) return;
    const latest = (radarMeta.radar_past || []).slice(-1)[0];
    if (!latest) return;
    const url = tileUrlForFrame(radarMeta.host, latest, "radar");
    if (!url) return;
    if (miniRadarLayer) miniMap.removeLayer(miniRadarLayer);
    miniRadarLayer = L.tileLayer(url, {
      opacity: 0.58,
      zIndex: 20,
      maxNativeZoom: 7,
      maxZoom: 18,
    }).addTo(miniMap);
  }

  function initFullMap(lat, lon, label, onPointClick) {
    onPointClickCb = onPointClick || onPointClickCb;
    const el = document.getElementById("full-map");
    if (!el || typeof L === "undefined") return;
    fullCenter = [Number(lat), Number(lon)];

    if (!fullMap) {
      fullMap = L.map(el, {
        center: fullCenter,
        zoom: 7,
        zoomControl: true,
      });
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 18,
        attribution: "&copy; OpenStreetMap &middot; RainViewer",
      }).addTo(fullMap);

      fullMarker = L.marker(fullCenter, { icon: makePinIcon() }).addTo(fullMap);
      fullMap.on("click", (ev) => {
        const clat = Number(ev.latlng.lat.toFixed(4));
        const clon = Number(ev.latlng.lng.toFixed(4));
        fullCenter = [clat, clon];
        fullMarker.setLatLng(fullCenter);
        if (onPointClickCb) onPointClickCb(clat, clon);
      });
      if (typeof ResizeObserver !== "undefined" && !fullResizeObs) {
        fullResizeObs = new ResizeObserver(() => {
          clearTimeout(fullResizeTimer);
          fullResizeTimer = setTimeout(() => {
            if (fullMap && fullCenter) {
              fullMap.invalidateSize({ animate: false });
              fullMap.setView(fullCenter, fullMap.getZoom(), { animate: false });
            }
          }, 100);
        });
        fullResizeObs.observe(el);
      }
    } else {
      fullMap.setView(fullCenter, Math.max(fullMap.getZoom(), 7), { animate: false });
      fullMarker.setLatLng(fullCenter);
    }

    if (label) fullMarker.bindPopup(`<strong>${window.WeatherAPI.escapeHtml(label)}</strong>`);
    setTimeout(() => {
      if (fullMap && fullCenter) {
        fullMap.invalidateSize({ animate: false });
        fullMap.setView(fullCenter, fullMap.getZoom(), { animate: false });
      }
    }, 180);
    renderActiveFullFrame();
  }

  function setRadarMetadata(meta) {
    radarMeta = meta;
    rebuildFrames();
    applyLatestRadarToMini();
    renderActiveFullFrame();
  }

  function setLayerType(type) {
    activeLayerType = type;
    stopPlayback();
    rebuildFrames();
    renderActiveFullFrame();
  }

  function rebuildFrames() {
    if (!radarMeta || activeLayerType === "none") {
      frames = [];
      frameIndex = 0;
    } else if (activeLayerType === "satellite") {
      frames = radarMeta.satellite_infrared || [];
      frameIndex = Math.max(0, frames.length - 1);
    } else {
      frames = [...(radarMeta.radar_past || []), ...(radarMeta.radar_nowcast || [])];
      const pastLen = (radarMeta.radar_past || []).length;
      frameIndex = Math.max(0, pastLen > 0 ? pastLen - 1 : frames.length - 1);
    }

    const slider = document.getElementById("radar-frame-slider");
    if (slider) {
      slider.max = String(Math.max(0, frames.length - 1));
      slider.value = String(frameIndex);
      slider.disabled = frames.length === 0;
    }
  }

  function setFrameIndex(idx) {
    if (!frames.length) return;
    frameIndex = Math.max(0, Math.min(frames.length - 1, Number(idx)));
    const slider = document.getElementById("radar-frame-slider");
    if (slider) slider.value = String(frameIndex);
    renderActiveFullFrame();
  }

  function setOpacity(valPct) {
    if (fullOverlayLayer) {
      fullOverlayLayer.setOpacity(Math.max(0.1, Math.min(1, Number(valPct) / 100)));
    }
  }

  function renderActiveFullFrame() {
    const timeEl = document.getElementById("radar-frame-time");
    if (!fullMap) return;
    if (fullOverlayLayer) {
      fullMap.removeLayer(fullOverlayLayer);
      fullOverlayLayer = null;
    }
    if (activeLayerType === "none" || !frames.length || !radarMeta) {
      if (timeEl) timeEl.textContent = activeLayerType === "none" ? "Capa oculta" : "Sin cuadros disponibles";
      return;
    }

    const frame = frames[frameIndex];
    const url = tileUrlForFrame(radarMeta.host, frame, activeLayerType);
    const opSlider = document.getElementById("radar-opacity-slider");
    const opacity = opSlider ? Number(opSlider.value) / 100 : 0.65;
    if (url) {
      fullOverlayLayer = L.tileLayer(url, {
        opacity,
        zIndex: 25,
        maxNativeZoom: 7,
        maxZoom: 18,
      }).addTo(fullMap);
    }
    if (timeEl) {
      const pastCount = (radarMeta.radar_past || []).length;
      const tag = activeLayerType === "radar" && frameIndex >= pastCount ? "Pronóstico" : "Observado";
      timeEl.textContent = `${tag} · ${formatUnixLocal(frame.time)}`;
    }
  }

  function togglePlayback() {
    if (playTimer) {
      stopPlayback();
      return false;
    }
    if (!frames.length) return false;
    playTimer = setInterval(() => {
      frameIndex = (frameIndex + 1) % frames.length;
      const slider = document.getElementById("radar-frame-slider");
      if (slider) slider.value = String(frameIndex);
      renderActiveFullFrame();
    }, 650);
    updatePlayIcon(true);
    return true;
  }

  function stopPlayback() {
    if (playTimer) {
      clearInterval(playTimer);
      playTimer = null;
    }
    updatePlayIcon(false);
  }

  function updatePlayIcon(isPlaying) {
    const btn = document.getElementById("radar-play-btn");
    if (!btn) return;
    btn.innerHTML = `<i data-lucide="${isPlaying ? "pause" : "play"}"></i>`;
    if (window.lucide) window.lucide.createIcons();
  }

  function invalidateAll() {
    setTimeout(() => {
      if (miniMap && miniCenter) {
        miniMap.invalidateSize({ animate: false });
        miniMap.setView(miniCenter, miniMap.getZoom(), { animate: false });
      }
      if (fullMap && fullCenter) {
        fullMap.invalidateSize({ animate: false });
        fullMap.setView(fullCenter, fullMap.getZoom(), { animate: false });
      }
    }, 150);
  }

  return {
    initMiniMap,
    initFullMap,
    setRadarMetadata,
    setLayerType,
    setFrameIndex,
    setOpacity,
    togglePlayback,
    stopPlayback,
    invalidateAll,
  };
})();
