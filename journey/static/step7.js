// step7.js — display-only mocked payment -> issue policy, then reload.
(function () {
  const btn = document.getElementById("pay");
  if (!btn) return;
  const appId = Number(document.getElementById("app-id").value);
  btn.addEventListener("click", async () => {
    const msg = document.getElementById("pay-msg");
    btn.disabled = true;
    msg.textContent = "Processing payment…"; msg.className = "msg";
    const r = await fetch("/api/journey/payment", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ app_id: appId, payment_mode: document.getElementById("pay-mode").value }),
    });
    const d = await r.json();
    if (d.success) { msg.textContent = "Payment success — issuing policy…"; msg.className = "msg ok"; location.reload(); }
    else { btn.disabled = false; msg.textContent = d.message || "Failed"; msg.className = "msg err"; }
  });
})();
