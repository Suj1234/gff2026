import { useRef, useState } from "react";
import { LoginForm } from "@/components/login-form";
import { Console } from "@/console/Console";
import { TransitionLoader } from "@/console/TransitionLoader";

// ?v=teal|light switches variant. Loader shows from the moment Verify is clicked
// (covers the verify + Mobile->PAN prefill wait) until the console has its data.
export default function App() {
  const params = new URLSearchParams(window.location.search);
  const v = params.get("v");
  const variant = v === "teal" ? "teal" : "light";
  const forceConsole = params.get("step") === "console";

  const [appId, setAppId] = useState<number | null>(null);
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
        // keep the loader up for a minimum ~1.6s so it reads as a real step, not a flash
        const elapsed = performance.now() - loadStart.current;
        window.setTimeout(() => setView("console"), Math.max(0, 1600 - elapsed));
      }}
      onVerifyFail={() => setView("landing")}
    />
  );
}
