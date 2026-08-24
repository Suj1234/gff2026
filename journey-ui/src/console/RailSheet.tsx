import { useState } from "react"
import type { AppSnapshot, Rail } from "./useJourney"
import { RailBody, LitigationCard, Gauge, railTone, railRows } from "./AgentRail"
import { CaretUp } from "@phosphor-icons/react"

// Mobile-only Agent Read: a sticky bottom bar (score + band) that expands into the
// full rail on tap. Standard mobile pattern — keeps the live read visible without
// stealing form space. Shown only < md (tablet+ get the side rail).

export function RailSheet({ rail, snap, showLitigation = false }: {
  rail: Rail | null; snap?: AppSnapshot | null; showLitigation?: boolean
}) {
  const [open, setOpen] = useState(false)
  const score = rail?.safety_score ?? null
  const band = rail?.band
  const tone = railTone(band)
  const rows = railRows(rail)
  const toneText = tone === "ok" ? "text-emerald-700" : tone === "warn" ? "text-amber-700" : tone === "bad" ? "text-red-700" : "text-muted-foreground"

  return (
    <div className="md:hidden">
      {/* backdrop when expanded */}
      {open && <button aria-label="Close agent read" onClick={() => setOpen(false)} className="fixed inset-0 z-40 bg-black/30" />}

      <div className="fixed inset-x-0 bottom-0 z-50">
        {/* expanded panel */}
        {open && (
          <div className="mx-auto max-h-[70dvh] overflow-y-auto rounded-t-2xl border-t bg-white shadow-[0_-8px_40px_rgba(0,0,0,0.15)] animate-fade-up space-y-3 p-0">
            <RailBody rail={rail} />
            {showLitigation && <div className="px-0 pb-3"><LitigationCard snap={snap ?? null} /></div>}
          </div>
        )}
        {/* summary bar (always visible) */}
        <button
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="w-full flex items-center gap-3 border-t bg-white px-4 py-2.5 shadow-[0_-2px_12px_rgba(0,0,0,0.08)]"
        >
          <Gauge value={score} tone={tone} size={40} />
          <div className="text-left min-w-0">
            <div className="text-[11px] uppercase tracking-wider text-muted-foreground font-semibold">Agent read</div>
            <div className={`text-sm font-bold ${toneText}`}>{band || "accumulating"}</div>
          </div>
          <span className="ml-auto flex items-center gap-2 text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1"><span className="size-1.5 rounded-full bg-emerald-500 animate-pulse" /> {rows.length}</span>
            <CaretUp weight="bold" className={`size-4 transition-transform ${open ? "rotate-180" : ""}`} />
          </span>
        </button>
      </div>
    </div>
  )
}
