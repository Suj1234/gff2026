// step6.js — reveal appointee block if nominee DOB < 18; save nominee.
(function () {
  const appId = Number(document.getElementById("app-id").value);
  const $ = (id) => document.getElementById(id);
  const block = $("appointee-block");

  function ageFrom(dob) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(dob || "");
    if (!m) return null;
    const b = new Date(+m[1], +m[2] - 1, +m[3]), t = new Date();
    let a = t.getFullYear() - b.getFullYear();
    if (t.getMonth() < b.getMonth() || (t.getMonth() === b.getMonth() && t.getDate() < b.getDate())) a--;
    return a;
  }
  function syncAppointee() {
    const age = ageFrom($("n-dob").value);
    block.hidden = !(age !== null && age < 18);
  }
  $("n-dob").addEventListener("input", syncAppointee);
  syncAppointee();

  $("save-nominee").addEventListener("click", async () => {
    const msg = $("nominee-msg");
    const minor = !block.hidden;
    const body = {
      app_id: appId,
      name: $("n-name").value.trim(),
      dob: $("n-dob").value.trim(),
      relationship: $("n-rel").value,
      share_pct: Number($("n-share").value) || 100,
      address: $("n-addr").value.trim(),
    };
    if (minor) {
      body.appointee_name = $("a-name").value.trim();
      body.appointee_dob = $("a-dob").value.trim();
      body.appointee_relationship = $("a-rel").value.trim();
    }
    if (!body.name) { msg.textContent = "Nominee name is required."; msg.className = "msg err"; return; }
    $("save-nominee").disabled = true;
    const r = await fetch("/api/journey/nominee", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const d = await r.json();
    $("save-nominee").disabled = false;
    msg.textContent = d.success ? "Saved ✓ — click Continue ›" : (d.message || "Failed");
    msg.className = "msg " + (d.success ? "ok" : "err");
  });
})();
