#!/usr/bin/env python3
"""
app_web.py — browser-based GUI for dwork (replaces Tkinter app_gui.py).

    python app_web.py
    open http://localhost:5050

Type a goal, pick a mode, hit Run. Logs stream live. In confirm mode the
browser pops a window.confirm() dialog before each action. Stop button
kills the session mid-run. Config (model, key, target OS) from .env.
"""
import os
import queue
import threading

from flask import Flask, Response, jsonify, render_template_string, request

import agent

app = Flask(__name__)

# ── session state (one run at a time) ──────────────────────────────────────
_log_q: queue.Queue = queue.Queue()
_confirm_q: queue.Queue = queue.Queue()   # str action → waiting for bool reply
_confirm_reply: queue.Queue = queue.Queue()
_stop_event = threading.Event()
_running = threading.Event()

# ── HTML ───────────────────────────────────────────────────────────────────
PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>dwork — desktop agent</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, sans-serif; background: #1a1a1a; color: #e0e0e0;
         margin: 0; padding: 20px; }
  h2 { margin: 0 0 16px; font-size: 18px; color: #fff; }
  textarea { width: 100%; height: 90px; background: #2a2a2a; color: #e0e0e0;
             border: 1px solid #444; border-radius: 6px; padding: 10px;
             font-size: 14px; resize: vertical; }
  .row { display: flex; gap: 10px; align-items: center; margin: 10px 0; }
  select { background: #2a2a2a; color: #e0e0e0; border: 1px solid #444;
           border-radius: 6px; padding: 6px 10px; font-size: 14px; }
  button { padding: 8px 20px; border: none; border-radius: 6px; font-size: 14px;
           cursor: pointer; font-weight: 600; }
  #runBtn  { background: #2ea84f; color: #fff; }
  #stopBtn { background: #c0392b; color: #fff; }
  button:disabled { opacity: 0.4; cursor: default; }
  #log { margin-top: 14px; background: #111; border: 1px solid #333;
         border-radius: 6px; padding: 12px; height: 420px; overflow-y: auto;
         font-family: Menlo, monospace; font-size: 12px; white-space: pre-wrap;
         word-break: break-word; }
  .info  { color: #aaa; }
  .agent { color: #7ec8e3; }
  .step  { color: #f0c040; }
  .ok    { color: #2ea84f; }
  .err   { color: #e74c3c; }
  .meta  { color: #888; font-size: 11px; }
</style>
</head>
<body>
<h2>dwork — desktop agent</h2>

<textarea id="goal" placeholder="What should I do?">Open the Controlling module in nCara and take a screenshot.</textarea>

<div class="row">
  <label>Mode:</label>
  <select id="mode">
    <option value="confirm">confirm</option>
    <option value="dryrun">dryrun</option>
    <option value="auto">auto</option>
  </select>
  <button id="runBtn"  onclick="startRun()">Run ▶</button>
  <button id="stopBtn" onclick="stopRun()" disabled>Stop ■</button>
  <span class="meta" id="meta"></span>
</div>

<div id="log"></div>

<script>
let es = null;

function log(text, cls) {
  const d = document.getElementById('log');
  const line = document.createElement('div');
  if (cls) line.className = cls;
  line.textContent = text;
  d.appendChild(line);
  d.scrollTop = d.scrollHeight;
}

function setButtons(running) {
  document.getElementById('runBtn').disabled  =  running;
  document.getElementById('stopBtn').disabled = !running;
}

async function startRun() {
  const goal = document.getElementById('goal').value.trim();
  const mode = document.getElementById('mode').value;
  if (!goal) return;
  document.getElementById('log').innerHTML = '';
  document.getElementById('meta').textContent = '';
  setButtons(true);

  await fetch('/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({goal, mode})
  });

  es = new EventSource('/stream');
  es.onmessage = async (e) => {
    const msg = JSON.parse(e.data);

    if (msg.type === 'log') {
      const cls = msg.text.startsWith('agent:') ? 'agent'
                : msg.text.startsWith('  step') ? 'step'
                : msg.text.startsWith('[done]') || msg.text.startsWith('[finished]') ? 'ok'
                : msg.text.startsWith('[error]') || msg.text.startsWith('[stopped') ? 'err'
                : 'info';
      log(msg.text, cls);
    }

    else if (msg.type === 'confirm') {
      const ok = window.confirm('Run this action?\\n\\n' + msg.action);
      await fetch('/confirm', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ok})
      });
    }

    else if (msg.type === 'done') {
      es.close(); es = null;
      setButtons(false);
      document.getElementById('meta').textContent = msg.text || '';
    }
  };
  es.onerror = () => { es && es.close(); setButtons(false); };
}

async function stopRun() {
  await fetch('/stop', {method: 'POST'});
  log('[stopping…]', 'err');
}
</script>
</body>
</html>"""


# ── routes ─────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(PAGE)


@app.route("/start", methods=["POST"])
def start():
    if _running.is_set():
        return jsonify({"error": "already running"}), 409

    data = request.get_json()
    goal = data.get("goal", "")
    mode = data.get("mode", "confirm")

    # clear queues
    for q in (_log_q, _confirm_q, _confirm_reply):
        while not q.empty():
            try: q.get_nowait()
            except queue.Empty: break

    _stop_event.clear()
    _running.set()
    agent.MODE = mode

    def log_fn(msg):
        _log_q.put(("log", str(msg)))

    def confirm_fn(action):
        if mode != "confirm":
            return True
        _log_q.put(("confirm", str(action)))   # signal SSE to ask browser
        _confirm_q.put(str(action))             # unblock SSE sender
        reply = _confirm_reply.get()            # wait for browser answer
        return reply

    def work():
        try:
            agent.run_session(goal, log=log_fn, confirm_fn=confirm_fn,
                              stop_fn=_stop_event.is_set)
        except Exception as e:
            _log_q.put(("log", f"[error] {type(e).__name__}: {e}"))
        finally:
            _log_q.put(("done", ""))
            _running.clear()

    threading.Thread(target=work, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/stream")
def stream():
    """SSE endpoint — browser listens here for log lines and confirm requests."""
    def generate():
        pending_confirm = None
        while True:
            try:
                kind, text = _log_q.get(timeout=30)
            except queue.Empty:
                yield "data: {\"type\":\"ping\"}\n\n"
                continue

            if kind == "done":
                yield f'data: {{"type":"done","text":""}}\n\n'
                return

            if kind == "confirm":
                # Send confirm event; worker is already blocked on _confirm_reply
                import json
                yield f'data: {json.dumps({"type":"confirm","action":text})}\n\n'
            else:
                import json
                yield f'data: {json.dumps({"type":"log","text":text})}\n\n'

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/confirm", methods=["POST"])
def confirm():
    data = request.get_json()
    _confirm_reply.put(bool(data.get("ok", False)))
    return jsonify({"ok": True})


@app.route("/stop", methods=["POST"])
def stop():
    _stop_event.set()
    return jsonify({"ok": True})


# ── main ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import webbrowser, time
    port = 5050
    print(f"dwork web UI → http://localhost:{port}")
    print("(kill-switch: slam the mouse into a screen corner)")
    threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
