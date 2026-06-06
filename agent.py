"""
agent.py — the screenshot -> brain -> action loop (backend-agnostic).

Usage (on the machine running the target app, with it open & focused):
    python agent.py "Open a new tab and search Google for the weather in Berlin."

Brain is pluggable via CU_BACKEND:
    claude  -> Anthropic computer-use tool (cloud). Default.
    uitars  -> local UI-TARS endpoint (screenshots stay local). Stub for now.

The brain only PROPOSES actions; safety.py decides and executor.py acts.
"""
import os
import platform
import sys

from dotenv import load_dotenv

from backends import ClaudeBackend, UITarsBackend, Observation
from executor import Executor
from safety import Safety

load_dotenv()

CU_BACKEND = os.getenv("CU_BACKEND", "claude").lower()
MODEL = os.getenv("CU_MODEL", "claude-opus-4-8")
MODE = os.getenv("CU_MODE", "confirm")
ALLOWED_WINDOW = os.getenv("CU_ALLOWED_WINDOW", "")
MAX_STEPS = int(os.getenv("CU_MAX_STEPS", "40"))
MAX_WIDTH = int(os.getenv("CU_MAX_WIDTH", "1280"))
KEEP_IMAGES = int(os.getenv("CU_KEEP_IMAGES", "3"))
THINKING = os.getenv("CU_THINKING", "adaptive").lower()
TARGET_OS = os.getenv("CU_TARGET_OS", "auto").lower()
ENABLE_ZOOM = os.getenv("CU_ENABLE_ZOOM", "false").lower() in ("1", "true", "yes")


def build_system_prompt(allowed_window: str) -> str:
    host_mac = platform.system() == "Darwin"
    target_mac = (TARGET_OS == "mac") or (TARGET_OS == "auto" and host_mac)
    os_name = "macOS" if target_mac else "Windows"
    if target_mac:
        keys = ("- On macOS use the Command (⌘) key for shortcuts — ⌘L (address bar), ⌘T (new tab), "
                "⌘C/⌘V (copy/paste).\n")
    else:
        keys = ("- This is a Windows desktop: use Ctrl for shortcuts (Ctrl+C/Ctrl+V, Alt+Tab). "
                "Prefer clicking; use the keyboard sparingly.\n")
    relay = ("- NOTE: you're viewing this Windows desktop inside a remote-control window — the image "
             "can be slightly compressed and updates may lag, so after each action wait for the "
             "screenshot to refresh and verify before continuing.\n" if (host_mac and not target_mac) else "")
    return (
        f"You control a {os_name} desktop through the `computer` tool. The user gives you a goal; "
        "accomplish it by taking screenshots and issuing mouse/keyboard actions.\n"
        "- Start by taking a screenshot to see the current screen.\n"
        "- After EACH action a fresh screenshot is returned. Verify the result before the next "
        "step; if a click missed or the screen isn't what you expected, correct it.\n"
        + keys + relay +
        f"- Work ONLY inside the target app (window/app title contains '{allowed_window}'). "
        "Do not click into other apps, and never use destructive controls (delete, close-without-save).\n"
        "- Be precise with click coordinates.\n"
        "- When the goal is complete, STOP calling tools and reply with a short confirmation. "
        "If you were asked to read text, include the exact text you read."
    )


def make_backend(system_prompt, ex):
    if CU_BACKEND == "claude":
        return ClaudeBackend(MODEL, system_prompt, ex.display_w, ex.display_h, THINKING, KEEP_IMAGES, ENABLE_ZOOM)
    if CU_BACKEND == "uitars":
        return UITarsBackend(
            base_url=os.getenv("UITARS_BASE_URL", "http://localhost:8000/v1"),
            model=os.getenv("UITARS_MODEL", "ui-tars"),
            system_prompt=system_prompt, display_w=ex.display_w, display_h=ex.display_h)
    raise SystemExit(f"unknown CU_BACKEND={CU_BACKEND!r} (use 'claude' or 'uitars')")


def run_session(goal, log=print, confirm_fn=None, stop_fn=None):
    """Run one agent session. Shared by the CLI and the GUI.
    log(msg): emit a line. confirm_fn(action)->bool: used in 'confirm' mode (else stdin).
    stop_fn()->bool: checked between steps to abort early."""
    ex = Executor(max_width=MAX_WIDTH)
    safety = Safety(mode=MODE, allowed_window=ALLOWED_WINDOW, log_fn=log, confirm_fn=confirm_fn)
    backend = make_backend(build_system_prompt(ALLOWED_WINDOW), ex)

    log(f"backend={CU_BACKEND}  model={MODEL if CU_BACKEND == 'claude' else os.getenv('UITARS_MODEL')}  "
        f"target={TARGET_OS}  mode={MODE}  scope='{ALLOWED_WINDOW}'  zoom={ENABLE_ZOOM}  "
        f"display={ex.display_w}x{ex.display_h} (real {ex.real_w}x{ex.real_h})")
    log(f"goal: {goal}")

    backend.start(goal)
    for step in range(MAX_STEPS):
        if stop_fn and stop_fn():
            log("[stopped by user]"); return
        result = backend.step()
        if result.text:
            log(f"agent: {result.text}")
        if not result.actions:
            log(f"[done] {result.usage}"); return

        observations = []
        for act in result.actions:
            a = act.input
            log(f"  step {step + 1}: {a.get('action')} {a.get('coordinate', '')}")
            if stop_fn and stop_fn():
                log("[stopped by user]"); return
            if not safety.allow(a):
                observations.append(Observation(act.id, error="Blocked by safety policy."))
                continue
            try:
                img_b64, _, _ = ex.run(a)
                observations.append(Observation(act.id, image_b64=img_b64))
                safety.record(a, "ok")
            except Exception as e:
                log(f"    action error: {e}")
                observations.append(Observation(act.id, error=f"action error: {e}"))
                safety.record(a, f"error:{e}")

        backend.observe(observations)
    log(f"[stopped] hit CU_MAX_STEPS={MAX_STEPS} without finishing.")


def main():
    if len(sys.argv) < 2:
        print('Usage: python agent.py "your goal here"   (or the GUI:  python app_gui.py)')
        sys.exit(1)
    print("(kill-switch: slam the mouse into a screen corner to abort)\n")
    run_session(sys.argv[1])   # CLI: log=print, confirm via stdin


if __name__ == "__main__":
    main()
