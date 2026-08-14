import { useState } from "react"
import { CaretUp } from "@phosphor-icons/react"
import type { PremiumSummary } from "./ProductStep"

// Step-2 premium bar. Sticks to the bottom of the CENTER column (rendered outside the
// content card so the card's overflow-hidden doesn't clip sticky). Shows the total; the
// breakdown (base + each rider) expands upward on click. Standard insurance buy-flow.
const inr = (n: number) => "₹" + n.toLocaleString("en-IN")

export function PremiumBar({ premium, subtitle }: { premium: PremiumSummary | null; subtitle?: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="sticky bottom-4 z-20 mt-4">
      <div className="rounded-2xl elev-card overflow-hidden shadow-[0_8px_28px_-8px_rgba(24,20,14,0.18)]">
        {/* expandable breakdown */}
        {open && premium && (
          <div className="px-5 py-3.5 border-b bg-secondary/60 space-y-2 text-[13px]">
            <Line k="Base cover" v={premium.base} />
            {premium.riders.map((r) => <Line key={r.id} k={r.label} v={r.amount} add />)}
          </div>
        )}
        <button onClick={() => setOpen((v) => !v)} className="w-full flex items-center justify-between gap-4 px-5 pt-3 pb-2 text-left">
          <div>
            <div className="text-[10px] uppercase tracking-[0.08em] font-semibold text-muted-foreground">Indicative annual premium</div>
            <div className="text-[24px] font-bold tracking-tight tabular-nums leading-tight">
              {premium ? inr(premium.total_annual) : "—"}
              <span className="text-[13px] font-medium text-muted-foreground"> /yr</span>
            </div>
            {subtitle && <div className="text-[12px] text-muted-foreground mt-0.5">{subtitle}</div>}
          </div>
          <span className="flex items-center gap-1.5 text-[12px] font-medium text-primary">
            {open ? "Hide" : "Breakdown"}
            <CaretUp weight="bold" className={`size-4 transition-transform ${open ? "" : "rotate-180"}`} />
          </span>
        </button>
        <p className="px-5 pb-2.5 text-[11px] text-muted-foreground">Indicative — subject to underwriting. Not a firm quote.</p>
      </div>
    </div>
  )
}

function Line({ k, v, add }: { k: string; v: number; add?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-muted-foreground">{k}</span>
      <span className="font-semibold tabular-nums">{add ? "+" : ""}{inr(v)}</span>
    </div>
  )
}
