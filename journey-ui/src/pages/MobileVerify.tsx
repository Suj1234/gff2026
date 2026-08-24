import { useState } from "react";
import { ShieldCheck, Lock, ArrowRight, Activity, FileCheck2, ChevronLeft } from "lucide-react";

// Mobile-verification gate — Variant A (Split canvas). Real page, calls the real
// FastAPI auth API (/api/auth/send-otp, /api/auth/verify-otp) through the Vite proxy.
type Phase = "mobile" | "otp";

export function MobileVerify() {
  const [phase, setPhase] = useState<Phase>("mobile");
  const [mobile, setMobile] = useState("");
  const [otp, setOtp] = useState("");
  const [consent, setConsent] = useState(false);
  const [otpRef, setOtpRef] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const mobileValid = /^[6-9]\d{9}$/.test(mobile);

  async function sendOtp() {
    setBusy(true); setMsg("");
    try {
      const r = await fetch("/api/auth/send-otp", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mobile, insurer_slug: "acme", purpose: "mobile_verification" }),
      });
      const d = await r.json();
      if (!d.success) { setMsg(d.message || "Could not send OTP"); return; }
      setOtpRef(d.otp_ref_id); setPhase("otp");
    } catch { setMsg("Network error — is the API running on :8899?"); }
    finally { setBusy(false); }
  }

  async function verifyOtp() {
    setBusy(true); setMsg("");
    try {
      const r = await fetch("/api/auth/verify-otp", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mobile, otp, otp_ref_id: otpRef, insurer_slug: "acme" }),
      });
      const d = await r.json();
      if (!d.success) { setMsg(d.message || "Invalid OTP"); return; }
      setMsg("Verified ✓ — opening console…");
    } catch { setMsg("Network error"); }
    finally { setBusy(false); }
  }

  return (
    <div className="min-h-screen grid lg:grid-cols-[1.05fr_1fr]">
      {/* LEFT — brand + trust story */}
      <aside className="hidden lg:flex flex-col justify-between p-14 bg-[var(--color-surface-2)] border-r border-[var(--color-line)]">
        <div className="flex items-center gap-2.5">
          <span className="grid place-items-center w-9 h-9 rounded-[var(--radius-sm)] bg-brand text-white">
            <ShieldCheck size={19} strokeWidth={2} />
          </span>
          <span className="text-[15px] font-medium tracking-tight">Acme Insurance</span>
        </div>

        <div className="max-w-[30ch]">
          <span className="mono text-[11px] uppercase tracking-[0.14em] text-ink-3">Underwriting Console</span>
          <h1 className="display text-[44px] leading-[1.05] mt-4 text-ink text-balance">
            Health cover, underwritten in minutes.
          </h1>
          <ul className="mt-9 space-y-4">
            {[
              [Activity, "Live identity, income & health signals assembled as you go"],
              [FileCheck2, "An explainable agent decision — every source cited"],
              [Lock, "DPDP-compliant, consent-first at every step"],
            ].map(([Icon, text], i) => (
              <li key={i} className="flex items-start gap-3 text-[15px] text-ink-2 leading-snug">
                <Icon size={18} className="mt-0.5 shrink-0 text-brand" strokeWidth={1.75} />
                <span>{text as string}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="flex items-center gap-5 text-[12px] text-ink-3">
          <span className="flex items-center gap-1.5"><Lock size={13} /> 256-bit TLS</span>
          <span className="h-3 w-px bg-[var(--color-line-2)]" />
          <span>IRDAI regulated</span>
        </div>
      </aside>

      {/* RIGHT — verify card */}
      <main className="flex items-center justify-center p-6 lg:p-14">
        <div className="w-full max-w-[380px]">
          <div className="lg:hidden flex items-center gap-2.5 mb-10">
            <span className="grid place-items-center w-8 h-8 rounded-[var(--radius-sm)] bg-brand text-white">
              <ShieldCheck size={17} />
            </span>
            <span className="text-[15px] font-medium">Acme Insurance</span>
          </div>

          <h2 className="display text-[28px] text-ink leading-tight">Start a new application</h2>
          <p className="text-[14px] text-ink-2 mt-2 leading-relaxed">
            {phase === "mobile"
              ? "Enter the applicant's mobile number to begin. We'll send a one-time code."
              : <>Enter the 6-digit code sent to <span className="mono text-ink">+91 {mobile}</span></>}
          </p>

          <div className="mt-8 space-y-4">
            {phase === "mobile" ? (
              <>
                <Field label="Mobile number">
                  <div className="flex items-stretch rounded-[var(--radius-sm)] border border-[var(--color-line-2)] bg-surface overflow-hidden focus-within:border-brand transition-colors">
                    <span className="mono grid place-items-center px-3 text-[14px] text-ink-2 bg-[var(--color-surface-2)] border-r border-[var(--color-line)]">+91</span>
                    <input
                      value={mobile} onChange={(e) => setMobile(e.target.value.replace(/\D/g, "").slice(0, 10))}
                      inputMode="numeric" placeholder="98765 43210" autoFocus
                      className="mono flex-1 px-3 py-2.5 text-[15px] bg-transparent outline-none placeholder:text-ink-3"
                    />
                  </div>
                </Field>

                <label className="flex items-start gap-2.5 cursor-pointer select-none">
                  <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)}
                    className="mt-0.5 w-4 h-4 accent-[var(--color-brand)]" />
                  <span className="text-[12.5px] text-ink-2 leading-snug">
                    The applicant consents (DPDP Act) to identity, financial &amp; health data being fetched for underwriting.
                  </span>
                </label>

                <PrimaryButton disabled={!mobileValid || !consent || busy} onClick={sendOtp}>
                  {busy ? "Sending…" : <>Get OTP <ArrowRight size={16} /></>}
                </PrimaryButton>
              </>
            ) : (
              <>
                <Field label="One-time code">
                  <input
                    type="password"
                    value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
                    inputMode="numeric" placeholder="••••••" autoFocus autoComplete="one-time-code"
                    className="mono w-full text-center tracking-[0.5em] text-[22px] py-3 rounded-[var(--radius-sm)] border border-[var(--color-line-2)] bg-surface outline-none focus:border-brand transition-colors placeholder:text-ink-3 placeholder:tracking-[0.3em]"
                  />
                </Field>
                <PrimaryButton disabled={otp.length !== 6 || busy} onClick={verifyOtp}>
                  {busy ? "Verifying…" : <>Verify &amp; continue <ArrowRight size={16} /></>}
                </PrimaryButton>
                <button onClick={() => { setPhase("mobile"); setOtp(""); setMsg(""); }}
                  className="flex items-center gap-1 text-[13px] text-ink-2 hover:text-ink transition-colors mx-auto">
                  <ChevronLeft size={14} /> Change number
                </button>
              </>
            )}

            {msg && <p className="text-[13px] text-ink-2 mono">{msg}</p>}
          </div>
        </div>
      </main>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="mono block text-[11px] uppercase tracking-[0.1em] text-ink-3 mb-2">{label}</label>
      {children}
    </div>
  );
}

function PrimaryButton({ children, disabled, onClick }: {
  children: React.ReactNode; disabled?: boolean; onClick?: () => void;
}) {
  return (
    <button onClick={onClick} disabled={disabled}
      className="w-full flex items-center justify-center gap-2 py-2.5 rounded-[var(--radius-sm)] bg-brand text-white text-[14.5px] font-medium
                 hover:bg-[var(--color-brand-hover)] hover:-translate-y-px transition-all duration-150
                 disabled:opacity-40 disabled:cursor-not-allowed disabled:translate-y-0">
      {children}
    </button>
  );
}
