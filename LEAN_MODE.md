# Lean Mode — in-chat desktop loop (locked in 2026-06-06)

Goal: make the me-as-brain (in-chat) desktop loop fast by minimizing what enters
the conversation context. Model for this run: **Sonnet 4.6** (matches prod loop).

## Rules (always)
1. **No vision unless required.** If the target is addressable by name/path/command
   (`open`, `osascript`, AppleScript, `mdfind`), do that — don't screenshot to hunt.
2. **Lean perception.** When a screenshot is needed: downscale to ≤800px wide (or
   crop to the region), Read that ONE image. Never full-res + zoom + file.
3. **Text over pixels.** Push locating/finding into scripts that print short text
   (coordinates, the value, yes/no). Pixels enter chat only to read real content.
4. **No big dumps.** Always filter shell output (`grep`/`head`/`wc`). Never paste long lists.
5. **Terse turns.** Short replies during execution; explain only at decision points.
6. **Batch** independent ops into one tool block. Keep cadence tight (stay in the ~5-min cache window).

## Why chat ≠ the dedicated dwork loop (irreducible gaps)
- Chat always carries the big **fixed context** (system prompt + memory + tool list +
  transcript). Cached ~5 min, but expires on pauses; the dedicated loop carries ~none.
- Chat **can't prune** old images/turns the way dwork prunes to last 3 — so chat
  degrades on long tasks; the loop stays flat.
- Model is set per-session (we set 4.6 to match).

=> Lean-chat is a **conservative** simulation: faithful per-step feel on SHORT tasks,
   and a pessimistic floor — if it's fast enough here, the real loop is faster.
   Don't extrapolate a 15–20-step marathon from chat.
