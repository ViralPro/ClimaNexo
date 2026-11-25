/* Gráficos meteorológicos — Réplica exacta del estilo DynamicChart (Google AI Studio) */

window.WeatherCharts = (() => {
  const PALETTE = ["#0B57D0", "#1e88e5", "#2a9d8f", "#f4a261", "#e9c46a", "#264653"];
  const instances = new Map();

  function destroy(canvasId) {
    const existing = instances.get(canvasId);
    if (existing) {
      existing.destroy();
      instances.delete(canvasId);
    }
  }

  function icyTooltip(unitSuffixMap = {}) {
    return {
      enabled: true,
      backgroundColor: "#f1fcff",
      borderColor: "rgba(45, 212, 191, 0.55)",
      borderWidth: 1,
      cornerRadius: 12,
      padding: 11,
      titleColor: "#0369a1",
      titleFont: { family: "Inter, sans-serif", size: 11, weight: "600" },
      bodyColor: "#0f172a",
      bodyFont: { family: "Inter, sans-serif", size: 12, weight: "500" },
      displayColors: true,
      boxPadding: 5,
      callbacks: {
        label(ctx) {
          const val = ctx.parsed?.y ?? ctx.parsed?.x ?? ctx.raw;
          if (val === null || val === undefined) return `${ctx.dataset.label}: —`;
          const suffix = unitSuffixMap[ctx.dataset.label] || "";
          return ` ${ctx.dataset.label}: ${val}${suffix}`;
        },
      },
    };
  }

  function isCompactScreen() {
    return typeof window !== "undefined" && window.innerWidth < 560;
  }

  function baseScales(hideXOnMany, labelsLen, yUnit = "", y2Unit = null) {
    const compact = isCompactScreen();
    const scales = {
      x: {
        grid: { display: false, drawBorder: false },
        border: { display: false },
        ticks: {
          display: !hideXOnMany || labelsLen < (compact ? 10 : 16),
          color: "rgba(255, 255, 255, 0.72)",
          font: { family: "Inter, sans-serif", size: compact ? 9.5 : 10.5 },
          maxRotation: 0,
          autoSkip: true,
          maxTicksLimit: compact ? 5 : 9,
        },
      },
      y: {
        grid: { color: "rgba(255, 255, 255, 0.08)", drawBorder: false },
        border: { display: false },
        ticks: {
          color: "rgba(255, 255, 255, 0.72)",
          font: { family: "Inter, sans-serif", size: compact ? 9.5 : 10.5 },
          maxTicksLimit: compact ? 5 : 6,
          callback: (v) => (yUnit ? `${v}${yUnit}` : v),
        },
      },
    };
    if (y2Unit !== null) {
      scales.y1 = {
        position: "right",
        beginAtZero: true,
        grid: { display: false, drawBorder: false },
        border: { display: false },
        ticks: {
          display: !compact,
          color: "rgba(186, 230, 253, 0.78)",
          font: { family: "Inter, sans-serif", size: 10 },
          callback: (v) => `${v}${y2Unit}`,
        },
      };
    }
    return scales;
  }

  function create(canvasId, config) {
    destroy(canvasId);
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === "undefined") return null;
    const chart = new Chart(canvas.getContext("2d"), config);
    instances.set(canvasId, chart);
    return chart;
  }

  function renderTempPrecipChart(canvasId, labels, tempMaxVals, precipVals, unitSystem) {
    const tUnit = unitSystem === "imperial" ? "°F" : "°C";
    return create(canvasId, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            type: "bar",
            label: `Temp. Máxima (${tUnit})`,
            data: tempMaxVals,
            backgroundColor: PALETTE[0],
            hoverBackgroundColor: "#0842a0",
            borderRadius: 6,
            borderSkipped: false,
            maxBarThickness: 28,
            yAxisID: "y",
            order: 2,
          },
          {
            type: "line",
            label: "Precipitación (mm)",
            data: precipVals,
            borderColor: "#38bdf8",
            backgroundColor: "rgba(56, 189, 248, 0.18)",
            borderWidth: 2.5,
            tension: 0.38,
            pointRadius: 2.5,
            pointHoverRadius: 5,
            fill: true,
            yAxisID: "y1",
            order: 1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            display: true,
            position: "bottom",
            labels: { color: "rgba(255,255,255,0.85)", boxWidth: 10, font: { size: 11 } },
          },
          tooltip: icyTooltip({
            [`Temp. Máxima (${tUnit})`]: ` ${tUnit}`,
            "Precipitación (mm)": " mm",
          }),
        },
        scales: baseScales(false, labels.length, tUnit, " mm"),
      },
    });
  }

  function renderRainBarsChart(canvasId, labels, rainVals, labelName = "Precipitación (mm)") {
    return create(canvasId, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: labelName,
            data: rainVals,
            backgroundColor: PALETTE[0],
            hoverBackgroundColor: "#1d4ed8",
            borderRadius: 6,
            borderSkipped: false,
            maxBarThickness: 24,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: icyTooltip({ [labelName]: " mm" }),
        },
        scales: baseScales(true, labels.length, " mm"),
      },
    });
  }

  function renderWindLineChart(canvasId, labels, windVals, unitSystem) {
    const wUnit = unitSystem === "imperial" ? " mph" : " km/h";
    const lbl = `Viento Máx. (${wUnit.trim()})`;
    return create(canvasId, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: lbl,
            data: windVals,
            borderColor: PALETTE[0],
            backgroundColor: "rgba(11, 87, 208, 0.14)",
            borderWidth: 2.6,
            tension: 0.42,
            fill: true,
            pointRadius: 2,
            pointHoverRadius: 5,
            pointBackgroundColor: "#ffffff",
            pointBorderColor: PALETTE[0],
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: icyTooltip({ [lbl]: wUnit }),
        },
        scales: baseScales(true, labels.length, wUnit),
      },
    });
  }

  function renderHourlyDetailChart(canvasId, labels, temps, feels, rainProbs, unitSystem) {
    const tUnit = unitSystem === "imperial" ? "°F" : "°C";
    return create(canvasId, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            type: "line",
            label: `Temperatura (${tUnit})`,
            data: temps,
            borderColor: PALETTE[0],
            backgroundColor: "rgba(11, 87, 208, 0.14)",
            borderWidth: 2.8,
            tension: 0.4,
            fill: true,
            pointRadius: 1.5,
            pointHoverRadius: 5,
            yAxisID: "y",
          },
          {
            type: "line",
            label: `Sensación (${tUnit})`,
            data: feels,
            borderColor: "#f4a261",
            borderDash: [5, 4],
            borderWidth: 2,
            tension: 0.4,
            fill: false,
            pointRadius: 0,
            pointHoverRadius: 4,
            yAxisID: "y",
          },
          {
            type: "bar",
            label: "Prob. lluvia (%)",
            data: rainProbs,
            backgroundColor: "rgba(56, 189, 248, 0.38)",
            borderRadius: 4,
            maxBarThickness: 14,
            yAxisID: "y1",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            display: true,
            position: "bottom",
            labels: { color: "rgba(255,255,255,0.88)", boxWidth: 10, font: { size: 11 } },
          },
          tooltip: icyTooltip({
            [`Temperatura (${tUnit})`]: ` ${tUnit}`,
            [`Sensación (${tUnit})`]: ` ${tUnit}`,
            "Prob. lluvia (%)": "%",
          }),
        },
        scales: {
          ...baseScales(false, labels.length, tUnit, "%"),
          y1: {
            position: "right",
            min: 0,
            max: 100,
            grid: { display: false },
            border: { display: false },
            ticks: { color: "rgba(186,230,253,0.8)", font: { size: 10 }, callback: (v) => `${v}%` },
          },
        },
      },
    });
  }

  function renderMultiLineChart(canvasId, labels, seriesList, yUnit = "°C") {
    const suffixMap = {};
    const datasets = seriesList.map((s, idx) => {
      const color = PALETTE[idx % PALETTE.length];
      suffixMap[s.label] = ` ${yUnit}`;
      return {
        label: s.label,
        data: s.values,
        borderColor: color,
        backgroundColor: color,
        borderWidth: 2.5,
        tension: 0.38,
        fill: false,
        spanGaps: true,
        pointRadius: labels.length > 18 ? 0 : 2.5,
        pointHoverRadius: 5,
      };
    });

    return create(canvasId, {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            display: true,
            position: "bottom",
            labels: { color: "rgba(255,255,255,0.88)", boxWidth: 10, font: { size: 11 } },
          },
          tooltip: icyTooltip(suffixMap),
        },
        scales: baseScales(true, labels.length, yUnit),
      },
    });
  }

  function renderGroupedBarChart(canvasId, labels, seriesList, yUnit = " mm") {
    const suffixMap = {};
    const datasets = seriesList.map((s, idx) => {
      const color = PALETTE[idx % PALETTE.length];
      suffixMap[s.label] = yUnit;
      return {
        label: s.label,
        data: s.values,
        backgroundColor: color,
        borderRadius: 4,
        borderSkipped: false,
        maxBarThickness: 18,
      };
    });

    return create(canvasId, {
      type: "bar",
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            display: true,
            position: "bottom",
            labels: { color: "rgba(255,255,255,0.88)", boxWidth: 10, font: { size: 11 } },
          },
          tooltip: icyTooltip(suffixMap),
        },
        scales: baseScales(true, labels.length, yUnit),
      },
    });
  }

  function renderAccuracyBarChart(canvasId, rankingWithData) {
    const compact = isCompactScreen();
    const labels = rankingWithData.map((r) => {
      const shortName = compact ? r.display_name.replace(/\s*\([^)]*\)/g, "") : r.display_name;
      return `${shortName} (${r.sample_count})`;
    });
    const values = rankingWithData.map((r) => r.composite_accuracy_pct);
    return create(canvasId, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "Precisión Compuesta",
            data: values,
            backgroundColor: values.map((_, i) => PALETTE[i % PALETTE.length]),
            borderRadius: 8,
            borderSkipped: false,
            maxBarThickness: compact ? 24 : 32,
          },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: icyTooltip({ "Precisión Compuesta": "%" }),
        },
        scales: {
          x: {
            min: 0,
            max: 100,
            grid: { color: "rgba(255,255,255,0.10)" },
            border: { display: false },
            ticks: {
              color: "rgba(255,255,255,0.75)",
              font: { size: compact ? 9.5 : 11 },
              stepSize: compact ? 25 : 10,
              callback: (v) => `${v}%`,
            },
          },
          y: {
            grid: { display: false },
            border: { display: false },
            ticks: { color: "#ffffff", font: { size: compact ? 10 : 11.5, weight: "500" } },
          },
        },
      },
    });
  }

  return {
    PALETTE,
    destroy,
    renderTempPrecipChart,
    renderRainBarsChart,
    renderWindLineChart,
    renderHourlyDetailChart,
    renderMultiLineChart,
    renderGroupedBarChart,
    renderAccuracyBarChart,
  };
})();
