import { useEffect, useRef, useState } from "react"
import { useAppSnapshot, useRail } from "./useJourney"
import { StepSidebar } from "./StepSidebar"
import { TabletStepBar } from "./TabletStepBar"
import { SubStepStrip } from "./SubStepStrip"
import { MobileStepHeader } from "./MobileStepHeader"
import { IdentityCenter } from "./IdentityCenter"
import { ProductStep, type ProductState, type PremiumSummary } from "./ProductStep"
import { FinancialStep, type FinancialState } from "./FinancialStep"
import { HealthStep, type HealthState, emptyHealth, healthPayload, visibleHealthSubSteps } from "./HealthStep"
import { DecisionStep } from "./DecisionStep"
import { NomineeStep, type Nominee, emptyNominee, nomineePayload, shareTotal } from "./NomineeStep"
import { PaymentStep } from "./PaymentStep"
import { PremiumBar } from "./PremiumBar"
import { AgentRail } from "./AgentRail"
import { RailSheet } from "./RailSheet"
import { slugToStep, stepToPath } from "./steps"
import { ShieldCheck, Question } from "@phosphor-icons/react"

// The console shell drives all 7 journey steps. Each step declares its title, sub-steps,
// center panel, and a save() that POSTs to its endpoint before advancing. Continue advances
// + saves; the sidebar lets you jump back to a completed step. The rail re-polls per step.
type Variant = "teal" | "light"

const STEP_META: { title: string; desc: string; subSteps: string[]; cta: string }[] = [
  { title: "Identity & KYC", desc: "Confirm the applicant's identity, add an email, and verify Aadhaar.", subSteps: ["Profile", "Aadhaar", "Consent"], cta: "Continue to Product" },
  { title: "Product & Cover", desc: "Choose the plan, cover amount, policy term, and any riders.", subSteps: [], cta: "Continue to Financial" },
  { title: "Financial", desc: "Declared income and source of funds.", subSteps: [], cta: "Continue to Health" },
  { title: "Health", desc: "Health declaration, lifestyle, and a live face scan.", subSteps: ["Health", "Vitals & lifestyle", "Face scan & ABHA"], cta: "Continue to Decision" },
  { title: "Decision", desc: "The underwriting agent's verdict and full report.", subSteps: [], cta: "Continue to Nominee" },
  { title: "Nominee", desc: "Who receives the benefit.", subSteps: ["Nominee"], cta: "Continue to Payment" },
  { title: "Payment", desc: "Complete the premium payment to issue the policy.", subSteps: ["Payment"], cta: "Issue policy" },
]

// one-line summary of the chosen cover for the premium bar (research: re-unify the decision)
function coverSummary(p: ProductState): string {
  const sa = p.sum_assured
  const saLabel = sa >= 10_000_000 ? `₹${(sa / 10_000_000).toFixed(sa % 10_000_000 ? 1 : 0)} Cr`
    : sa >= 100_000 ? `₹${(sa / 100_000).toFixed(sa % 100_000 ? 1 : 0)} L` : `₹${sa.toLocaleString("en-IN")}`
  const parts = [saLabel, `${p.tenure_years} yr`]
  if (p.riders.length) parts.push(`${p.riders.length} rider${p.riders.length > 1 ? "s" : ""}`)
  return parts.join("  ·  ")
}

export function Console({ appId, variant }: { appId: number | null; variant: Variant }) {
  const { snap } = useAppSnapshot(appId)
  // Initial step: URL path (/demo/life/health) first, then ?start=N (demo/dev), else 1.
  // Either way it lands the console on that step and unlocks up to it.
  const startStep = slugToStep(window.location.pathname)
    || Math.min(7, Math.max(1, Number(new URLSearchParams(window.location.search).get("start")) || 1))
  const [step, setStepState] = useState(startStep)     // 1-indexed active step
  const [maxReached, setMaxReached] = useState(startStep)  // furthest step unlocked (for sidebar jump-back)

  // Change step AND the URL together, so each step is its own history entry (Back/Forward
  // walks the journey) and the path names the page. replace=true for the initial URL sync.
  const setStep = (n: number, replace = false) => {
    setStepState(n)
    const path = stepToPath(n)
    if (window.location.pathname !== path) {
      replace ? window.history.replaceState(null, "", path) : window.history.pushState(null, "", path)
    }
  }
  // Name the current step in the URL on first paint (covers ?start=N / a bare console boot).
  useEffect(() => { setStep(startStep, true) }, [])  // eslint-disable-line react-hooks/exhaustive-deps
  // Browser Back/Forward -> sync the view to the URL (only to an unlocked step).
  useEffect(() => {
    const onPop = () => { const n = slugToStep(window.location.pathname); if (n && n <= maxReached) setStepState(n) }
    window.addEventListener("popstate", onPop)
    return () => window.removeEventListener("popstate", onPop)
  }, [maxReached])
  const [saving, setSaving] = useState(false)
  const rail = useRail(appId, step)
  void variant

  // Step 2 product state (lifted so it survives step navigation + drives the save)
  const [product, setProduct] = useState<ProductState>({ plan: "term_protect", sum_assured: 10_000_000, tenure_years: 20, riders: [] })
  const [premium, setPremium] = useState<PremiumSummary | null>(null)

  // Step 3 financial state (lifted; pre-filled once from the snapshot on first load / revisit)
  const [financial, setFinancial] = useState<FinancialState>({ declared_annual_income: 0, source_of_funds: "", purpose_of_cover: "" })
  // Step 4 health state (lifted so it survives navigation; defaults are all "No" / empty)
  const [health, setHealth] = useState<HealthState>(emptyHealth)
  // Step 4 is paginated over its visible sub-steps (Screeners/Conditions/Vitals/Face scan);
  // the footer walks these before advancing the whole step. maxHealthSub = furthest visited.
  const [healthSub, setHealthSub] = useState(0)
  const [maxHealthSub, setMaxHealthSub] = useState(0)
  // Step 6 nominee state (lifted; survives navigation). Multiple nominees + share split.
  const [nominees, setNominees] = useState<Nominee[]>([emptyNominee()])
  const [stepMsg, setStepMsg] = useState("")  // inline save error (currently step 6)
  const healthSubs = visibleHealthSubSteps(health)
  const onLastHealthSub = step !== 4 || healthSub >= healthSubs.length - 1
  const prefilled = useRef(false)
  useEffect(() => {
    if (prefilled.current || !snap?.financial) return
    const f = snap.financial
    if (f.declared_annual_income || f.source_of_funds || f.purpose_of_cover) {
      setFinancial({
        declared_annual_income: f.declared_annual_income || 0,
        source_of_funds: f.source_of_funds || "",
        purpose_of_cover: f.purpose_of_cover || "",
      })
    }
    prefilled.current = true
  }, [snap])

  // No snapshot yet -> render the REAL shell in a loading state (same header/sidebar/grid/
  // rail as below, gray placeholders in the center). Identical components + grid classes =>
  // zero layout shift when data lands. Also used by TransitionLoader (App shows it pre-appId).
  if (!snap) return <ConsoleShell loading />

  const meta = STEP_META[step - 1]

  // Per-step save: POST to the step's endpoint, return ok. Steps not yet built just pass through.
  async function saveStep(): Promise<boolean> {
    if (appId == null) return true
    try {
      if (step === 2) {
        const r = await fetch("/api/journey/product", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ app_id: appId, product_type: "term_life", ...product }),
        })
        return (await r.json()).success !== false
      }
      if (step === 3) {
        const r = await fetch("/api/journey/financial", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ app_id: appId, ...financial }),
        })
        return (await r.json()).success !== false
      }
      if (step === 4) {
        const r = await fetch("/api/journey/health", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ app_id: appId, ...healthPayload(health) }),
        })
        return (await r.json()).success !== false
      }
      if (step === 6) {
        const named = nominees.filter((n) => n.name.trim())
        if (!named.length) { setStepMsg("Add at least one nominee (a name is required)."); return false }
        if (named.length > 1 && shareTotal(named) !== 100) {
          setStepMsg(`Nominee shares must total 100% (currently ${shareTotal(named)}%).`); return false
        }
        const r = await fetch("/api/journey/nominee", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ app_id: appId, ...nomineePayload(named) }),
        })
        const d = await r.json()
        if (d.success === false) { setStepMsg(d.message || "Could not save nominee."); return false }
        setStepMsg("")
        return true
      }
      // step 1 has no explicit save (identity is fetched); later steps wire their own here
      return true
    } catch { return false }
  }

  const advanceStep = (from: number) => {
    const next = Math.min(7, from + 1)
    setStep(next)
    setStepMsg("")
    setMaxReached((m) => Math.max(m, next))
  }

  async function onContinue() {
    // Step 4 paginates its sub-steps; Continue walks them, saving only on the last one.
    if (step === 4 && !onLastHealthSub) {
      const ns = healthSub + 1
      setHealthSub(ns)
      setMaxHealthSub((m) => Math.max(m, ns))
      return
    }
    setSaving(true)
    const ok = await saveStep()
    setSaving(false)
    if (!ok) return
    advanceStep(step)
  }

  function onBack() {
    if (step === 4 && healthSub > 0) { setHealthSub((s) => s - 1); return }
    goTo(step - 1)
  }

  // Jump to a step (only if unlocked). Entering step 4 starts at its first sub-step.
  const goTo = (n: number) => {
    if (n > maxReached) return
    setStep(n)
    setStepMsg("")
    if (n === 4) { setHealthSub(0); setMaxHealthSub((m) => Math.max(m, 0)) }
  }

  // Jump within step 4's sub-steps (strip click) — only to a visited sub-step.
  const goToHealthSub = (i: number) => { if (i <= maxHealthSub) setHealthSub(i) }

  return (
    <div className="min-h-[100dvh] flex flex-col bg-background">
      {/* HEADER — full width, sticky */}
      <header className="flex items-center gap-3 px-6 lg:px-8 h-14 bg-card border-b shrink-0 sticky top-0 z-30">
        <span className="grid place-items-center size-7 rounded-lg bg-primary text-primary-foreground">
          <ShieldCheck weight="fill" className="size-4" />
        </span>
        <span className="text-[15px] font-extrabold tracking-tight">Acme Life Insurance</span>
        <span className="ml-auto flex items-center gap-3">
          {snap.seeded && <span className="rounded-full bg-amber-100 text-amber-700 px-2 py-0.5 text-[11px] font-semibold">demo data</span>}
          <span className="hidden sm:flex items-center gap-2 rounded-full px-2.5 py-1 bg-muted">
            <span className="grid place-items-center size-5 rounded-full bg-primary/15 text-primary text-[10px] font-bold">
              {(snap.applicant.name || "?").trim().charAt(0).toUpperCase()}
            </span>
            <span className="text-[12px] font-semibold max-w-[150px] truncate">{snap.applicant.name || "New applicant"}</span>
            <span className="font-mono text-[11px] text-muted-foreground">{snap.application_number}</span>
          </span>
          <button className="text-muted-foreground hover:text-foreground"><Question className="size-5" /></button>
        </span>
      </header>

      {/* MOBILE (<md) step header  &  TABLET (md..lg) top step bar */}
      <MobileStepHeader step={step} title={meta.title} description={meta.desc}
        subSteps={step === 4 ? healthSubs.map((s) => s.label) : meta.subSteps}
        subActive={step === 4 ? Math.min(healthSub, healthSubs.length - 1) : 0} />
      <TabletStepBar active={step} />

      {/* CANVAS */}
      <div className="flex-1 flex min-h-0">
        <StepSidebar active={step} maxReached={maxReached} onJump={goTo}
          appId={snap.application_number} startedAt="Today" />

        <div className="flex-1 min-w-0 grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_340px] xl:grid-cols-[minmax(0,1fr)_400px] gap-5 p-4 sm:p-5 lg:pl-0 pb-24 md:pb-5">
          <main className="min-w-0">
            <div className="rounded-2xl elev-card overflow-hidden">
              <div className="hidden md:block px-6 lg:px-8 pt-5">
                <h1 className="text-xl font-bold tracking-tight">{meta.title}</h1>
                <p className="text-sm text-muted-foreground mt-0.5">{meta.desc}</p>
                {step === 4
                  ? <div className="mt-4 border-b"><SubStepStrip steps={healthSubs.map((s) => s.label)} active={Math.min(healthSub, healthSubs.length - 1)} onJump={goToHealthSub} maxReached={maxHealthSub} /></div>
                  : meta.subSteps.length > 1
                  ? <div className="mt-4 border-b"><SubStepStrip steps={meta.subSteps} active={0} /></div>
                  : <div className="mt-5 border-b" />}
              </div>
              <div className="px-4 sm:px-6 lg:px-8 py-6">
                {step === 1 && <IdentityCenter snap={snap} appId={appId} />}
                {step === 2 && <ProductStep appId={appId} snap={snap} value={product} onChange={setProduct} onPremium={setPremium} />}
                {step === 3 && <FinancialStep appId={appId} snap={snap} value={financial} onChange={setFinancial} />}
                {step === 4 && <HealthStep appId={appId} snap={snap} value={health} onChange={setHealth} subStep={healthSub} />}
                {step === 5 && <DecisionStep appId={appId} />}
                {step === 6 && <NomineeStep value={nominees} onChange={(n) => { setNominees(n); setStepMsg("") }} />}
                {step === 7 && <PaymentStep appId={appId} snap={snap} premium={premium} />}
              </div>
              {/* shared footer */}
              <div className="border-t px-4 sm:px-6 lg:px-8 py-3.5 flex items-center justify-between gap-3">
                <button onClick={onBack} disabled={step === 1}
                  className="text-sm text-muted-foreground hover:text-foreground disabled:opacity-40">‹ Back</button>
                <div className="flex items-center gap-3">
                  {stepMsg && <span className="text-[12px] text-red-600 font-medium max-w-[22rem] text-right">{stepMsg}</span>}
                  {/* Step 7's primary action is the Pay button inside the step; no footer Continue. */}
                  {step !== 7 && (
                    <button onClick={onContinue} disabled={saving}
                      className="rounded-md bg-primary text-primary-foreground text-sm font-medium px-5 h-10 hover:bg-primary/90 transition-colors disabled:opacity-60">
                      {saving ? "Saving…" : `${onLastHealthSub ? meta.cta : "Continue"} ›`}
                    </button>
                  )}
                </div>
              </div>
            </div>

            {/* Step-2 premium bar — OUTSIDE the card (so card overflow-hidden doesn't clip
                sticky), sticks to the bottom of the center column as you scroll. */}
            {step === 2 && <PremiumBar premium={premium} subtitle={coverSummary(product)} />}
          </main>

          <AgentRail rail={rail} className="hidden md:block" />
        </div>
      </div>

      <RailSheet rail={rail} />
    </div>
  )
}

// Loading shell — the SAME header / StepSidebar / center grid / AgentRail frame as the real
// Console, with gray placeholder bars where the step content will land. Because it reuses the
// exact components + grid classes, swapping to the real page causes ZERO layout shift.
const SkBar = ({ w, h = "h-3" }: { w: string; h?: string }) =>
  <div className={`${h} ${w} rounded-md bg-black/[0.06] animate-pulse`} />

export function ConsoleShell({ loading }: { loading?: boolean }) {
  void loading
  return (
    <div className="min-h-[100dvh] flex flex-col bg-background">
      {/* HEADER — identical structure/height to the real one. */}
      <header className="flex items-center gap-3 px-6 lg:px-8 h-14 bg-card border-b shrink-0 sticky top-0 z-30">
        <span className="grid place-items-center size-7 rounded-lg bg-primary text-primary-foreground">
          <ShieldCheck weight="fill" className="size-4" />
        </span>
        <span className="text-[15px] font-extrabold tracking-tight">Acme Life Insurance</span>
        <span className="ml-auto text-xs text-muted-foreground animate-pulse">Preparing your application…</span>
      </header>

      <div className="flex-1 flex min-h-0">
        {/* SIDEBAR skeleton — same width/position as StepSidebar, gray rows (no labels). */}
        <nav className="hidden lg:block w-80 xl:w-96 shrink-0 p-5 self-start sticky top-14 space-y-4">
          <div className="rounded-2xl elev-card p-4 space-y-5">
            {Array.from({ length: 7 }).map((_, i) => (
              <div key={i} className="flex gap-3">
                <div className="size-7 rounded-full bg-black/[0.06] animate-pulse shrink-0" />
                <div className="flex-1 space-y-1.5 pt-0.5"><SkBar w="w-12" h="h-2" /><SkBar w="w-28" /></div>
              </div>
            ))}
          </div>
        </nav>

        <div className="flex-1 min-w-0 grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_340px] xl:grid-cols-[minmax(0,1fr)_400px] gap-5 p-4 sm:p-5 lg:pl-0 pb-24 md:pb-5">
          <main className="min-w-0">
            <div className="rounded-2xl elev-card overflow-hidden">
              <div className="hidden md:block px-6 lg:px-8 pt-5">
                <SkBar w="w-44" h="h-5" />
                <div className="mt-2"><SkBar w="w-64" h="h-3" /></div>
                <div className="mt-5 border-b" />
              </div>
              <div className="px-4 sm:px-6 lg:px-8 py-6 space-y-5">
                <div className="rounded-xl bg-[#faf9f7] p-4 space-y-3">
                  <SkBar w="w-24" />
                  <SkBar w="w-52" h="h-6" />
                  <div className="grid grid-cols-3 gap-2.5">
                    {Array.from({ length: 3 }).map((_, i) => <div key={i} className="h-12 rounded-lg bg-black/[0.04] animate-pulse" />)}
                  </div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div className="h-32 rounded-xl bg-[#faf9f7] animate-pulse" />
                  <div className="h-32 rounded-xl bg-[#faf9f7] animate-pulse" />
                </div>
              </div>
              <div className="border-t px-4 sm:px-6 lg:px-8 py-3.5 flex items-center justify-between">
                <SkBar w="w-12" h="h-4" />
                <div className="h-10 w-40 rounded-md bg-black/[0.06] animate-pulse" />
              </div>
            </div>
          </main>

          {/* RAIL skeleton — same card frame as AgentRail, gray placeholders (no labels). */}
          <aside className="hidden md:block shrink-0 self-start sticky top-14">
            <div className="rounded-2xl border bg-white shadow-[0_1px_2px_rgba(0,0,0,0.04)] p-5 space-y-4">
              <div className="flex items-center gap-4">
                <div className="size-[72px] rounded-full bg-black/[0.06] animate-pulse shrink-0" />
                <div className="flex-1 space-y-2"><SkBar w="w-20" h="h-2" /><SkBar w="w-28" h="h-4" /></div>
              </div>
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="space-y-1.5 pt-1"><SkBar w="w-32" /><SkBar w="w-full" h="h-2" /></div>
              ))}
            </div>
          </aside>
        </div>
      </div>
    </div>
  )
}
