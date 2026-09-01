/* Minimal fetch helpers + formatters shared by every page. */
window.addEventListener("DOMContentLoaded", () => {
  const el = document.getElementById("tz-note");
  if (el) el.textContent = "horario: " + (window.PNLApi ? window.PNLApi.tz : "local") + " · dataset en UTC";
});

window.PNLApi = {
  async get(path) {
    const r = await fetch(path, { headers: { Accept: "application/json" } });
    if (!r.ok) throw new Error(`${path} -> ${r.status}`);
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) throw new Error(`${path} -> ${r.status}`);
    return r.json();
  },
  // The API returns UTC ISO strings; the dashboard shows them in the viewer's
  // local timezone. Exports and the database keep UTC.
  tz: (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { return "local"; } })(),
  fmtTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleString([], {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  },
  fmtDuration(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    const s = Math.floor(seconds);
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
  },
  fmtBytes(b) {
    if (b === null || b === undefined) return "—";
    const u = ["B", "KB", "MB", "GB", "TB"];
    let i = 0; let n = b;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(1)} ${u[i]}`;
  },
};
