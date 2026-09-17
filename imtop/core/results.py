"""Where results go on disk, and the JSON export.

Outputs sit next to the source image when there is one; a drag-dropped image
has no file, so its outputs go to the per-user reports folder instead of being
silently dropped in whatever the current directory happens to be.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from ..config import APP_VERSION, REPORTS_DIR, ensure_dirs
from .session import Session


def output_dir(session: Session) -> Path:
    if session.image_path is not None and session.image_path.parent.is_dir():
        return session.image_path.parent
    ensure_dirs()
    return REPORTS_DIR


def output_path(session: Session, suffix: str) -> Path:
    """``<dir>/<image-stem><suffix>`` for a given suffix such as ``_metrics.json``."""
    return output_dir(session) / f"{session.image_name or 'image'}{suffix}"


def slug(text: str) -> str:
    """``"SAM (ViT-B)"`` -> ``"sam_vit_b"``: lower case, underscores, nothing else."""
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "x"


def report_path(session: Session, when: datetime | None = None) -> Path:
    """``imtop_report_<image>_<method>_<yyyymmdd_hhmm>.pdf``, next to the image."""
    when = when or datetime.now()
    name = f"imtop_report_{slug(session.image_name or 'image')}_{slug(session.method or 'manual')}_{when:%Y%m%d_%H%M}.pdf"
    return output_dir(session) / name


def metrics_document(session: Session) -> dict:
    """Metrics plus enough provenance to read them later without the app."""
    info = session.metrics_info()
    doc = dict(session.public_metrics())
    doc["meta"] = {
        "app": "IMTOP",
        "version": APP_VERSION,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "image": session.image_path.name if session.image_path else session.image_name,
        "image_size_px": [session.w, session.h],
        "method": session.method,
        "calibrated": info["calibrated"],
        "px_per_mm": info["px_per_mm"],
        "length_unit": info["length_unit"],
        "area_unit": info["area_unit"],
        "note": ("Keys ending in _mm/_mm2 are in pixels when calibrated is false."
                 if not info["calibrated"] else ""),
    }
    return doc


def save_metrics_json(session: Session) -> Path:
    path = output_path(session, "_metrics.json")
    path.write_text(json.dumps(metrics_document(session), indent=2), encoding="utf-8")
    return path
