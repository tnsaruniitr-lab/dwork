"""
tools.py — helper implementations for the non-vision, non-bash tools.

find_text_on_screen(label, display_w, display_h)
    Uses macOS Vision framework (on-device OCR, no external service) to locate
    text on the current screen. Returns display-space pixel coords for clicking.

wait_for_screen_stable(timeout, display_w, display_h)
    Polls screenshots until two consecutive frames are similar (screen settled).
    Returns a fresh screenshot when stable.
"""
import base64
import io
import time

import mss
import numpy as np
from PIL import Image

# ── macOS Vision OCR ──────────────────────────────────────────────────────
try:
    import Vision
    import Quartz
    _HAS_VISION = True
except ImportError:
    _HAS_VISION = False


def _grab_display(display_w: int, display_h: int):
    """Capture primary monitor, downscale to display dims. Returns PIL Image."""
    with mss.MSS() as sct:
        raw = sct.grab(sct.monitors[1])
    img = Image.frombytes("RGB", raw.size, raw.rgb)
    if img.size != (display_w, display_h):
        img = img.resize((display_w, display_h), Image.LANCZOS)
    return img


def _ocr_pil(pil_img):
    """Run macOS Vision OCR. Returns list of (text, cx, cy) in pixel coords."""
    if not _HAS_VISION:
        return []
    w, h = pil_img.size
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    data = Quartz.CFDataCreate(None, buf.getvalue(), len(buf.getvalue()))
    src = Quartz.CGDataProviderCreateWithCFData(data)
    cg = Quartz.CGImageCreateWithPNGDataProvider(
        src, None, False, Quartz.kCGRenderingIntentDefault)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(1)   # 0=fast, 1=accurate
    req.setUsesLanguageCorrection_(False)   # off → better for German UI labels
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, {})
    handler.performRequests_error_([req], None)
    results = []
    for obs in (req.results() or []):
        txt = obs.topCandidates_(1)[0].string()
        bb = obs.boundingBox()   # normalized, origin = bottom-left
        cx = (bb.origin.x + bb.size.width  / 2) * w
        cy = (1 - bb.origin.y - bb.size.height / 2) * h   # flip to top-left
        results.append((txt, int(cx), int(cy)))
    return results


def find_text_on_screen(label: str, display_w: int, display_h: int) -> dict:
    """
    Locate `label` on screen using OCR. Returns:
      {"found": True,  "x": px, "y": py, "matched": "exact text found"}
      {"found": False, "candidates": ["nearby labels…"]}
    x/y are in display-space pixels (what the model uses for clicking).
    """
    if not _HAS_VISION:
        return {"found": False, "error": "macOS Vision not available — use computer tool"}

    img = _grab_display(display_w, display_h)
    regions = _ocr_pil(img)

    label_lower = label.lower()
    # exact / substring match, case-insensitive
    matches = [(t, x, y) for t, x, y in regions if label_lower in t.lower()]
    if matches:
        # pick the highest-confidence (longest) match
        best = max(matches, key=lambda m: len(m[0]))
        return {"found": True, "x": best[1], "y": best[2], "matched": best[0]}

    # no match — return nearby candidates so agent can adapt
    candidates = [t for t, _, _ in regions][:10]
    return {"found": False, "candidates": candidates}


def img_to_b64(pil_img: Image.Image) -> str:
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def wait_for_screen_stable(timeout: float, display_w: int, display_h: int) -> tuple:
    """
    Poll screenshots until two consecutive frames differ by < 1% pixels.
    Returns (base64_png, stable: bool).
    """
    deadline = time.time() + timeout
    prev = None
    while time.time() < deadline:
        img = _grab_display(display_w, display_h)
        arr = np.array(img)
        if prev is not None:
            diff = np.mean(np.abs(arr.astype(int) - prev.astype(int)))
            if diff < 2.5:   # ~1% change threshold
                return img_to_b64(img), True
        prev = arr
        time.sleep(0.4)
    img = _grab_display(display_w, display_h)
    return img_to_b64(img), False
