import type { AppSnapshot } from "./useJourney"
import { SealCheck, Warning, Buildings, EnvelopeSimple, FingerprintSimple, ShieldCheck } from "@phosphor-icons/react"

// Center panel for Step 1. Editability-spectrum layout (per the reference):
//   HERO card   = core identity, PAN/Aadhaar-verified. Heaviest weight, NOT editable.
//   Flat rows   = fetched read-only facts (Employment). No box -> "confirm only".
//   Input box   = the ONE thing the user provides (email). Box -> "editable".
// Rule: only editable things get a box. Read-only facts are boxless.

function Tag({ tone, children }: { tone: "ok" | "warn" | "bad"; children: React.ReactNode }) {
  const cls = tone === "ok" ? "stat-ok" : tone === "warn" ? "stat-warn" : "stat-bad"
  return <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${cls}`}>{children}</span>
}

function SectionHead({ icon: Icon, title, children }: { icon: React.ElementType; title: string; children?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <span className="grid place-items-center size-6 rounded-md bg-primary/10 text-primary shrink-0">
        <Icon weight="regular" className="size-3.5" />
      </span>
      <h2 className="text-sm font-semibold">{title}</h2>
      {children}
    </div>
  )
}

// Flat read-only row: label left, value right, no box. For fetched facts (Employment, Aadhaar).
function FactRow({ k, v, mono }: { k: string; v?: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2.5 border-b border-black/[0.05] last:border-0">
      <span className="text-[13px] text-muted-foreground shrink-0">{k}</span>
      <span className={`text-[13.5px] font-semibold text-foreground text-right ${mono ? "font-mono tracking-tight" : ""}`}>{v || "—"}</span>
    </div>
  )
}

// The core-identity HERO. Premium = restraint + space + typographic confidence.
// Clean warm-white surface, NO decoration (no tint, no gradient, no colored edge bar).
// The name is large and dominant; everything else is visibly quieter. Generous padding,
// hairline dividers, mono for the ID-like PAN. This is the Stripe/Mercury/Amex read.
function IdentityHero({ snap }: { snap: AppSnapshot }) {
  const a = snap.applicant
  const pan = snap.signals.pan_verify || {}
  const ported = snap.signals.mobile_intel?.ported_recently
  const verified = pan.pan_status === "valid"

  // Light hero that BELONGS to the page: same warm tone, a soft shadow groups it — it does
  // not shout, it's just a clean grouped identity block. Premium via coherence, not contrast.
  const HeroFact = ({ k, v, mono, flag }: { k: string; v?: string; mono?: boolean; flag?: string }) => (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-[0.09em] font-medium text-muted-foreground/70">{k}</div>
      <div className="mt-1 flex items-center gap-2">
        <span className={`text-[14px] font-semibold text-foreground truncate ${mono ? "font-mono tracking-tight" : ""}`}>{v || "—"}</span>
        {flag && <span className="rounded-full stat-warn border px-1.5 py-0.5 text-[9px] font-bold shrink-0">{flag}</span>}
      </div>
    </div>
  )

  const HeroFactWide = ({ k, v }: { k: string; v?: string }) => (
    <div>
      <div className="text-[10px] uppercase tracking-[0.09em] font-medium text-muted-foreground/70">{k}</div>
      <div className="mt-1 text-[14px] font-semibold text-foreground leading-snug">{v || "—"}</div>
    </div>
  )

  // HERO only: pure-white surface + deeper warm shadow so it lifts MORE than the other
  // cards. Scoped here — does not touch the shared .elev-card token.
  return (
    <section className="rounded-2xl bg-white px-6 py-5 border border-[oklch(0.88_0.004_90)] shadow-[0_1px_2px_rgba(42,41,36,0.06),0_10px_28px_-8px_rgba(42,41,36,0.14)]">
      {/* header row: source label + name on the left, verified anchored right */}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <span className="text-[10.5px] uppercase tracking-[0.1em] font-semibold text-muted-foreground/70">Based on PAN &amp; Mobile</span>
          <div className="mt-1.5 text-[26px] font-bold tracking-[-0.02em] leading-[1.1] text-foreground truncate">{a.name || "New applicant"}</div>
          <div className="mt-1 text-[13px] text-muted-foreground">
            {[a.gender, a.dob].filter(Boolean).join("  ·  ") || "—"}
          </div>
        </div>
        {verified
          ? <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-primary shrink-0 mt-0.5"><SealCheck weight="fill" className="size-4" /> Verified</span>
          : <span className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-amber-700 shrink-0 mt-0.5"><Warning weight="fill" className="size-4" /> Not verified</span>}
      </div>

      {/* facts — 3 short facts fill the width, address gets its own full-width line (no truncation) */}
      <div className="mt-5 pt-4 border-t border-black/[0.06] space-y-4">
        <div className="grid grid-cols-3 gap-x-6">
          <HeroFact k="PAN" v={pan.pan} mono />
          <HeroFact k="Pincode" v={a.pincode} mono />
          <HeroFact k="Mobile" v={a.mobile ? `+91 ${a.mobile}` : undefined} mono flag={ported ? "Ported" : undefined} />
        </div>
        <div><HeroFactWide k="Address" v={a.address} /></div>
      </div>
    </section>
  )
}

export function IdentityCenter({ snap, appId }: { snap: AppSnapshot; appId: number | null }) {
  const a = snap.applicant
  const epfo = snap.signals.epfo || {}
  const email = snap.signals.email_intel?.email
  const aadhaar = snap.signals.aadhaar_ekyc || {}
  const aadhaarDone = aadhaar.status === "available"

  return (
    <div className="space-y-6">
      {/* HERO — core verified identity */}
      <IdentityHero snap={snap} />

      {/* Fetched read-only groups: Employment | Aadhaar. Flat rows, no field boxes. */}
      <div className="grid lg:grid-cols-2 gap-6">
        <section>
          <SectionHead icon={Buildings} title="Employment">
            {(epfo.employer || epfo.uan)
              ? <Tag tone="ok"><SealCheck className="size-3" weight="fill" /> EPFO verified</Tag>
              : <Tag tone="warn">Awaiting EPFO</Tag>}
          </SectionHead>
          <div className="rounded-xl bg-[#faf9f7] px-4 py-1">
            <FactRow k="Employer" v={epfo.employer} />
            <FactRow k="Type" v={epfo.employment_type} />
            <FactRow k="UAN" v={epfo.uan} mono />
          </div>
        </section>

        <section>
          <SectionHead icon={FingerprintSimple} title="Aadhaar e-KYC (DigiLocker)">
            {aadhaarDone && <Tag tone="ok"><SealCheck className="size-3" weight="fill" /> Verified</Tag>}
          </SectionHead>
          {aadhaarDone ? (
            <div className="rounded-xl bg-[#faf9f7] px-4 py-1">
              <FactRow k="Name" v={aadhaar.name || a.name} />
              <FactRow k="Date of birth" v={aadhaar.dob || a.dob} />
              <FactRow k="Address" v={aadhaar.address ? "Matches records" : undefined} />
              <div className="py-2.5 flex items-center gap-1.5 text-[12px] text-muted-foreground">
                <ShieldCheck weight="fill" className="size-3.5 text-primary" /> No Aadhaar number is stored
              </div>
            </div>
          ) : (
            <div className="rounded-xl bg-[#faf9f7] p-4 flex flex-col justify-between gap-3 min-h-[128px]">
              <p className="text-sm text-muted-foreground">
                Fetches verified name, DOB and address from Aadhaar. No Aadhaar number is stored.
              </p>
              <button
                onClick={() => { if (appId != null) window.location.href = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/api/journey/digilocker/start/${appId}` }}
                disabled={appId == null}
                className="rounded-md bg-primary text-primary-foreground text-sm font-medium px-4 h-10 hover:bg-primary/90 transition-colors self-start disabled:opacity-50">
                Verify via DigiLocker
              </button>
            </div>
          )}
        </section>
      </div>

      {/* The ONE user-entered field — boxed input signals "editable" */}
      <section>
        <SectionHead icon={EnvelopeSimple} title="Your email">
          <span className="text-xs text-muted-foreground">fraud &amp; contactability check</span>
        </SectionHead>
        <div className="max-w-md">
          <input type="email" defaultValue={email} placeholder="applicant@email.com"
            className="w-full rounded-md border-2 bg-white px-3.5 h-11 text-sm outline-none focus:border-ring focus:ring-[3px] focus:ring-ring/30 transition-[color,box-shadow]" />
          <p className="mt-1.5 text-xs text-muted-foreground">We use this to reach the applicant. Saved when you continue.</p>
        </div>
      </section>
    </div>
  )
}
