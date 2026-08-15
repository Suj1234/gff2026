// Single source of truth: journey step (1-indexed) <-> URL slug. Used by App (boot view)
// and Console (URL <-> step sync). Paths are BASE-aware so /demo/life/health works in prod
// and /health works in dev, with no per-call-site changes.
export const STEP_SLUGS = ["identity", "product", "financial", "health", "decision", "nominee", "payment"] as const

const BASE = import.meta.env.BASE_URL.replace(/\/$/, "")  // "/demo/life" in prod, "" in dev

// pathname -> step number (1..7), or 0 if it's not a step path (root, unknown, etc.)
export function slugToStep(pathname: string): number {
  const slug = pathname.replace(BASE, "").replace(/^\/|\/$/g, "")
  const i = STEP_SLUGS.indexOf(slug as (typeof STEP_SLUGS)[number])
  return i < 0 ? 0 : i + 1
}

// step number (1..7) -> full path incl. BASE, e.g. 4 -> "/demo/life/health"
export function stepToPath(step: number): string {
  return `${BASE}/${STEP_SLUGS[Math.min(7, Math.max(1, step)) - 1]}`
}
