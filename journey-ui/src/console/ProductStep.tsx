import { useEffect, useState, useCallback } from "react"
import type { AppSnapshot } from "./useJourney"
import { Check, Info } from "@phosphor-icons/react"
import { Modal } from "./Modal"
import { PlanInfoBody } from "./PlanInfo"

// STEP 2 — Product & Sum Assured (TERM LIFE, single life). The underwriter picks the
// sum assured, policy term, and optional life riders; an indicative premium recomputes
// live via /api/journey/quote as they toggle. Continue persists via /api/journey/product.
// Term life is single-life only — no product-type or family picker.

// mirrors journey/pricing.py PLANS
const PLANS = [
  { id: "term_protect", name: "Acme Term Protect", tag: "Essential", desc: "Core life cover at the most accessible premium." },
  { id: "term_plus", name: "Acme Term Plus", tag: "Popular", desc: "Added flexibility and richer benefits." },
  { id: "term_elite", name: "Acme Term Elite", tag: "Premium", desc: "The fullest protection and benefit set." },
]

const SUM_ASSURED = [
  { v: 2_500_000, label: "₹25 L" },
  { v: 5_000_000, label: "₹50 L" },
  { v: 10_000_000, label: "₹1 Cr" },
  { v: 20_000_000, label: "₹2 Cr" },
  { v: 50_000_000, label: "₹5 Cr" },
]
const TERMS = [10, 15, 20, 25, 30]
// mirrors journey/pricing.py RIDERS. mode: "sa" = ₹ cover input, "income" = ₹/month, "flat" = checkbox-only.
// Terminal Illness dropped — it's a built-in accelerated base benefit, not a sizeable rider (research).
const RIDERS: { id: string; label: string; desc: string; mode: "sa" | "income" | "flat"; presets: number[] }[] = [
  { id: "critical_illness", label: "Critical Illness", desc: "Lump sum on a covered critical illness", mode: "sa", presets: [1_000_000, 2_500_000, 5_000_000] },
  { id: "accidental_death", label: "Accidental Death Benefit", desc: "Extra cover on accidental death", mode: "sa", presets: [2_500_000, 5_000_000, 10_000_000] },
  { id: "accidental_disability", label: "Accidental Disability", desc: "Cover on total & permanent disability", mode: "sa", presets: [1_000_000, 2_500_000, 5_000_000] },
  { id: "income_benefit", label: "Income Benefit", desc: "Monthly income to the family", mode: "income", presets: [25_000, 50_000, 100_000] },
  { id: "waiver_of_premium", label: "Waiver of Premium", desc: "Future premiums waived on disability", mode: "flat", presets: [] },
]

type Premium = {
  base: number; rider_total: number; total_annual: number
  riders: { id: string; label: string; amount: number; cover?: number; monthly?: number }[]; note: string
}

export type RiderSel = { id: string; amount: number }
export type ProductState = {
  plan: string; sum_assured: number; tenure_years: number; riders: RiderSel[]
}

const inr = (n: number) => "₹" + n.toLocaleString("en-IN")
const inrShort = (n: number) =>
  n >= 10_000_000 ? `₹${(n / 10_000_000).toFixed(n % 10_000_000 ? 1 : 0)} Cr`
  : n >= 100_000 ? `₹${(n / 100_000).toFixed(n % 100_000 ? 1 : 0)} L`
  : "₹" + n.toLocaleString("en-IN")

export type PremiumSummary = {
  base: number; rider_total: number; total_annual: number
  riders: { id: string; label: string; amount: number }[]
}

export function ProductStep({
  appId, snap, value, onChange, onPremium,
}: {
  appId: number | null; snap: AppSnapshot
  value: ProductState; onChange: (s: ProductState) => void
  onPremium?: (p: PremiumSummary | null) => void
}) {
  const [premium, setPremium] = useState<Premium | null>(null)
  const [infoPlan, setInfoPlan] = useState<{ id: string; name: string } | null>(null)
  const [customSA, setCustomSA] = useState(false)
  const [customTerm, setCustomTerm] = useState(false)
  const isPresetSA = SUM_ASSURED.some((s) => s.v === value.sum_assured)
  const isPresetTerm = TERMS.includes(value.tenure_years)

  const quote = useCallback(async (s: ProductState) => {
    if (appId == null) return
    try {
      const r = await fetch("/api/journey/quote", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ app_id: appId, product_type: "term_life", ...s }),
      })
      const d = await r.json()
      if (d.success) { setPremium(d.premium); onPremium?.(d.premium) }
    } catch { /* transient — keep last premium */ }
  }, [appId, onPremium])

  useEffect(() => { quote(value) }, [value, quote])

  const set = (patch: Partial<ProductState>) => onChange({ ...value, ...patch })
  const riderOf = (id: string) => value.riders.find((r) => r.id === id)
  const toggleRider = (id: string, defaultAmt: number) =>
    set({
      riders: riderOf(id)
        ? value.riders.filter((r) => r.id !== id)
        : [...value.riders, { id, amount: defaultAmt }],
    })
  const setRiderAmount = (id: string, amount: number) =>
    set({ riders: value.riders.map((r) => (r.id === id ? { ...r, amount } : r)) })

  void snap  // premium already accounts for age/pincode server-side; tobacco loads apply after Step 4

  return (
    <div className="space-y-8">
      {/* plan variant — the term product tier (base plan everything else configures) */}
      <section>
        <RegionHead title="Choose your plan" hint="Term life, single life. Pick the plan that fits." />
        <div className="grid sm:grid-cols-3 gap-2.5">
          {PLANS.map((p) => {
            const on = value.plan === p.id
            return (
              <div key={p.id}
                onClick={() => set({ plan: p.id })}
                className={`relative cursor-pointer rounded-xl border p-3.5 transition-colors ${on ? "border-primary bg-primary/[0.04]" : "border-border bg-white hover:border-muted-foreground/30"}`}>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] uppercase tracking-[0.06em] font-semibold text-primary">{p.tag}</span>
                  <span className={`grid place-items-center size-4 rounded-full shrink-0 ${on ? "bg-primary text-primary-foreground" : "border border-muted-foreground/40"}`}>
                    {on && <Check weight="bold" className="size-3" />}
                  </span>
                </div>
                <div className="mt-1.5 flex items-center gap-2">
                  <span className="text-[14px] font-bold">{p.name}</span>
                  <button type="button" aria-label={`About ${p.name}`}
                    onClick={(e) => { e.stopPropagation(); setInfoPlan({ id: p.id, name: p.name }) }}
                    className="grid place-items-center size-5 rounded-full bg-primary/10 text-primary hover:bg-primary hover:text-primary-foreground transition-colors shrink-0">
                    <Info weight="bold" className="size-3.5" />
                  </button>
                </div>
                <div className="text-[12px] text-muted-foreground mt-0.5 leading-snug">{p.desc}</div>
              </div>
            )
          })}
        </div>
      </section>

      {/* Configure your cover — ONE region. Cover amount + policy term are ONE decision,
          held tight in a single bordered card (common region = strongest grouping cue).
          Riders are a demoted add-on beneath, not an equal-weight block. */}
      <section>
        <RegionHead title="Configure your cover" hint="Set the amount and how long it runs." />

        {/* the ONE border on this screen: amount + term together */}
        <fieldset className="rounded-xl border border-border bg-secondary/40 p-4 space-y-4">
          <legend className="sr-only">Your cover</legend>

          <div>
            <div className="text-[12px] font-semibold mb-2">Cover amount <span className="font-normal text-muted-foreground">· the life cover paid to the nominee</span></div>
            <div className="grid grid-cols-2 sm:grid-cols-6 gap-2.5">
              {SUM_ASSURED.map((s) => (
                <Choice key={s.v} active={value.sum_assured === s.v} onClick={() => { setCustomSA(false); set({ sum_assured: s.v }) }}>
                  {s.label}
                </Choice>
              ))}
              {customSA || !isPresetSA ? (
                <div className={`flex items-stretch rounded-lg border overflow-hidden bg-white ${!isPresetSA ? "border-primary ring-1 ring-primary/20" : "border-input"} focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/30`}>
                  <span className="grid place-items-center px-2 text-[13px] text-muted-foreground bg-muted border-r">₹</span>
                  <input autoFocus inputMode="numeric" placeholder="Custom" value={!isPresetSA && value.sum_assured ? value.sum_assured.toLocaleString("en-IN") : ""}
                    onChange={(e) => set({ sum_assured: Math.min(Number(e.target.value.replace(/[^\d]/g, "")), 100_000_000) })}
                    className="w-full min-w-0 px-2 h-11 text-[13px] font-semibold outline-none bg-white" />
                </div>
              ) : (
                <Choice active={false} onClick={() => setCustomSA(true)}>Custom</Choice>
              )}
            </div>
            <p className="mt-1.5 text-[11px] text-muted-foreground">Between ₹10 L and ₹10 Cr.</p>
          </div>

          <div>
            <div className="text-[12px] font-semibold mb-2">Policy term <span className="font-normal text-muted-foreground">· how many years the cover runs</span></div>
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2.5">
              {TERMS.map((t) => (
                <Choice key={t} active={value.tenure_years === t} onClick={() => { setCustomTerm(false); set({ tenure_years: t }) }}>
                  {t} yr
                </Choice>
              ))}
              {customTerm || !isPresetTerm ? (
                <div className={`flex items-stretch rounded-lg border overflow-hidden bg-white ${!isPresetTerm ? "border-primary ring-1 ring-primary/20" : "border-input"} focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/30`}>
                  <input autoFocus inputMode="numeric" placeholder="Custom" value={!isPresetTerm && value.tenure_years ? String(value.tenure_years) : ""}
                    onChange={(e) => set({ tenure_years: Math.min(Number(e.target.value.replace(/[^\d]/g, "")), 40) })}
                    className="w-full min-w-0 px-2 h-11 text-[13px] font-semibold outline-none bg-white" />
                  <span className="grid place-items-center px-2 text-[12px] text-muted-foreground bg-muted border-l">yr</span>
                </div>
              ) : (
                <Choice active={false} onClick={() => setCustomTerm(true)}>Custom</Choice>
              )}
            </div>
            <p className="mt-1.5 text-[11px] text-muted-foreground">Between 5 and 40 years.</p>
          </div>
        </fieldset>
      </section>

      {/* Add-ons — riders demoted beneath the cover card. Lighter sub-heading, no outer box. */}
      <fieldset>
        <legend className="text-[13px] font-semibold text-muted-foreground mb-2.5">Add-ons <span className="font-normal">· optional</span></legend>
        <div className="space-y-2.5">
          {RIDERS.map((r) => {
            const sel = riderOf(r.id)
            const on = !!sel
            const line = premium?.riders.find((x) => x.id === r.id)
            return (
              <div key={r.id}
                className={`rounded-xl border transition-colors ${on ? "border-primary bg-primary/[0.04]" : "border-border bg-white"}`}>
                <button type="button" onClick={() => toggleRider(r.id, r.presets[0] || 0)}
                  className="w-full text-left p-3.5 flex items-start gap-2.5">
                  <span className={`mt-0.5 grid place-items-center size-4 rounded shrink-0 ${on ? "bg-primary text-primary-foreground" : "border border-muted-foreground/40"}`}>
                    {on && <Check weight="bold" className="size-3" />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[13px] font-semibold">{r.label}</span>
                      {on && line && <span className="text-[12px] font-semibold text-primary tabular-nums shrink-0">+{inr(line.amount)}/yr</span>}
                    </div>
                    <div className="text-[12px] text-muted-foreground mt-0.5">{r.desc}</div>
                  </div>
                </button>

                {/* reveal-on-check amount control for amount-taking riders */}
                {on && r.mode !== "flat" && (
                  <div className="px-3.5 pb-3.5 pl-[42px]">
                    <div className="text-[11px] font-medium text-muted-foreground mb-1.5">
                      {r.mode === "income" ? "Monthly income benefit" : "Rider cover amount"}
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      {r.presets.map((p) => (
                        <button key={p} type="button" onClick={() => setRiderAmount(r.id, p)}
                          className={`h-9 px-3 rounded-lg border text-[13px] font-semibold transition-colors ${sel!.amount === p ? "border-primary bg-primary/[0.08] text-primary" : "border-border bg-white hover:border-muted-foreground/30"}`}>
                          {r.mode === "income" ? `${inr(p)}/mo` : inrShort(p)}
                        </button>
                      ))}
                      <div className="flex items-stretch rounded-lg border border-input overflow-hidden focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/30">
                        <span className="grid place-items-center px-2.5 text-[13px] text-muted-foreground bg-muted border-r">₹</span>
                        <input inputMode="numeric" value={sel!.amount ? sel!.amount.toLocaleString("en-IN") : ""}
                          onChange={(e) => {
                            const n = Number(e.target.value.replace(/[^\d]/g, ""))
                            // sa riders cap at base SA; income capped at a sane ₹5L/mo
                            const cap = r.mode === "income" ? 500_000 : value.sum_assured
                            setRiderAmount(r.id, Math.min(n, cap))
                          }}
                          className="w-28 px-2.5 h-9 text-[13px] outline-none bg-white" placeholder="Custom" />
                        {r.mode === "income" && <span className="grid place-items-center px-2 text-[12px] text-muted-foreground bg-muted border-l">/mo</span>}
                      </div>
                    </div>
                    {r.mode === "sa" && <p className="mt-1 text-[11px] text-muted-foreground">Up to your base cover ({inrShort(value.sum_assured)}).</p>}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </fieldset>

      {/* plan info modal */}
      <Modal open={!!infoPlan} onClose={() => setInfoPlan(null)} title={infoPlan?.name || "Plan details"}>
        {infoPlan && <PlanInfoBody planId={infoPlan.id} planName={infoPlan.name} />}
      </Modal>
    </div>
  )
}

// Region header — no number badge (the research: numbers make peer sections read as
// separate tasks, which is exactly what felt "disconnected"). Grouping does the work.
function RegionHead({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="mb-3">
      <h2 className="text-[15px] font-bold tracking-tight">{title}</h2>
      <p className="text-[12px] text-muted-foreground mt-0.5">{hint}</p>
    </div>
  )
}

function Choice({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick}
      className={`h-11 rounded-lg border text-[14px] font-semibold transition-colors ${active ? "border-primary bg-primary/[0.06] text-primary" : "border-border bg-white text-foreground hover:border-muted-foreground/30"}`}>
      {children}
    </button>
  )
}
