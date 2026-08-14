import { Check } from "@phosphor-icons/react"

// Tablet (md..lg) main-step nav: a slim HORIZONTAL bar of 7 numbered dots with the
// active step named. The tall left column wastes a whole column on a tablet, so on
// tablet the main steps go up top and the width goes to form + rail side by side.
const STEPS = ["Identity & KYC", "Product & Cover", "Financial", "Health", "Decision", "Nominee", "Payment"]
const TOTAL = 7

export function TabletStepBar({ active }: { active: number }) {
  return (
    <div className="hidden md:block lg:hidden border-b bg-white px-6 py-3">
      <div className="flex items-center gap-2">
        {STEPS.map((label, i) => {
          const n = i + 1
          const done = n < active
          const isActive = n === active
          return (
            <div key={label} className="flex items-center gap-2 shrink-0">
              <span className={`grid place-items-center size-6 rounded-full text-[11px] font-bold shrink-0
                ${done || isActive ? "bg-primary text-primary-foreground" : "border-2 border-border text-muted-foreground bg-white"}`}>
                {done ? <Check weight="bold" className="size-3.5" /> : n}
              </span>
              {isActive && <span className="text-[13px] font-semibold text-primary whitespace-nowrap">{label}</span>}
              {n < TOTAL && <span className={`h-0.5 w-4 rounded-full ${done ? "bg-primary" : "bg-border"}`} />}
            </div>
          )
        })}
        <span className="ml-auto text-[12px] text-muted-foreground whitespace-nowrap">Step {active}/{TOTAL}</span>
      </div>
    </div>
  )
}
