"""Automatic SAM prompt for high-contrast images (bright object, dark background).

Otsu-thresholds the image, picks the largest object that does not touch the
border, and returns its smoothed contour. That contour then feeds the ordinary
SAM pipeline exactly as a hand-traced one would.
"""
from __future__ import annotations

import numpy as np
import cv2

from .geometry import mask_to_contour, largest_interior_component


class AutoPromptError(ValueError):
    """No usable object found; the message is meant for the user."""


def otsu_object_contour(img: np.ndarray) -> np.ndarray:
    """Contour of the dominant central object, or raise :class:`AutoPromptError`."""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    fg = (th > 0).astype(np.uint8)
    # Polarity: the object is central and the background touches the borders.
    # If the current foreground dominates the border it *is* the background.
    border = np.concatenate([fg[0, :], fg[-1, :], fg[:, 0], fg[:, -1]])
    if border.mean() > 0.5:
        fg = 1 - fg
    n_labels, labels = cv2.connectedComponents(fg)
    best = largest_interior_component(labels, n_labels)
    if best is None:
        raise AutoPromptError("Auto-prompt: no object found")
    comp = labels == best
    if int(comp.sum()) < max(64, int(5e-4 * h * w)):
        raise AutoPromptError("Auto-prompt: no valid object found")
    crv = mask_to_contour(comp)
    if crv is None:
        raise AutoPromptError("Auto-prompt: object too small")
    return crv
