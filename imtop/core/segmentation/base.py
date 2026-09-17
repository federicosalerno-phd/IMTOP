"""What every segmentation back-end receives and must return."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

import numpy as np

# Progress callback: (percent 0-100, human-readable stage).
Progress = Callable[[int, str], None]


def no_progress(pct: int, stage: str) -> None:  # noqa: ARG001 - deliberate no-op
    pass


@dataclass(frozen=True)
class SegmentationContext:
    """Everything a back-end may look at. Arrays are read-only by convention."""
    image: np.ndarray            # RGB uint8 (h, w, 3)
    image_id: int                # identifies the image for caches (SAM encoding)
    gt_mask: np.ndarray          # boolean (h, w) seed region from the manual contour
    gt_contour: np.ndarray       # (n, 2) pixels

    @property
    def h(self) -> int:
        return int(self.image.shape[0])

    @property
    def w(self) -> int:
        return int(self.image.shape[1])


class SegmentationBackend(Protocol):
    id: str            # the string the UI sends, e.g. "GrabCut"
    label: str         # what to show
    needs_model: bool  # True when a (large) model must be loaded first

    def available(self) -> bool: ...
    def unavailable_reason(self) -> str: ...
    def segment(self, ctx: SegmentationContext, progress: Progress = no_progress) -> np.ndarray | None: ...
