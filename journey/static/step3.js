// step3.js — save income + upload bank statement to iAdore.
(function () {
  const appId = Number(document.getElementById("app-id").value);
  const $ = (id) => document.getElementById(id);

  const saveBtn = $("save-financial");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const msg = $("financial-msg");
      saveBtn.disabled = true;
      const r = await fetch("/api/journey/financial", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          app_id: appId,
          declared_annual_income: Number($("f-income").value) || null,
          source_of_funds: $("f-source").value,
          purpose_of_cover: $("f-purpose").value,
        }),
      });
      const d = await r.json();
      saveBtn.disabled = false;
      msg.textContent = d.success ? "Saved ✓" : (d.message || "Failed");
      msg.className = "msg " + (d.success ? "ok" : "err");
    });
  }

  const upBtn = $("upload-stmt");
  if (upBtn) {
    upBtn.addEventListener("click", async () => {
      const msg = $("stmt-msg");
      const f = $("stmt-file").files[0];
      if (!f) { msg.textContent = "Choose a PDF first."; msg.className = "msg err"; return; }
      const fd = new FormData();
      fd.append("app_id", appId);
      fd.append("file", f);
      upBtn.disabled = true; msg.textContent = "Analysing… (iAdore submit → poll → report)"; msg.className = "msg";
      const r = await fetch("/api/journey/bank-statement", { method: "POST", body: fd });
      const d = await r.json();
      upBtn.disabled = false;
      if (d.success) { msg.textContent = "Analysed ✓ — reloading"; msg.className = "msg ok"; location.reload(); }
      else { msg.textContent = d.message || "Failed"; msg.className = "msg err"; }
    });
  }
})();
