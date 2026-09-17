#!/usr/bin/env python
"""Experiment A, headless: run every back-end over an image/mask dataset with the
app's own core (``imtop.core``) and write a tidy metrics CSV.

Same CSV schema as the published ``results_all.csv``. No Qt is needed.

    python tools/batch_run.py --images <images_dir> --gt <masks_dir> --out results_all.csv

Masks are paired with images by file stem (``photo.jpg`` <-> ``photo.png``).
Scale is absent on purpose (scale-free run): the ``*_mm`` / ``*_mm2`` columns
are in pixels; dice, iou, area_rel_err, cri_lambda3, the coverage fractions,
compactness and aspect ratios are dimensionless and valid as they are.
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # make `imtop` importable

import numpy as np
from PIL import Image

from imtop.config import HAS_SAM
from imtop.core.geometry import mask_to_contour
from imtop.core.imaging import IMAGE_EXTENSIONS
from imtop.core.metrics import (compute_metrics, public_metrics, coverage_extras,
                                METRIC_KEYS, EXTRA_KEYS)
from imtop.core.segmentation import Segmenters, SegmentationContext

LABEL_TO_METHOD = {"SAM": "SAM (ViT-B)", "GrabCut": "GrabCut", "Watershed": "Watershed"}
METHOD_TO_LABEL = {v: k for k, v in LABEL_TO_METHOD.items()}
CSV_COLUMNS = ["experiment", "image", "backend", "scale_px_per_mm", "status"] + EXTRA_KEYS + METRIC_KEYS


def load_bool_mask(path: str, shape_hw) -> np.ndarray | None:
    m = np.array(Image.open(path).convert("L"))
    return None if m.shape != tuple(shape_hw) else m > 127


def pair_files(images_dir: str, gt_dir: str):
    imgs = sorted([p for p in glob.glob(os.path.join(images_dir, "*"))
                   if p.lower().endswith(IMAGE_EXTENSIONS)],
                  key=lambda p: os.path.basename(p).lower())
    pairs = []
    for ip in imgs:
        stem = os.path.splitext(os.path.basename(ip))[0]
        hits = [g for g in glob.glob(os.path.join(gt_dir, stem + ".*"))
                if g.lower().endswith(IMAGE_EXTENSIONS)]
        pairs.append((stem, ip, hits[0] if hits else None))
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser(description="Experiment A, headless, on the app's own core.")
    ap.add_argument("--images", default="Wound Image Dataset_renamed")
    ap.add_argument("--gt", default="Wound Image Dataset_masks")
    ap.add_argument("--out", default="results_all.csv")
    ap.add_argument("--methods", nargs="+", default=["SAM", "GrabCut", "Watershed"],
                    choices=list(LABEL_TO_METHOD))
    ap.add_argument("--limit", type=int, default=0, help="only the first N images (smoke test)")
    a = ap.parse_args()

    seg = Segmenters()
    methods = [LABEL_TO_METHOD[m] for m in a.methods]
    want_sam = "SAM (ViT-B)" in methods
    if want_sam and not HAS_SAM:
        print("!! torch / segment_anything are not installed in this Python -> skipping SAM.")
        methods = [m for m in methods if m != "SAM (ViT-B)"]
        want_sam = False
    elif want_sam:
        if not seg.sam.checkpoint_present():
            print(f"SAM checkpoint not found -> it will be downloaded once (~375 MB) to {seg.sam.checkpoint_path().parent}")
        print(f"SAM device: {seg.sam.device}")

    if not os.path.isdir(a.images):
        sys.exit(f"Images folder not found: '{a.images}'")
    if not os.path.isdir(a.gt):
        sys.exit(f"GT masks folder not found: '{a.gt}'")
    pairs = pair_files(a.images, a.gt)
    if a.limit:
        pairs = pairs[:a.limit]
    if not pairs:
        sys.exit(f"No images in '{a.images}'.")
    if all(gp is None for _, _, gp in pairs):
        sys.exit(f"{len(pairs)} images in '{a.images}' but no GT mask in '{a.gt}' "
                 f"(masks must share the image stem, e.g. {pairs[0][0]}.png).")

    rows = []
    fails = {METHOD_TO_LABEL[m]: 0 for m in methods}
    no_gt = mismatch = empty_gt = 0
    t0 = time.time()

    for k, (stem, ip, gp) in enumerate(pairs, 1):
        if gp is None:
            no_gt += 1
            print(f"[{k}/{len(pairs)}] {stem}: no GT mask -> skipped")
            continue
        try:
            img = np.array(Image.open(ip).convert("RGB"))
        except Exception as e:
            print(f"[{k}/{len(pairs)}] {stem}: cannot read image: {e}")
            continue
        gt = load_bool_mask(gp, img.shape[:2])
        if gt is None:
            mismatch += 1
            print(f"[{k}/{len(pairs)}] {stem}: GT size differs from the image -> skipped")
            continue
        if gt.sum() == 0:
            empty_gt += 1
            print(f"[{k}/{len(pairs)}] {stem}: empty GT -> skipped")
            continue

        gt_contour = mask_to_contour(gt)          # rebuilt from the saved mask
        ctx = SegmentationContext(img, k, gt, gt_contour)
        if want_sam:
            try:
                seg.sam.prepare(img, k)           # encode this image once
            except Exception as e:
                print(f"   SAM encode failed on {stem}: {e}")

        for m in methods:
            label = METHOD_TO_LABEL[m]
            row = {c: "" for c in CSV_COLUMNS}
            row.update(experiment="A", image=stem, backend=label, scale_px_per_mm="")
            try:
                mask, contour = seg.run(m, ctx)
                if mask is None:
                    row["status"] = "fail:empty_mask"
                    fails[label] += 1
                    rows.append(row)
                    continue
                metrics = public_metrics(compute_metrics(gt, mask, gt_contour, contour, None))
                row.update(coverage_extras(gt, mask, metrics))
                for kk in METRIC_KEYS:
                    v = metrics.get(kk, "")
                    row[kk] = "" if v is None else v
                row["status"] = "ok"
            except Exception as e:
                row["status"] = f"fail:{type(e).__name__}"
                fails[label] += 1
            rows.append(row)

        if k % 10 == 0 or k == len(pairs):
            print(f"[{k}/{len(pairs)}] done ({time.time() - t0:.0f}s)")

    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)

    ok = sum(1 for r in rows if r["status"] == "ok")
    print(f"\nWrote {len(rows)} rows ({ok} ok) -> {a.out}")
    print(f"  images: {len(pairs)} | no GT: {no_gt} | size mismatch: {mismatch} | empty GT: {empty_gt}")
    for label, c in fails.items():
        n = sum(1 for r in rows if r["backend"] == label and r["status"] == "ok")
        print(f"  {label:10s}: {n} ok, {c} failed")


if __name__ == "__main__":
    main()
