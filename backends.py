"""
backends.py — the pluggable "brain" for the computer-use loop.

agent.py is backend-agnostic. A backend owns the model conversation and, each step,
returns the model's text + a list of actions (in executor's schema); it then receives
the resulting screenshots via observe().

  ClaudeBackend  — Anthropic API computer-use tool (CLOUD). Implemented & default.
  UITarsBackend  — local UI-TARS via an OpenAI-compatible endpoint (screenshots stay
                   on your machine). STUB — fill in when you stand up UI-TARS.

Switching backends never touches executor.py or safety.py.
"""
import time
from dataclasses import dataclass, field


@dataclass
class Action:
    id: str
    input: dict          # executor schema: {"action": "left_click", "coordinate": [x, y], ...}


@dataclass
class StepResult:
    text: str = ""
    actions: list = field(default_factory=list)   # empty => the model is done
    usage: dict = field(default_factory=dict)


@dataclass
class Observation:
    action_id: str
    image_b64: str = None    # screenshot taken after a vision action
    text: str = None         # stdout returned from a bash action
    error: str = None        # set instead of image/text if blocked / failed


class ClaudeBackend:
    """Anthropic computer-use tool. Pairing below is for Opus 4.8 / 4.7 / 4.6 / Sonnet 4.6."""
    BETA = "computer-use-2025-11-24"
    TOOL_TYPE = "computer_20251124"

    def __init__(self, model, system_prompt, display_w, display_h, thinking="adaptive",
                 keep_images=3, enable_zoom=False):
        import anthropic
        self._anthropic = anthropic
        self.client = anthropic.Anthropic()      # reads ANTHROPIC_API_KEY
        self.model = model
        self.thinking = thinking
        self.keep_images = keep_images
        self.computer_tool = {
            "type": self.TOOL_TYPE, "name": "computer",
            "display_width_px": display_w, "display_height_px": display_h, "display_number": 1
        }
        if enable_zoom:
            self.computer_tool["enable_zoom"] = True
        # bash tool — lets the agent run local shell commands instead of navigating by vision.
        self.bash_tool = {
            "name": "bash",
            "description": (
                "Run a shell command on the local machine (macOS). "
                "PREFER this over clicking/vision whenever you know the path or app name. "
                "Use Python one-liners (python3 -c) to handle filenames with spaces/special chars. "
                "Return value is stdout+stderr."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"}
                },
                "required": ["command"]
            }
        }
        # find_text tool — macOS Vision OCR to locate a label on screen by text, not coordinate.
        # Critical for nCara/TeamViewer where coordinates are fragile.
        self.find_text_tool = {
            "name": "find_text",
            "description": (
                "Find a text label on the current screen using OCR and return its pixel "
                "coordinates for clicking. Use this instead of guessing coordinates — "
                "works even through TeamViewer compression. "
                "If found, returns x/y you can immediately use in a left_click computer action. "
                "If not found, returns nearby candidate labels so you can adapt."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "label": {"type": "string",
                              "description": "Text to find (partial match OK, case-insensitive). "
                                             "E.g. 'Kostenträgerblatt', 'Budget Übersicht', 'Export'"}
                },
                "required": ["label"]
            }
        }
        # wait_for_screen tool — waits until screen stops changing after a click.
        self.wait_for_screen_tool = {
            "name": "wait_for_screen",
            "description": (
                "Wait until the screen stops changing, then return a fresh screenshot. "
                "Use after every click that opens a new screen, dialog, or triggers loading. "
                "nCara is slow — always wait before the next click."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "timeout": {"type": "number",
                                "description": "Max seconds to wait (default 8, max 30)"}
                }
            }
        }
        # open_image tool — reads an image file from disk and returns it as pixels.
        # Eliminates the need to open Preview + screenshot + zoom just to read an image.
        self.open_image_tool = {
            "name": "open_image",
            "description": (
                "Read an image file from disk and view its contents directly — no Preview needed. "
                "Use this INSTEAD of opening the file in Preview whenever you need to read "
                "the contents of a .png, .jpg, .jpeg, .gif, .bmp or .webp file. "
                "Returns the image for you to read directly."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to the image file"}
                },
                "required": ["path"]
            }
        }
        # cache_control on the system block caches tools + system together (tools render first).
        self.system = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
        self.messages = []

    def start(self, goal):
        self.messages = [{"role": "user", "content": goal}]

    def step(self):
        kwargs = dict(model=self.model, max_tokens=8192, system=self.system,
                      tools=[self.computer_tool, self.bash_tool,
                             self.find_text_tool, self.wait_for_screen_tool,
                             self.open_image_tool],
                      betas=[self.BETA], messages=self.messages)
        if self.thinking == "adaptive":
            kwargs["thinking"] = {"type": "adaptive"}
        resp = self._call(kwargs)
        self.messages.append({"role": "assistant", "content": resp.content})
        text = " ".join(b.text.strip() for b in resp.content if b.type == "text" and b.text.strip())
        actions = []
        for b in resp.content:
            if b.type != "tool_use":
                continue
            if b.name == "bash":
                actions.append(Action(id=b.id, input={"action": "bash", "command": b.input.get("command", "")}))
            elif b.name == "open_image":
                actions.append(Action(id=b.id, input={"action": "open_image", "path": b.input.get("path", "")}))
            elif b.name == "find_text":
                actions.append(Action(id=b.id, input={"action": "find_text", "label": b.input.get("label", "")}))
            elif b.name == "wait_for_screen":
                actions.append(Action(id=b.id, input={"action": "wait_for_screen",
                                                       "timeout": b.input.get("timeout", 8)}))
            else:
                actions.append(Action(id=b.id, input=b.input))
        u = resp.usage
        usage = {"in": u.input_tokens, "out": u.output_tokens,
                 "cache_read": getattr(u, "cache_read_input_tokens", 0)}
        return StepResult(text=text, actions=actions, usage=usage)

    def observe(self, observations):
        results = []
        for o in observations:
            if o.error is not None:
                results.append({"type": "tool_result", "tool_use_id": o.action_id,
                                "content": o.error, "is_error": True})
            elif o.text is not None:
                # bash result — plain text back to the model
                results.append({"type": "tool_result", "tool_use_id": o.action_id,
                                "content": o.text})
            else:
                results.append({"type": "tool_result", "tool_use_id": o.action_id, "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": o.image_b64}}]})
        self.messages.append({"role": "user", "content": results})
        self._prune_images()

    def _prune_images(self):
        keep = self.keep_images
        positions = []
        for mi, msg in enumerate(self.messages):
            if msg["role"] != "user" or not isinstance(msg.get("content"), list):
                continue
            for bi, block in enumerate(msg["content"]):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    c = block.get("content")
                    if isinstance(c, list) and c and c[0].get("type") == "image":
                        positions.append((mi, bi))
        for mi, bi in (positions[:-keep] if keep > 0 else positions):
            self.messages[mi]["content"][bi]["content"] = "[older screenshot omitted to save tokens]"

    def _call(self, kwargs):
        delay = 2.0
        for attempt in range(5):
            try:
                return self.client.beta.messages.create(**kwargs)
            except (self._anthropic.RateLimitError, self._anthropic.InternalServerError,
                    self._anthropic.APIConnectionError) as e:
                if attempt == 4:
                    raise
                print(f"  [retry {attempt + 1}] {type(e).__name__}; waiting {delay:.0f}s")
                time.sleep(delay)
                delay *= 2


class UITarsBackend:
    """Local UI-TARS over an OpenAI-compatible endpoint — screenshots NEVER leave the box.

    STUB / seam. Same Action / StepResult / Observation contract as ClaudeBackend, so
    agent.py, executor.py and safety.py stay unchanged. To complete it:
      1. Point an OpenAI client at your UI-TARS server  (base_url=UITARS_BASE_URL).
      2. start():   seed the UI-TARS system/instruction prompt + the goal.
      3. step():    send the latest screenshot + short history; UI-TARS replies in ITS
                    action grammar (e.g. click(start_box='(x,y)'), type(content='...'),
                    hotkey, scroll, wait, finished()). Parse those into executor schema:
                      {"action":"left_click","coordinate":[x,y]} / {"action":"type","text":...}
                    NB: UI-TARS coords are usually normalised 0-1000 -> scale to display_w/h.
      4. observe(): append the returned screenshot(s) as the next user image.
    """
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "UI-TARS backend is a documented stub. Stand up a UI-TARS OpenAI-compatible "
            "endpoint, then implement start/step/observe (see this docstring + README). "
            "Until then use CU_BACKEND=claude.")
