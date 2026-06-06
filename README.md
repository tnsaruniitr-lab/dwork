# dwork — Claude computer-use harness (Windows)

A small, safety-gated agent that drives a Windows desktop app with **Claude's computer-use
tool**: it screenshots the screen, asks Claude for the next mouse/keyboard action, executes it,
screenshots again, and loops until the task is done.

Built to validate the loop on **Notepad** first, then point the same harness at **nCara**.

```
goal ─► Claude (computer tool) ─► action ─► executor (real click/type) ─► screenshot ─► … ─► done
                                    ▲                                                   │
                                    └──────────────── safety gate ◄─────────────────────┘
```

Claude only *proposes* actions — `safety.py` decides and `executor.py` acts. Nothing runs
unless your code runs it.

## Files
- `agent.py` — the loop: Claude call, tool_use → execute → tool_result, prompt caching, image pruning, retries.
- `executor.py` — screen capture + **coordinate scaling** + action execution (pyautogui), Windows DPI-aware, clipboard-paste typing for Unicode.
- `safety.py` — scope-lock (act only when the right window is foreground), dryrun/confirm/auto modes, JSONL audit log.
- `.env.example` — config. Copy to `.env`.

## Setup (on the Windows box)
```bat
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```
Edit `.env`: set `ANTHROPIC_API_KEY`, keep `CU_MODE=confirm` and `CU_ALLOWED_WINDOW=Notepad` for the first run.

## GUI — type a prompt, watch it run
```bash
python3 app_gui.py
```
A window with a prompt box, a **Mode** selector (dryrun / confirm / auto), **Run** / **Stop**, and a
live log pane. Type an instruction → Run. In `confirm` mode a Yes/No dialog pops before each action;
mouse-to-a-screen-corner is the hard kill-switch. Model / scope / target / key all come from `.env`
(same config as the CLI). Tkinter ships with standard Python — nothing extra to install.

## Run the Notepad test (CLI)
1. Put a `notes.txt` with a couple of sentences on the Desktop and **open it in Notepad**.
2. With Notepad focused:
   ```bat
   python agent.py "Read the contents of the open Notepad file and tell me exactly what it says."
   ```
3. In `confirm` mode it prints each planned action and waits for `y`. Watch it work.

Tip: start in `CU_MODE=dryrun` to see the plan with **zero** execution, then switch to `confirm`, then `auto`.

## macOS (drive a browser)
The same harness runs on macOS — the code auto-detects the OS and handles the Mac
differences: **Retina coordinate scaling**, **⌘V paste**, **⌘ shortcuts**, and
**frontmost-app scope-lock**.

**Grant permissions first** (one-time): System Settings → Privacy & Security →
- **Accessibility** → enable your terminal (Terminal / iTerm) or the Python app — needed to move/click/type
- **Screen Recording** → same app — needed to capture screenshots

Then, e.g. to drive Chrome (set `CU_ALLOWED_WINDOW=Google Chrome` in `.env`):
```bash
python3 agent.py "Open a new tab and search Google for the weather in Berlin."
```
Run on a single display at default scaling for the cleanest first run.

## Phase 1 — drive a Windows box (nCara) from your Mac via TeamViewer
Test the agent against nCara **without installing anything on the Windows box**: run dwork on your
Mac and let it drive the **TeamViewer window** — clicks/keys/screenshots happen on the Mac and
TeamViewer relays them to Windows. It's **vision-only** (the deterministic export macro still runs
on Windows) and accuracy is lower through TeamViewer's compressed stream, but it's a fast way to
prove the loop against nCara.

`.env`:
```
CU_TARGET_OS=windows           # send Windows-style keys + relay-safe typing
CU_ALLOWED_WINDOW=TeamViewer   # scope-lock to the TeamViewer app (macOS frontmost-app match)
CU_MAX_WIDTH=1440              # bigger image → nCara's tiny text is more legible
CU_MODE=confirm
# CU_ENABLE_ZOOM=true          # optional: let Claude zoom into tiny labels
```
Then: full-screen the TeamViewer window (scaling = **Original / 1:1**), nCara showing; grant Terminal
**Accessibility + Screen Recording**; run a **big-target** goal first:
```bash
python3 agent.py "In the nCara window, open the Controlling module and take a screenshot."
```
Limits: pixels only (no handle control), slower + shakier on tiny targets, **ASCII-only typing**
through the relay (German text needs the on-box run). Patient-data screenshots still go to the
cloud — keep Phase-1 tests on **non-patient screens**.

## Pluggable brain (cloud Claude ↔ local UI-TARS)
`CU_BACKEND` selects what decides the actions; the loop, `executor.py`, and `safety.py` are unchanged either way.
- **`claude`** (default) — Anthropic computer-use tool. **Screenshots go to Anthropic's cloud.** Fine for dummy data; use **Bedrock-EU + DPA** for patient data.
- **`uitars`** — a local **UI-TARS** model over an OpenAI-compatible endpoint. **Screenshots stay on your machine.** This is a documented **stub** in `backends.py`; wire it up once you stand up a UI-TARS server (needs a GPU).

There is **no local *Claude*** — Claude always runs in the cloud. True on-device inference (nothing leaves the box) means **UI-TARS, not Claude**.

## Key facts baked in
- Tool `computer_20251124` + beta header `computer-use-2025-11-24` — the pairing for **Opus 4.8 / 4.7 / 4.6 / Sonnet 4.6 / Opus 4.5**. (For Sonnet 4.5 / Haiku 4.5 / Opus 4.1, switch both to the `...2025-01-24` variants in `agent.py`.)
- Model defaults to `claude-opus-4-8`. **Sonnet 4.6 is a cheaper swap** for this high-volume loop and uses the *same* tool/beta — just set `CU_MODEL=claude-sonnet-4-6`.
- **Coordinate scaling** (the #1 thing that breaks these): we downscale the screenshot to `CU_MAX_WIDTH` (≤1280), declare *those* dims to the model, and scale the model's coordinates back to real pixels. Declared dims must match the image sent — they do, by construction.
- **Prompt caching** on system + tool defs (stable prefix) via `cache_control`.
- **Token control**: only the last `CU_KEEP_IMAGES` screenshots are kept in full; older ones are pruned to a text stub.

## Safety
- `CU_MODE`: `dryrun` (no execution) · `confirm` (y/N each action) · `auto`.
- **Scope-lock**: actions are refused unless the foreground window title contains `CU_ALLOWED_WINDOW`. Screenshots are always allowed (read-only).
- **Kill-switch**: slam the mouse into any screen corner (pyautogui FAILSAFE) to abort instantly.
- The harness only references navigation/click/type actions — there is no code path that does anything destructive on its own; an unrecognized action is refused, not improvised.

## Gotchas (already handled, noted so you know)
- **DPI**: `executor.py` sets per-monitor DPI awareness so coordinates are physical pixels. Run the display at 100% scaling for the cleanest first run.
- **German Unicode** (ä/ö/ü/ß): `type` uses clipboard-paste, not keystroke typing, which pyautogui mangles.
- **Single monitor** recommended for the MVP (multi-monitor offsets are handled but untested).

## nCara later (patient data → GDPR)
Point it at nCara by setting `CU_ALLOWED_WINDOW=nCara` and giving a goal. **But**: every
screenshot then contains patient data and is sent to the model. For that, run Claude via
**AWS Bedrock (Frankfurt) under a DPA + zero-retention**, not the default direct API. Until
then, only test against dummy data (Notepad).
