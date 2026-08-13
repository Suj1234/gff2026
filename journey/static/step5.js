// step5.js — run the engine, then reload to render the report.
(function () {
  const btn = document.getElementById("run-decision");
  if (!btn) return;
  const appId = Number(document.getElementById("app-id").value);
  btn.addEventListener("click", async () => {
    const msg = document.getElementById("decision-msg");
    btn.disabled = true;
    msg.textContent = "Running the underwriting agent… (grey-zone cases consult the LLM)";
    msg.className = "msg";
    const r = await fetch("/api/journey/decide/" + appId, { method: "POST" });
    const d = await r.json();
    if (d.success) {
      msg.textContent = "Decision: " + d.verdict + " — rendering report…";
      msg.className = "msg ok";
      location.reload();
    } else {
      btn.disabled = false;
      msg.textContent = d.message || "Failed";
      msg.className = "msg err";
    }
  });
})();
