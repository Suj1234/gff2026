import { useEffect, useRef, useState } from "react"
import { ChatCircleDots, PaperPlaneTilt, Spinner, Check, SkipForward } from "@phosphor-icons/react"
import type { AppSnapshot, HealthAgentState, HealthThreadState } from "./useJourney"

// HealthChatPanel — the conversational health-triage deep-dive (HEALTH_AGENT_PLAN.md §7).
//
// UI principles, straight from §7 (do not casually "simplify" these away — each maps to
// a specific reason in the plan doc):
//  - One question at a time, no "step N of M" progress dots — there is no fixed step
//    count per condition anymore (the agent is genuinely adaptive), so showing a count
//    would be lying about a number the system doesn't have.
//  - A real "thinking" pause between the applicant's answer and the next question — the
//    next question genuinely depends on an LLM call reading the whole conversation, so
//    this delay is real and should be shown as such, not hidden.
//  - No visible checklist of fields — the conversation itself is the only interface.
//  - A one-line plain-language recap when a condition thread closes, before the next
//    flagged condition's questions start (natural chat-turn-taking cue).
//  - A visible "Skip for now" on every question — independent of the model's own
//    turn-cap/is_terminal stopping, the applicant must feel in control of stopping too.
//  - Trust-building copy: explain WHY this is asking, cite the fact generically ("your
//    health check"), never claim "100% anonymous" — state plainly what it's for.

type Bucket = { bucket: string; label?: string; trigger_fact: string }

export function HealthChatPanel({
  appId, snap, onAllDone,
}: {
  appId: number | null
  snap: AppSnapshot
  onAllDone: () => void   // Console advances to the next sub-step once every thread is done
}) {
  const [phase, setPhase] = useState<"loading" | "triaging" | "chatting" | "empty" | "skipped" | "triage_error">("loading")
  const [flagged, setFlagged] = useState<Bucket[]>([])
  const [activeIdx, setActiveIdx] = useState(0)
  const [thread, setThread] = useState<HealthThreadState | null>(null)
  const [answer, setAnswer] = useState("")
  const [thinking, setThinking] = useState(false)
  const [justClosed, setJustClosed] = useState<{ label: string } | null>(null)
  const [error, setError] = useState("")
  const startedForApp = useRef<number | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  // Resume from a saved snapshot (revisit) before running triage fresh.
  useEffect(() => {
    if (appId == null || startedForApp.current === appId) return
    startedForApp.current = appId
    const saved: HealthAgentState | undefined = snap.health_agent
    if (saved?.flagged?.length) {
      setFlagged(saved.flagged as Bucket[])
      // Found 2026-08-21: this used to assume `saved.threads` is filled strictly in
      // flagged-list order (thread 0 done -> thread 1 done -> ... -> first not-done),
      // so it picked `saved.flagged[doneCount]` by COUNT. That's wrong whenever the
      // first not-done bucket doesn't literally sit at index `doneCount` (e.g. a resume
      // right after triage, before ANY thread has been started at all — doneCount=0,
      // but flagged[0] also has no thread state, so `t` came out null and the chat
      // panel rendered an empty question box forever). Find the first bucket that is
      // NOT done by scanning for a missing/incomplete thread, not by counting dones.
      const idx = saved.flagged.findIndex((f) => !saved.threads?.[f.bucket]?.done)
      if (idx === -1) {
        onAllDone()
        return
      }
      setActiveIdx(idx)
      const t = saved.threads?.[saved.flagged[idx].bucket] ?? null
      if (t) {
        setThread(t)
        setPhase("chatting")
      } else {
        // Triage ran (or a resume landed) before this bucket's thread was ever
        // started — start it now instead of showing an empty chat with no question.
        startThread(saved.flagged[idx] as Bucket)
      }
      return
    }
    runTriage()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
  }, [thread?.transcript.length, phase])

  async function runTriage() {
    if (appId == null) return
    setPhase("triaging")
    setError("")
    try {
      const resp = await fetch(`/api/journey/health/triage/${appId}`, { method: "POST" })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const r = await resp.json()
      if (!r.success) throw new Error(r.message || "Could not run the health check.")
      const list: Bucket[] = r.flagged || []
      setFlagged(list)
      if (!list.length) { setPhase("empty"); onAllDone(); return }
      await startThread(list[0])
    } catch (err) {
      // Real cause logged (not swallowed) — triage can legitimately take 10-20s+ for
      // multiple flagged conditions; a network hiccup or dev-server restart mid-request
      // shouldn't silently look identical to "nothing to ask". Retry stays available
      // rather than forcing onAllDone() on what may just be a transient failure.
      console.error("health triage failed:", err)
      setError("Couldn't check your health-check results just now.")
      setPhase("triage_error")
    }
  }

  async function startThread(b: Bucket) {
    if (appId == null) return
    setThinking(true)
    try {
      const r = await fetch(`/api/journey/health/thread/start/${appId}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ app_id: appId, bucket: b.bucket }),
      }).then((x) => x.json())
      if (!r.success) throw new Error(r.message)
      setThread({
        bucket: b.bucket, trigger_fact: b.trigger_fact, transcript: [], covered: [],
        turns_used: 0, done: false, next_question: r.question,
      })
      setPhase("chatting")
    } catch {
      setError("Couldn't start that follow-up — you can continue and we'll ask an underwriter to follow up.")
      setPhase("empty")
    } finally {
      setThinking(false)
    }
  }

  async function submitAnswer() {
    if (appId == null || !thread || !answer.trim()) return
    const q = thread.next_question || ""
    const a = answer.trim()
    setAnswer("")
    setThread({ ...thread, transcript: [...thread.transcript, { q, a }] })
    setThinking(true)
    try {
      const r = await fetch(`/api/journey/health/thread/answer/${appId}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ app_id: appId, thread_id: thread.bucket, answer: a }),
      }).then((x) => x.json())
      if (!r.success) throw new Error(r.message)
      if (r.done) {
        setThread((t) => t && { ...t, done: true, summary: r.summary })
        const closedLabel = flagged[activeIdx]?.label || flagged[activeIdx]?.bucket || "that"
        setJustClosed({ label: closedLabel })
        const nextList = r.next_thread ? [...flagged, r.next_thread] : flagged
        if (r.next_thread) setFlagged(nextList)
        const nextIdx = activeIdx + 1
        if (nextIdx < nextList.length) {
          setTimeout(async () => {
            setJustClosed(null)
            setActiveIdx(nextIdx)
            await startThread(nextList[nextIdx])
          }, 900)
        } else {
          setTimeout(() => { setJustClosed(null); onAllDone() }, 900)
        }
      } else {
        setThread((t) => t && { ...t, next_question: r.question })
      }
    } catch {
      setError("Something went wrong sending that — please try again.")
    } finally {
      setThinking(false)
    }
  }

  function skip() {
    setPhase("skipped")
    onAllDone()
  }

  if (phase === "loading" || phase === "triaging") {
    return (
      <section className="animate-[fade-up_.2s_ease]">
        <RegionHead title="Just a moment" hint="Checking your health-check results for anything worth a quick follow-up." />
        <div className="flex items-center gap-2 text-[13px] text-muted-foreground py-6 justify-center">
          <Spinner className="animate-spin" size={16} />
          Reviewing what we have so far…
        </div>
      </section>
    )
  }

  if (phase === "empty" || phase === "skipped") {
    return (
      <section className="animate-[fade-up_.2s_ease]">
        <RegionHead title="Health follow-up" hint="Nothing extra to ask right now — you're all set here." />
        {error && <p className="text-[12px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">{error}</p>}
      </section>
    )
  }

  if (phase === "triage_error") {
    return (
      <section className="animate-[fade-up_.2s_ease]">
        <RegionHead title="Health follow-up" hint="We couldn't check your health-check results just now." />
        {error && <p className="mb-3 text-[12px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">{error}</p>}
        <div className="flex items-center gap-2">
          <button type="button" onClick={runTriage}
            className="inline-flex items-center gap-2 rounded-md bg-primary text-primary-foreground text-[13px] font-medium px-4 h-9 hover:bg-primary/90 transition-colors">
            Try again
          </button>
          <button type="button" onClick={skip}
            className="inline-flex items-center gap-1.5 text-[12px] text-muted-foreground hover:text-foreground">
            <SkipForward size={14} />
            Skip for now — an underwriter will follow up if needed
          </button>
        </div>
      </section>
    )
  }

  return (
    <section className="animate-[fade-up_.2s_ease]">
      <RegionHead
        title="A couple of quick follow-ups"
        hint="Based on your health check, we have a few follow-up questions to make sure your cover is priced correctly. Your answers are reviewed as part of your application."
      />
      <div className="rounded-xl border border-border bg-white overflow-hidden">
        <div className="max-h-[420px] overflow-y-auto px-4 py-4 space-y-3">
          {thread?.transcript.map((turn, i) => (
            <div key={i} className="space-y-2">
              <ChatBubble side="agent">{turn.q}</ChatBubble>
              <ChatBubble side="user">{turn.a}</ChatBubble>
            </div>
          ))}
          {thread && !thread.done && thread.next_question && (
            <ChatBubble side="agent">{thread.next_question}</ChatBubble>
          )}
          {justClosed && (
            <div className="text-[12px] text-muted-foreground italic px-1 flex items-center gap-1.5">
              <Check size={14} className="text-primary" weight="bold" />
              Got it — thanks for sharing that about {justClosed.label.toLowerCase()}.
            </div>
          )}
          {thinking && (
            <div className="flex items-center gap-1.5 px-1 text-muted-foreground">
              <TypingDots />
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {thread && !thread.done && (
          <div className="border-t border-border p-3 flex items-end gap-2 bg-secondary/30">
            <textarea
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitAnswer() }
              }}
              placeholder="Type your answer…"
              rows={1}
              disabled={thinking}
              className="flex-1 resize-none px-3 py-2.5 rounded-lg border border-input text-[13px] outline-none bg-white focus:border-ring focus:ring-[3px] focus:ring-ring/30 disabled:opacity-60"
            />
            <button type="button" onClick={submitAnswer} disabled={thinking || !answer.trim()}
              className="h-10 w-10 grid place-items-center rounded-lg bg-primary text-primary-foreground disabled:opacity-40 shrink-0">
              <PaperPlaneTilt size={16} weight="fill" />
            </button>
          </div>
        )}
      </div>

      {error && <p className="mt-2 text-[12px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">{error}</p>}

      <button type="button" onClick={skip}
        className="mt-3 inline-flex items-center gap-1.5 text-[12px] text-muted-foreground hover:text-foreground">
        <SkipForward size={14} />
        Skip for now — an underwriter will follow up if needed
      </button>
    </section>
  )
}

function ChatBubble({ side, children }: { side: "agent" | "user"; children: React.ReactNode }) {
  const isAgent = side === "agent"
  return (
    <div className={`flex ${isAgent ? "justify-start" : "justify-end"}`}>
      <div className={`max-w-[85%] rounded-2xl px-3.5 py-2 text-[13px] leading-relaxed ${
        isAgent
          ? "bg-secondary text-foreground rounded-tl-sm"
          : "bg-primary text-primary-foreground rounded-tr-sm"
      }`}>
        {isAgent && (
          <span className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-[0.06em] text-muted-foreground mb-0.5">
            <ChatCircleDots size={11} /> Assistant
          </span>
        )}
        {children}
      </div>
    </div>
  )
}

function TypingDots() {
  return (
    <div className="flex items-center gap-1 rounded-2xl rounded-tl-sm bg-secondary px-3.5 py-2.5">
      {[0, 1, 2].map((i) => (
        <span key={i}
          className="h-1.5 w-1.5 rounded-full bg-muted-foreground/50 animate-bounce"
          style={{ animationDelay: `${i * 0.12}s` }} />
      ))}
    </div>
  )
}

function RegionHead({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="mb-3">
      <h2 className="text-[15px] font-bold tracking-tight">{title}</h2>
      <p className="text-[12px] text-muted-foreground mt-0.5">{hint}</p>
    </div>
  )
}
