// step4.js — progressive disclosure + health save + face scan + ABHA.
(function () {
  const appId = Number(document.getElementById("app-id").value);
  const $ = (id) => document.getElementById(id);

  // Reveal conditions block if any screener is Yes (or conditions already chosen).
  const condBlock = $("conditions-block");
  const screeners = Array.from(document.querySelectorAll(".screener"));
  function syncConditions() {
    const anyYes = screeners.some((s) => s.checked)
      || document.querySelectorAll(".cond:checked").length > 0;
    condBlock.hidden = !anyYes;
  }
  screeners.forEach((s) => s.addEventListener("change", syncConditions));
  syncConditions();

  // Live BMI as height/weight change.
  function bmi() {
    const h = Number($("h-height").value), w = Number($("h-weight").value);
    if (h > 0 && w > 0) $("h-bmi").value = (w / ((h / 100) ** 2)).toFixed(1);
  }
  ["h-height", "h-weight"].forEach((id) => $(id).addEventListener("input", bmi));

  $("save-health").addEventListener("click", async () => {
    const msg = $("health-msg");
    const body = {
      app_id: appId,
      conditions: Array.from(document.querySelectorAll(".cond:checked")).map((c) => c.value),
      height_cm: Number($("h-height").value) || null,
      weight_kg: Number($("h-weight").value) || null,
      tobacco: $("h-tobacco").checked,
      alcohol: $("h-alcohol").checked,
      drugs: $("h-drugs").checked,
      ongoing_medication: ($("h-med") ? $("h-med").value : "") || null,
      family_history: ($("h-family").value || "").split(",").map((s) => s.trim()).filter(Boolean),
    };
    $("save-health").disabled = true;
    const r = await fetch("/api/journey/health", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const d = await r.json();
    $("save-health").disabled = false;
    msg.textContent = d.success ? ("Saved ✓" + (d.bmi ? " · BMI " + d.bmi : "")) : (d.message || "Failed");
    msg.className = "msg " + (d.success ? "ok" : "err");
  });

  const scanBtn = $("start-scan");
  if (scanBtn) scanBtn.addEventListener("click", async () => {
    const msg = $("scan-msg");
    scanBtn.disabled = true; msg.textContent = "Starting scan…"; msg.className = "msg";
    const r = await fetch("/api/journey/face-scan/start/" + appId, { method: "POST" });
    const d = await r.json();
    scanBtn.disabled = false;
    if (d.success && d.mode === "real" && d.scan_url) {
      msg.innerHTML = 'Scan link ready — open on the applicant\'s phone: <a href="' + d.scan_url + '" target="_blank">scan</a>. Vitals arrive when done.';
      msg.className = "msg ok";
    } else if (d.success) {
      msg.textContent = "Vitals captured ✓ — reloading"; msg.className = "msg ok"; setTimeout(() => location.reload(), 600);
    } else {
      msg.textContent = d.message || "Failed"; msg.className = "msg err";
    }
  });

  const abhaBtn = $("fetch-abha");
  if (abhaBtn) abhaBtn.addEventListener("click", async () => {
    const msg = $("abha-msg");
    abhaBtn.disabled = true; msg.textContent = "Fetching…"; msg.className = "msg";
    const r = await fetch("/api/journey/abha/fetch/" + appId, { method: "POST" });
    const d = await r.json();
    abhaBtn.disabled = false;
    if (d.success) { msg.textContent = "Fetched ✓ — reloading"; msg.className = "msg ok"; setTimeout(() => location.reload(), 600); }
    else { msg.textContent = d.message || "Failed"; msg.className = "msg err"; }
  });
})();
