# UI_REBUILD_PLAN.md — World-class UI, from scratch (UI ONLY)

**Objective:** a world-class, end-to-end UI for the journey. **Backend is NOT touched** —
the agent (`underwriting/`), all APIs, and the 222 tests stay exactly as they are. This new
UI is a pure front-end that CALLS the existing FastAPI endpoints.

**This round:** 2 pages — **Mobile-verification gate** + **Step 1 (Identity & KYC)** — in
**3 variants** you pick from. Built live in the real app, screenshotted, fixed before you see it.

---

## 1. The tech stack (best-in-class for UI — decided, no build-your-own)

| Layer | Choice | Why it's the best |
|---|---|---|
| **Framework** | **React 18 + Vite + TypeScript** | The stack the whole "world-class AI UI" workflow is built for. Vite = instant dev server + one static build. |
| **Styling** | **Tailwind CSS v4** | Utility-first; the shadcn ecosystem's native language. |
| **Components** | **shadcn/ui** (Radix primitives) | The gold standard. NOT a dependency — components are copied into the repo, so we own + theme them. Accessible, unstyled-then-themed = never generic. |
| **Component source** | **shadcn MCP / `npx shadcn add`** | Pull canonical components live. The real anti-slop lever. |
| **Icons** | **Lucide** (ships with shadcn) | Clean line icons. Kills the emoji 🛡️🔒. |
| **Fonts** (off-slop, OFL, self-hosted) | **Fraunces** (display / big numbers) + **IBM Plex Sans** (UI) + **IBM Plex Mono** (PAN/paths/scores) | Deliberately NOT Inter/Geist/Space Grotesk (the AI-slop tells). Fraunces reads "premium financial report"; Plex is precise + open-source. |
| **Charts/gauge** | **Recharts** (or hand-built SVG ring) | For the Safety-Score gauge + any rail viz. |
| **Preview/QA** | built-in `browse` headless browser | I open the REAL running page, screenshot it, fix slop before it reaches you. |

**All free, all MIT/OFL.** No paid tools, no Figma/Framer subscription.

---

## 2. How it fits WITHOUT touching the backend

```
  NEW (this plan)                         EXISTING (untouched)
┌───────────────────────┐   HTTP calls   ┌──────────────────────────┐
│  journey-ui/  (React)  │ ─────────────► │  FastAPI                 │
│  Vite + shadcn + TW    │                │   /api/auth/*  (OTP)     │
│  builds to static/     │ ◄───────────── │   /api/journey/rail/{id} │
└───────────────────────┘   JSON          │   POST /underwrite       │
                                          │   underwriting/ (222 ✓)  │
                                          └──────────────────────────┘
```

- New React app lives in **`journey-ui/`** (its own folder — nothing else in the repo moves).
- **Dev:** `npm run dev` → Vite server on :5173, proxying `/api/*` to your FastAPI (:8000).
- **Ship:** `npm run build` → static JS/CSS bundle. FastAPI serves it (one mount line, or copy
  the `dist/` into `journey/static/` — decided at wiring time). No ongoing build on the server.
- **Zero backend edits.** If an endpoint returns a shape the UI needs slightly differently, the
  UI adapts to the endpoint — never the reverse.

---

## 3. The design language (what makes it world-class, not slop — checkable rules)

The anti-slop research (vibecodekit / 925studios) named the failure precisely. These rules are
the fix, and every screen is checked against them:

- **Fonts:** Fraunces + IBM Plex. NOT Inter/Geist/Space Grotesk.
- **Color:** max 3 hues — a dominant neutral (warm-tinted, not pure #fff/#000), one restrained
  brand accent (rationed to primary action / active step / focus only), and the semantic
  severity ramp (green/amber/red) kept SEPARATE from brand. No purple→blue gradients.
- **Cards:** borderless-first hierarchy — whitespace → a 3–5% surface-lightness step → one soft
  shadow. A hairline border only if those three fail. No flat-gray-1px-on-everything.
- **Spacing:** strict 8pt grid (4 as half-step). Generous: 24 between blocks, 64–96 between
  regions. Vary section weight so nothing feels like a template row.
- **Type scale:** Display 44 / H1 30 / H2 20 / Body 16 / Caption 13 / Micro-label 11 uppercase.
  Weights 300–600. Big numbers light. Type does the hierarchy, not heavy borders.
- **Motion:** 150–200ms, transform/opacity only, on hover/focus/step-change. Respect
  `prefers-reduced-motion`. One celebratory exception (the Safety-Score gauge reveal).
- **Contrast:** APCA-checked (Lc ≥75 body). Never color alone for state — always icon+label too.

---

## 4. The 2 pages × 3 variants (distinct LAYOUT, not color swaps)

Content is fixed (from JOURNEY_PLAN.md); the **structure/flow** differs per variant.

**Data per page (locked, unchanged):**
- **Gate:** +91 mobile · DPDP consent · OTP → on verify, Mobile→PAN fetch fires.
- **Step 1:** prefilled Name/DOB/gender/PAN/address/pincode/mobile-intel (read-only confirm) ·
  Email (fraud check) · Aadhaar via DigiLocker · inline consent · + the live agent rail
  (Identity/KYC · Contactability · Fraud · Litigation · Occupation groups, polled per-step).

| | Mobile-verify gate | Step 1 — Identity & KYC | Feels like |
|---|---|---|---|
| **A · Split canvas** | Brand/trust story left · verify card right | Form left · live agent rail right | Stripe |
| **B · Centered focus** | One centered card, quiet canvas, big type, one action | Full-width sections · rail as a slim top summary strip | Linear / Apple |
| **C · Console-dense** | Compact operator top-bar · centered verify | Dense grid · prefilled = read-only confirm cards · rail docked right | Ramp / terminal |

Switch variants via a URL param (`?v=a|b|c`) so you flip between REAL running pages, not mockups.

---

## 5. Build order (each step ends at something you can SEE)

1. **Scaffold** `journey-ui/` — Vite + React + TS + Tailwind v4 + shadcn init. Fonts wired.
   Vite proxy → FastAPI. `npm run dev` shows a themed empty shell. *(you see: it runs)*
2. **Tokens + theme** — brand accent, neutral ramp, severity ramp, type scale as CSS vars +
   Tailwind theme. shadcn components themed. *(you see: a themed button/card/input)*
3. **Variant A** — both pages, full content, calling real APIs (OTP + rail). Screenshotted + fixed.
4. **Variants B & C** — same content, different layout. All three live behind `?v=`.
5. **You pick** — a whole variant or a mix ("A's gate + C's step 1"). Notes welcome.
6. **QA pass** — I browse each, fix spacing/contrast/motion against §3, then hand you the URL.

*(Steps 2–7 of the journey come later, reusing the picked variant's components — no re-design.)*

---

## 6. What I do NOT touch
- `underwriting/` (the agent), `POST /underwrite`, the 222 tests.
- The FastAPI route logic / data plumbing in `journey/*.py`.
- No backend deps, no rule/scoring changes. The old `journey/templates` + `journey/static`
  stay in place until you're happy with the React UI, then we retire them.

---

## 7. Go
On your "go", I execute §5 step 1 (scaffold) and show you it running. Nothing before that.
```
```
