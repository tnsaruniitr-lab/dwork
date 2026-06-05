"""
agent.py — the screenshot -> Claude -> action loop.

Usage (on the Windows box, with the target app already open & focused):
    python agent.py "Open notes.txt on the desktop and read it back to me."

Pipeline:  goal -> Claude (computer tool) -> action -> execute on Windows ->
           fresh screenshot -> back to Claude -> ... until Claude stops calling tools.

Claude only PROPOSES actions; safety.py + executor.py decide and act. Patient data
in screenshots goes to the model — for nCara, run Claude via Bedrock (Frankfurt) + DPA.
"""
import os
import sys
import time

import anthropic
from dotenv import load_dotenv

from executor import Executor
from safety import Safety

load_dotenv()

MODEL = os.getenv("CU_MODEL", "claude-opus-4-8")
MODE = os.getenv("CU_MODE", "confirm")
ALLOWED_WINDOW = os.getenv("CU_ALLOWED_WINDOW", "")
MAX_STEPS = int(os.getenv("CU_MAX_STEPS", "40"))
MAX_WIDTH = int(os.getenv("CU_MAX_WIDTH", "1280"))
KEEP_IMAGES = int(os.getenv("CU_KEEP_IMAGES", "3"))
THINKING = os.getenv("CU_THINKING", "adaptive").lower()

# Computer-use beta pairing for Opus 4.8 / 4.7 / 4.6 / Sonnet 4.6 / Opus 4.5.
# (For Sonnet 4.5 / Haiku 4.5 / Opus 4.1 use "computer-use-2025-01-24" + "computer_20250124".)
COMPUTER_BETA = "computer-use-2025-11-24"
COMPUTER_TOOL_TYPE = "computer_20251124"

SYSTEM_PROMPT = (
    "You control a Windows desktop through the `computer` tool. The user gives you a goal; "
    "accomplish it by taking screenshots and issuing mouse/keyboard actions.\n"
    "- Start by taking a screenshot to see the current screen.\n"
    "- After EACH action a fresh screenshot is returned. Verify the result before the next "
    "step; if a click missed or the screen isn't what you expected, correct it.\n"
    f"- Work ONLY inside the target application (window title contains '{ALLOWED_WINDOW}'). "
    "Do not click into other apps, and never use destructive controls (delete, close-without-save).\n"
    "- Be precise with click coordinates.\n"
    "- When the goal is complete, STOP calling tools and reply with a short confirmation. "
    "If you were asked to read text, include the exact text you read."
)


def call_with_backoff(client, **kwargs):
    """Thin retry on top of the SDK's own retries, for transient errors."""
    delay = 2.0
    for attempt in range(5):
        try:
            return client.beta.messages.create(**kwargs)
        except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError) as e:
            if attempt == 4:
                raise
            print(f"  [retry {attempt + 1}] {type(e).__name__}; waiting {delay:.0f}s")
            time.sleep(delay)
            delay *= 2


def prune_images(messages, keep: int):
    """Keep only the last `keep` screenshots in full; replace older image tool_results
    with a short text stub so the conversation's token cost stays bounded."""
    img_positions = []
    for mi, msg in enumerate(messages):
        if msg["role"] != "user" or not isinstance(msg.get("content"), list):
            continue
        for bi, block in enumerate(msg["content"]):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                content = block.get("content")
                if isinstance(content, list) and content and content[0].get("type") == "image":
                    img_positions.append((mi, bi))
    for mi, bi in img_positions[:-keep] if keep > 0 else img_positions:
        messages[mi]["content"][bi]["content"] = "[older screenshot omitted to save tokens]"


def main():
    if len(sys.argv) < 2:
        print('Usage: python agent.py "your goal here"')
        sys.exit(1)
    goal = sys.argv[1]

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    ex = Executor(max_width=MAX_WIDTH)
    safety = Safety(mode=MODE, allowed_window=ALLOWED_WINDOW)

    print(f"model={MODEL}  mode={MODE}  scope='{ALLOWED_WINDOW}'  "
          f"display={ex.display_w}x{ex.display_h} (real {ex.real_w}x{ex.real_h})")
    print(f"goal: {goal}\n(kill-switch: slam the mouse into a screen corner to abort)\n")

    tool = {
        "type": COMPUTER_TOOL_TYPE, "name": "computer",
        "display_width_px": ex.display_w, "display_height_px": ex.display_h, "display_number": 1,
    }
    # cache_control on the system block caches tools + system together (tools render first).
    system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
    messages = [{"role": "user", "content": goal}]

    for step in range(MAX_STEPS):
        kwargs = dict(
            model=MODEL, max_tokens=8192, system=system,
            tools=[tool], betas=[COMPUTER_BETA], messages=messages,
        )
        if THINKING == "adaptive":
            kwargs["thinking"] = {"type": "adaptive"}

        resp = call_with_backoff(client, **kwargs)
        messages.append({"role": "assistant", "content": resp.content})

        for b in resp.content:
            if b.type == "text" and b.text.strip():
                print(f"Claude: {b.text.strip()}")

        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            print("\n[done] Claude stopped calling tools.")
            u = resp.usage
            print(f"usage: in={u.input_tokens} out={u.output_tokens} "
                  f"cache_read={getattr(u, 'cache_read_input_tokens', 0)}")
            break

        results = []
        for tu in tool_uses:
            action = tu.input
            print(f"  step {step + 1}: {action.get('action')} {action.get('coordinate', '')}")
            if not safety.allow(action):
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": "Blocked by safety policy.", "is_error": True})
                continue
            try:
                png_b64, _, _ = ex.run(action)
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png_b64}}
                ]})
                safety.record(action, "ok")
            except Exception as e:
                print(f"    action error: {e}")
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": f"action error: {e}", "is_error": True})
                safety.record(action, f"error:{e}")

        messages.append({"role": "user", "content": results})
        prune_images(messages, keep=KEEP_IMAGES)
    else:
        print(f"\n[stopped] hit CU_MAX_STEPS={MAX_STEPS} without finishing.")


if __name__ == "__main__":
    main()
