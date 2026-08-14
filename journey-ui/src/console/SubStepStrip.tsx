// Sub-steps inside the content card: solid-underline segmented tabs (subway pattern).
// Done = full emerald underline, active = full brand underline, upcoming = faint line.
// Matches the reference CIMSME sub-step bar.

// `onJump(i)` (when given) makes visited sub-steps clickable; `maxReached` bounds which
// can be jumped to. Without them the strip is display-only (the original behaviour).
export function SubStepStrip({
  steps, active, onJump, maxReached,
}: {
  steps: string[]; active: number
  onJump?: (i: number) => void; maxReached?: number
}) {
  return (
    <div className="hidden lg:grid gap-3 pb-3" style={{ gridTemplateColumns: `repeat(${steps.length}, 1fr)` }}>
      {steps.map((s, i) => {
        const done = i < active
        const isActive = i === active
        const clickable = !!onJump && i <= (maxReached ?? active)
        return (
          <button key={s} type="button" disabled={!clickable}
            onClick={() => clickable && onJump!(i)}
            className={`pt-1 text-left ${clickable ? "cursor-pointer" : "cursor-default"}`}>
            {/* separated segment: each sub-step is its own track, gap between them */}
            <div className={`h-[3px] rounded-full ${done ? "bg-emerald-500" : isActive ? "bg-primary" : "bg-border"}`} />
            <div className={`mt-2 text-[13px] font-medium ${done ? "text-emerald-600" : isActive ? "text-primary" : "text-muted-foreground"}`}>{s}</div>
          </button>
        )
      })}
    </div>
  )
}
