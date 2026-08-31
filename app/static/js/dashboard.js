/* Main dashboard: KPIs, stat grid, current values and the 8 metric charts.
   Refreshes on an interval without reloading the page. */
(function () {
  const METRICS = ["latency_ms", "jitter_ms", "packet_loss_pct", "rx_mbps", "tx_mbps", "cpu_pct", "memory_pct", "active_sessions"];
  const rangeSelect = document.getElementById("range-select");
  (window.PNL.historyRanges || ["1h", "6h", "24h", "7d"]).forEach(r => {
    const o = document.createElement("option"); o.value = r; o.textContent = r;
    if (r === "24h") o.selected = true;
    rangeSelect.appendChild(o);
  });

  function kpi(id, label, ok) {
    const el = document.getElementById(id);
    el.className = "kpi " + (ok === null ? "kpi-muted" : ok ? "kpi-ok" : "kpi-bad");
    el.querySelector(".kpi-value").textContent = label;
  }

  async function refreshStatus() {
    try {
      const h = await PNLApi.get("/api/status");
      kpi("kpi-fortigate", tri(h.checks.fortigate_api), triOk(h.checks.fortigate_api));
      kpi("kpi-collector", tri(h.checks.collector), triOk(h.checks.collector));
      kpi("kpi-database", tri(h.checks.database), triOk(h.checks.database));
      document.getElementById("topbar-status").innerHTML =
        `<span class="muted">Modo ${PNL.mode}</span> · <span class="muted">API ${h.average_api_latency_ms ?? "—"} ms</span>`;
      document.getElementById("s-next").textContent = PNLApi.fmtTime(h.next_scheduled_collection);
      document.getElementById("s-apierr").textContent = h.failed_requests_24h ?? "—";
      document.getElementById("s-dbsize").textContent = PNLApi.fmtBytes(h.database_size_bytes);
    } catch (e) { console.warn(e); }
  }

  async function refreshLatest() {
    try {
      const d = await PNLApi.get("/api/telemetry/latest");
      document.getElementById("s-last").textContent = PNLApi.fmtTime(d.timestamp_utc);
      const wanUp = (d.metrics && d.metrics.packet_loss_pct !== null);
      kpi("kpi-wan", d.states && d.states.packet_loss_pct === "critical" ? "DEGRADED" : "UP",
          d.states && d.states.packet_loss_pct === "critical" ? false : true);
      const row = document.getElementById("values-row");
      row.innerHTML = Object.entries(d.metrics).map(([k, v]) => {
        const st = (d.states && d.states[k]) || "unknown";
        return `<div class="value-card state-${st}"><div class="v-name">${k}</div>
          <div class="v-num">${v === null || v === undefined ? "—" : (+v).toLocaleString()}</div></div>`;
      }).join("");
    } catch (e) { /* no data yet */ }
  }

  async function refreshDataset() {
    try {
      const s = await PNLApi.get("/api/dataset/statistics");
      document.getElementById("s-total").textContent = s.total_observations;
      document.getElementById("s-days").textContent = s.capture_days;
      document.getElementById("s-complete").textContent = s.completeness_pct + " %";
      document.getElementById("s-gaps").textContent = s.gaps.count;
    } catch (e) {}
    try {
      const ev = await PNLApi.get("/api/events?hours=24");
      document.getElementById("s-events").textContent = ev.length;
    } catch (e) {}
    try {
      const hist = await PNLApi.get("/api/telemetry/history?range=24h&metrics=latency_ms");
      document.getElementById("s-24h").textContent = (hist.timestamps || []).length;
    } catch (e) {}
  }

  async function refreshCharts() {
    const range = rangeSelect.value;
    document.getElementById("chart-note").textContent =
      PNLCharts.available() ? "" : "Chart.js no vendorizado — ver README §12.";
    try {
      const h = await PNLApi.get(`/api/telemetry/history?range=${range}&metrics=${METRICS.join(",")}`);
      METRICS.forEach(m => PNLCharts.update(m, h.timestamps || [], (h.series && h.series[m]) || []));
    } catch (e) { console.warn(e); }
  }

  const tri = v => (v === "n/a" ? "N/A" : v.toUpperCase());
  const triOk = v => (v === "n/a" ? null : v === "ok");

  function refreshAll() { refreshStatus(); refreshLatest(); refreshDataset(); refreshCharts(); }
  rangeSelect.addEventListener("change", refreshCharts);
  document.addEventListener("DOMContentLoaded", () => {
    refreshAll();
    setInterval(() => { refreshStatus(); refreshLatest(); refreshCharts(); }, PNL.refreshSeconds * 1000);
    setInterval(refreshDataset, 60000);
  });
})();
