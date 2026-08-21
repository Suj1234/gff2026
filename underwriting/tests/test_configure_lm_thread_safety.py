"""test_configure_lm_thread_safety.py — regression lock for the 2026-08-21 prod bug:
`underwriting.judge` and `journey.health_agent.engine` BOTH call `configure_lm()` at
import time; DSPy only allows the OS thread that first called `dspy.configure()` in
this process to call it again (`dspy/dsp/utils/settings.py`). FastAPI's sync route
handlers run on `anyio` threadpool workers, so a second module lazily imported on a
different thread than the first crashed with a fast, 100% reproducible RuntimeError —
found via `/api/journey/health/triage/{id}` returning a raw 500. `configure_lm()` must
treat "another thread already configured a working LM" as a safe no-op, not an error.

Needs its own process (module import order/thread ownership is process-global, sticky
for the process lifetime) — run standalone, not merged into a shared-process suite.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap


def test_second_module_can_configure_dspy_from_a_different_thread():
    """The exact repro that crashed before the fix: import underwriting.judge on
    thread A (claims dspy's global config ownership), then import
    journey.health_agent.engine on a DIFFERENT thread B. Before the fix this raised
    RuntimeError; now it must succeed."""
    script = textwrap.dedent("""
        import threading

        def on_thread_a():
            import underwriting.judge as judge
            assert judge._LM_READY is True, "thread A should configure the LM successfully"

        t = threading.Thread(target=on_thread_a)
        t.start()
        t.join()

        result = {}
        def on_thread_b():
            try:
                import journey.health_agent.engine as engine
                result["ok"] = engine._LM_READY
            except Exception as e:
                result["ok"] = False
                result["error"] = f"{type(e).__name__}: {e}"

        t2 = threading.Thread(target=on_thread_b)
        t2.start()
        t2.join()

        assert result.get("ok") is True, f"cross-thread import failed: {result}"
        print("OK")
    """)
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "OK" in proc.stdout
