// step1.js — email save (DigiLocker is a plain redirect link, no JS needed).
(function () {
  const appId = document.getElementById("app-id").value;
  const emailBtn = document.getElementById("save-email");
  if (!emailBtn) return;
  emailBtn.addEventListener("click", async () => {
    const email = document.getElementById("email").value.trim();
    const msg = document.getElementById("email-msg");
    if (!/^\S+@\S+\.\S+$/.test(email)) { msg.textContent = "Enter a valid email."; msg.className = "msg err"; return; }
    emailBtn.disabled = true;
    const r = await fetch("/api/journey/email", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ app_id: Number(appId), email }),
    });
    const d = await r.json();
    emailBtn.disabled = false;
    msg.textContent = d.success ? "Saved ✓" : (d.message || "Failed");
    msg.className = "msg " + (d.success ? "ok" : "err");
  });

  // Manual identity save (shown only when prefill was empty/unavailable)
  const idBtn = document.getElementById("save-identity");
  if (idBtn) {
    idBtn.addEventListener("click", async () => {
      const msg = document.getElementById("identity-msg");
      const body = {
        app_id: Number(appId),
        name: document.getElementById("f-name").value.trim(),
        dob: document.getElementById("f-dob").value.trim(),
        gender: document.getElementById("f-gender").value,
        pan: document.getElementById("f-pan").value.trim(),
        pincode: document.getElementById("f-pincode").value.trim(),
      };
      idBtn.disabled = true;
      const r = await fetch("/api/journey/identity", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const d = await r.json();
      idBtn.disabled = false;
      if (d.success) { msg.textContent = "Saved ✓ — reloading"; msg.className = "msg ok"; location.reload(); }
      else { msg.textContent = d.message || "Failed"; msg.className = "msg err"; }
    });
  }

  // Retry the Mobile->PAN prefill (POC vendor flaps)
  const retryBtn = document.getElementById("retry-prefill");
  if (retryBtn) {
    retryBtn.addEventListener("click", async () => {
      retryBtn.disabled = true; retryBtn.textContent = "Retrying…";
      const r = await fetch("/api/journey/prefill-retry/" + appId, { method: "POST" });
      const d = await r.json();
      if (d.success) location.reload();
      else { retryBtn.disabled = false; retryBtn.textContent = "↻ Retry prefill"; alert(d.message || "Still unavailable"); }
    });
  }
})();
