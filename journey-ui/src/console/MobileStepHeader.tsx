// Mobile step header (< lg): a "N/7" progress ring + step title + description, with
// sub-steps as an underlined tab bar below. Matches the reference upload-flow pattern.
const TOTAL = 7

export function MobileStepHeader({
  step, title, description, subSteps, subActive,
}: { step: number; title: string; description: string; subSteps: string[]; subActive: number }) {
  const r = 15, circ = 2 * Math.PI * r
  const pct = step / TOTAL
  return (
    <div className="lg:hidden border-b bg-white">
      <div className="flex items-start gap-3 px-5 pt-4 pb-3">
        <div className="flex-1 min-w-0">
          <h1 className="text-lg font-bold tracking-tight truncate">{title}</h1>
          <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{description}</p>
        </div>
        <div className="relative grid place-items-center size-11 shrink-0">
          <svg viewBox="0 0 40 40" className="size-11 -rotate-90">
            <circle cx="20" cy="20" r={r} fill="none" strokeWidth="3" className="stroke-muted" />
            <circle cx="20" cy="20" r={r} fill="none" strokeWidth="3" strokeLinecap="round"
              className="stroke-primary" strokeDasharray={circ} strokeDashoffset={circ * (1 - pct)} />
          </svg>
          <span className="absolute text-[11px] font-bold tabular-nums">{step}/{TOTAL}</span>
        </div>
      </div>
      {/* sub-step tab bar */}
      <div className="flex px-5 gap-6 overflow-x-auto">
        {subSteps.map((s, i) => {
          const isActive = i === subActive
          const done = i < subActive
          return (
            <div key={s} className={`relative pb-2.5 text-[13px] whitespace-nowrap
              ${isActive ? "text-primary font-semibold" : done ? "text-foreground/70" : "text-muted-foreground"}`}>
              {s}
              {isActive && <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-primary rounded-full" />}
            </div>
          )
        })}
      </div>
    </div>
  )
}
