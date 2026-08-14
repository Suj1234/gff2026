import { useRef, useState } from "react"
import type { AppSnapshot } from "./useJourney"
import { Check, FilePdf, SealCheck, Spinner, Warning } from "@phosphor-icons/react"

// STEP 3 — Financial (TERM LIFE). The underwriter captures declared annual income, source
// of funds and purpose of cover; the GST turnover slab from Step 1 shows as a read-only
// cross-check. An OPTIONAL bank-statement PDF upload runs iAdore analysis live (falls back
// gracefully). Continue persists via /api/journey/financial (upload posts on pick, separately).
//
// Income validates the sum assured the applicant already chose (income-multiple R-007/R-008),
// so this step is what turns the Step-2 "indicative" premium into a real decision at Step 5.

const SOURCES = ["Salary", "Business income", "Professional fees", "Investments", "Rental income"]
const PURPOSES = ["Family protection", "Loan / liability cover", "Income replacement", "Wealth transfer"]

const inr = (n: number) => "₹" + n.toLocaleString("en-IN")

export type FinancialState = {
  declared_annual_income: number
  source_of_funds: string
  purpose_of_cover: string
}

export function FinancialStep({
  appId, snap, value, onChange,
}: {
  appId: number | null; snap: AppSnapshot
  value: FinancialState; onChange: (s: FinancialState) => void
}) {
  const set = (patch: Partial<FinancialState>) => onChange({ ...value, ...patch })

  return (
    <div className="space-y-8">
      {/* Income + source/purpose — the ONE editable region, held in a single bordered card */}
      <section>
        <RegionHead title="Declared income" hint="Income validates the cover already chosen. Enter the applicant's figures." />

        <fieldset className="rounded-xl border border-border bg-secondary/40 p-4 space-y-4">
          <legend className="sr-only">Income and funds</legend>

          <div>
            <div className="text-[12px] font-semibold mb-2">Annual income <span className="font-normal text-muted-foreground">· gross, before tax</span></div>
            <div className="flex items-stretch rounded-lg border border-input overflow-hidden bg-white max-w-xs focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/30">
              <span className="grid place-items-center px-3 text-[13px] text-muted-foreground bg-muted border-r">₹</span>
              <input inputMode="numeric" autoFocus placeholder="Annual income"
                value={value.declared_annual_income ? value.declared_annual_income.toLocaleString("en-IN") : ""}
                onChange={(e) => set({ declared_annual_income: Math.min(Number(e.target.value.replace(/[^\d]/g, "")), 1_000_000_000) })}
                className="w-full min-w-0 px-3 h-11 text-[14px] font-semibold outline-none bg-white tabular-nums" />
              <span className="grid place-items-center px-3 text-[12px] text-muted-foreground bg-muted border-l">/yr</span>
            </div>
            {value.declared_annual_income > 0 &&
              <p className="mt-1.5 text-[11px] text-muted-foreground">{amountWords(value.declared_annual_income)}</p>}
          </div>

          <div>
            <div className="text-[12px] font-semibold mb-2">Source of funds</div>
            <ChipRow options={SOURCES} value={value.source_of_funds} onPick={(v) => set({ source_of_funds: v })} />
          </div>

          <div>
            <div className="text-[12px] font-semibold mb-2">Purpose of cover</div>
            <ChipRow options={PURPOSES} value={value.purpose_of_cover} onPick={(v) => set({ purpose_of_cover: v })} />
          </div>
        </fieldset>
      </section>

      {/* Bank statement — OPTIONAL, its own region. iAdore corroboration; not a gate.
          GST turnover / vehicle / imputed income now surface on the agent rail (Financial group). */}
      <BankStatement appId={appId} snap={snap} declared={value.declared_annual_income} />
    </div>
  )
}

// ---- Bank statement upload (optional; posts to /api/journey/bank-statement on pick) ------
type UploadState = "idle" | "uploading" | "done" | "error"

function BankStatement({ appId, snap, declared }: { appId: number | null; snap: AppSnapshot; declared: number }) {
  const already = snap.signals.account_aggregator?.status === "available"
  const [state, setState] = useState<UploadState>(already ? "done" : "idle")
  const [fileName, setFileName] = useState<string>("")
  const [msg, setMsg] = useState<string>("")
  const inputRef = useRef<HTMLInputElement>(null)

  async function upload(file: File) {
    if (appId == null) return
    setFileName(file.name); setState("uploading"); setMsg("")
    const body = new FormData()
    body.append("app_id", String(appId))
    body.append("file", file)
    try {
      const r = await fetch("/api/journey/bank-statement", { method: "POST", body })
      const d = await r.json()
      if (d.success) setState("done")
      else { setState("error"); setMsg(d.message || "Analysis unavailable — you can proceed.") }
    } catch { setState("error"); setMsg("Upload failed — you can proceed; income can be corroborated later.") }
  }

  return (
    <section>
      <RegionHead title="Bank statement" hint="Optional. Corroborates the declared income; the agent can also request it later." />

      <div className="rounded-xl border border-border bg-white p-4">
        <input ref={inputRef} type="file" accept="application/pdf" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f) }} />

        {state === "done" ? (
          <div className="flex items-center gap-3">
            <span className="grid place-items-center size-9 rounded-lg stat-ok border shrink-0"><SealCheck weight="fill" className="size-[18px]" /></span>
            <div className="min-w-0 flex-1">
              <div className="text-[13.5px] font-semibold">Statement analysed</div>
              <div className="text-[12px] text-muted-foreground truncate">{fileName || "Read for imputed income, average balance and salary credits."}</div>
            </div>
            <span className="inline-flex items-center gap-1 rounded-full stat-ok border px-2 py-0.5 text-[11px] font-semibold shrink-0"><Check weight="bold" className="size-3" /> Done</span>
          </div>
        ) : (
          <div className="flex items-start gap-3">
            <span className="grid place-items-center size-9 rounded-lg bg-primary/10 text-primary shrink-0"><FilePdf weight="regular" className="size-[18px]" /></span>
            <div className="min-w-0 flex-1">
              <p className="text-[13px] text-muted-foreground leading-snug">
                Upload a PDF statement — iAdore reads imputed income, average balance and salary credits.
                A document-sharing consent is recorded on upload.
              </p>
              {state === "error" && (
                <p className="mt-1.5 flex items-center gap-1.5 text-[12px] text-amber-700"><Warning weight="fill" className="size-3.5 shrink-0" /> {msg}</p>
              )}
              <button type="button" disabled={state === "uploading"}
                onClick={() => inputRef.current?.click()}
                className="mt-2.5 inline-flex items-center gap-2 rounded-md bg-primary text-primary-foreground text-[13px] font-medium px-4 h-9 hover:bg-primary/90 transition-colors disabled:opacity-60">
                {state === "uploading"
                  ? (<><Spinner weight="bold" className="size-4 animate-spin" /> Analysing…</>)
                  : (state === "error" ? "Try another file" : "Upload statement")}
              </button>
            </div>
          </div>
        )}
      </div>
      {/* declared echo kept for the underwriter's context; numbers surface in the rail + report */}
      {declared > 0 && state !== "done" && (
        <p className="mt-2 text-[11px] text-muted-foreground">Declared income on file: {inr(declared)}/yr.</p>
      )}
    </section>
  )
}

// Choice chip row + inline custom (mirrors ProductStep's Choice pattern). A value not in the
// preset list opens the custom input pre-filled with it, so revisit + custom both round-trip.
function ChipRow({ options, value, onPick }: { options: string[]; value: string; onPick: (v: string) => void }) {
  const isPreset = options.includes(value)
  const [custom, setCustom] = useState(false)
  const showInput = custom || (!!value && !isPreset)
  return (
    <div className="flex flex-wrap gap-2.5">
      {options.map((o) => (
        <Choice key={o} active={value === o} onClick={() => { setCustom(false); onPick(o) }}>{o}</Choice>
      ))}
      {showInput ? (
        <div className={`flex items-stretch rounded-lg border overflow-hidden bg-white ${value && !isPreset ? "border-primary ring-1 ring-primary/20" : "border-input"} focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/30`}>
          <input autoFocus placeholder="Custom" value={value && !isPreset ? value : ""}
            onChange={(e) => onPick(e.target.value)}
            className="w-40 min-w-0 px-3 h-11 text-[13px] font-semibold outline-none bg-white" />
        </div>
      ) : (
        <Choice active={false} onClick={() => setCustom(true)}>Custom…</Choice>
      )}
    </div>
  )
}

// ₹ figure -> lakh/crore words, so a big number reads at a glance (₹18,40,000 -> "18.4 lakh/yr").
function amountWords(n: number): string {
  const s = n >= 10_000_000 ? `${(n / 10_000_000).toFixed(n % 10_000_000 ? 2 : 0)} crore`
    : n >= 100_000 ? `${(n / 100_000).toFixed(n % 100_000 ? 1 : 0)} lakh`
    : n.toLocaleString("en-IN")
  return `${s} per year`
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
