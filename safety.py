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
    if platform.system() != "Windows":
        return ""  # scope-lock is a no-op off Windows (dev only)
    import ctypes
    u = ctypes.windll.user32
    hwnd = u.GetForegroundWindow()
    length = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    u.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value or ""


class Safety:
    def __init__(self, mode: str = "confirm", allowed_window: str = "", log_path: str = "logs/actions.jsonl"):
        self.mode = (mode or "confirm").lower()
        self.allowed_window = (allowed_window or "").lower()
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self._log = open(log_path, "a", encoding="utf-8")

    def _in_scope(self) -> bool:
        if not self.allowed_window:
            return True
        return self.allowed_window in foreground_window_title().lower()

    def allow(self, action: dict) -> bool:
        a = action.get("action", "?")
        # screenshot is always safe (read-only); everything else is scope-locked
        if a != "screenshot" and not self._in_scope():
            print(f"  [BLOCKED] foreground window is not '{self.allowed_window}' — refusing {a}")
            self.record(action, "blocked-out-of-scope")
            return False
        if self.mode == "dryrun":
            print(f"  [DRYRUN] would run: {action}")
            self.record(action, "dryrun")
            return False
        if self.mode == "confirm":
            ans = input(f"  run {action} ? [y/N] ").strip().lower()
            if ans != "y":
                self.record(action, "declined")
                return False
        return True

    def record(self, action: dict, result: str):
        self._log.write(json.dumps({"t": time.time(), "action": action, "result": result}) + "\n")
        self._log.flush()
