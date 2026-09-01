/* Chart.js wrappers. Chart.js is vendored at /static/vendor/chart.umd.min.js
   (see scripts/development_mac.sh / install_raspberry.sh which fetch it once). */
window.PNLCharts = (function () {
  const charts = {};
  const COLORS = {
    latency_ms: "#3ea6ff", jitter_ms: "#7c5cff", packet_loss_pct: "#f85149",
    rx_mbps: "#3fb950", tx_mbps: "#d29922", cpu_pct: "#ff8c69",
    memory_pct: "#5cc8ff", active_sessions: "#b98eff",
  };

  function available() {
    return typeof window.Chart !== "undefined" && !window.__noChart;
  }

  function ensure(metric) {
    if (!available()) return null;
    if (charts[metric]) return charts[metric];
    const el = document.getElementById("chart-" + metric);
    if (!el) return null;
    charts[metric] = new window.Chart(el, {
      type: "line",
      data: { labels: [], datasets: [{
        label: metric, data: [], borderColor: COLORS[metric] || "#3ea6ff",
        backgroundColor: "transparent", borderWidth: 1.5, pointRadius: 0, tension: 0.25,
      }]},
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        scales: {
          x: { ticks: { color: "#8b98a9", maxTicksLimit: 6 }, grid: { color: "#26303f" } },
          y: { ticks: { color: "#8b98a9" }, grid: { color: "#26303f" }, beginAtZero: true },
        },
        plugins: { legend: { display: false } },
      },
    });
    return charts[metric];
  }

  function update(metric, timestamps, values) {
    const c = ensure(metric);
    if (!c) {
      const el = document.getElementById("chart-" + metric);
      if (el && !el.dataset.warned) {
        el.dataset.warned = "1";
        el.insertAdjacentHTML("afterend",
          '<p class="muted">Chart.js no cargado: coloca chart.umd.min.js en app/static/vendor/</p>');
      }
      return;
    }
    // Show the viewer's local time on the axis (the dataset itself stays UTC).
    c.data.labels = timestamps.map(t =>
      new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
    c.data.datasets[0].data = values;
    c.update();
  }

  return { update, available };
})();
