#!/usr/bin/env python3
"""
app_gui.py — a tiny window: type a prompt, hit Run, watch the agent work.

    python app_gui.py

Reuses agent.run_session(). The agent runs in a background thread so the window
stays responsive; logs stream into the pane; in 'confirm' mode a Yes/No dialog
pops before each action. Mouse-to-a-screen-corner is the hard kill-switch.

Config (model, target OS, scope, max width, key) comes from .env — same as the CLI.
Tkinter ships with standard Python; no extra install.
"""
import os
import queue
import threading
import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk

import agent   # run_session() + the env-derived config it reads


class App:
    def __init__(self, root):
        self.root = root
        root.title("dwork — desktop agent")
        root.geometry("760x560")

        tk.Label(root, text="What should I do?", anchor="w").pack(fill="x", padx=8, pady=(8, 0))
        self.prompt = tk.Text(root, height=3, wrap="word")
        self.prompt.pack(fill="x", padx=8)
        self.prompt.insert("1.0", "Open notes.txt on the Desktop and tell me exactly what it says.")

        row = tk.Frame(root)
        row.pack(fill="x", padx=8, pady=6)
        tk.Label(row, text="Mode:").pack(side="left")
        self.mode = tk.StringVar(value=os.getenv("CU_MODE", "confirm"))
        ttk.Combobox(row, textvariable=self.mode, values=["dryrun", "confirm", "auto"],
                     width=9, state="readonly").pack(side="left", padx=(2, 12))
        self.run_btn = tk.Button(row, text="Run ▶", command=self.on_run)
        self.run_btn.pack(side="left")
        self.stop_btn = tk.Button(row, text="Stop ■", command=self.on_stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        tk.Label(row, text=f"scope='{os.getenv('CU_ALLOWED_WINDOW', '')}'  target={os.getenv('CU_TARGET_OS', 'auto')}",
                 fg="#666").pack(side="right")

        self.log = scrolledtext.ScrolledText(root, state="disabled", wrap="word", font=("Menlo", 11))
        self.log.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.q = queue.Queue()              # ("log", text) | ("confirm", action, event, holder)
        self.stop_event = threading.Event()
        self.root.after(80, self._drain)

    # ---- main-thread UI helpers ----
    def _append(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _drain(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "log":
                    self._append(item[1])
                elif item[0] == "confirm":
                    _, action, ev, holder = item
                    holder["ok"] = messagebox.askyesno("Confirm action", f"Run this action?\n\n{action}")
                    ev.set()
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    # ---- thread-safe callbacks handed to the worker ----
    def _log_ts(self, msg):
        self.q.put(("log", str(msg)))

    def _confirm_ts(self, action):
        ev, holder = threading.Event(), {}
        self.q.put(("confirm", action, ev, holder))
        ev.wait()                            # blocks the worker until the dialog is answered
        return holder.get("ok", False)

    # ---- buttons ----
    def on_run(self):
        goal = self.prompt.get("1.0", "end").strip()
        if not goal:
            return
        agent.MODE = self.mode.get()         # run_session reads this module global
        self.stop_event.clear()
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._append(f"\n=== {goal} ===")
        confirm = self._confirm_ts if self.mode.get() == "confirm" else None

        def work():
            try:
                agent.run_session(goal, log=self._log_ts, confirm_fn=confirm,
                                  stop_fn=self.stop_event.is_set)
            except Exception as e:
                self._log_ts(f"[error] {type(e).__name__}: {e}")
            finally:
                self._log_ts("[finished]")
                self.root.after(0, self._done)

        threading.Thread(target=work, daemon=True).start()

    def on_stop(self):
        self.stop_event.set()
        self._append("[stopping after the current step…]")

    def _done(self):
        self.run_btn.config(state="normal")
        self.stop_btn.config(state="disabled")


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
