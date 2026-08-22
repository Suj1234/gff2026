import { useEffect, useRef, useState } from "react"
import { useAppSnapshot, useRail } from "./useJourney"
import { StepSidebar } from "./StepSidebar"
import { TabletStepBar } from "./TabletStepBar"
import { SubStepStrip } from "./SubStepStrip"
import { MobileStepHeader } from "./MobileStepHeader"
import { IdentityCenter } from "./IdentityCenter"
import { ProductStep, type ProductState, type PremiumSummary } from "./ProductStep"
import { FinancialStep, type FinancialState } from "./FinancialStep"
import { HealthStep, type HealthState, emptyHealth, healthPayload, healthFromPayload, visibleHealthSubSteps } from "./HealthStep"
import { DecisionStep } from "./DecisionStep"
import { NomineeStep, type Nominee, emptyNominee, nomineePayload, shareTotal } from "./NomineeStep"
import { PaymentStep } from "./PaymentStep"
import { PremiumBar } from "./PremiumBar"
import { AgentRail } from "./AgentRail"
import { RailSheet } from "./RailSheet"
import { slugToStep, stepToPath } from "./steps"
import { ShieldCheck } from "@phosphor-icons/react"

// The console shell drives all 7 journey steps. Each step declares its title, sub-steps,
// center panel, and a save() that POSTs to its endpoint before advancing. Continue advances
// + saves; the sidebar lets you jump back to a completed step. The rail re-polls per step.
type Variant = "teal" | "light"

const STEP_META: { title: string; desc: string; subSteps: string[]; cta: string }[] = [
  { title: "Identity & KYC", desc: "Confirm the applicant's identity, add an email, and verify Aadhaar.", subSteps: [], cta: "Continue to Product" },
  { title: "Product & Cover", desc: "Choose the plan, cover amount, policy term, and any riders.", subSteps: [], cta: "Continue to Financial" },
  { title: "Financial", desc: "Declared income and source of funds.", subSteps: [], cta: "Continue to Health" },
  { title: "Health", desc: "Health declaration, lifestyle, and a live face scan.", subSteps: ["Face scan & ABHA", "Follow-up questions", "Health", "Vitals & lifestyle"], cta: "Continue to Decision" },
  { title: "Decision", desc: "The underwriting agent's verdict and full report.", subSteps: [], cta: "Continue to Nominee" },
  { title: "Nominee", desc: "Who receives the benefit.", subSteps: ["Nominee"], cta: "Continue to Payment" },
  { title: "Payment", desc: "Complete the premium payment to issue the policy.", subSteps: ["Payment"], cta: "Issue policy" },
]

// created_at is UTC (from the DB). Render exact date + time in IST (Asia/Kolkata handles the
// +5:30 conversion natively). Falls back to "Today" if the timestamp isn't present yet.
function istDateTime(iso?: string): string {
  if (!iso) return "Today"
  const d = new Date(iso)
  if (isNaN(d.getTime())) return "Today"
  return d.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit", hour12: true,
    // en-IN emits lowercase am/pm; uppercase the meridiem so it reads "09:44 AM IST".
  }).replace(/\b(am|pm)\b/i, (m) => m.toUpperCase()) + " IST"
}

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
  const { snap, reload } = useAppSnapshot(appId)
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
  void variant

  // Step 2 product state (lifted so it survives step navigation + drives the save)
  const [product, setProduct] = useState<ProductState>({ plan: "term_protect", sum_assured: 10_000_000, tenure_years: 20, riders: [] })
  // Step 4 sub-step index (declared here so the rail can scope its chips to the active
  // health sub-step, the way Steps 1–3 scope theirs). 0 Health · 1 Vitals · 2 Face/ABHA.
  const [healthSub, setHealthSub] = useState(0)
  // On Step 2, feed the live-selected SI to the rail so the Cover/R-006 chip reacts to
  // cover toggles before Continue persists them. 0 on other steps (rail uses the saved SI).
  const rail = useRail(appId, step, step === 2 ? product.sum_assured : 0, healthSub)
  const [premium, setPremium] = useState<PremiumSummary | null>(null)

  // Step 1 email (lifted so Continue can save it -> triggers the email-intel fraud API)
  const [email, setEmail] = useState("")
  // Step 3 financial state (lifted; pre-filled once from the snapshot on first load / revisit)
  const [financial, setFinancial] = useState<FinancialState>({ declared_annual_income: 0, source_of_funds: "", purpose_of_cover: "" })
  // Step 4 health state (lifted so it survives navigation; defaults are all "No" / empty)
  const [health, setHealth] = useState<HealthState>(emptyHealth)
  // Step 4 is paginated over its visible sub-steps (Health/Vitals/Face scan); the footer
  // walks these before advancing the whole step. maxHealthSub = furthest visited.
  // (healthSub itself is declared above, near useRail, so the rail can read it.)
  const [maxHealthSub, setMaxHealthSub] = useState(0)
  // The conversational deep-dive sub-step (HEALTH_AGENT_PLAN.md §7) gates Continue until
  // the agent's own turn-cap/completion decides it's done, or the applicant explicitly
  // skips — set true immediately if the sub-step isn't even reached (nothing to gate on).
  const [healthChatDone, setHealthChatDone] = useState(false)
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
    const e = snap.signals?.email_intel?.email
    if (e) setEmail(e)
    // Step 2 revisit: rehydrate the saved product/cover choice, else a page reload (or a
    // deep link straight to a later step) silently reverts it to the hardcoded default.
    const p = snap.product
    if (p && (p.plan || p.sum_assured || p.tenure_years)) {
      setProduct({
        plan: p.plan || "term_protect", sum_assured: p.sum_assured || 10_000_000,
        tenure_years: p.tenure_years || 20, riders: p.riders || [],
      })
    }
    // Step 4 revisit: rehydrate the health form from what was already saved, so the form
    // shows the conditions the applicant declared (and the rail + form agree). Editing then
    // overwrites it normally. Only when there IS saved health data.
    const hd = snap.health_declaration
    if (hd && Object.keys(hd).length) setHealth(healthFromPayload(hd as any))
    // Revisit: if every flagged thread already finished (or nothing was ever flagged),
    // Continue on "healthchat" shouldn't re-block — HealthChatPanel itself also detects
    // this on mount, but that happens a render later, so set it here too to avoid a
    // one-frame flash of a disabled Continue button.
    const ha = snap.health_agent
    if (ha && (!ha.flagged?.length || ha.flagged.every((f) => ha.threads?.[f.bucket]?.done))) {
      setHealthChatDone(true)
    }
    // Step 6 revisit: rehydrate saved nominees, else a page reload (or a deep link straight
    // to Payment) silently reverts the form to blank and Continue would overwrite them.
    if (snap.nominees?.length) {
      setNominees(snap.nominees.map((n) => ({
        name: n.name || "", dob: n.dob || "", relationship: n.relationship || "",
        share_pct: n.share_pct ?? 100, address: n.address || "",
        appointee_name: n.appointee?.name || "", appointee_dob: n.appointee?.dob || "",
        appointee_relationship: n.appointee?.relationship || "",
      })))
    }
    prefilled.current = true
  }, [snap])

  // Live-save the health declaration as the user edits it on Step 4, so the agent rail
  // reacts in real time (toggle a condition → Medical score moves) instead of only on
  // Continue. Debounced + gated to Step 4 + after prefill (so hydration doesn't re-save).
  // /health accepts partials and only writes set fields.
  useEffect(() => {
    if (step !== 4 || appId == null || !prefilled.current) return
    const t = setTimeout(() => {
      fetch("/api/journey/health", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ app_id: appId, ...healthPayload(health) }),
      }).catch(() => {})
    }, 400)
    return () => clearTimeout(t)
  }, [health, step, appId])

  // No snapshot yet -> render the REAL shell in a loading state (same header/sidebar/grid/
  // rail as below, gray placeholders in the center). Identical components + grid classes =>
  // zero layout shift when data lands. Also used by TransitionLoader (App shows it pre-appId).
  if (!snap) return <ConsoleShell loading />

  const meta = STEP_META[step - 1]

  // Step 1 is complete only once identity is resolved (a PAN exists — from mobile prefill or
  // the PAN gate). Until then the PAN-entry view is showing, so Continue must be disabled.
  // Step 4's "healthchat" sub-step similarly gates Continue until the deep-dive agent
  // itself says it's done (or the applicant skips) — HEALTH_AGENT_PLAN.md §7.
  const onHealthChatSub = step === 4 && healthSubs[Math.min(healthSub, healthSubs.length - 1)]?.key === "healthchat"
  const stepComplete = (step !== 1 || !!snap.seeded || !!snap.signals.pan_verify?.pan)
    && (!onHealthChatSub || healthChatDone)

  // Per-step save: POST to the step's endpoint, return ok. Steps not yet built just pass through.
  async function saveStep(): Promise<boolean> {
    if (appId == null) return true
    try {
      if (step === 1) {
        // Save the email on Continue -> this is what fires the email-intel fraud API
        // (backend /api/journey/email -> vendor fetch -> signals.email_intel). Skip the
        // call if the field is empty (email is optional); a bad email doesn't block the step.
        const e = email.trim()
        if (e) {
          await fetch("/api/journey/email", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ app_id: appId, email: e }),
          })
        }
        return true
      }
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
    // Step 4 paginates its sub-steps; walk them, persisting the (partial) declaration on
    // EACH Continue so the agent rail reacts as evidence lands (health -> vitals -> face),
    // not only after the final sub-step. /health accepts a partial (writes only set fields).
    if (step === 4 && !onLastHealthSub) {
      await saveStep()
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
          <span className="hidden sm:flex items-center gap-2 rounded-full px-2.5 py-1 bg-muted">
            <span className="grid place-items-center size-5 rounded-full bg-primary/15 text-primary text-[10px] font-bold">
              {(snap.applicant.name || "?").trim().charAt(0).toUpperCase()}
            </span>
            <span className="text-[12px] font-semibold max-w-[150px] truncate">{snap.applicant.name || "New applicant"}</span>
          </span>
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
          appId={snap.application_number} startedAt={istDateTime(snap.created_at)} />

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
                {step === 1 && <IdentityCenter snap={snap} appId={appId} onPrefilled={reload} email={email} onEmailChange={setEmail} />}
                {step === 2 && <ProductStep appId={appId} snap={snap} value={product} onChange={setProduct} onPremium={setPremium} />}
                {step === 3 && <FinancialStep appId={appId} snap={snap} value={financial} onChange={setFinancial} />}
                {step === 4 && <HealthStep appId={appId} snap={snap} value={health} onChange={setHealth} subStep={healthSub}
                  onHealthChatDone={() => setHealthChatDone(true)} reload={reload} />}
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
                    <button onClick={onContinue} disabled={saving || !stepComplete}
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
