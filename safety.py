"""
safety.py — the gate every action passes through before it touches the machine.

Three things:
  1. scope-lock  : on Windows, only act when the FOREGROUND window title contains
                   the allowed substring (so the agent can never act on the wrong app).
  2. mode        : dryrun (execute nothing) / confirm (ask y/N) / auto (just do it).
  3. logger      : JSONL audit trail of every intended + executed action.

The kill-switch (mouse to a screen corner) lives in executor via pyautogui.FAILSAFE.
"""
import json
import os
import platform
import time


def foreground_window_title() -> str:
    sysname = platform.system()
    if sysname == "Darwin":
        import subprocess
        try:
            out = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get name of first application '
                 'process whose frontmost is true'],
                capture_output=True, text=True, timeout=3)
            return (out.stdout or "").strip()   # e.g. "Google Chrome"
        except Exception:
            return ""
    if sysname != "Windows":
        return ""  # scope-lock no-op on other platforms
    import ctypes
    u = ctypes.windll.user32
    hwnd = u.GetForegroundWindow()
    length = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    u.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value or ""


class Safety:
    def __init__(self, mode: str = "confirm", allowed_window: str = "", log_path: str = "logs/actions.jsonl",
                 log_fn=print, confirm_fn=None):
        self.mode = (mode or "confirm").lower()
        self.allowed_window = (allowed_window or "").lower()
        self.log_fn = log_fn              # how to emit a line (print, or a GUI append)
        self.confirm_fn = confirm_fn      # confirm(action)->bool for 'confirm' mode (else stdin)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self._log = open(log_path, "a", encoding="utf-8")

    def _in_scope(self) -> bool:
        if not self.allowed_window:
            return True
        return self.allowed_window in foreground_window_title().lower()

    def allow(self, action: dict) -> bool:
        a = action.get("action", "?")
        # these are local/read-only — exempt from window scope-lock
        if a not in ("screenshot", "bash", "open_image", "find_text", "wait_for_screen") \
                and not self._in_scope():
            self.log_fn(f"  [BLOCKED] foreground app/window is not '{self.allowed_window}' — refusing {a}")
            self.record(action, "blocked-out-of-scope")
            return False
        if self.mode == "dryrun":
            self.log_fn(f"  [DRYRUN] would run: {action}")
            self.record(action, "dryrun")
            return False
        if self.mode == "confirm":
            ok = self.confirm_fn(action) if self.confirm_fn else (
                input(f"  run {action} ? [y/N] ").strip().lower() == "y")
            if not ok:
                self.record(action, "declined")
                return False
        return True

    def record(self, action: dict, result: str):
        self._log.write(json.dumps({"t": time.time(), "action": action, "result": result}) + "\n")
        self._log.flush()
