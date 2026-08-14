import { Plus, Trash, User, Warning } from "@phosphor-icons/react"

// STEP 6 — Nominee(s) (TERM LIFE, display-capture; no gateway/enrichment). Captures one or
// more nominees with a % share split that must total 100. When a nominee's DOB makes them a
// minor (<18), an appointee sub-block progressively reveals — an appointee is required
// (Insurance Act §39); the backend re-checks this and rejects a minor nominee with no
// appointee. Continue persists via /api/journey/nominee (nominees[]) then advances to Payment.
//
// The first nominee is also stored as application.nominee (the single dict the engine reads,
// R-M2 relationship / insurable interest), so the underwriting contract stays unchanged.

const RELATIONSHIPS = ["Spouse", "Son", "Daughter", "Father", "Mother", "Brother", "Sister", "Other"]

export type Nominee = {
  name: string
  dob: string            // YYYY-MM-DD (native date input)
  relationship: string
  share_pct: number
  address: string
  appointee_name: string
  appointee_dob: string
  appointee_relationship: string
}

export const emptyNominee = (share = 100): Nominee => ({
  name: "", dob: "", relationship: "", share_pct: share, address: "",
  appointee_name: "", appointee_dob: "", appointee_relationship: "",
})

// Whole-years age from a YYYY-MM-DD string (mirrors the backend _age_from_dob).
function ageFromDob(dob: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(dob)
  if (!m) return null
  const [y, mo, d] = [+m[1], +m[2], +m[3]]
  const t = new Date()
  let age = t.getFullYear() - y - (t.getMonth() + 1 < mo || (t.getMonth() + 1 === mo && t.getDate() < d) ? 1 : 0)
  return age >= 0 && age < 130 ? age : null
}

export const isMinor = (dob: string) => { const a = ageFromDob(dob); return a !== null && a < 18 }
export const shareTotal = (list: Nominee[]) => list.reduce((s, n) => s + (Number(n.share_pct) || 0), 0)

// Payload sent to /api/journey/nominee — the backend validates share sum + appointee.
export const nomineePayload = (list: Nominee[]) => ({
  nominees: list.map((n) => ({
    name: n.name.trim(), dob: n.dob || null, relationship: n.relationship || null,
    share_pct: Number(n.share_pct) || 0, address: n.address.trim() || null,
    ...(isMinor(n.dob)
      ? { appointee_name: n.appointee_name.trim() || null, appointee_dob: n.appointee_dob || null,
          appointee_relationship: n.appointee_relationship || null }
      : {}),
  })),
})

export function NomineeStep({
  value, onChange,
}: {
  value: Nominee[]; onChange: (n: Nominee[]) => void
}) {
  const list = value.length ? value : [emptyNominee()]
  const total = shareTotal(list)
  const multi = list.length > 1

  const setAt = (i: number, patch: Partial<Nominee>) =>
    onChange(list.map((n, j) => (j === i ? { ...n, ...patch } : n)))
  const remove = (i: number) => {
    const next = list.filter((_, j) => j !== i)
    onChange(next.length ? next : [emptyNominee()])
  }
  const add = () => onChange([...list, emptyNominee(0)])

  return (
    <div className="space-y-8">
      <section>
        <RegionHead title="Nominee" hint="Who receives the benefit. Add more than one to split the share; shares must total 100%." />

        <div className="space-y-4">
          {list.map((n, i) => (
            <NomineeCard key={i} n={n} index={i} showRemove={multi}
              onChange={(p) => setAt(i, p)} onRemove={() => remove(i)} />
          ))}
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button type="button" onClick={add}
            className="inline-flex items-center gap-2 rounded-md border border-border bg-white text-[13px] font-semibold px-3.5 h-9 hover:border-muted-foreground/30 transition-colors">
            <Plus weight="bold" className="size-4" /> Add nominee
          </button>
          {multi && (
            <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] font-semibold ${
              total === 100 ? "stat-ok" : "text-amber-700 border-amber-300 bg-amber-50"}`}>
              {total !== 100 && <Warning weight="fill" className="size-3.5" />}
              Total share {total}%{total !== 100 ? " · must be 100%" : ""}
            </span>
          )}
        </div>
      </section>
    </div>
  )
}

function NomineeCard({
  n, index, showRemove, onChange, onRemove,
}: {
  n: Nominee; index: number; showRemove: boolean
  onChange: (p: Partial<Nominee>) => void; onRemove: () => void
}) {
  const minor = isMinor(n.dob)
  return (
    <fieldset className="rounded-xl border border-border bg-secondary/40 p-4 space-y-4">
      <legend className="sr-only">Nominee {index + 1}</legend>

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-[13px] font-semibold text-muted-foreground">
          <User weight="regular" className="size-4" /> Nominee {index + 1}
        </div>
        {showRemove && (
          <button type="button" onClick={onRemove}
            className="inline-flex items-center gap-1 text-[12px] text-muted-foreground hover:text-red-600 transition-colors">
            <Trash weight="regular" className="size-3.5" /> Remove
          </button>
        )}
      </div>

      <div className="grid sm:grid-cols-2 gap-x-4 gap-y-4">
        <Field label="Full name">
          <input autoFocus={index === 0} placeholder="Nominee's full name" value={n.name}
            onChange={(e) => onChange({ name: e.target.value })} className={inputCls} />
        </Field>

        <Field label="Date of birth" hint="optional">
          <input type="date" value={n.dob} max={today()}
            onChange={(e) => onChange({ dob: e.target.value })} className={inputCls} />
        </Field>

        <div className="sm:col-span-2">
          <FieldLabel label="Relationship" hint="optional" />
          <ChipRow options={RELATIONSHIPS} value={n.relationship} onPick={(v) => onChange({ relationship: v })} />
        </div>

        <Field label="Share">
          <div className="flex items-stretch rounded-lg border border-input overflow-hidden bg-white max-w-[10rem] focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/30">
            <input inputMode="numeric" value={n.share_pct || ""}
              onChange={(e) => onChange({ share_pct: Math.min(100, Number(e.target.value.replace(/[^\d]/g, ""))) })}
              className="w-full min-w-0 px-3 h-11 text-[14px] font-semibold outline-none bg-white tabular-nums" />
            <span className="grid place-items-center px-3 text-[13px] text-muted-foreground bg-muted border-l">%</span>
          </div>
        </Field>

        <Field label="Address" hint="optional">
          <input placeholder="Nominee's address" value={n.address}
            onChange={(e) => onChange({ address: e.target.value })} className={inputCls} />
        </Field>
      </div>

      {/* Appointee — revealed only when the nominee is a minor (Insurance Act §39). */}
      {minor && (
        <div className="rounded-lg border border-amber-300 bg-amber-50/60 p-4 space-y-4">
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1 rounded-md bg-amber-100 text-amber-800 px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide">§39</span>
            <span className="text-[13px] font-semibold">Appointee required — nominee is a minor</span>
          </div>
          <p className="text-[12px] text-amber-800/90 leading-snug">
            A minor cannot receive the benefit directly, so an appointee must be named to receive it on their behalf until they turn 18.
          </p>
          <div className="grid sm:grid-cols-2 gap-x-4 gap-y-4">
            <Field label="Appointee name">
              <input placeholder="Appointee's full name" value={n.appointee_name}
                onChange={(e) => onChange({ appointee_name: e.target.value })} className={inputCls} />
            </Field>
            <Field label="Appointee date of birth" hint="optional">
              <input type="date" value={n.appointee_dob} max={today()}
                onChange={(e) => onChange({ appointee_dob: e.target.value })} className={inputCls} />
            </Field>
            <div className="sm:col-span-2">
              <FieldLabel label="Relationship to nominee" hint="optional" />
              <ChipRow options={RELATIONSHIPS} value={n.appointee_relationship}
                onPick={(v) => onChange({ appointee_relationship: v })} />
            </div>
          </div>
        </div>
      )}
    </fieldset>
  )
}

// A value not in the preset list opens the custom input pre-filled (mirrors FinancialStep).
function ChipRow({ options, value, onPick }: { options: string[]; value: string; onPick: (v: string) => void }) {
  const isPreset = options.includes(value)
  return (
    <div className="flex flex-wrap gap-2.5">
      {options.map((o) => (
        <Choice key={o} active={value === o || (o === "Other" && !!value && !isPreset)}
          onClick={() => onPick(o === "Other" ? (isPreset || !value ? "Other relative" : value) : o)}>{o}</Choice>
      ))}
      {!!value && !isPreset && (
        <div className="flex items-stretch rounded-lg border border-primary ring-1 ring-primary/20 overflow-hidden bg-white focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/30">
          <input autoFocus placeholder="Relationship" value={value}
            onChange={(e) => onPick(e.target.value)}
            className="w-40 min-w-0 px-3 h-11 text-[13px] font-semibold outline-none bg-white" />
        </div>
      )}
    </div>
  )
}

const inputCls = "w-full min-w-0 px-3 h-11 rounded-lg border border-input bg-white text-[14px] font-semibold outline-none focus:border-ring focus:ring-[3px] focus:ring-ring/30 transition-shadow"

const today = () => new Date().toISOString().slice(0, 10)

function FieldLabel({ label, hint }: { label: string; hint?: string }) {
  return (
    <div className="text-[12px] font-semibold mb-2">
      {label}{hint && <span className="font-normal text-muted-foreground"> · {hint}</span>}
    </div>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (<div><FieldLabel label={label} hint={hint} />{children}</div>)
}

function RegionHead({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="mb-3">
      <h2 className="text-[15px] font-bold tracking-tight">{title}</h2>
      <p className="text-[12px] text-muted-foreground mt-0.5">{hint}</p>
    </div>
  )
}

function Choice({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick}
      className={`h-11 px-4 rounded-lg border text-[14px] font-semibold transition-colors ${active ? "border-primary bg-primary/[0.06] text-primary" : "border-border bg-white text-foreground hover:border-muted-foreground/30"}`}>
      {children}
    </button>
  )
}
