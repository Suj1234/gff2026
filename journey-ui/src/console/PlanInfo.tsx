import { CheckCircle, XCircle, Info } from "@phosphor-icons/react"

// Illustrative term-life plan details. SAMPLE terms for the demo — not a filed policy
// wording. Structure mirrors a real term-life benefit sheet (overview / inclusions /
// exclusions / riders). Wide two-column layout so the modal reads horizontal, not tall.

type PlanDetail = {
  overview: string
  inclusions: string[]
  exclusions: string[]
  riders: string
}

export const PLAN_DETAIL: Record<string, PlanDetail> = {
  term_protect: {
    overview: "Essential term cover: a lump-sum death benefit to the nominee, at the most accessible premium. Built for straightforward income protection.",
    inclusions: [
      "Death benefit paid to the nominee on death due to any cause, after the waiting period.",
      "Terminal illness advance of the sum assured on diagnosis.",
      "Level premium for the full policy term.",
      "30-day free-look period (IRDAI).",
    ],
    exclusions: [
      "Suicide within 12 months of policy start (or revival).",
      "Claims arising from non-disclosure or misrepresentation.",
      "Death while engaged in declared hazardous activities not covered.",
    ],
    riders: "Compatible with Accidental Death Benefit and Waiver of Premium riders.",
  },
  term_plus: {
    overview: "Popular tier: everything in Protect, plus added flexibility and a broader benefit set for growing responsibilities.",
    inclusions: [
      "All Term Protect benefits.",
      "Option to increase cover at key life events (marriage, childbirth).",
      "Premium-payment flexibility (regular / limited pay).",
      "Terminal illness advance and 30-day free-look.",
    ],
    exclusions: [
      "Suicide within 12 months of policy start (or revival).",
      "Claims arising from non-disclosure or misrepresentation.",
      "Pre-existing conditions not declared at underwriting.",
    ],
    riders: "Adds Critical Illness and Income Benefit riders on top of Protect's set.",
  },
  term_elite: {
    overview: "Premium tier: the fullest protection and benefit set, for comprehensive family security and the widest rider choice.",
    inclusions: [
      "All Term Plus benefits.",
      "Highest sum-assured bands available.",
      "Return-of-premium option (if selected).",
      "Priority claims handling and full rider eligibility.",
    ],
    exclusions: [
      "Suicide within 12 months of policy start (or revival).",
      "Claims arising from non-disclosure or misrepresentation.",
      "Specific exclusions noted in the policy schedule.",
    ],
    riders: "Eligible for the full rider suite, including Terminal Illness and Accidental Disability.",
  },
}

export function PlanInfoBody({ planId, planName }: { planId: string; planName: string }) {
  const d = PLAN_DETAIL[planId] || PLAN_DETAIL.term_protect
  return (
    <div className="space-y-5">
      <div className="rounded-xl bg-secondary p-4 text-[13.5px] leading-relaxed">{d.overview}</div>

      {/* two-column so the modal reads horizontal on desktop, not tall */}
      <div className="grid md:grid-cols-2 gap-5">
        <div>
          <div className="flex items-center gap-2 mb-2.5">
            <CheckCircle weight="fill" className="size-4 text-emerald-600" />
            <h3 className="text-[13px] font-semibold uppercase tracking-[0.05em] text-muted-foreground">What's covered</h3>
          </div>
          <ul className="space-y-2">
            {d.inclusions.map((x, i) => (
              <li key={i} className="flex gap-2 text-[13px] leading-snug">
                <span className="mt-1.5 size-1.5 rounded-full bg-emerald-500 shrink-0" />{x}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <div className="flex items-center gap-2 mb-2.5">
            <XCircle weight="fill" className="size-4 text-red-500" />
            <h3 className="text-[13px] font-semibold uppercase tracking-[0.05em] text-muted-foreground">What's not covered</h3>
          </div>
          <ul className="space-y-2">
            {d.exclusions.map((x, i) => (
              <li key={i} className="flex gap-2 text-[13px] leading-snug">
                <span className="mt-1.5 size-1.5 rounded-full bg-red-400 shrink-0" />{x}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="rounded-xl border p-3.5 text-[13px] text-muted-foreground">
        <span className="font-semibold text-foreground">Riders. </span>{d.riders}
      </div>

      <p className="flex items-start gap-2 text-[11px] text-muted-foreground">
        <Info weight="fill" className="size-3.5 shrink-0 mt-0.5" />
        Illustrative sample terms for {planName}. The full policy wording governs actual cover and is available on request.
      </p>
    </div>
  )
}
