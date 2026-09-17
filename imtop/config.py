"""Paths, tunables and environment overrides.

Everything that depends on *where things live* is resolved here, so the rest of
the package never builds a path by hand. Import this module first if you need to
know whether an optional dependency is installed: the ``HAS_*`` flags are probed
once, at import, and are safe to read from anywhere.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── identity ─────────────────────────────────────────────────────────────────
APP_NAME = "IMTOP"
APP_TITLE = "IMTOP Wound Annotator"
APP_VERSION = "2.0.0"
# Shown in the title bar and in the report footer.
APP_CREDIT = "Designed and developed by Dr. Federico Salerno"
# The taskbar identity (AppUserModelID). app.py gives it to the process and the
# installer writes the same string on the Start Menu shortcut: that is what makes
# "Pin to taskbar" pin IMTOP instead of a bare pythonw.exe. installer/install.ps1
# reads it from this line, so keep the exact form.
APP_USER_MODEL_ID = "FedericoSalerno.IMTOP"

# ── locations ────────────────────────────────────────────────────────────────
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
UI_DIR = PACKAGE_DIR / "ui"
VENDOR_DIR = PACKAGE_DIR / "vendor"
ASSETS_DIR = UI_DIR / "assets"


def user_data_dir() -> Path:
    """Per-user writable directory for models, logs and generated reports.

    Kept out of the install location on purpose: an installed copy may sit in a
    read-only folder, and a user should never need admin rights to run the app.
    """
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(root) / APP_NAME


MODELS_DIR = Path(os.environ.get("IMTOP_MODELS_DIR", user_data_dir() / "models"))
REPORTS_DIR = Path(os.environ.get("IMTOP_REPORTS_DIR", user_data_dir() / "reports"))

# ── Segment Anything ─────────────────────────────────────────────────────────
# The checkpoint is large (~375 MB) and is therefore NOT shipped with the app.
# It is fetched on first use into MODELS_DIR. A copy sitting next to the project
# (the historical location) still wins, so existing installs keep working.
SAM_CHECKPOINT_NAME = "sam_vit_b.pth"
SAM_CHECKPOINT_URL = (
    "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth"
)
SAM_CHECKPOINT_SIZE = 375_042_383  # bytes, used for the download progress bar
SAM_MODEL_TYPE = "vit_b"


def sam_checkpoint_path() -> Path:
    """Where to read the SAM checkpoint from, in order of preference.

    ``SAM_CHECKPOINT`` (env) wins outright. Otherwise a checkpoint already
    sitting beside the project is preferred over the managed copy, so a user who
    downloaded it by hand before never downloads it twice.
    """
    override = os.environ.get("SAM_CHECKPOINT")
    if override:
        return Path(override)
    legacy = PROJECT_DIR / SAM_CHECKPOINT_NAME
    if legacy.exists():
        return legacy
    return MODELS_DIR / SAM_CHECKPOINT_NAME


# ── geometry / segmentation tunables ─────────────────────────────────────────
CONTOUR_N = 300      # points sampled along a contour spline
AUTOPROMPT_CTRL_PTS = 32   # control points the auto-prompt hands to the manual trace
SPLINE_K = 3         # B-spline degree for the hand-traced contour
MORPH_FRAC = 0.012   # mask cleanup kernel, as a fraction of the image's long side
SEED_FRAC = 0.02     # GrabCut/Watershed seed erosion/dilation, same fraction

# ── rendering ────────────────────────────────────────────────────────────────
# GPU compositing for the embedded web view. Set IMTOP_SOFTWARE_RENDER=1 if the
# window comes up black or empty on a machine with a broken driver.
USE_GPU = os.environ.get("IMTOP_SOFTWARE_RENDER", "") not in ("1", "true", "True")

# ── optional dependencies, probed once ───────────────────────────────────────
try:  # pragma: no cover - depends on the install
    import torch  # noqa: F401
    import segment_anything  # noqa: F401

    HAS_SAM = True
except Exception:
    HAS_SAM = False


def sam_device_forced() -> str:
    """``"cpu"`` or ``"cuda"`` when ``IMTOP_SAM_DEVICE`` settles it by hand, else "".

    The escape hatch for a machine where the automatic choice is wrong: the app
    picks the card when there is room on it, and this overrules that either way.
    """
    forced = os.environ.get("IMTOP_SAM_DEVICE", "").strip().lower()
    return forced if forced in ("cpu", "cuda") else ""


def sam_device() -> str:
    """``"cuda"`` when a CUDA-capable torch is installed, otherwise ``"cpu"``."""
    forced = sam_device_forced()
    if forced:
        return forced
    if not HAS_SAM:
        return "cpu"
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def ensure_dirs() -> None:
    """Create the writable directories. Safe to call repeatedly."""
    for d in (MODELS_DIR, REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
