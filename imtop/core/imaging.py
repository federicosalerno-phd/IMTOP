"""Loading, encoding and drawing images.

Images are RGB ``uint8`` arrays of shape ``(h, w, 3)``; contours are ``(n, 2)``
pixel arrays from :mod:`geometry`.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path
from urllib.parse import unquote, urlparse

import numpy as np
import cv2
from PIL import Image

# Overlay strokes, chosen to match the strokes the web UI draws on the canvas.
GT_COLOR = (82, 214, 138)      # manual (ground-truth) contour, green
AUTO_COLOR = (240, 112, 112)   # automatic contour, red

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")


def local_path(path_or_url: str) -> str:
    """Turn a ``file:///…`` URL (or a plain path) into a local filesystem path.

    Handles percent-encoding properly, the previous ``replace("%20", " ")``
    broke on any other encoded character, and both drive-letter and UNC forms.
    """
    s = path_or_url.strip()
    if not s.lower().startswith("file:"):
        return s
    u = urlparse(s)
    p = unquote(u.path)
    if u.netloc:                                   # file://server/share/x -> //server/share/x
        p = "//" + u.netloc + p
    elif len(p) >= 3 and p[0] == "/" and p[2] == ":":   # /C:/x -> C:/x
        p = p[1:]
    return p


def load_image_file(path: str | Path) -> np.ndarray:
    """Decode an image file to an RGB array. Raises on failure."""
    with Image.open(path) as pil:
        return np.array(pil.convert("RGB"))


def load_image_data_url(data_url: str) -> np.ndarray:
    """Decode a ``data:image/…;base64,…`` URL (drag & drop) to an RGB array."""
    b64 = data_url.split(",", 1)[1] if "," in data_url else data_url
    raw = base64.b64decode(b64)
    with Image.open(io.BytesIO(raw)) as pil:
        return np.array(pil.convert("RGB"))


def to_data_url(arr: np.ndarray, fmt: str = "JPEG", quality: int = 90) -> str:
    """Encode an RGB array as a ``data:`` URL the web view can display."""
    img = Image.fromarray(arr.astype(np.uint8))
    buf = io.BytesIO()
    if fmt.upper() == "JPEG":
        img.save(buf, format="JPEG", quality=quality)
        mime = "image/jpeg"
    else:
        img.save(buf, format=fmt)
        mime = "image/" + fmt.lower()
    return f"data:{mime};base64," + base64.b64encode(buf.getvalue()).decode()


def downscale_to(arr: np.ndarray, max_side: int) -> np.ndarray:
    """Shrink so the long side is at most ``max_side``. Smaller images are
    returned untouched: a report figure is never worth upsampling."""
    h, w = arr.shape[:2]
    longest = max(h, w)
    if longest <= max_side or longest == 0:
        return arr
    sc = max_side / float(longest)
    return cv2.resize(arr, (max(1, round(w * sc)), max(1, round(h * sc))),
                      interpolation=cv2.INTER_AREA)


def mask_to_png_bytes(mask: np.ndarray) -> bytes:
    """Boolean mask -> binary PNG bytes (0/255, mode L)."""
    out = mask.astype(np.uint8) * 255
    buf = io.BytesIO()
    Image.fromarray(out, mode="L").save(buf, format="PNG")
    return buf.getvalue()


def _closed(contour: np.ndarray) -> np.ndarray:
    return np.vstack([contour, contour[:1]]).astype(np.int32).reshape(-1, 1, 2)


def render_overlay(img: np.ndarray,
                   gt_contour: np.ndarray | None = None,
                   auto_contour: np.ndarray | None = None,
                   thickness: int = 2) -> np.ndarray:
    """Copy of ``img`` with the contours drawn on top (GT green, auto red)."""
    out = img.copy()
    if gt_contour is not None:
        cv2.polylines(out, [_closed(gt_contour)], True, GT_COLOR, thickness)
    if auto_contour is not None:
        cv2.polylines(out, [_closed(auto_contour)], True, AUTO_COLOR, thickness)
    return out


def render_filled_overlay(img: np.ndarray,
                          gt_mask: np.ndarray | None,
                          auto_mask: np.ndarray | None,
                          alpha: float = 0.35) -> np.ndarray:
    """Translucent fills for the report: GT green, auto red, overlap blended."""
    out = img.astype(np.float32)
    if gt_mask is not None:
        col = np.array(GT_COLOR, np.float32)
        out[gt_mask] = out[gt_mask] * (1 - alpha) + col * alpha
    if auto_mask is not None:
        col = np.array(AUTO_COLOR, np.float32)
        out[auto_mask] = out[auto_mask] * (1 - alpha) + col * alpha
    return np.clip(out, 0, 255).astype(np.uint8)
