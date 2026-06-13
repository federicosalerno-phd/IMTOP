# IMTOP — wound annotator

A desktop tool to **calibrate, segment and measure wounds from a single
photograph**, and to preview/export a printable 3D patch. It is the software
released alongside the IMTOP (*IMage-TO-Print wound dressings*) paper.

The UI is an embedded web view (`ui.html`) driven by a Python/Qt backend over
`QWebChannel`. The workflow is:

1. **Calibration** — set the image scale (px/mm) from a two-point reference.
2. **Segmentation** — trace the wound by hand and/or run an automatic back-end:
   **Segment Anything (SAM, ViT-B)**, **GrabCut**, or **Watershed**.
3. **Metrics** — area, perimeter, Dice/IoU, Hausdorff and coverage metrics,
   reported in mm / mm² when a scale is set and in pixels otherwise.
4. **3D patch** — preview and export a printable patch mesh (STL), built
   in-browser with Three.js (bundled in `libs/`).

> Whole-page zoom is disabled by design: `Ctrl`+mouse wheel zooms only the
> loaded image inside the canvas, centred on the cursor.

## Repository layout

```
wound_app.py            Main application (Qt window + backend)
ui.html                 Embedded web UI
libs/                   Bundled JS for the in-browser 3D viewer (three.min.js, OrbitControls.js)
launch.bat              Windows launcher (per-user Python 3.11)
requirements.txt        Pinned Python dependencies
batch_run.py            Reproducibility: Experiment A headless runner (uses the app backend)
montecarlo_distortion.py  Reproducibility: Monte Carlo error/distortion analysis
Graphs/                 R scripts that render the paper figures (rendered outputs are gitignored)
dev/                    One-shot development utilities (local only — git-ignored)
```

**Not included in this repository** (see `.gitignore`):

- `sam_vit_b.pth` — the SAM model checkpoint (≈375 MB), downloaded separately.
- Clinical wound images and derived masks.

## Requirements

Clone the repository and enter it:

```bash
git clone https://github.com/federicosalerno-phd/IMTOP.git
cd IMTOP
```

- **Python 3.11**
- The packages in [`requirements.txt`](requirements.txt) (NumPy, SciPy,
  OpenCV, Pillow, PyQt6 + PyQt6-WebEngine, PyTorch, Segment Anything; plus
  pandas/matplotlib for the analysis scripts).

```bash
# PyTorch is pinned to a CUDA (cu124) build; install it from the wheel index:
pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu124
```

On a CPU-only machine, install the CPU PyTorch wheels first, then the rest:

```bash
pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

The app prefers **PyQt6** (smoother embedded-Chromium compositor) and falls
back to **PyQt5 + PyQtWebEngine** automatically if PyQt6 is not installed.

### SAM checkpoint

Download the **SAM ViT-B** checkpoint and place it next to `wound_app.py` as
`sam_vit_b.pth`:

```bash
# Meta's official ViT-B checkpoint:
#   https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth
# Save it as sam_vit_b.pth (or point SAM_CHECKPOINT at it — see below).
```

The app still runs without it — GrabCut and Watershed work — but the SAM
back-end will report that the checkpoint is missing.

## Running

```bash
python wound_app.py
```

On Windows you can also double-click **`launch.bat`**, which uses the per-user
Python 3.11 interpreter (the one that has the SAM dependencies).

### Configuration (environment variables)

| Variable         | Default                                                        | Purpose                                                        |
|------------------|----------------------------------------------------------------|---------------------------------------------------------------|
| `SAM_CHECKPOINT` | `sam_vit_b.pth`                                                | Path to the SAM checkpoint.                                    |
| `WOUND_PYTHON`   | `%LOCALAPPDATA%\Programs\Python\Python311\python.exe`          | Interpreter to relaunch with when SAM deps are missing.       |

Both are optional; behaviour is unchanged when they are not set.

## Reproducing the analysis

`batch_run.py` re-runs the segmentation back-ends over an image/mask dataset
using the **same backend code** as the app and writes a tidy metrics CSV:

```bash
python batch_run.py --images <images_dir> --gt <masks_dir> --out results_all.csv
```

`montecarlo_distortion.py` consumes that CSV to model the geometric distortion
of the fabricated patch, writing `montecarlo_summary.csv` and
`montecarlo_samples.csv`. The `Graphs/` R scripts render the paper figures.

## License

Released under the [MIT License](LICENSE).

## Citation

If you use this software, please cite it using [`CITATION.cff`](CITATION.cff).
