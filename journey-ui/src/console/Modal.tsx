import { useEffect } from "react"
import { X } from "@phosphor-icons/react"

// Lightweight modal. Responsive width on desktop (wide, capped), scrolls internally so it
// never runs tall. Closes on backdrop click or Esc. No dependency — one focus-trap-lite.
export function Modal({
  open, onClose, title, children,
}: { open: boolean; onClose: () => void; title: string; children: React.ReactNode }) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose() }
    window.addEventListener("keydown", onKey)
    document.body.style.overflow = "hidden"
    return () => { window.removeEventListener("keydown", onKey); document.body.style.overflow = "" }
  }, [open, onClose])

  if (!open) return null
  return (
    <div className="fixed inset-0 z-[60] grid place-items-center p-4 sm:p-6">
      <button aria-label="Close" onClick={onClose} className="absolute inset-0 bg-black/40 animate-[fade-up_.15s_ease]" />
      <div role="dialog" aria-modal="true" aria-label={title}
        className="relative w-full max-w-2xl lg:max-w-3xl max-h-[85dvh] flex flex-col rounded-2xl bg-card border shadow-[0_20px_60px_-12px_rgba(24,20,14,0.30)] animate-fade-up">
        <div className="flex items-center justify-between gap-3 px-6 py-4 border-b shrink-0">
          <h2 className="text-base font-bold tracking-tight">{title}</h2>
          <button onClick={onClose} className="grid place-items-center size-8 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors">
            <X weight="bold" className="size-4" />
          </button>
        </div>
        <div className="overflow-y-auto px-6 py-5">{children}</div>
      </div>
    </div>
  )
}
