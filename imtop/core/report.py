"""The printable report: one self-contained HTML page, and the PDF made from it.

Self-contained is the whole point. The images travel inside the file as data
URLs and the stylesheet is inline, so the page can be printed to PDF by the
app's own engine with nothing fetched from anywhere: no internet, no fonts to
install, no second renderer to keep in sync.

The look is the Review Desk one turned inside out: the app is dark because it
sits behind a photo all day, a report is read on paper. Same family all the
same, same gold accent, same type, same radii, same rule that a block is told
apart from its neighbour by its fill and never by a hairline.

What the report holds, in order: the five numbers that answer "did it work?",
the facts of the run, the photo and the photo with both masks, a short note on
how to read the rest, the 22 metrics with a one-line meaning each, then one
figure per metric (the geometry the number was measured on, drawn the way the
app draws it on screen), the 3D patch when one was built, and a footer.

The table mirrors ``MGRPS`` in ``imtop/ui/js/metrics.js``: same groups, same
order, same labels, same decimals, so the report and the panel can never
disagree about a number. ``dev/test_phase4.py`` reads both and fails if they
drift apart.
"""
from __future__ import annotations

import base64
import html
from datetime import datetime
from pathlib import Path

from ..config import APP_VERSION, APP_CREDIT, ASSETS_DIR
from . import figures, imaging
from .results import output_path, report_path
from .session import Session

# ── the table ────────────────────────────────────────────────────────────────
# `u` is what the value measures ('len', 'area', or nothing for a ratio) and
# decides which unit label it gets; `p` is how many decimals it deserves.
# `tip` is the row's tooltip in the app, kept here for the parity test.
METRIC_LAYOUT = [
    {"t": "Overlap", "g": "ov", "items": [
        {"k": "dice", "l": "Dice", "p": 4},
        {"k": "iou", "l": "IoU / Jaccard", "p": 4},
    ]},
    {"t": "Area", "g": "ar", "items": [
        {"k": "area_gt_mm2", "l": "Manual area", "u": "area", "p": 1},
        {"k": "area_auto_mm2", "l": "Auto area", "u": "area", "p": 1},
        {"k": "area_rel_err", "l": "Rel. area error", "p": 3},
        {"k": "under_cov_mm2", "l": "Uncovered", "u": "area", "p": 1,
         "tip": "Wound area the patch leaves uncovered (manual minus auto)"},
        {"k": "over_cov_mm2", "l": "Excess", "u": "area", "p": 1,
         "tip": "Patch area on healthy skin (auto minus manual)"},
        {"k": "cri_lambda3", "l": "Cost index λ=3", "p": 3,
         "tip": "Coverage cost: uncovered wound weighs 3 times the excess, "
                "relative to the wound area"},
    ]},
    {"t": "Distance", "g": "di", "items": [
        {"k": "hausdorff_mm", "l": "Hausdorff", "u": "len", "p": 2},
        {"k": "avg_surf_dist_mm", "l": "Avg surface dist", "u": "len", "p": 2},
    ]},
    {"t": "Manual geometry", "g": "gm", "items": [
        {"k": "perimeter_gt_mm", "l": "Perimeter", "u": "len", "p": 2},
        {"k": "compactness_gt", "l": "Compactness", "p": 3},
        {"k": "bbox_gt_major_mm", "l": "BBox major", "u": "len", "p": 2},
        {"k": "bbox_gt_minor_mm", "l": "BBox minor", "u": "len", "p": 2},
        {"k": "aspect_ratio_gt", "l": "Aspect ratio", "p": 3},
    ]},
    {"t": "Auto geometry", "g": "ga", "items": [
        {"k": "perimeter_auto_mm", "l": "Perimeter", "u": "len", "p": 2},
        {"k": "compactness_auto", "l": "Compactness", "p": 3},
        {"k": "bbox_auto_major_mm", "l": "BBox major", "u": "len", "p": 2},
        {"k": "bbox_auto_minor_mm", "l": "BBox minor", "u": "len", "p": 2},
        {"k": "aspect_ratio_auto", "l": "Aspect ratio", "p": 3},
        {"k": "bbox_auto_x_mm", "l": "A (X)", "u": "len", "p": 2},
        {"k": "bbox_auto_y_mm", "l": "B (Y)", "u": "len", "p": 2},
    ]},
]

# What each group is about, one line under its heading.
GROUP_ABOUT = {
    "ov": "How well the automatic segmentation agrees with the manual trace. "
          "Both scores run from 0 (no overlap) to 1 (identical).",
    "ar": "How big the two regions are, and how much of the wound the patch "
          "would miss or add.",
    "di": "How far apart the two boundaries are, in the length unit of the image.",
    "gm": "Shape and size of the hand-traced outline.",
    "ga": "Shape and size of the automatic outline, the one the patch is cut from.",
}

# What each number means (the table) and what its figure shows (the cards).
METRIC_TEXT = {
    "dice": ("Agreement between the two masks: twice the shared area over the sum of "
             "the two areas. 1 is a perfect match.",
             "Shared area in the dark shade, the part each mask has on its own in the lighter ones."),
    "iou": ("Shared area over the area covered by either mask (Jaccard index). At or "
            "below Dice, and harder on any disagreement.",
            "The union in the light shade, the intersection in the dark one."),
    "area_gt_mm2": ("Area inside the hand-traced boundary: the wound as the operator sees "
                    "it, the reference for every comparison.",
                    "The manual outline, filled."),
    "area_auto_mm2": ("Area inside the automatic boundary: the shape the patch is cut from.",
                      "The automatic outline, filled."),
    "area_rel_err": ("Difference between the two areas over the manual area. 0.05 means the "
                     "automatic area is 5 % off, either way.",
                     "Wound the automatic outline misses in the dark shade, skin it takes in "
                     "in the light one."),
    "under_cov_mm2": ("Wound the patch would leave uncovered: inside the manual outline, "
                      "outside the automatic one.",
                      "The uncovered wound, filled."),
    "over_cov_mm2": ("Patch lying on healthy skin: inside the automatic outline, outside "
                     "the manual one.",
                     "The excess on healthy skin, filled."),
    "cri_lambda3": ("Coverage cost: uncovered wound counts three times as much as excess on "
                    "skin, relative to the wound area. 0 is a perfect fit, lower is better.",
                    "Uncovered wound in the dark shade, excess in the light one, each as "
                    "strong as its weight in the index."),
    "hausdorff_mm": ("Largest distance between the two boundaries: the single worst local "
                     "disagreement.",
                     "The manual outline coloured by its distance to the automatic one, "
                     "darker is farther; the worst point is joined to its nearest counterpart."),
    "avg_surf_dist_mm": ("Mean distance between the two boundaries, measured from each to "
                         "the other and averaged.",
                         "Both outlines coloured by their local distance to the other one, "
                         "darker is farther."),
    "perimeter_gt_mm": ("Length of the hand-traced boundary.", "The measured boundary."),
    "compactness_gt": ("4πA / P²: 1 for a circle, lower the more irregular the outline.",
                       "The outline against a circle of the same area."),
    "bbox_gt_major_mm": ("Longer side of the smallest upright box around the manual outline.",
                         "The bounding box, with the side that is measured."),
    "bbox_gt_minor_mm": ("Shorter side of that box.",
                         "The bounding box, with the side that is measured."),
    "aspect_ratio_gt": ("Shorter side of the box over the longer one: 1 for a square box, "
                        "small for an elongated wound.",
                        "The bounding box with both sides marked."),
    "perimeter_auto_mm": ("Length of the automatic boundary.", "The measured boundary."),
    "compactness_auto": ("4πA / P²: 1 for a circle, lower the more irregular the outline.",
                         "The outline against a circle of the same area."),
    "bbox_auto_major_mm": ("Longer side of the smallest upright box around the automatic outline.",
                           "The bounding box, with the side that is measured."),
    "bbox_auto_minor_mm": ("Shorter side of that box.",
                           "The bounding box, with the side that is measured."),
    "aspect_ratio_auto": ("Shorter side of the box over the longer one: 1 for a square box, "
                          "small for an elongated wound.",
                          "The bounding box with both sides marked."),
    "bbox_auto_x_mm": ("Width of the box around the automatic outline: the size of the blank "
                       "a patch is cut from.",
                       "The bounding box with its horizontal side measured."),
    "bbox_auto_y_mm": ("Height of the box around the automatic outline.",
                       "The bounding box with its vertical side measured."),
}

# The five numbers that answer "did it work?" before anyone scrolls.
SUMMARY_KEYS = ["dice", "iou", "area_gt_mm2", "area_auto_mm2", "cri_lambda3"]

# The panel's group hues, darkened until they read as ink on white, and a tint
# of each for the group's own strip. Same hue, printable contrast.
GROUP_INK = {"ov": "#2F7A46", "ar": "#2F5EC2", "di": "#B0524A",
             "gm": "#7B44C4", "ga": "#8A6A12"}
GROUP_TINT = {"ov": "#E9F4EC", "ar": "#E9F0FD", "di": "#FCEBE7",
              "gm": "#F1E8FB", "ga": "#FCF3DB"}

REPORT_IMAGE_MAX_SIDE = 1400   # px: enough for a full-page figure, small enough to mail
OVERLAY_ALPHA = 0.32
FIGURE_JPEG_QUALITY = 82       # the per-metric figures: 22 of them, so a little tighter


# ── values ───────────────────────────────────────────────────────────────────
def unit_for(item: dict, info: dict) -> str:
    """The unit label an item carries right now, or ``""`` for a pure ratio."""
    if not item.get("u"):
        return ""
    return info["area_unit"] if item["u"] == "area" else info["length_unit"]


def label_for(item: dict, info: dict) -> str:
    u = unit_for(item, info)
    return f"{item['l']} ({u})" if u else item["l"]


def format_value(item: dict, value, info: dict) -> str:
    """Same rule as ``fmtMetric()`` in the UI: a pixel area is a whole number, a
    pixel length gets one decimal, everything else keeps the item's precision."""
    if value is None:
        return "-"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    p = item["p"]
    if item.get("u") and unit_for(item, info).startswith("px"):
        p = 0 if item["u"] == "area" else 1
    return f"{float(value):.{p}f}"


def metric_rows(metrics: dict, info: dict) -> list[dict]:
    """The table, ready to render: groups of ``{key, label, value, hint}``.
    The hint is the one-line meaning of the number."""
    groups = []
    for grp in METRIC_LAYOUT:
        rows = [{"key": it["k"],
                 "label": label_for(it, info),
                 "value": format_value(it, metrics.get(it["k"]), info),
                 "hint": METRIC_TEXT[it["k"]][0]}
                for it in grp["items"]]
        groups.append({"title": grp["t"], "g": grp["g"], "rows": rows})
    return groups


# ── figures ──────────────────────────────────────────────────────────────────
def _gt_contour(session: Session):
    """The manual contour, rebuilt from the trace when the session dropped it
    (an auto-prompt run keeps the trace but not the rasterised contour)."""
    if session.gt_contour is not None:
        return session.gt_contour
    return session.spline()


def report_figures(session: Session) -> dict:
    """The photo, and the same photo with the two masks filled over it.

    The fill is what makes a report readable at a glance: an outline says where
    the boundary is, a fill says how much of the wound the patch actually got.
    """
    img = session.image
    if img is None:
        return {}
    over = imaging.render_filled_overlay(img, session.gt_mask_or_build(),
                                         session.auto_mask, OVERLAY_ALPHA)
    over = imaging.render_overlay(over, _gt_contour(session), session.auto_contour,
                                  thickness=max(2, round(max(img.shape[:2]) / 700)))

    def small(arr):
        return imaging.to_data_url(imaging.downscale_to(arr, REPORT_IMAGE_MAX_SIDE),
                                   "JPEG", 88)

    return {"photo": small(img), "overlay": small(over)}


def metric_figure_urls(session: Session) -> dict:
    """``{key: data URL}``, one figure per metric, on one crop around the wound."""
    keys = [it["k"] for grp in METRIC_LAYOUT for it in grp["items"]]
    arrays = figures.metric_figures(session.image, _gt_contour(session),
                                    session.auto_contour, keys)
    return {k: imaging.to_data_url(v, "JPEG", FIGURE_JPEG_QUALITY) for k, v in arrays.items()}


def _logo_data_url() -> str:
    p = ASSETS_DIR / "logo.png"
    if not p.is_file():
        return ""
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


# ── html ─────────────────────────────────────────────────────────────────────
_CSS = """
:root{
  --paper:#FFFFFF; --page:#EFEFF2; --panel:#F7F7F9; --chip:#F1F1F4;
  --ink:#17171B; --soft:#45454E; --dim:#7A7A85;
  --gold:#F5C542; --gold-ink:#8A6A12; --tint:#FFF8E4;
  --gt:#2F7A46; --auto:#B0524A;
  --r-md:6px; --r-lg:7px; --r-pill:99px;
  --font:'Segoe UI',system-ui,-apple-system,sans-serif;
  --font-brand:'Abadi','Abadi MT Std','Segoe UI Variable Display','Segoe UI',system-ui,sans-serif;
  --mono:Consolas,'Courier New',monospace;
}
*{box-sizing:border-box;margin:0;padding:0;border:0}
html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{background:var(--page);color:var(--ink);font:12px/1.45 var(--font);padding:18px}
.sheet{max-width:840px;margin:0 auto;background:var(--paper);border-radius:var(--r-lg);
       padding:30px 30px 22px}

/* header */
.hd{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;margin-bottom:18px}
.hd-l{display:flex;align-items:center;gap:11px}
.hd-l img{width:30px;height:30px;border-radius:var(--r-md);display:block}
.brand{font:600 20px/1 var(--font-brand);letter-spacing:.2px}
.brand span{font-weight:400;color:var(--dim);font-size:13px;margin-left:7px;letter-spacing:0}
.hd-sub{color:var(--soft);font-size:11.5px;margin-top:4px}
.hd-r{text-align:right}
.case{font-weight:600;font-size:14px}
.when{color:var(--dim);font-size:11px;margin-top:3px}

/* summary */
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:16px}
.kpi{background:var(--tint);border-radius:var(--r-md);padding:11px 12px}
/* No uppercase here: one of these labels is "Cost index λ=3", and uppercasing
   it turns the lambda into a capital one, which means something else. */
.kpi .k{color:var(--gold-ink);font-size:10px;letter-spacing:.3px;font-weight:600}
.kpi .v{font:600 19px/1.15 var(--font);margin-top:5px;font-variant-numeric:tabular-nums}
.kpi .u{color:var(--soft);font-size:10.5px;font-weight:400;margin-left:4px}

/* facts */
.facts{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:16px}
.fact{background:var(--chip);border-radius:var(--r-md);padding:8px 11px;display:flex;
      justify-content:space-between;gap:10px;min-width:0}
.fact b{font-weight:400;color:var(--dim);white-space:nowrap}
.fact span{font-weight:600;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

/* figures */
.figs{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.fig{background:var(--panel);border-radius:var(--r-lg);padding:8px;break-inside:avoid}
.fig img{width:100%;display:block;border-radius:var(--r-md)}
.cap{color:var(--soft);font-size:10.5px;margin-top:7px}
.legend{display:flex;gap:16px;margin:9px 0 18px;color:var(--soft);font-size:10.5px}
.dot{width:8px;height:8px;border-radius:var(--r-pill);display:inline-block;margin-right:5px;vertical-align:-1px}

/* prose */
h2{font-size:10px;letter-spacing:.9px;text-transform:uppercase;color:var(--dim);
   font-weight:600;margin:18px 0 7px}
.intro{color:var(--soft);font-size:11.5px;line-height:1.55;margin-bottom:4px}
.intro+.intro{margin-top:6px}

/* table */
/* Two columns, each a continuous run of groups. The split is decided in
   Python: a grid of equal cells would leave a hole under the short group,
   and CSS columns break unpredictably across printed pages. */
.keep{break-inside:avoid}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:0 16px;align-items:start}
.col{min-width:0}
.grp{break-inside:avoid;margin-bottom:10px}
.gh{display:flex;align-items:center;font-weight:600;font-size:11.5px;
    padding:5px 9px;border-radius:var(--r-md);margin-bottom:3px}
.row{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
     background:var(--chip);border-radius:var(--r-md);padding:5px 9px;margin-bottom:3px}
.row .l{color:var(--soft);min-width:0}
.row .l i{display:block;font-style:normal;color:var(--dim);font-size:9.5px;line-height:1.3;margin-top:2px}
.row .v{font:600 12.5px/1 var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}

/* one figure per metric. A group moves to the next page whole when it does
   not fit, so a heading is never left alone at the bottom of a page. */
.mgroup{margin-bottom:12px;break-inside:avoid}
.mgroup .gh{margin-bottom:5px}
.gabout{color:var(--soft);font-size:10.5px;line-height:1.45;margin:0 0 7px 2px}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.mcard{background:var(--panel);border-radius:var(--r-lg);padding:7px;break-inside:avoid}
.mcard img{width:100%;display:block;border-radius:var(--r-md)}
.mc-h{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin-top:6px}
.mc-l{font-weight:600;font-size:11px;min-width:0}
.mc-v{font:600 11.5px/1 var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}
.mc-d{color:var(--dim);font-size:9.5px;line-height:1.35;margin-top:3px}

/* notes + footer */
.note{background:var(--tint);border-radius:var(--r-md);padding:9px 12px;color:var(--gold-ink);
      font-size:10.5px;margin-top:14px}
.foot{color:var(--dim);font-size:10px;margin-top:14px;display:flex;justify-content:space-between;gap:12px}

@page{size:A4;margin:12mm 10mm}
@media print{
  body{background:var(--paper);padding:0}
  .sheet{max-width:none;border-radius:0;padding:0}
}
"""


def _esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _kpi_cards(metrics: dict, info: dict) -> str:
    by_key = {it["k"]: it for grp in METRIC_LAYOUT for it in grp["items"]}
    cards = []
    for key in SUMMARY_KEYS:
        it = by_key[key]
        unit = unit_for(it, info)
        cards.append(
            '<div class="kpi"><div class="k">{k}</div><div class="v">{v}{u}</div></div>'.format(
                k=_esc(it["l"]),
                v=_esc(format_value(it, metrics.get(key), info)),
                u=f'<span class="u">{_esc(unit)}</span>' if unit else ""))
    return '<section class="kpis">' + "".join(cards) + "</section>"


def _facts(session: Session, info: dict) -> str:
    s = session
    scale = f"{info['px_per_mm']:.3f} px/mm" if info["calibrated"] else "not calibrated"
    items = [
        ("Method", s.method or "-"),
        ("Scale", scale),
        ("Image", f"{s.w} × {s.h} px"),
        ("Units", f"{info['length_unit']} · {info['area_unit']}"),
        ("Source", (s.image_path.name if s.image_path else s.image_name) or "-"),
        ("App", f"IMTOP v{APP_VERSION}"),
    ]
    return '<section class="facts">' + "".join(
        f'<div class="fact"><b>{_esc(k)}</b><span>{_esc(v)}</span></div>'
        for k, v in items) + "</section>"


def _figures(figs: dict) -> str:
    if not figs:
        return ""
    return (
        '<section class="figs">'
        f'<div class="fig"><img src="{figs["photo"]}" alt="Source image">'
        '<div class="cap">Source image</div></div>'
        f'<div class="fig"><img src="{figs["overlay"]}" alt="Segmentation overlay">'
        '<div class="cap">Segmentation overlay</div></div>'
        '</section>'
        '<div class="legend">'
        '<span><i class="dot" style="background:var(--gt)"></i>Manual trace</span>'
        '<span><i class="dot" style="background:var(--auto)"></i>Automatic segmentation</span>'
        '</div>')


def _fmt(metrics: dict, key: str, p: int):
    v = metrics.get(key)
    return None if v is None else f"{float(v):.{p}f}"


def _in_short(session: Session, metrics: dict, info: dict) -> str:
    """The result in plain words, before any table."""
    method = session.method or "automatic"
    u, ua = info["length_unit"], info["area_unit"]
    dice, iou = _fmt(metrics, "dice", 3), _fmt(metrics, "iou", 3)
    if dice is None:
        return ""
    ap = 0 if not info["calibrated"] else 1
    parts = [f"The {method} outline agrees with the manual trace with a Dice score of {dice} "
             f"(IoU {iou})."]
    a_gt, a_auto, rel = _fmt(metrics, "area_gt_mm2", ap), _fmt(metrics, "area_auto_mm2", ap), metrics.get("area_rel_err")
    if a_gt and a_auto and rel is not None:
        parts.append(f"It encloses {a_auto} {ua} against {a_gt} {ua} traced by hand, a difference "
                     f"of {100 * float(rel):.1f} %.")
    under, over, cri = _fmt(metrics, "under_cov_mm2", ap), _fmt(metrics, "over_cov_mm2", ap), _fmt(metrics, "cri_lambda3", 3)
    if under and over:
        parts.append(f"A patch cut on it would leave {under} {ua} of wound uncovered and cover "
                     f"{over} {ua} of healthy skin, a coverage cost index of {cri}.")
    elif cri:
        parts.append(f"The coverage cost index is {cri}.")
    avg, haus = _fmt(metrics, "avg_surf_dist_mm", 2), _fmt(metrics, "hausdorff_mm", 2)
    if avg and haus:
        parts.append(f"The two boundaries are {avg} {u} apart on average and {haus} {u} apart "
                     f"at the worst point.")
    return '<h2>In short</h2><p class="intro">' + _esc(" ".join(parts)) + "</p>"


def _how_to_read(session: Session, info: dict) -> str:
    """Three short paragraphs: what was compared with what, how the numbers
    were produced, and in which unit."""
    method = session.method or "automatic"
    first = (f"The wound in the photo was outlined twice: by hand (the manual trace, in "
             f"green, drawn by the operator) and by the {method} algorithm (the automatic "
             f"segmentation, in red). The manual trace is the reference; the automatic "
             f"outline is the shape the patch is cut from. Every number below compares the "
             f"two or describes one of them, and the figures further down show what each "
             f"number was measured on.")
    how = (f"How the numbers were produced: the operator placed a few points along the wound "
           f"boundary, and a smooth closed curve through them became the manual trace. The "
           f"{method} algorithm was then run on the photo, seeded by that trace, and its result "
           f"cleaned into one closed region. Both outlines were rasterised to masks the size of "
           f"the photo; the overlap scores and the areas come from the masks, the distances and "
           f"the shape descriptors from the boundaries.")
    if info["calibrated"]:
        unit = (f"Lengths are in millimetres and areas in square millimetres, from a scale "
                f"of {info['px_per_mm']:.3f} pixels per millimetre set on a reference of "
                f"known length in the photo.")
    else:
        unit = ("No scale was set for this photo, so lengths are in pixels and areas in "
                "square pixels. Ratios and scores do not depend on the scale.")
    return ('<h2>How to read this report</h2>'
            f'<p class="intro">{_esc(first)}</p><p class="intro">{_esc(how)}</p>'
            f'<p class="intro">{_esc(unit)}</p>')


def _group_block(title: str, g: str, rows: list[dict]) -> str:
    ink, tint = GROUP_INK[g], GROUP_TINT[g]
    out = [f'<div class="grp"><div class="gh" style="background:{tint};color:{ink}">'
           f'<i class="dot" style="background:{ink}"></i>{_esc(title)}</div>']
    for row in rows:
        hint = f'<i>{_esc(row["hint"])}</i>' if row.get("hint") else ""
        out.append(f'<div class="row"><span class="l">{_esc(row["label"])}{hint}</span>'
                   f'<span class="v" style="color:{ink}">{_esc(row["value"])}</span></div>')
    out.append("</div>")
    return "".join(out)


def _two_columns(blocks: list[tuple[str, float]]) -> str:
    """Fill the left column until it holds about half the weight, then the
    right one. Order is preserved, and neither column ends with a hole."""
    total = sum(w for _, w in blocks)
    left, right, used = [], [], 0.0
    for html_block, weight in blocks:
        if used + weight / 2 <= total / 2 or not left:
            left.append(html_block)
            used += weight
        else:
            right.append(html_block)
    return ('<section class="grid"><div class="col">' + "".join(left)
            + '</div><div class="col">' + "".join(right) + "</section>")


def _table(groups: list[dict]) -> str:
    # A group weighs its rows plus its header; a meaning line makes a row taller.
    blocks = [(_group_block(g["title"], g["g"], g["rows"]),
               1.5 + sum(1.6 if r.get("hint") else 1.0 for r in g["rows"]))
              for g in groups]
    # Heading and table kept together: a heading alone at the foot of a page
    # is what "break-inside: avoid" on the pair prevents.
    return '<section class="keep"><h2>Metrics</h2>' + _two_columns(blocks) + "</section>"


def _metric_cards(groups: list[dict], urls: dict) -> str:
    """One card per metric: its figure, the label and value, what is drawn."""
    if not urls:
        return ""
    out = ['<h2>Metric by metric</h2>',
           '<p class="intro">Each figure is the drawing the app shows when the metric is '
           'clicked in the Metrics step: the geometry that number was measured on, on a '
           'crop of the photo around the wound. The manual outline is drawn solid, the '
           'automatic one dashed.</p>']
    for g in groups:
        ink, tint = GROUP_INK[g["g"]], GROUP_TINT[g["g"]]
        out.append(f'<section class="mgroup"><div class="gh" style="background:{tint};color:{ink}">'
                   f'<i class="dot" style="background:{ink}"></i>{_esc(g["title"])}</div>'
                   f'<p class="gabout">{_esc(GROUP_ABOUT[g["g"]])}</p><div class="cards">')
        for row in g["rows"]:
            url = urls.get(row["key"])
            if not url:
                continue
            shown = METRIC_TEXT[row["key"]][1]
            out.append(f'<div class="mcard"><img src="{url}" alt="">'
                       f'<div class="mc-h"><span class="mc-l">{_esc(row["label"])}</span>'
                       f'<span class="mc-v" style="color:{ink}">{_esc(row["value"])}</span></div>'
                       f'<div class="mc-d">{_esc(shown)}</div></div>')
        out.append("</div></section>")
    return "".join(out)


def _patch_section(patch: dict | None, info: dict) -> str:
    """The 3D patch parameters, when step 6 built one. Optional by design: a
    report written straight after the metrics has no patch to describe."""
    if not patch:
        return ""
    u = info["length_unit"]

    def mm(v):
        return "-" if v is None else f"{float(v):.1f} {u}"

    params = [("Thickness", mm(patch.get("thickness"))),
              ("Edge size", mm(patch.get("edge"))),
              ("Edge mode", patch.get("mode") or "-"),
              ("Offset", mm(patch.get("offset")))]
    # Two columns, not four: "Chamfer 45°" has to fit at A4 width without the
    # chip clipping it to an ellipsis.
    out = ['<h2>3D patch</h2>',
           '<p class="intro">The solid built on step 6 from the automatic outline (and, for '
           'comparison, from the manual one): the wound shape extruded to the thickness '
           'below, with the chosen edge and an optional margin of healthy skin around it '
           '(the offset). The volumes are those of the solids on screen.</p>',
           '<section class="facts" style="grid-template-columns:repeat(2,1fr)">',
           "".join(f'<div class="fact"><b>{_esc(k)}</b><span>{_esc(v)}</span></div>'
                   for k, v in params),
           "</section>"]

    cols = []
    for title, side, g in (("Manual patch", patch.get("gt"), "ov"),
                           ("Auto patch", patch.get("auto"), "ga")):
        if not side:
            continue
        rows = [{"label": f"Area ({u}²)", "value": side.get("area")},
                {"label": f"Perimeter ({u})", "value": side.get("perimeter")},
                {"label": f"Volume ({u}³)", "value": side.get("volume")}]
        for r in rows:
            r["value"] = "-" if r["value"] is None else f"{float(r['value']):.1f}"
        cols.append((_group_block(title, g, rows), 4.5))
    if cols:
        out.append(_two_columns(cols))
    return "".join(out)


def build_report_html(session: Session, patch3d: dict | None = None) -> str:
    """The whole report as one HTML string. No external file is referenced."""
    info = session.metrics_info()
    metrics = session.public_metrics()
    logo = _logo_data_url()
    case = (session.image_path.name if session.image_path else session.image_name) or "Untitled"
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    groups = metric_rows(metrics, info)

    note = ""
    if not info["calibrated"]:
        note = ('<div class="note">No scale was set for this image, so every area and every '
                'length above is in pixels, not millimetres. The keys keep their historical '
                '"_mm" suffix in the JSON export.</div>')

    head = ('<header class="hd"><div class="hd-l">'
            + (f'<img src="{logo}" alt="">' if logo else "")
            + '<div><div class="brand">IMTOP<span>Wound Annotator</span></div>'
            '<div class="hd-sub">Segmentation report</div></div></div>'
            f'<div class="hd-r"><div class="case">{_esc(case)}</div>'
            f'<div class="when">{_esc(when)}</div></div></header>')

    body = "".join([
        head,
        _kpi_cards(metrics, info),
        _facts(session, info),
        _in_short(session, metrics, info),
        _figures(report_figures(session)),
        _how_to_read(session, info),
        _table(groups),
        _metric_cards(groups, metric_figure_urls(session)),
        _patch_section(patch3d, info),
        note,
        f'<div class="foot"><span>Generated by IMTOP v{_esc(APP_VERSION)}. {_esc(APP_CREDIT)}.</span>'
        f'<span>{_esc(when)}</span></div>',
    ])

    return ('<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
            f'<title>IMTOP report · {_esc(case)}</title>'
            f"<style>{_CSS}</style></head><body>"
            f'<div class="sheet">{body}</div></body></html>')


def default_report_path(session: Session) -> Path:
    """The report is a PDF: the name the save dialog proposes,
    ``imtop_report_<image>_<method>_<date_time>.pdf``."""
    return report_path(session)


def save_report_html(session: Session, path: str | Path | None = None,
                     patch3d: dict | None = None) -> Path:
    """Write the report page as an HTML file (the PDF is printed from the same
    page by ``imtop.printing``). Next to the image unless ``path`` says otherwise."""
    out = Path(path) if path else output_path(session, "_report.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_report_html(session, patch3d), encoding="utf-8")
    return out
