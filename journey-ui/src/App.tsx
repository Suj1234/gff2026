import { useRef, useState } from "react";
import { LoginForm } from "@/components/login-form";
import { Console } from "@/console/Console";
import { TransitionLoader } from "@/console/TransitionLoader";
import { slugToStep, stepToPath } from "@/console/steps";

// ?v=teal|light switches variant. Loader shows from the moment Verify is clicked
// (covers the verify + Mobile->PAN prefill wait) until the console has its data.
export default function App() {
  const params = new URLSearchParams(window.location.search);
  const v = params.get("v");
  const variant = v === "teal" ? "teal" : "light";

  // Session = a persisted appId (localStorage: survives refresh AND tab close / dev-server
  // restart). Was sessionStorage, which died with the tab and silently dropped the user to
  // the demo SEED identity mid-journey — the source of the "why is it Rajesh Menon?" bug.
  // A step URL (e.g. /demo/life/health) is only meaningful WITH a session.
  const stored = Number(localStorage.getItem("appId")) || null;
  const onStepUrl = slugToStep(window.location.pathname) > 0;
  const forceConsole = params.get("step") === "console" || (onStepUrl && stored != null);

  // No session but a step URL was opened cold -> can't resume, send to the landing gate
  // and normalize the URL back to root so a subsequent verify starts clean.
  if (onStepUrl && stored == null) {
    window.history.replaceState(null, "", stepToPath(1).replace(/\/[^/]+$/, "/"));
  }

  const [appId, setAppId] = useState<number | null>(forceConsole ? stored : null);
  const [view, setView] = useState<"landing" | "loading" | "console">(forceConsole ? "console" : "landing");
  const loadStart = useRef(0);

  if (view === "console") return <Console appId={appId} variant={variant} />;
  if (view === "loading") return <TransitionLoader variant={variant} />;

  return (
    <LoginForm
      variant={variant}
      onVerifyStart={() => { loadStart.current = performance.now(); setView("loading"); }}
      onVerified={(id) => {
        setAppId(id);
        localStorage.setItem("appId", String(id));  // persist across refresh, tab close + restart
        // keep the loader up for a minimum ~1.6s so it reads as a real step, not a flash
        const elapsed = performance.now() - loadStart.current;
        window.setTimeout(() => setView("console"), Math.max(0, 1600 - elapsed));
      }}
      onVerifyFail={() => setView("landing")}
    />
  );
}
