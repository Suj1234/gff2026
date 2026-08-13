// rail.js — the live agent-signal rail (Phase D). Polls GET /api/journey/rail/{id}
// and renders the source groups. Each chip's severity/why comes straight from
// scoring.py via the endpoint — no client-side risk logic (rail = report).
(function () {
  const appId = document.getElementById("rail-app-id");
  if (!appId) return;
  const id = appId.value;
  const step = appId.dataset.step || "5";   // scope chips to the current step's groups
  const groupsEl = document.getElementById("rail-groups");
  const numEl = document.getElementById("rail-score-num");
  const bandEl = document.getElementById("rail-score-band");
  const ringEl = document.getElementById("rail-ring");
  const CIRC = 213.6; // 2*pi*r, r=34

  const SEV_CLASS = { ok: "s-ok", warn: "s-warn", bad: "s-bad", idle: "s-idle" };
  const prev = {}; // group key -> last severity, to bump only what changed

  function render(data) {
    if (data.safety_score != null) {
      const s = Math.round(data.safety_score);
      numEl.textContent = s;
      bandEl.textContent = data.band || "";
      const sev = data.band === "Low Risk" ? "ok" : data.band === "Moderate Risk" ? "warn" : "bad";
      bandEl.className = "tag " + sev;
      // sweep the ring to the score; arc color = band (semantic, never brand)
      ringEl.setAttribute("stroke", "var(--" + sev + ")");
      ringEl.style.strokeDashoffset = CIRC * (1 - Math.max(0, Math.min(100, s)) / 100);
    }
    if (!data.groups || !data.groups.length) return;

    groupsEl.innerHTML = "";
    for (const g of data.groups) {
      const changed = prev[g.key] !== undefined && prev[g.key] !== g.severity;
      prev[g.key] = g.severity;

      const wrap = document.createElement("div");
      wrap.className = "rgroup";
      const sub = Math.round(g.sub_score);
      // track color follows the chip severity (semantic, never brand)
      const barColor = g.severity === "ok" ? "var(--ok)" : g.severity === "warn"
        ? "var(--warn)" : g.severity === "bad" ? "var(--bad)" : "var(--n-3)";
      wrap.innerHTML =
        '<div class="rgroup__head">' +
          '<span class="rgroup__label">' + g.label + "</span>" +
          '<span class="rgroup__score">' + (g.severity === "idle" ? "—" : sub) + "</span>" +
        "</div>" +
        '<div class="track"><span style="width:' + (g.severity === "idle" ? 0 : sub) +
          "%;background:" + barColor + '"></span></div>' +
        '<div class="chip ' + (SEV_CLASS[g.severity] || "s-idle") + (changed ? " bump" : "") +
          '"><span class="led"></span><span class="why">' + escapeHtml(g.why) + "</span></div>";
      groupsEl.appendChild(wrap);
    }
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  async function poll() {
    try {
      const r = await fetch("/api/journey/rail/" + id + "?step=" + step, { headers: { "Cache-Control": "no-cache" } });
      if (r.ok) render(await r.json());
    } catch (_) { /* transient — next tick retries */ }
  }

  poll();                    // paint immediately on load
  setInterval(poll, 4000);   // then keep live (async signals: face scan, ABHA)
})();
