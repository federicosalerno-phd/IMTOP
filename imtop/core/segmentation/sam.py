"""Segment Anything (ViT-B), prompted with the manual contour's box + a point inside.

The engine owns the model lifecycle so the rest of the app never touches torch:

* the checkpoint is downloaded on first use into the per-user models folder;
* weights load once per process;
* the image is encoded once per image (``image_id``) and reused by every
  predict call until a new image arrives.

All of that runs on whichever thread calls :meth:`prepare`, the bridge keeps
it off the GUI thread. Progress is reported through a plain callback.
"""
from __future__ import annotations

import io
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path

import numpy as np

from ...config import (HAS_SAM, SAM_CHECKPOINT_URL, SAM_CHECKPOINT_SIZE,
                       SAM_MODEL_TYPE, MODELS_DIR, sam_checkpoint_path, sam_device,
                       sam_device_forced)
from ..geometry import axis_aligned_bbox, deepest_interior_point
from .base import SegmentationContext, Progress, no_progress

MIN_VALID_CHECKPOINT = 300_000_000   # bytes; anything smaller is a broken download

# What the card must still have free before the model is put on it. The fp16
# encoder peaks at about 1.8 GB and the weights take another 0.4; below this the
# app works on the processor instead. A CUDA allocation that is merely tight does
# not always fail outright: it can thrash for minutes holding Python's lock, which
# freezes the window, spinner and all. A slow answer beats a frozen one.
MIN_FREE_VRAM = 2_200_000_000


# Left to the rest of the system when the card is capped below. Windows itself,
# the web view's compositor and whatever else is on screen keep asking the card
# for memory while a segmentation runs.
VRAM_HEADROOM = 250_000_000


def _card_free_bytes() -> int | None:
    """Free video memory, or None when there is no card to ask."""
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        free, _total = torch.cuda.mem_get_info()
        return int(free)
    except Exception:
        return None


def _cap_card() -> None:
    """Refuse to allocate past what is free on the card, instead of overcommitting.

    This is the difference between an error and a frozen window. When a CUDA
    allocation does not fit, Windows does not fail it: the display driver starts
    paging video memory out to system RAM and the allocation eventually returns,
    minutes later. All of that happens inside one torch call, with Python's lock
    held, so the GUI thread cannot run a single timer and the app stops repainting
    even though it is still answering the window manager. Capping the process at
    what the card really has free turns the same situation into an
    ``OutOfMemoryError``, which ``retry_on_cpu`` answers by moving to the
    processor in a few seconds.
    """
    try:
        import torch

        free, total = torch.cuda.mem_get_info()
        # What this process may hold in total: what it already holds, plus what
        # the card still has, less the margin. Counting only the free memory
        # would put the ceiling under the model that is already loaded and make
        # the very next allocation fail for no reason.
        held = torch.cuda.memory_reserved()
        frac = (held + free - VRAM_HEADROOM) / float(total)
        torch.cuda.set_per_process_memory_fraction(min(0.95, max(0.10, frac)))
    except Exception:
        pass


def _is_out_of_memory(exc: BaseException) -> bool:
    """Did this exception mean "the card is full"?

    Recent torch raises ``torch.cuda.OutOfMemoryError``, older ones a plain
    ``RuntimeError`` carrying the same sentence, and cuBLAS and cuDNN report
    their own allocation failures, which here mean exactly the same thing.
    """
    if type(exc).__name__ in ("OutOfMemoryError", "CudaOutOfMemoryError"):
        return True
    msg = str(exc).lower()
    return ("out of memory" in msg
            or "cublas_status_alloc_failed" in msg
            or "cudnn_status_alloc_failed" in msg)


# Progress bands, so the single bar in the UI moves monotonically:
#   download 0-40 · read weights 40-58 · build 58-66 · encode 66-100
_DL0, _DL1, _RD0, _RD1, _BUILD, _ENC = 0, 40, 40, 58, 60, 66


class SamEngine:
    def __init__(self, device: str | None = None):
        self._device = device
        self._predictor = None
        self._encoded_id: int | None = None

    # ── availability ─────────────────────────────────────────────────────
    @property
    def available(self) -> bool:
        return HAS_SAM

    @property
    def device(self) -> str:
        if self._device is None:
            self._device = sam_device()
        return self._device

    def checkpoint_path(self) -> Path:
        return sam_checkpoint_path()

    def checkpoint_present(self) -> bool:
        p = self.checkpoint_path()
        return p.is_file() and p.stat().st_size >= MIN_VALID_CHECKPOINT

    @property
    def loaded(self) -> bool:
        return self._predictor is not None

    def ready_for(self, image_id: int) -> bool:
        return self._predictor is not None and self._encoded_id == image_id

    def unavailable_reason(self) -> str:
        if not HAS_SAM:
            return "SAM needs PyTorch and segment-anything, re-run the installer to add them."
        return ""

    # ── lifecycle ────────────────────────────────────────────────────────
    def prepare(self, image: np.ndarray, image_id: int, progress: Progress = no_progress) -> None:
        """Make sure the model is loaded and *this* image is encoded. Idempotent.

        The ViT-B encoder needs about 1.8 GB of video memory in fp16 and it shares
        the card with the web view's compositor: on a 4 GB laptop GPU with a
        browser open there may not be that much left. Rather than fail in front of
        whoever is watching, everything held on the card is dropped and the whole
        thing starts again on the processor: about 11 s for a 1280x720 photo
        instead of a few, and the progress bar says what happened.
        """
        if not HAS_SAM:
            raise RuntimeError(self.unavailable_reason())
        try:
            self._prepare(image, image_id, progress)
            return
        except Exception as exc:
            if not self.retry_on_cpu(exc, image, image_id, progress):
                raise

    def _move_to_cpu(self, reason: str, progress: Progress) -> None:
        """Let go of the card and stay off it for the rest of the process. A card
        that could not fit this image will not fit the next one either."""
        self._predictor = None
        self._encoded_id = None
        self._device = "cpu"
        try:
            import torch

            torch.cuda.empty_cache()
            torch.cuda.set_per_process_memory_fraction(1.0)   # the cap was ours, let it go
        except Exception:
            pass
        progress(_BUILD, reason)

    def _check_room(self, progress: Progress) -> None:
        """Before every build and every encode: is there still room on the card?

        Checking once, when the model is built, is not enough. The web view's
        compositor sits on the same card and grows, and so does whatever else the
        operator has open; the encode that runs a minute later is the allocation
        that matters. ``IMTOP_SAM_DEVICE`` overrules this either way.
        """
        if sam_device_forced() == "cpu" or self.device != "cuda":
            return
        free = _card_free_bytes()
        if free is not None and free < MIN_FREE_VRAM and sam_device_forced() != "cuda":
            self._move_to_cpu(
                f"Only {free / 1e9:.1f} GB free on the card, using the processor…", progress)
            return
        _cap_card()

    def _prepare(self, image: np.ndarray, image_id: int, progress: Progress) -> None:
        # The room check is cheap, and the card fills up between one run and the
        # next: the compositor grows, and so does whatever else is on screen.
        self._check_room(progress)
        if self._predictor is None:
            path = self.ensure_checkpoint(progress)
            model = self._build(path, progress)
            from segment_anything import SamPredictor
            progress(_ENC - 2, "Building predictor…")
            self._predictor = SamPredictor(model)
        if self._encoded_id != image_id:
            progress(_ENC, "Encoding image…")
            self._encode(image)
            self._encoded_id = image_id
        progress(100, "Ready")

    def retry_on_cpu(self, exc: BaseException, image: np.ndarray, image_id: int,
                     progress: Progress = no_progress) -> bool:
        """``True`` when *exc* was the card running out of memory and the model has
        been rebuilt on the processor, so the caller can simply try again. The
        engine stays on the processor for the rest of the process: a card that
        could not fit this image will not fit the next one either."""
        if self.device != "cuda" or sam_device_forced() == "cuda" or not _is_out_of_memory(exc):
            return False
        self._move_to_cpu("Video memory full, switching to the processor…", progress)
        self._prepare(image, image_id, progress)
        return True

    def ensure_checkpoint(self, progress: Progress = no_progress) -> Path:
        """Return the checkpoint path, downloading it first if it is missing."""
        if self.checkpoint_present():
            return self.checkpoint_path()
        target = MODELS_DIR / self.checkpoint_path().name
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        progress(_DL0, "Downloading SAM model…")
        tmp_fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".part", dir=str(MODELS_DIR))
        os.close(tmp_fd)
        tmp = Path(tmp_name)
        try:
            req = urllib.request.Request(SAM_CHECKPOINT_URL, headers={"User-Agent": "IMTOP"})
            with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
                total = int(resp.headers.get("Content-Length") or SAM_CHECKPOINT_SIZE)
                done = 0
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    pct = _DL0 + int((_DL1 - _DL0) * min(done / max(total, 1), 1.0))
                    progress(pct, f"Downloading SAM model… {done / 1e6:.0f} / {total / 1e6:.0f} MB")
            if tmp.stat().st_size < MIN_VALID_CHECKPOINT:
                raise RuntimeError("download ended early")
            shutil.move(str(tmp), str(target))
        except Exception as e:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"Could not download the SAM checkpoint ({e}). Check the connection, "
                f"or place {target.name} in {MODELS_DIR} by hand.") from e
        return target

    def _build(self, path: Path, progress: Progress):
        """Read the checkpoint in chunks so the bar shows real byte progress."""
        import torch
        from segment_anything import sam_model_registry

        size = path.stat().st_size
        buf = io.BytesIO()
        read = 0
        with open(path, "rb") as f:
            while True:
                b = f.read(8 * 1024 * 1024)
                if not b:
                    break
                buf.write(b)
                read += len(b)
                progress(_RD0 + int((_RD1 - _RD0) * read / max(size, 1)), "Loading SAM weights…")
        buf.seek(0)
        progress(_BUILD, "Preparing weights…")
        state = torch.load(buf, map_location="cpu")
        model = sam_model_registry[SAM_MODEL_TYPE]()
        model.load_state_dict(state)
        model.to(self.device).eval()
        return model

    def _encode(self, image: np.ndarray) -> None:
        """Run the image encoder. FP16 autocast on CUDA: the ViT-B encoder's peak
        VRAM drops from ~2.9 GB to ~1.8 GB, which is what lets it coexist with the
        web view's GPU compositor on a 4 GB card (measured IoU vs FP32: 0.9998).
        Features are cast back to fp32 so predict() runs at full precision."""
        import torch

        if self.device == "cuda":
            with torch.autocast("cuda", dtype=torch.float16):
                self._predictor.set_image(image)
            try:
                self._predictor.features = self._predictor.features.float()
            except Exception:
                pass
        else:
            self._predictor.set_image(image)

    # ── inference ────────────────────────────────────────────────────────
    def predict(self, box: np.ndarray, point: tuple[int, int]) -> np.ndarray:
        masks, _, _ = self._predictor.predict(
            point_coords=np.array([[int(point[0]), int(point[1])]]),
            point_labels=np.array([1]),
            box=box.astype(np.float32),
            multimask_output=False)
        return masks[0].astype(bool)


class SamBackend:
    id = "SAM (ViT-B)"
    label = "SAM (ViT-B)"
    needs_model = True

    def __init__(self, engine: SamEngine):
        self.engine = engine

    def available(self) -> bool:
        return self.engine.available

    def unavailable_reason(self) -> str:
        return self.engine.unavailable_reason()

    def segment(self, ctx: SegmentationContext, progress: Progress = no_progress) -> np.ndarray | None:
        self.engine.prepare(ctx.image, ctx.image_id, progress)
        gc = ctx.gt_contour
        x0, y0, x1, y1 = axis_aligned_bbox(gc)
        box = np.array([x0, y0, x1, y1], dtype=np.float32)
        # Positive point: the deepest point inside the seed mask, not the centroid,
        # which for a concave shape can fall outside the wound (see geometry.py).
        if ctx.gt_mask is not None and ctx.gt_mask.any():
            point = deepest_interior_point(ctx.gt_mask)
        else:
            point = (int(gc[:, 0].mean()), int(gc[:, 1].mean()))
        try:
            return self.engine.predict(box, point)
        except Exception as exc:
            # The encoder fitted but the decoder did not: same answer, one retry
            # on the processor (see SamEngine.prepare).
            if not self.engine.retry_on_cpu(exc, ctx.image, ctx.image_id, progress):
                raise
        return self.engine.predict(box, point)
