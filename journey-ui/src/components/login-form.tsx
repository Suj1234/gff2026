import { useState, useEffect, useRef } from "react"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { ShieldCheck, UsersThree, SealCheck, LockKey, ArrowRight, CaretLeft, X, IdentificationCard, Bank, Heartbeat, ChatCircleText } from "@phosphor-icons/react"

// Landing gate, redesigned via design-taste-frontend (redesign / preserve mode).
// Design read: life-insurance onboarding gate, trust-first calm-premium, one accent (teal),
// dials VARIANCE 4 / MOTION 3 / DENSITY 4. shadcn/ui + Tailwind v4. Phosphor icons.
// Full-bleed split (desktop) / stacked (mobile). No em-dashes anywhere. No hand-drawn art.
type Variant = "teal" | "light" | "dark"

const PANEL: Record<Variant, {
  panel: string; strip: string; glow?: React.CSSProperties;
  tile: string; foot: string; footRule: string; iconWrap: string;
}> = {
  teal: {
    panel: "bg-gradient-to-br from-primary to-[oklch(0.42_0.08_206)] text-primary-foreground",
    strip: "bg-gradient-to-br from-primary to-[oklch(0.44_0.08_205)] text-primary-foreground",
    glow: { background: "radial-gradient(60% 55% at 82% 18%, oklch(0.62 0.11 195 / 0.45), transparent)" },
    tile: "bg-primary-foreground/12", iconWrap: "bg-primary-foreground/15",
    foot: "opacity-75", footRule: "bg-primary-foreground/30",
  },
  light: {
    // ~3% darker than the original #f7f6f3, same warm hue.
    panel: "bg-[#efeeea] text-foreground border-r",
    strip: "bg-[#efeeea] text-foreground border-b",
    tile: "bg-primary/10 text-primary", iconWrap: "bg-primary text-primary-foreground",
    foot: "text-muted-foreground", footRule: "bg-border",
  },
  dark: {
    panel: "bg-gradient-to-br from-[oklch(0.24_0.01_240)] to-[oklch(0.17_0.008_250)] text-white",
    strip: "bg-[oklch(0.22_0.01_245)] text-white",
    glow: { background: "radial-gradient(60% 55% at 82% 18%, oklch(0.53 0.09 199 / 0.32), transparent)" },
    tile: "bg-white/8 text-[oklch(0.72_0.1_195)]", iconWrap: "bg-white/10",
    foot: "opacity-65", footRule: "bg-white/20",
  },
}

// Trust factors: a headline stat + what it means. Real insurers lead with these (IRDAI
// requires the claim-settlement ratio to be published). Demo figures for the POC.
const POINTS: [React.ElementType, string, string][] = [
  [SealCheck, "99.1%", "Claims settled, FY25"],   /* demo */
  [UsersThree, "12.4L", "Families protected"],     /* demo */
  [ShieldCheck, "IRDAI No. 512", "Licensed and regulated"],  /* demo */
]

// DPDP Act, 2023 (Rule 3): the notice must, at minimum, give an itemised description of the
// personal data, the specified purpose, and an itemised description of the goods/services
// each processing enables. `enables` carries that last element per data category.
type Purpose = { key: string; icon: React.ElementType; label: string; purpose: string; enables: string; required?: boolean }
const PURPOSES: Purpose[] = [
  { key: "identity", icon: IdentificationCard, label: "Identity (PAN, Aadhaar)", purpose: "Verify who you are (KYC), as required to issue a policy.", enables: "Policy issuance and regulatory KYC", required: true },
  { key: "financial", icon: Bank, label: "Financial (bank statements, income)", purpose: "Assess affordability and the sum assured you qualify for.", enables: "Your eligible cover amount and premium" },
  { key: "health", icon: Heartbeat, label: "Health (ABHA, medical records)", purpose: "Underwrite your cover and set an accurate premium.", enables: "An accurate, personalised premium" },
  { key: "contact", icon: ChatCircleText, label: "Contact (mobile, email)", purpose: "Send updates about your application and policy.", enables: "Application status and policy documents" },
]

// Rule 3 also requires the notice to state how the Data Principal may exercise their rights.
const DATA_RIGHTS = [
  "Access a summary of the data we hold about you",
  "Correct, complete, or update your data",
  "Erase your data once its purpose is served",
  "Nominate someone to act on your behalf",
]

export function LoginForm({ variant = "teal", onVerified, onVerifyStart, onVerifyFail }:
  { variant?: Variant; onVerified?: (appId: number) => void; onVerifyStart?: () => void; onVerifyFail?: () => void }) {
  const [phase, setPhase] = useState<"mobile" | "otp">("mobile")
  const [mobile, setMobile] = useState("")
  const [otp, setOtp] = useState("")
  // DPDP consent: accepted only after the notice modal is reviewed + accepted.
  const [consented, setConsented] = useState(false)
  const [purposes, setPurposes] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(PURPOSES.map((p) => [p.key, !!p.required])))
  const [consentOpen, setConsentOpen] = useState(false)
  const [otpRef, setOtpRef] = useState("")
  const [debugOtp, setDebugOtp] = useState<string | null>(null)
  const [msg, setMsg] = useState("")
  const [busy, setBusy] = useState(false)
  // Resend cooldown so the button can't be spammed. Resend re-calls send-otp (fresh ref).
  const [resendIn, setResendIn] = useState(0)
  const v = PANEL[variant]

  const mobileValid = /^[6-9]\d{9}$/.test(mobile)
  const consentOk = consented && PURPOSES.every((p) => !p.required || purposes[p.key])

  // One ticker drives both timers while on the OTP screen.
  useEffect(() => {
    if (phase !== "otp") return
    const id = window.setInterval(() => {
      setResendIn((s) => (s > 0 ? s - 1 : 0))
    }, 1000)
    return () => window.clearInterval(id)
  }, [phase])

  async function sendOtp() {
    setBusy(true); setMsg("")
    try {
      const r = await fetch("/api/auth/send-otp", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mobile, insurer_slug: "acme", purpose: "mobile_verification" }),
      })
      const d = await r.json()
      if (!d.success) { setMsg(d.message || "Could not send OTP"); return }
      setOtpRef(d.otp_ref_id); setDebugOtp(d.debug_otp ?? null); setPhase("otp"); setOtp("")
      setResendIn(30)  // 30s cooldown before resend is allowed
    } catch { setMsg("Network error. Is the API running on :8899?") }
    finally { setBusy(false) }
  }

  async function verifyOtp() {
    setBusy(true); setMsg("")
    onVerifyStart?.()   // show the loader IMMEDIATELY — covers verify + backend prefill wait
    try {
      const r = await fetch("/api/auth/verify-otp", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mobile, otp, otp_ref_id: otpRef, insurer_slug: "acme" }),
      })
      const d = await r.json()
      if (!d.success) { setMsg(d.message || "Invalid OTP"); onVerifyFail?.(); return }
      if (d.application_id != null) onVerified?.(d.application_id)
    } catch { setMsg("Network error"); onVerifyFail?.() }
    finally { setBusy(false) }
  }

  const Brand = ({ size = "sm" }: { size?: "sm" | "lg" }) => (
    <div className="flex items-center gap-2.5">
      <span className={`grid place-items-center size-9 rounded-lg ${v.iconWrap}`}>
        <ShieldCheck weight="fill" className="size-5" />
      </span>
      <span className={`${size === "lg" ? "text-lg" : "text-base"} font-extrabold tracking-tight`}>Acme Life Insurance</span>
    </div>
  )

  return (
    <div className={`min-h-[100dvh] flex flex-col xl:grid xl:grid-cols-2 ${variant === "light" ? "bg-[#fcfbf9]" : ""}`}>
      {/* MOBILE + TABLET brand strip (top). Split-screen only kicks in at xl (>=1280) —
          iPad Pro portrait is 1024px and can't hold two full columns, so tablet gets the
          stacked layout: brand strip up top, centered form below.
          Below xl we're a flex column so the strip is CONTENT-sized (no stretched empty
          block) and the form owns the remaining height. */}
      <div className={`xl:hidden shrink-0 px-6 pt-8 pb-7 ${v.strip}`}>
        <Brand />
        <h2 className="mt-4 text-2xl font-semibold leading-tight text-balance">
          Protection for the people who matter most.
        </h2>
      </div>

      {/* FORM. Right column on desktop (xl), single column below it.
          Below xl: sit right under the strip (justify-start) so there's no centered gap on a
          tall tablet. At xl: vertically centered in the side panel. */}
      <div className="xl:flex-1 xl:order-2 flex flex-col justify-start xl:justify-center px-6 pt-10 pb-10 sm:px-10 md:px-16 xl:px-20">
        <div className="w-full max-w-md mx-auto">
          <div className="hidden xl:block mb-9"><Brand /></div>

          <form onSubmit={(e) => e.preventDefault()}>
            <FieldGroup>
              <div className="flex flex-col gap-2">
                <h1 className="text-3xl xl:text-4xl font-bold tracking-tight">Let's protect your family</h1>
                <p className="text-muted-foreground text-[15px]">
                  {phase === "mobile"
                    ? "Enter the applicant's mobile number to begin. We'll send a one-time code."
                    : <>Enter the 6-digit code sent to <span className="font-medium text-foreground">+91 {mobile}</span></>}
                </p>
              </div>

              {phase === "mobile" ? (
                <>
                  <Field>
                    <FieldLabel htmlFor="mobile">Mobile number</FieldLabel>
                    <div className="flex items-stretch rounded-md border border-input overflow-hidden focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/50 transition-[color,box-shadow]">
                      <span className="grid place-items-center px-3 text-sm text-muted-foreground bg-muted border-r">+91</span>
                      <Input id="mobile" inputMode="numeric" placeholder="98765 43210" autoFocus
                        value={mobile} onChange={(e) => setMobile(e.target.value.replace(/\D/g, "").slice(0, 10))}
                        className="border-0 rounded-none shadow-none focus-visible:ring-0 h-11" />
                    </div>
                  </Field>
                  <Field orientation="horizontal" className="items-start">
                    <input id="consent" type="checkbox" checked={consentOk} readOnly
                      onChange={() => (consentOk ? setConsented(false) : setConsentOpen(true))}
                      className="mt-0.5 size-4 accent-primary shrink-0 cursor-pointer" />
                    <FieldLabel htmlFor="consent" onClick={(e) => { if (!consentOk) { e.preventDefault(); setConsentOpen(true) } }}
                      className="text-xs font-normal text-muted-foreground leading-snug cursor-pointer">
                      I agree to how my data is collected and used, under the DPDP Act, 2023.
                    </FieldLabel>
                  </Field>
                  <Field>
                    <Button type="button" size="lg" className="h-11" disabled={!mobileValid || !consentOk || busy} onClick={sendOtp}>
                      {busy ? "Sending" : <>Get OTP <ArrowRight className="size-4" weight="bold" /></>}
                    </Button>
                  </Field>
                </>
              ) : (
                <>
                  <Field>
                    <FieldLabel htmlFor="otp">One-time code</FieldLabel>
                    <Input id="otp" inputMode="numeric" placeholder="000000" autoFocus
                      value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
                      className="text-center tracking-[0.5em] text-lg h-12" />
                    {debugOtp && <FieldDescription>debug OTP: <span className="text-primary font-medium">{debugOtp}</span></FieldDescription>}
                  </Field>
                  <Field>
                    <Button type="button" size="lg" className="h-11"
                      disabled={otp.length !== 6 || busy} onClick={verifyOtp}>
                      {busy ? "Verifying" : <>Verify and continue <ArrowRight className="size-4" weight="bold" /></>}
                    </Button>
                    <div className="flex flex-col items-center gap-3 text-sm">
                      <div className="flex items-center gap-2 text-muted-foreground">
                        {resendIn > 0 ? (
                          <>
                            <CountdownRing seconds={resendIn} total={30} />
                            <span>Didn't get it? Resend in <span className="tabular-nums font-medium text-foreground">0:{String(resendIn).padStart(2, "0")}</span></span>
                          </>
                        ) : (
                          <span>
                            Didn't get the code?{" "}
                            <button type="button" disabled={busy} onClick={sendOtp}
                              className="text-primary font-semibold hover:underline underline-offset-2 disabled:opacity-50">
                              Resend code
                            </button>
                          </span>
                        )}
                      </div>
                      <button type="button"
                        onClick={() => { setPhase("mobile"); setOtp(""); setMsg(""); setResendIn(0) }}
                        className="flex items-center gap-1 text-muted-foreground hover:text-foreground">
                        <CaretLeft className="size-4" /> Change number
                      </button>
                    </div>
                  </Field>
                </>
              )}

              {msg && <FieldDescription>{msg}</FieldDescription>}
            </FieldGroup>
          </form>

          <p className="mt-10 text-xs text-muted-foreground flex items-center gap-3 xl:hidden">
            <span className="flex items-center gap-1.5"><LockKey className="size-3.5" /> Bank-grade encryption</span>
            <span className="h-3 w-px bg-border" /><span>IRDAI regulated</span>
          </p>
        </div>
      </div>

      {/* BRAND PANEL. Left column on desktop (xl+ only). Content vertically centered + anchored. */}
      <div className={`relative hidden xl:order-1 xl:flex flex-col overflow-hidden p-12 xl:p-16 ${v.panel}`}>
        {v.glow && <div className="pointer-events-none absolute inset-0" style={v.glow} aria-hidden="true" />}
        <PanelBackdrop tinted={variant !== "light"} />

        <Brand size="lg" />

        {/* centered block carries the weight; cards sit right under the copy */}
        <div className="relative flex-1 flex flex-col justify-center max-w-3xl">
          <h2 className="font-bold leading-[1.15] tracking-tight whitespace-nowrap"
            style={{ fontSize: "clamp(1.25rem, 2.1vw, 2rem)" }}>
            Protection for the people who matter most.
          </h2>
          <p className="mt-3 font-medium leading-relaxed opacity-90 whitespace-nowrap"
            style={{ fontSize: "clamp(0.75rem, 1.1vw, 1rem)" }}>
            A simple, guided way to secure your family's future and lasting peace of mind.
          </p>

          {/* Trust-factor cards: stat + meaning. One responsive row (stacks on small). */}
          <div className="mt-10 grid grid-cols-1 sm:grid-cols-3 gap-4 max-w-xl">
            {POINTS.map(([Icon, stat, sub], i) => (
              <div key={i}
                className={`rounded-xl p-4 ${
                  variant === "light"
                    ? "bg-white border border-black/[0.06] shadow-sm"
                    : "bg-white/[0.14] border border-white/15 backdrop-blur-sm"
                }`}>
                <span className={`grid place-items-center size-9 rounded-lg ${v.tile}`}><Icon className="size-5" weight="regular" /></span>
                <p className="mt-3 text-[19px] font-bold tracking-tight tabular-nums leading-none">{stat}</p>
                <p className="mt-1.5 text-[11.5px] opacity-70 leading-snug">{sub}</p>
              </div>
            ))}
          </div>
        </div>

        <div className={`relative flex items-center gap-4 text-xs ${v.foot}`}>
          <span className="flex items-center gap-1.5"><LockKey className="size-3.5" /> Bank-grade encryption</span>
          <span className={`h-3 w-px ${v.footRule}`} /><span className="flex items-center gap-1.5"><SealCheck className="size-3.5" /> IRDAI regulated</span>
        </div>
      </div>

      {consentOpen && (
        <ConsentModal
          purposes={purposes} setPurposes={setPurposes}
          onClose={() => setConsentOpen(false)}
          onAccept={() => { setConsented(true); setConsentOpen(false) }}
        />
      )}
    </div>
  )
}

// Small circular countdown for the resend cooldown: a faint track + a depleting brand-teal
// arc driven by strokeDashoffset. The 1s CSS transition tweens each tick so it reads as a
// live timer, not a jumping number. Purely decorative -> aria-hidden.
function CountdownRing({ seconds, total }: { seconds: number; total: number }) {
  const r = 8, c = 2 * Math.PI * r
  const frac = Math.max(0, Math.min(1, seconds / total))
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" className="-rotate-90 shrink-0" aria-hidden="true">
      <circle cx="10" cy="10" r={r} fill="none" stroke="currentColor" strokeWidth="2" className="text-border" />
      <circle cx="10" cy="10" r={r} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
        className="text-primary transition-[stroke-dashoffset] duration-1000 ease-linear"
        strokeDasharray={c} strokeDashoffset={c * (1 - frac)} />
    </svg>
  )
}

// Very-light background texture for the brand panel. Per placement best practice, background
// motifs belong bleeding OUT of a corner with no focal point sliced through the middle. So:
// a soft radial wash (top-left, behind the copy) + concentric rings anchored fully off the
// bottom-right corner (a quiet "ripple / protection" motif, no croppable icon). Decoration
// only: pointer-events-none + aria-hidden. Tints to white on dark/teal, teal on light.
function PanelBackdrop({ tinted }: { tinted: boolean }) {
  const ring = tinted ? "rgba(255,255,255,0.05)" : "rgba(14,124,134,0.055)"
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden="true">
      <div className="absolute inset-0"
        style={{ background: tinted
          ? "radial-gradient(60% 55% at 14% 10%, rgba(255,255,255,0.06), transparent 62%)"
          : "radial-gradient(60% 55% at 14% 10%, rgba(14,124,134,0.05), transparent 62%)" }} />
      {/* Concentric rings, centered on the bottom-right corner so only arcs bleed in. */}
      <svg className="absolute" style={{ right: "-14rem", bottom: "-14rem", width: "40rem", height: "40rem" }}
        viewBox="0 0 400 400" fill="none">
        {[70, 120, 170, 200].map((r) => (
          <circle key={r} cx="200" cy="200" r={r} stroke={ring} strokeWidth="1.5" />
        ))}
      </svg>
    </div>
  )
}

// DPDP Act, 2023 (Rule 3) consent notice: standalone, itemised per data category,
// purpose-mapped, per-purpose toggles, withdrawal + grievance line. Accept is gated on
// scroll-to-end and on every required purpose being on. Native overlay, no dialog dep.
function ConsentModal({ purposes, setPurposes, onClose, onAccept }: {
  purposes: Record<string, boolean>
  setPurposes: React.Dispatch<React.SetStateAction<Record<string, boolean>>>
  onClose: () => void
  onAccept: () => void
}) {
  const [scrolledEnd, setScrolledEnd] = useState(false)
  const bodyRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose()
    document.addEventListener("keydown", onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = "hidden"
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = prev }
  }, [onClose])

  // If the notice fits without scrolling, treat it as read.
  useEffect(() => {
    const el = bodyRef.current
    if (el && el.scrollHeight <= el.clientHeight + 4) setScrolledEnd(true)
  }, [])

  const requiredOk = PURPOSES.every((p) => !p.required || purposes[p.key])

  return (
    <div className="fixed inset-0 z-50 grid place-items-center p-4 bg-black/45 backdrop-blur-sm"
      role="dialog" aria-modal="true" aria-label="Consent notice" onClick={onClose}>
      <div className="w-full max-w-3xl max-h-[90dvh] flex flex-col rounded-2xl bg-card text-card-foreground border shadow-xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-4 px-7 pt-6 pb-5 border-b">
          <div>
            <h3 className="text-xl font-bold tracking-tight">How Acme Life uses your data</h3>
            <p className="mt-1.5 text-[13px] text-muted-foreground leading-relaxed max-w-prose">
              Consent notice under the Digital Personal Data Protection Act, 2023. Each item below is
              collected for the stated purpose only. Turn off anything optional; identity is required
              to issue a policy.
            </p>
          </div>
          <button onClick={onClose} aria-label="Close"
            className="shrink-0 grid place-items-center size-8 rounded-md text-muted-foreground hover:bg-muted transition-colors">
            <X className="size-4" weight="bold" />
          </button>
        </div>

        <div ref={bodyRef} onScroll={(e) => {
          const el = e.currentTarget
          if (el.scrollTop + el.clientHeight >= el.scrollHeight - 8) setScrolledEnd(true)
        }} className="flex-1 overflow-y-auto px-7 py-5 space-y-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">What we collect, and why</p>
          {PURPOSES.map((p) => {
            const on = purposes[p.key]
            const Icon = p.icon
            return (
              <div key={p.key} className="flex items-start gap-3.5 rounded-xl border p-4">
                <span className={`grid place-items-center size-10 shrink-0 rounded-lg ${on ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"}`}>
                  <Icon className="size-5" weight="regular" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-semibold">{p.label}</p>
                    {p.required && <span className="text-[10px] font-semibold uppercase tracking-wide text-primary bg-primary/10 rounded px-1.5 py-0.5">Required</span>}
                  </div>
                  <p className="mt-1 text-[12.5px] text-muted-foreground leading-snug">{p.purpose}</p>
                  <p className="mt-1.5 text-[11.5px] text-muted-foreground/80 leading-snug">
                    <span className="font-medium text-foreground/70">Enables:</span> {p.enables}
                  </p>
                </div>
                <button type="button" role="switch" aria-checked={on} aria-label={p.label}
                  disabled={p.required}
                  onClick={() => setPurposes((s) => ({ ...s, [p.key]: !s[p.key] }))}
                  className={`relative shrink-0 mt-1 h-5 w-9 rounded-full transition-colors ${on ? "bg-primary" : "bg-muted-foreground/30"} ${p.required ? "opacity-60 cursor-not-allowed" : "cursor-pointer"}`}>
                  <span className={`absolute top-0.5 size-4 rounded-full bg-white shadow transition-all ${on ? "left-[18px]" : "left-0.5"}`} />
                </button>
              </div>
            )
          })}

          <div className="grid sm:grid-cols-2 gap-3 pt-1">
            <div className="rounded-xl border p-4">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Your rights</p>
              <ul className="mt-2 space-y-1.5">
                {DATA_RIGHTS.map((r) => (
                  <li key={r} className="flex items-start gap-2 text-[12.5px] text-muted-foreground leading-snug">
                    <SealCheck className="size-3.5 mt-0.5 shrink-0 text-primary" weight="fill" /> {r}
                  </li>
                ))}
              </ul>
            </div>
            <div className="rounded-xl border p-4">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">How long we keep it</p>
              <p className="mt-2 text-[12.5px] text-muted-foreground leading-relaxed">
                Your data is retained only while your policy is active and for the period regulations
                require after that. Once the purpose is served or you withdraw consent, it is erased,
                unless a law requires us to keep it.
              </p>
            </div>
          </div>

          <p className="pt-1 text-[12px] text-muted-foreground leading-relaxed">
            Withdrawing consent is as easy as giving it, anytime from your profile. To exercise a right
            or raise a grievance, contact our Data Protection Officer at{" "}
            <span className="text-foreground">dpo@acmelife.com</span>. Unresolved concerns may be
            escalated to the Data Protection Board of India. Read the full{" "}
            <a href="/privacy" target="_blank" rel="noreferrer" className="text-primary font-medium underline underline-offset-2">privacy notice</a>.
          </p>
        </div>

        <div className="px-6 py-4 border-t">
          <Button type="button" className="w-full h-11" disabled={!scrolledEnd || !requiredOk} onClick={onAccept}>
            {scrolledEnd ? "I consent and continue" : "Scroll to review, then continue"}
          </Button>
        </div>
      </div>
    </div>
  )
}
