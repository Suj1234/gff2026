// landing.js — drives the mobile gate against /api/auth/*. Vanilla, no deps.
(function () {
  const $ = (id) => document.getElementById(id);
  const msg = $("msg");
  let otpRef = null;

  function setMsg(text, kind) {
    msg.textContent = text || "";
    msg.className = "msg" + (kind ? " " + kind : "");
  }

  async function postJSON(url, body) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return { status: r.status, data: await r.json().catch(() => ({})) };
  }

  $("send-otp").addEventListener("click", async () => {
    const mobile = $("mobile").value.trim();
    if (!/^[6-9]\d{9}$/.test(mobile)) return setMsg("Enter a valid 10-digit mobile.", "err");
    if (!$("consent").checked) return setMsg("Applicant consent is required to proceed.", "err");
    setMsg("Sending OTP…");
    $("send-otp").disabled = true;
    const { data } = await postJSON("/api/auth/send-otp", {
      mobile, insurer_slug: "acme", purpose: "mobile_verification",
    });
    $("send-otp").disabled = false;
    if (!data.success) return setMsg(data.message || "Could not send OTP.", "err");
    otpRef = data.otp_ref_id;
    $("otp-target").textContent = "+91 " + mobile;
    $("step-mobile").hidden = true;
    $("step-otp").hidden = false;
    setMsg(data.message, "ok");
    $("otp").focus();
  });

  $("verify-otp").addEventListener("click", async () => {
    const mobile = $("mobile").value.trim();
    const otp = $("otp").value.trim();
    if (!/^\d{4,6}$/.test(otp)) return setMsg("Enter the 4-6 digit OTP.", "err");
    setMsg("Verifying…");
    $("verify-otp").disabled = true;
    const { data } = await postJSON("/api/auth/verify-otp", {
      mobile, otp, otp_ref_id: otpRef, insurer_slug: "acme",
    });
    $("verify-otp").disabled = false;
    if (!data.success) return setMsg(data.message || "Verification failed.", "err");
    setMsg("Verified. Opening console…", "ok");
    window.location.href = "/journey/app/" + data.application_id + "?step=1";
  });

  $("change-mobile").addEventListener("click", () => {
    $("step-otp").hidden = true;
    $("step-mobile").hidden = false;
    $("debug-otp").hidden = true;
    setMsg("");
  });
})();
