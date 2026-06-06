"""
executor.py — turns Claude computer-tool actions into real Windows input.

Owns the ONE thing that breaks computer-use harnesses: coordinate scaling.
We capture the real screen, downscale it to <= CU_MAX_WIDTH, declare THOSE
dimensions to the model, and scale the model's returned coordinates back up to
real pixels before clicking. Get this wrong and every click lands offset.
"""
import base64
import io
import os
import platform
import time

import mss
from PIL import Image
import pyautogui
import pyperclip

# kill-switch: slam the mouse into a screen corner to abort; small pause between actions
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

# On Windows, become DPI-aware so pyautogui/mss coordinates are true physical pixels.
if platform.system() == "Windows":
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor-v2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# Host = OS this runs on. Target = OS being controlled (differ when driving a Windows box
# from a Mac via TeamViewer). CU_TARGET_OS: auto | windows | mac.
_HOST_MAC = platform.system() == "Darwin"
_T = os.getenv("CU_TARGET_OS", "auto").lower()
_TARGET_MAC = (_T == "mac") or (_T == "auto" and _HOST_MAC)
_VIA_RELAY = _HOST_MAC and not _TARGET_MAC      # Mac driving a Windows target (TeamViewer relay)
_PASTE_MOD = "command" if _TARGET_MAC else "ctrl"
_SUPER = "command" if _TARGET_MAC else "win"     # the ⌘ / Win / Super key (for the TARGET)

# Claude emits xdotool-style key names; map the common ones to pyautogui.
_KEYMAP = {
    "return": "enter", "kp_enter": "enter", "escape": "esc", "esc": "esc",
    "backspace": "backspace", "delete": "delete", "tab": "tab", "space": "space",
    "page_up": "pageup", "page_down": "pagedown", "home": "home", "end": "end",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "option": "alt", "shift": "shift",
    "super": _SUPER, "meta": _SUPER, "win": _SUPER, "cmd": "command", "command": "command",
}


def _key(name: str) -> str:
    n = name.strip().lower()
    if n in _KEYMAP:
        return _KEYMAP[n]
    return n  # letters, digits, f1..f12, etc. pass through


def _combo(text: str):
    return [_key(p) for p in str(text).replace(" ", "").split("+") if p]


class Executor:
    """Screen capture + scaling + action execution for one monitor."""

    def __init__(self, max_width: int = 1280, monitor_index: int = 1, post_action_delay: float = 0.4):
        self.post_action_delay = post_action_delay
        with mss.mss() as sct:
            mon = sct.monitors[monitor_index]            # 1 = primary
        self.mon = mon
        self.real_w, self.real_h = mon["width"], mon["height"]
        self.scale = min(1.0, max_width / self.real_w)   # downscale factor (<=1)
        self.display_w = round(self.real_w * self.scale) # what we DECLARE to the model
        self.display_h = round(self.real_h * self.scale)
        # macOS Retina: mss grabs PHYSICAL pixels, pyautogui clicks in LOGICAL points.
        # logical_width / physical_width ≈ 1.0 on Windows & non-Retina, 0.5 on Retina.
        try:
            self.point_scale = pyautogui.size()[0] / self.real_w
        except Exception:
            self.point_scale = 1.0

    # ---- coordinate mapping (declared/model space -> real pixels) ----
    def _to_real(self, coord):
        x, y = coord
        rx = (self.mon["left"] + x / self.scale) * self.point_scale
        ry = (self.mon["top"] + y / self.scale) * self.point_scale
        return round(rx), round(ry)

    # ---- capture: returns (base64 png, width, height) at declared dims ----
    def screenshot(self):
        with mss.mss() as sct:
            raw = sct.grab(self.mon)
        img = Image.frombytes("RGB", raw.size, raw.rgb)
        if self.scale < 1.0:
            img = img.resize((self.display_w, self.display_h), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.standard_b64encode(buf.getvalue()).decode("ascii"), self.display_w, self.display_h

    # ---- run one action, then return a fresh screenshot ----
    def run(self, action: dict):
        a = (action.get("action") or "").lower()
        if a != "screenshot":
            self._act(a, action)
            time.sleep(self.post_action_delay)
        return self.screenshot()

    def _focus_relay(self):
        """When driving Windows via TeamViewer:
        1. Bring TeamViewer app to front on Mac (fixes browser confirm stealing focus)
        2. Click the centre of the remote session to give the Windows app focus
           (without this, menu-bar clicks are ignored by Windows even though TeamViewer
           receives them — the mouseover highlights but the click doesn't register)
        """
        if not _VIA_RELAY:
            return
        import subprocess
        subprocess.run(
            ["osascript", "-e", 'tell application "TeamViewer" to activate'],
            capture_output=True, timeout=3)
        time.sleep(0.6)   # wait for TeamViewer to be truly front
        # click the centre of the screen — neutral area that focuses the Windows session
        # without triggering any nCara UI element
        cx = round(self.real_w * self.point_scale / 2)
        cy = round(self.real_h * self.point_scale / 2)
        pyautogui.click(cx, cy)
        time.sleep(0.4)   # let Windows process the focus click before the real click

    def _act(self, a: str, action: dict):
        coord = action.get("coordinate")
        text = action.get("text")
        mods = _combo(text) if (text and a in (
            "left_click", "right_click", "middle_click", "double_click", "triple_click", "scroll")) else []

        if a == "mouse_move":
            pyautogui.moveTo(*self._to_real(coord))

        elif a in ("left_click", "right_click", "middle_click", "double_click", "triple_click"):
            self._focus_relay()   # restore TeamViewer focus lost to confirm popup
            if coord:
                pyautogui.moveTo(*self._to_real(coord))
            button = "right" if a == "right_click" else "middle" if a == "middle_click" else "left"
            clicks = 2 if a == "double_click" else 3 if a == "triple_click" else 1
            if mods:
                for m in mods:
                    pyautogui.keyDown(m)
            try:
                pyautogui.click(button=button, clicks=clicks, interval=0.05)
            finally:
                for m in reversed(mods):
                    pyautogui.keyUp(m)

        elif a == "left_click_drag":
            start = action.get("start_coordinate")
            if start:
                pyautogui.moveTo(*self._to_real(start))
            pyautogui.dragTo(*self._to_real(coord), duration=0.3, button="left")

        elif a == "left_mouse_down":
            if coord:
                pyautogui.moveTo(*self._to_real(coord))
            pyautogui.mouseDown(button="left")

        elif a == "left_mouse_up":
            if coord:
                pyautogui.moveTo(*self._to_real(coord))
            pyautogui.mouseUp(button="left")

        elif a == "type":
            if _VIA_RELAY:
                # Through TeamViewer, type char-by-char (forwards reliably). ASCII only —
                # for German text, run on the Windows box (clipboard path below).
                pyautogui.write(str(text or ""), interval=0.03)
            else:
                # clipboard-paste = reliable Unicode (German ä/ö/ü/ß), unlike pyautogui.write
                pyperclip.copy(str(text or ""))
                pyautogui.hotkey(_PASTE_MOD, "v")

        elif a == "key":
            keys = _combo(text)
            if len(keys) == 1:
                pyautogui.press(keys[0])
            elif keys:
                pyautogui.hotkey(*keys)

        elif a == "hold_key":
            duration = float(action.get("duration", 1.0))
            keys = _combo(text)
            for k in keys:
                pyautogui.keyDown(k)
            time.sleep(duration)
            for k in reversed(keys):
                pyautogui.keyUp(k)

        elif a == "scroll":
            if coord:
                pyautogui.moveTo(*self._to_real(coord))
            direction = (action.get("scroll_direction") or "down").lower()
            amount = int(action.get("scroll_amount", 3))
            if mods:
                for m in mods:
                    pyautogui.keyDown(m)
            try:
                if direction in ("up", "down"):
                    pyautogui.scroll(amount * 100 * (1 if direction == "up" else -1))
                else:  # left/right
                    pyautogui.hscroll(amount * 100 * (1 if direction == "right" else -1))
            finally:
                for m in reversed(mods):
                    pyautogui.keyUp(m)

        elif a == "wait":
            time.sleep(float(action.get("duration", 1.0)))

        elif a == "cursor_position":
            pass  # no-op; the returned screenshot reflects state

        else:
            # Unknown/unsupported action (e.g. a future variant) — do nothing, just re-screenshot.
            # Never improvise a click on an action we don't understand.
            raise ValueError(f"unsupported action: {a!r}")
