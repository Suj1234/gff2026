// step2.js — live indicative premium as riders/SI change + save.
(function () {
  const appId = Number(document.getElementById("app-id").value);
  const $ = (id) => document.getElementById(id);
  const fmt = (n) => "₹" + Number(n).toLocaleString("en-IN");

  function collect() {
    return {
      app_id: appId,
      product_type: $("p-type").value,
      sum_assured: Number($("p-si").value),
      tenure_years: Number($("p-tenure").value),
      riders: Array.from(document.querySelectorAll(".rider:checked")).map((c) => c.value),
    };
  }

  async function refreshQuote() {
    const r = await fetch("/api/journey/quote", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(collect()),
    });
    const d = await r.json();
    if (!d.success) return;
    const p = d.premium;
    $("premium-total").textContent = fmt(p.total_annual);
    const lines = [`Base ${fmt(p.base)}`].concat(p.riders.map((x) => `${x.label} +${fmt(x.amount)}`));
    $("premium-detail").textContent = lines.join("  ·  ") + "   — " + p.note;
  }

  ["p-type", "p-si", "p-tenure"].forEach((id) => $(id).addEventListener("change", refreshQuote));
  document.querySelectorAll(".rider").forEach((c) => c.addEventListener("change", refreshQuote));

  $("save-product").addEventListener("click", async () => {
    const msg = $("product-msg");
    $("save-product").disabled = true;
    const r = await fetch("/api/journey/product", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(collect()),
    });
    const d = await r.json();
    $("save-product").disabled = false;
    msg.textContent = d.success ? "Saved ✓ — click Continue ›" : (d.message || "Failed");
    msg.className = "msg " + (d.success ? "ok" : "err");
  });

  refreshQuote();  // initial
})();
