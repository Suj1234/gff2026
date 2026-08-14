import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { ShieldCheck, HandHeart, UsersThree, SealCheck, LockKey, ArrowRight, CaretLeft } from "@phosphor-icons/react"

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
    panel: "bg-[#f7f6f3] text-foreground border-r",
    strip: "bg-[#f7f6f3] text-foreground border-b",
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

const POINTS: [React.ElementType, string, string][] = [
  [ShieldCheck, "Financial security", "For the people you love"],
  [HandHeart, "Guided journey", "Simple, step by step"],
  [UsersThree, "Family-first", "Built around your needs"],
]

export function LoginForm({ variant = "teal", onVerified, onVerifyStart, onVerifyFail }:
  { variant?: Variant; onVerified?: (appId: number) => void; onVerifyStart?: () => void; onVerifyFail?: () => void }) {
  const [phase, setPhase] = useState<"mobile" | "otp">("mobile")
  const [mobile, setMobile] = useState("")
  const [otp, setOtp] = useState("")
  const [consent, setConsent] = useState(false)
  const [otpRef, setOtpRef] = useState("")
  const [debugOtp, setDebugOtp] = useState<string | null>(null)
  const [msg, setMsg] = useState("")
  const [busy, setBusy] = useState(false)
  const v = PANEL[variant]

  const mobileValid = /^[6-9]\d{9}$/.test(mobile)

  async function sendOtp() {
    setBusy(true); setMsg("")
    try {
      const r = await fetch("/api/auth/send-otp", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mobile, insurer_slug: "acme", purpose: "mobile_verification" }),
      })
      const d = await r.json()
      if (!d.success) { setMsg(d.message || "Could not send OTP"); return }
      setOtpRef(d.otp_ref_id); setDebugOtp(d.debug_otp ?? null); setPhase("otp")
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
                <h1 className="text-3xl xl:text-4xl font-bold tracking-tight">Start a new application</h1>
                <p className="text-muted-foreground text-[15px] text-balance">
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
                    <input id="consent" type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)}
                      className="mt-0.5 size-4 accent-primary shrink-0" />
                    <FieldLabel htmlFor="consent" className="text-xs font-normal text-muted-foreground leading-snug">
                      The applicant consents (DPDP Act) to their personal data being used to process this application.
                    </FieldLabel>
                  </Field>
                  <Field>
                    <Button type="button" size="lg" className="h-11" disabled={!mobileValid || !consent || busy} onClick={sendOtp}>
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
                    <Button type="button" size="lg" className="h-11" disabled={otp.length !== 6 || busy} onClick={verifyOtp}>
                      {busy ? "Verifying" : <>Verify and continue <ArrowRight className="size-4" weight="bold" /></>}
                    </Button>
                    <Button type="button" variant="ghost" size="sm" className="justify-self-center"
                      onClick={() => { setPhase("mobile"); setOtp(""); setMsg("") }}>
                      <CaretLeft className="size-4" /> Change number
                    </Button>
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

          {/* Three EVEN cards, one responsive row (3 -> stacks on small). Stronger contrast. */}
          <div className="mt-10 grid grid-cols-1 sm:grid-cols-3 gap-4 max-w-xl">
            {POINTS.map(([Icon, title, sub], i) => (
              <div key={i}
                className={`rounded-xl p-4 ${
                  variant === "light"
                    ? "bg-white border border-black/[0.08] shadow-sm"
                    : "bg-white/[0.14] border border-white/15 backdrop-blur-sm"
                }`}>
                <span className={`grid place-items-center size-9 rounded-lg ${v.tile}`}><Icon className="size-5" weight="regular" /></span>
                <p className="mt-3 text-[13px] font-semibold leading-snug">{title}</p>
                <p className="mt-1 text-[11px] opacity-75 leading-snug">{sub}</p>
              </div>
            ))}
          </div>
        </div>

        <div className={`relative flex items-center gap-4 text-xs ${v.foot}`}>
          <span className="flex items-center gap-1.5"><LockKey className="size-3.5" /> Bank-grade encryption</span>
          <span className={`h-3 w-px ${v.footRule}`} /><span className="flex items-center gap-1.5"><SealCheck className="size-3.5" /> IRDAI regulated</span>
        </div>
      </div>
    </div>
  )
}
