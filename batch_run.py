#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
batch_run.py -- Esperimento A headless, usando il VERO backend dell'app.

Importa Backend da wound_app.py e chiama le sue funzioni
(_segment / _clean_mask / _mask_to_contour / _compute_metrics), quindi ogni
metrica e' calcolata dallo STESSO codice dell'app (niente reimplementazione).
Cicla su una cartella di immagini + maschere GT (stesso nome-stem) e scrive un
CSV tidy (una riga per immagine x back-end) con TUTTE le metriche dell'app, gia'
pronto a essere ampliato (basta fare il join di nuove colonne su [image, backend]).

Lancia dalla cartella del progetto (dove ci sono wound_app.py, ui.html,
sam_vit_b.pth) e con il Python che ha torch+segment_anything (quello di launch.bat):

    python batch_run.py --images "Wound Image Dataset_renamed" --gt "Wound Image Dataset_masks" --out results_all.csv

Prima un test veloce (senza SAM) per validare l'aggancio:
    python batch_run.py --images "Wound Image Dataset_renamed" --gt "Wound Image Dataset_masks" --methods GrabCut Watershed --limit 3 --out _smoke.csv

UNITA': le immagini non sono calibrate -> px_per_mm assente -> scala = 1, quindi
le colonne *_mm2 sono px^2 e *_mm sono px. Le metriche SCALE-FREE (valide cosi'):
dice, iou, area_rel_err (= patch_area_err_pct/100), cri_lambda3, under/over_cov_frac,
compactness_*, aspect_ratio_*. Il valore in mm si recupera solo sui phantom calibrati
(Exp. B/C), che questo script non tocca.

GT: gt_contour e' ricostruito dalla maschera salvata con il tuo stesso _mask_to_contour
(stesso estrattore delle maschere automatiche); le metriche su maschera (Dice, IoU,
area, coverage) sono esatte e indipendenti da questa scelta.
"""
import os, sys, csv, glob, argparse, time
os.environ.setdefault("WOUND_REEXEC", "1")            # NON rilanciare l'interprete (evita di riavviare la GUI)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # nessuna finestra

import numpy as np
from PIL import Image

import wound_app as wa     # importa il vero backend (con Qt/torch/segment_anything come l'app)

IMG_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")
LABEL_TO_METHOD = {"SAM": "SAM (ViT-B)", "GrabCut": "GrabCut", "Watershed": "Watershed"}
METHOD_TO_LABEL = {v: k for k, v in LABEL_TO_METHOD.items()}

# tutte le chiavi prodotte da Backend._compute_metrics (ordine stabile)
METRIC_KEYS = ["dice", "iou", "area_gt_mm2", "area_auto_mm2", "area_rel_err",
    "cri_lambda3", "under_cov_mm2", "over_cov_mm2", "hausdorff_mm", "avg_surf_dist_mm",
    "perimeter_gt_mm", "perimeter_auto_mm", "compactness_gt", "compactness_auto",
    "bbox_gt_major_mm", "bbox_gt_minor_mm", "bbox_auto_major_mm", "bbox_auto_minor_mm",
    "aspect_ratio_gt", "aspect_ratio_auto", "bbox_auto_x_mm", "bbox_auto_y_mm"]

# colonne extra scale-free (calcolate come negli interni del backend) utili a RQ2/RQ3
EXTRA_KEYS = ["n_gt_px", "n_auto_px", "under_px", "over_px",
              "under_cov_frac", "over_cov_frac", "patch_area_err_pct"]

CSV_COLUMNS = ["experiment", "image", "backend", "scale_px_per_mm", "status"] + EXTRA_KEYS + METRIC_KEYS


def load_bool_mask(path, shape_hw):
    m = np.array(Image.open(path).convert("L"))
    if m.shape != shape_hw:
        return None
    return m > 127


def pair_files(images_dir, gt_dir):
    imgs = sorted([p for p in glob.glob(os.path.join(images_dir, "*"))
                   if p.lower().endswith(IMG_EXTS)],
                  key=lambda p: os.path.basename(p).lower())
    pairs = []
    for ip in imgs:
        stem = os.path.splitext(os.path.basename(ip))[0]
        hits = [g for g in glob.glob(os.path.join(gt_dir, stem + ".*"))
                if g.lower().endswith(IMG_EXTS)]
        pairs.append((stem, ip, hits[0] if hits else None))
    return pairs


def main():
    ap = argparse.ArgumentParser(description="Esperimento A headless sul vero backend dell'app.")
    ap.add_argument("--images", default="Wound Image Dataset_renamed",
                    help="cartella immagini (default: 'Wound Image Dataset_renamed')")
    ap.add_argument("--gt", default="Wound Image Dataset_masks",
                    help="cartella maschere GT, stesso nome-stem (default: 'Wound Image Dataset_masks')")
    ap.add_argument("--out", default="results_all.csv")
    ap.add_argument("--methods", nargs="+", default=["SAM", "GrabCut", "Watershed"],
                    choices=["SAM", "GrabCut", "Watershed"])
    ap.add_argument("--limit", type=int, default=0, help="processa solo le prime N immagini (test)")
    a = ap.parse_args()

    # QApplication offscreen: serve a creare il QObject Backend in modo headless
    app = wa.QApplication.instance() or wa.QApplication(sys.argv)
    be = wa.Backend()

    methods = [LABEL_TO_METHOD[m] for m in a.methods]
    want_sam = "SAM (ViT-B)" in methods

    if want_sam:
        if not getattr(wa, "HAS_SAM", False):
            print("!! torch/segment_anything non disponibili in questo Python -> niente SAM.\n"
                  "   Rilancia con il Python di launch_v17.bat (quello con le dipendenze SAM).")
            methods = [m for m in methods if m != "SAM (ViT-B)"]; want_sam = False
        elif not os.path.exists(wa.SAM_CHECKPOINT):
            print(f"!! checkpoint '{wa.SAM_CHECKPOINT}' non trovato in {os.getcwd()} -> niente SAM.")
            methods = [m for m in methods if m != "SAM (ViT-B)"]; want_sam = False
        else:
            print(f"Carico SAM una volta (device: {wa.SAM_DEVICE}) ...")
            mm = wa.sam_model_registry["vit_b"](checkpoint=wa.SAM_CHECKPOINT)
            mm.to(wa.SAM_DEVICE).eval()
            be.predictor = wa.SamPredictor(mm)

    if not os.path.isdir(a.images):
        sys.exit(f"Cartella immagini non trovata: '{a.images}'\n"
                 f"   Lancia prima Picture_rename.py oppure passa --images \"percorso\".")
    if not os.path.isdir(a.gt):
        sys.exit(f"Cartella maschere GT non trovata: '{a.gt}'\n"
                 f"   Creala e riempila con le maschere (stesso nome-stem), oppure passa --gt \"percorso\".")

    pairs = pair_files(a.images, a.gt)
    if a.limit:
        pairs = pairs[:a.limit]
    if not pairs:
        sys.exit(f"Nessuna immagine in '{a.images}'.")
    if all(gp is None for _, _, gp in pairs):
        sys.exit(f"Trovate {len(pairs)} immagini in '{a.images}' ma NESSUNA maschera GT in '{a.gt}'.\n"
                 f"   La cartella maschere e' vuota: annota prima le maschere (stesso nome dell'immagine,\n"
                 f"   es. {pairs[0][0]}.png) e poi rilancia.")

    rows = []
    fails = {METHOD_TO_LABEL[m]: 0 for m in methods}
    no_gt = mismatch = empty_gt = 0
    t0 = time.time()

    for k, (stem, ip, gp) in enumerate(pairs, 1):
        if gp is None:
            no_gt += 1; print(f"[{k}/{len(pairs)}] {stem}: nessuna maschera GT -> saltata"); continue
        try:
            img = np.array(Image.open(ip).convert("RGB"))
        except Exception as e:
            print(f"[{k}/{len(pairs)}] {stem}: errore lettura immagine: {e}"); continue
        gt = load_bool_mask(gp, img.shape[:2])
        if gt is None:
            mismatch += 1; print(f"[{k}/{len(pairs)}] {stem}: GT di dimensione diversa dall'immagine -> saltata"); continue
        if gt.sum() == 0:
            empty_gt += 1; print(f"[{k}/{len(pairs)}] {stem}: GT vuota -> saltata"); continue

        # stato del backend per questa immagine (scale-free: px_per_mm = None)
        be.img_np = img; be.img_h, be.img_w = img.shape[:2]
        be.px_per_mm = None
        be.gt_mask = gt
        be.gt_contour = be._mask_to_contour(gt)      # ricostruito dalla maschera salvata

        if want_sam:
            try:
                be._sam_encode()                     # encode di QUESTA immagine, una volta
            except Exception as e:
                print(f"   SAM encode fallito su {stem}: {e}")

        area_gt_px = int(gt.sum())
        for m in methods:
            label = METHOD_TO_LABEL[m]
            row = {c: "" for c in CSV_COLUMNS}
            row.update(experiment="A", image=stem, backend=label, scale_px_per_mm="")
            try:
                mask = be._clean_mask(be._segment(m))
                if mask is None or mask.sum() == 0:
                    be.auto_mask = None; be.auto_contour = None; be.metrics = {}
                    row["status"] = "fail:empty_mask"; fails[label] += 1
                    rows.append(row); continue
                be.auto_mask = mask
                be.auto_contour = be._mask_to_contour(mask)
                be.metrics = be._compute_metrics()
                under_px = int(np.logical_and(gt, ~mask).sum())
                over_px = int(np.logical_and(mask, ~gt).sum())
                row.update(n_gt_px=area_gt_px, n_auto_px=int(mask.sum()),
                           under_px=under_px, over_px=over_px,
                           under_cov_frac=round(under_px / (area_gt_px + 1e-9), 6),
                           over_cov_frac=round(over_px / (area_gt_px + 1e-9), 6))
                if "area_rel_err" in be.metrics:
                    row["patch_area_err_pct"] = round(100 * be.metrics["area_rel_err"], 4)
                for kk in METRIC_KEYS:
                    v = be.metrics.get(kk, "")
                    row[kk] = "" if v is None else v
                row["status"] = "ok"
            except Exception as e:
                row["status"] = f"fail:{type(e).__name__}"; fails[label] += 1
            rows.append(row)

        if k % 10 == 0 or k == len(pairs):
            print(f"[{k}/{len(pairs)}] elaborate ({time.time()-t0:.0f}s)")

    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader(); w.writerows(rows)

    ok = sum(1 for r in rows if r["status"] == "ok")
    print(f"\nScritte {len(rows)} righe ({ok} ok) -> {a.out}")
    print(f"  immagini: {len(pairs)} | senza GT: {no_gt} | dimensioni diverse: {mismatch} | GT vuote: {empty_gt}")
    for label, c in fails.items():
        n = sum(1 for r in rows if r["backend"] == label and r["status"] == "ok")
        print(f"  {label:10s}: {n} ok, {c} falliti")
    print("\nUNITA': scala assente -> *_mm2 in px^2, *_mm in px. Scale-free (valide cosi'): "
          "dice, iou, area_rel_err, patch_area_err_pct, cri_lambda3, under/over_cov_frac, "
          "compactness_*, aspect_ratio_*.")


if __name__ == "__main__":
    main()
