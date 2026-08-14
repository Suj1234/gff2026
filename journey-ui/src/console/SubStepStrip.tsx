// Sub-steps inside the content card: solid-underline segmented tabs (subway pattern).
// Done = full emerald underline, active = full brand underline, upcoming = faint line.
// Matches the reference CIMSME sub-step bar.

export function SubStepStrip({ steps, active }: { steps: string[]; active: number }) {
  return (
    <div className="hidden lg:grid gap-3 pb-3" style={{ gridTemplateColumns: `repeat(${steps.length}, 1fr)` }}>
      {steps.map((s, i) => {
        const done = i < active
        const isActive = i === active
        return (
          <div key={s} className="pt-1">
            {/* separated segment: each sub-step is its own track, gap between them */}
            <div className={`h-[3px] rounded-full ${done ? "bg-emerald-500" : isActive ? "bg-primary" : "bg-border"}`} />
            <div className={`mt-2 text-[13px] font-medium ${done ? "text-emerald-600" : isActive ? "text-primary" : "text-muted-foreground"}`}>{s}</div>
          </div>
        )
      })}
    </div>
  )
}
