# DESIGN.md — Aegis Underwriting Console · Design System

**The design source of truth.** Every new page or component refers to this file and
consumes [`design-tokens.css`](design-tokens.css). If a component and this document
disagree, this document wins until it is updated. Do not re-derive colors, type, or
spacing per-page — import the tokens.

Audience for the product: **insurers / underwriters** (a B2B risk console), not
customers. The screen is *scanned and operated*, not read top-to-bottom, so the craft
is information design: surface the summary before the detail, encode state in form
(pills, severity stripes, a score gauge) so what needs attention reads at a glance.

Grounded in three research passes (open-source design systems; Apple/Stripe/Linear
craft; India insurtech competitors). See §9 for the sources.

---

## 1. Principles (the non-negotiables)

1. **One accent, ruthlessly rationed.** The teal brand is spent ONLY on: the primary
   action, the active step, and selection/focus. Never on risk or status.
2. **Semantic color is separate from brand.** Risk/decision states use the green /
   amber / red severity ramp. "Important" (brand) and "dangerous" (red) must never be
   the same color.
3. **Light by default, dark as opt-in.** Light reads as trustworthy/auditable for an
   insurer buyer and photographs well in procurement decks. Dark is a focus mode, tuned
   (never naive-inverted).
4. **Depth from tint + hairlines, not shadows.** Separate surfaces with a 1px border
   and a surface-tint step. Reserve one soft shadow for genuinely floating layers.
5. **Confident restraint in type.** Weights 300–600 only. Emphasize with size and color,
   not heavier weight. Light weights on big numbers/headings.
6. **Every signal is real.** Nothing on the agent rail is theatre — each chip maps to a
   real field in the bundle. A "Low" section is not proof a source was checked (see the
   engine's report.py KNOWN LIMITATION).
7. **Motion is physical and brief.** ≤300ms, transform/opacity only, three curves, one
   celebratory exception. Respect `prefers-reduced-motion`.
8. **Accessibility is not optional.** Visible keyboard focus, ARIA on steppers/state,
   never color alone to convey state (pair with icon/label), legible contrast in both
   themes.

---

## 2. Color

Full values live in [`design-tokens.css`](design-tokens.css). Summary:

### Brand — deep teal-petrol (differentiator; unoccupied in India health-insurance)
| Token | Light | Use |
|---|---|---|
| `--brand` / `--brand-700` | `#0E7C86` | primary buttons, active step, links |
| `--brand-600` | `#0D9488` | hover |
| `--brand-500` | `#14B8A6` | focus ring, bright accents |
| `--brand-tint` | `#D3EFEF` | borders on brand surfaces |
| `--brand-wash` | `#EAF7F6` | subtle brand fills (active pill bg) |
| `--gold` | `#E0A73B` | the ONE warm/positive note, used sparingly |

### Neutral ramp — slate, blue-biased (chosen, not default grey)
`--n-0 #FBFCFD` → `--n-1 #F2F5F8` → `--n-2 #E4E9EF` → `--n-3 #CDD5DF` →
`--n-4 #94A0AE` → `--n-5 #4C5766` → `--n-6 #28313D` → `--n-7 #0F1620`

Use the **semantic aliases** in components, not the ramp directly:
`--bg`, `--surface`, `--surface-2`, `--line`, `--line-2`, `--text`, `--text-2`, `--text-3`.

### Semantic severity — risk + decision ONLY (never brand)
| Meaning | Token | Light | Tint | Maps to |
|---|---|---|---|---|
| Success | `--ok` | `#158048` | `--ok-tint #DEF4E7` | ISSUE · low risk · verified |
| Warning | `--warn` | `#B26A08` | `--warn-tint #FBEFD6` | REFER · STEP-UP · moderate · attention |
| Critical | `--bad` | `#BB2029` | `--bad-tint #FBE6E7` | DECLINE · high risk · hard fail |
| Info | `--info` | `#1E5AD6` | `--info-tint #E1EAFB` | neutral informational |

**Core-6 decision → color mapping** (used on the verdict banner and outcome list):
- ISSUE → `--ok` · ISSUE_WITH_LOADING → `--brand-600` · STEP_UP → `--warn` ·
  POSTPONE → `--n-4` (slate) · REFER → `--warn` · DECLINE → `--bad`.

**Safety Score gauge bands** reuse the semantics ONLY:
- 80–100 Low Risk → `--ok` · 66–79 Moderate → `--warn` · 0–65 High Risk → `--bad`.
  (Band cutoffs mirror `config.SAFETY_BANDS`. The band is context, never the verdict.)

---

## 3. Typography

Three roles, all SIL OFL 1.1, self-hosted as `@font-face` data URIs (CSP blocks CDNs).
**Deliberately not Inter.**

| Role | Family | Token |
|---|---|---|
| Display (headings, big numbers) | **Space Grotesk** | `--font-display` |
| Body / UI | **Geist Sans** | `--font-body` |
| Numerals / paths / code | **Geist Mono** (tabular) | `--font-mono` |

### Scale (weight band 300–600 only; tracking tightens as size grows)
| Role | Size | Weight | Tracking | Example |
|---|---|---|---|---|
| Display | 48px | 300 | −0.02em | the Safety Score number |
| H1 | 30px | 400 | −0.015em | screen title ("Identity & KYC") |
| H2 | 22px | 500 | −0.01em | section heading |
| Body | 15px | 400 | 0 | field values, prose |
| Caption | 13px | 400 | 0 | helper text |
| Micro-label | 11px | 600 | +0.08em, UPPERCASE | field keys, rail group names |

Rules: headings get `text-wrap: balance`. All numerals that line up in columns use
`font-family: var(--font-mono)` + `font-variant-numeric: tabular-nums`. Keep running
text near 65ch.

---

## 4. Spacing, radius, elevation

- **Base 4px, work on an 8px grid.** Rhythm: 8/12/16 inside a component · **24** between
  content blocks · **64–96** between major regions. Tokens `--sp-1..--sp-12`.
- **Radius:** `--radius-sm 10px` (chips, tags), `--radius 14px` (cards, default),
  `--radius-lg 20px` (large panels), `--radius-pill 999px` (steps, buttons-as-pills).
- **Elevation = borders + tint first, shadows last.** `--sh-1` resting card ·
  `--sh-2` raised · `--sh-3` floating (browser frame / true popover). One soft shadow
  per layer, never shadow-on-everything. `--glow` only on the primary button hover.
- **Layout with flex/grid + `gap`**, never per-element margins that collapse/double.
  Wide content (tables, code, the step map) scrolls inside its own `overflow-x:auto`
  container — the page body never scrolls sideways.

---

## 5. Layout — the console shell (LOCKED: Shell A)

**Decision:** the app uses **Shell A — merged top-bar**. (Alternatives A2, A3, and the
vertical sidebar B were evaluated and set aside; see §8 decision log.)

```
┌───────────────────────────────────────────────────────────────────────────┐
│ #GFF-2481 · Paulson Mathew  ① Identity[ OTP✓ · Profile · Face ] › ②Product …│  HEADER (white)
│                                                                        14%  │  main stepper, active
├──────────────────────────────────────────────────────┬────────────────────┤  step expands to a pill
│  CENTER — the journey step (workspace, "king")        │  RIGHT RAIL —       │  containing its sub-steps
│  Identity & KYC                                       │  "what the agent    │
│  [ field grid … ]                                     │   sees" — signals   │
│                                                       │   grouped by Safety │
│  ──────────────────────────────────────────────────  │   Score source,     │
│  ‹ Back                                   Continue ›  │   each with a       │  CENTER + RAIL on the
└──────────────────────────────────────────────────────┴────────────────────┘  content canvas (grey)
```

- **Main stepper** = the 7 steps (Identity → Product → Financial → Health → Decision →
  Nominee → Payment) as a horizontal row on the white header surface.
- **Sub-stepper** = the active step expands into a **pill** containing its sub-steps
  inline (e.g. `Identity [ OTP ✓ · Profile · Face scan ]`). Only the active step
  expands. Row scrolls horizontally on narrow widths; non-active chips shrink to
  number-only.
- **Center** is the star — the step's data collection / workspace. Content adapts per
  screen (see §7).
- **Right rail** = the agent's live read, grouped 1:1 to the Safety-Score source groups.
- **Continue** is a bottom footer bar: `‹ Back` (secondary, disabled on step 1) +
  `Continue ›` (primary). One primary action per footer (PatternFly wizard standard).
- **Mobile:** the 7-chip row collapses to `‹ Identity · 1/7` + the active step's
  sub-steps as a small segmented bar; rail becomes a pull-up bottom sheet; one column.

The right-rail groups mirror `config.SAFETY_SCORE_WEIGHTS`: Identity/KYC · Financial ·
Occupation/Employer · Medical · Lifestyle · Fraud · Litigation/FIR · Velocity/Graph ·
Geography · Insurance-portfolio · Contactability.

---

## 6. Components + their state model

State vocabulary is shared across the whole system: **done · active/current · upcoming ·
attention**. Never rely on color alone — pair with an icon or weight.

### Stepper (main, Shell A)
- **done** — filled teal circle with a **✓**.
- **active** — ringed teal circle + number, on a `--brand-wash` pill that contains the
  sub-steps.
- **upcoming** — grey outline circle + number, muted label.

### Sub-stepper (inside the active pill)
- **done** — filled dot + label (normal weight).
- **current** — haloed teal dot + **bold teal** label.
- **upcoming** — grey dot + muted label.

### Signal chip (right rail)
- Row: `[led] title / subtext … [rule-id]`. Background tint by severity
  (`s-ok/s-warn/s-bad/s-idle`), led dot matches. `idle` = a source not yet returned.
- Grouped under a header showing the group's running **sub-score (0–100)** + a mini
  track bar. Group color follows the sub-score band.

### Safety Score gauge
- A ring (SVG) that **animates once** on reveal (`--dur-gauge`, `--ease`). Number in
  `--font-display` weight 300. Arc color = the band's semantic color. Always paired with
  the band pill (label + range) — never the number alone.

### Verdict banner (Decision screen)
- Icon tile + `CORE-6 · <VERDICT>` mono code + H2 title + one-line reason. Tinted by the
  decision→color map (§2). This is the payoff — it owns the top of the Decision screen.

### Buttons
- **primary** — `--brand` bg, white text, `--glow` on hover, `translateY(-1px)`.
- **secondary** — surface bg, `--line-2` border, brand border on hover.
- **disabled** — `opacity:.45`, `not-allowed`.
- Radius `--radius-sm`; label states exactly what happens ("Show QR", "Continue").

### Tags / pills (inline status)
- `.tag.brand/.ok/.warn/.bad` — 10px uppercase, tinted bg + semantic text. For a field's
  verified/ported/undisclosed state.

### Field card
- `--surface` bg, `--line` border, `--sh-1`, `--radius-sm`..`--radius`. Key = micro-label
  (uppercase), value = body. **Weight-contrast:** a field carrying a flag (e.g. ported
  mobile, undisclosed condition) gets an `attn` treatment (`--bad-tint` wash + red
  border) so it reads heavier than clean fields. Clean fields stay quiet.

### Cited-evidence chain (Decision screen)
- Vertical timeline: knob (colored by severity) + connector stem + ruling title + reason
  + the grounded data path in `--font-mono` on a `--brand-wash` chip
  (e.g. `signals.abha.icd_codes[0] = "E11.9"`). Every ruling resolves to a real path.

---

## 7. Per-screen center treatment

One shell, but the **center adapts to the step's job**:
- **Identity (Step 1)** — split-focus: a field grid that pre-fills live; the rail
  confirms each source as it returns.
- **Health (Step 4)** — guided disclosure: progressive screeners (6 Yes/No → conditions
  reveal on a Yes → BMI/vitals). The agent reacts in the rail the instant a declaration
  conflicts with ABHA (non-disclosure, R-010).
- **Decision (Step 5)** — score-first: the verdict banner + Safety Score gauge own the
  top at hero scale; factor scores, the six outcomes, and the cited-evidence chain sit
  below to audit the "why".

---

## 8. Decision log (what was chosen and why)

| Date | Decision | Rationale |
|---|---|---|
| 2026-08-11 | **Teal (`#0E7C86`) brand** | Unoccupied in India health-insurance; credible + clinical + premium. All 3 research prongs converged on it. |
| 2026-08-11 | **Light default, dark opt-in** | Trust/auditability for an insurer buyer; dark reads "crypto/fraud-tool". |
| 2026-08-11 | **Space Grotesk + Geist + Geist Mono** | Premium, non-cliché, SIL OFL, self-hostable. Not Inter (the AI-slop tell). Satoshi rejected (license blocks self-host). |
| 2026-08-11 | **Semantic severity kept separate from brand** | So "important" and "dangerous" never collide; states stay legible. |
| 2026-08-11 | **Continue in a bottom footer bar** | PatternFly wizard standard: one primary action, Back disabled on step 1. |
| 2026-08-11 | **Shell A (merged top-bar) chosen** | Selected over A2 (two-tier), A3 (collapsed map), and B (vertical sidebar). Research note: 7 steps is past the horizontal comfort limit (3–6) and sub-steps are conventionally vertical — Shell A accepts that trade-off for a compact top-bar; revisit if step count grows or sub-steps deepen. |

---

## 9. Sources (research behind this system)

- **Design systems:** Radix Colors (12-step scale convention), Tremor (KPI/data density),
  Mantine (Stepper/Timeline/RingProgress), shadcn/ui (primitives), Vercel Geist
  (slate ramp, flat-premium elevation, the fonts), IBM Carbon, Material (stepper spec),
  Ant Design, Atlassian, PatternFly (wizard footer).
- **Craft:** Apple HIG (deference, materials, SF-style tracking), Stripe (restraint,
  single accent, tint-step depth), Linear (dense hairline consoles, dark surface ladder).
- **Competitors:** Oscar (tonal ramps), Ethos (restraint = premium), Unit21/Alloy
  (score→evidence→decision→audit spine), Acko/Digit/Lemonade (what to differentiate from).

---

## 10. How to use this system (for the next page/component)

1. Import `design-tokens.css` (or copy the `:root` block into the artifact's inline
   `<style>` for a self-contained Artifact — CSP-safe).
2. Style **through the semantic tokens** (`--surface`, `--text`, `--brand`, `--ok`…),
   never a raw hex.
3. Reuse the components and their state model in §6 — don't reinvent a stepper/chip/gauge.
4. Follow the layout shell (§5) and per-screen center rules (§7).
5. Design **both themes** via the token blocks — never hardcode inside a media query.
6. Check it against §1 principles and the "never" list below before shipping.

### Never
- Use the brand teal as a status/risk color, or a status color as the brand.
- Hardcode a hex, font name, or spacing value in a component.
- Use Inter, a purple→blue gradient hero, glassmorphism on text, or everything-centered.
- Convey state by color alone (always pair with icon/label).
- Naive-invert for dark mode, or use pure `#000`/`#fff`.
- Animate width/height/color for motion (transform/opacity only), or exceed 300ms
  outside the one gauge/celebration reveal.

_Last updated: 2026-08-11. Update the decision log (§8) whenever a design decision
changes, and bump the relevant token/section here before changing a component._
