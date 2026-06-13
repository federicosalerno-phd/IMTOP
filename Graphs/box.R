# ============================================================
# box.R  --  Fig. 7: Dice / IoU / Patch area error / Hausdorff (violin, stile tesi)
# Legge results_clean.csv (gemello del foglio results_clean dell'Excel).
# Salva: box.pdf + box.svg (sovrascrive).  Pacchetti:
#   install.packages(c("ggplot2","ggpubr","patchwork","svglite"))
# ============================================================
library(ggplot2); library(ggpubr); library(patchwork)

## ============================================================
## STILE  ---- modifica SOLO qui ----
## ============================================================
infile <- "results_clean.csv"      # per i dati completi: "results_full.csv"
out_name <- "box"; out_dir <- "."
ncol_layout <- 4                   # 4 = una riga (tutti e 4 affiancati) | 2 = griglia 2x2
fig_w <- 16; fig_h <- 4.3
font <- "Arial"                    # "Arial" / "Aptos" / "sans"
ts_axis <- 14; ts_title <- 16; ts_xtick <- 13; ts_signif <- 4.3

backend_order <- c("SAM","GrabCut","Watershed")
col <- c(SAM = "#0072B2", GrabCut = "#E69F00", Watershed = "#009E73")  # Okabe-Ito

violin_width <- 0.90; violin_alpha <- 0.30; violin_lw <- 0.20; violin_adjust <- 0.5; violin_trim <- TRUE
box_width <- 0.16; box_alpha <- 0.28; box_lw <- 0.20
pt_size <- 1.5; pt_alpha <- 0.45; pt_jitter <- 0.06
mean_col <- "#8B0000"; mean_size <- 1.6
sig_bracket <- 0.2; sig_tip <- 0.01; sig_short <- 0.05; sig_long <- 0.13; sig_toppad <- 0.06
axis_line_col <- "grey50"

# nomi assi: grandezza / unita'  (adimensionale: / -)
lab <- c(dice = "Dice / -", iou = "IoU / -",
         patch_area_err_pct = "Patch area error / %", hausdorff_mm = "Hausdorff / px")
## ============================================================

stopifnot(file.exists(infile))
d <- read.csv(infile, stringsAsFactors = FALSE)
d <- d[d$status == "ok", ]
d$backend <- factor(d$backend, levels = backend_order)

stars <- function(p) ifelse(is.na(p), "ns", ifelse(p < 1e-4, "****", ifelse(p < 1e-3, "***",
                       ifelse(p < 1e-2, "**", ifelse(p < 5e-2, "*", "ns")))))

pvals <- function(metric) {
  w <- reshape(d[, c("image", "backend", metric)], idvar = "image", timevar = "backend", direction = "wide")
  names(w) <- sub(paste0(metric, "."), "", names(w), fixed = TRUE)
  w <- w[stats::complete.cases(w[, backend_order]), ]
  cmp <- list(c("SAM", "GrabCut"), c("GrabCut", "Watershed"), c("SAM", "Watershed"))
  ps <- sapply(cmp, function(c2) tryCatch(suppressWarnings(
    wilcox.test(w[[c2[1]]], w[[c2[2]]], paired = TRUE)$p.value), error = function(e) NA))
  padj <- p.adjust(ps, method = "bonferroni")
  data.frame(group1 = sapply(cmp, `[`, 1), group2 = sapply(cmp, `[`, 2),
             p.signif = stars(padj), stringsAsFactors = FALSE)
}

panel <- function(metric) {
  dat <- d[!is.na(d[[metric]]), ]
  yr <- range(dat[[metric]]); r <- diff(yr); if (r == 0) r <- 1
  pv <- pvals(metric)
  pv$y.position <- c(yr[2] + sig_short * r, yr[2] + sig_short * r, yr[2] + sig_long * r)
  ytop <- yr[2] + (sig_long + sig_toppad) * r
  ggplot(dat, aes(backend, .data[[metric]], fill = backend, colour = backend)) +
    geom_violin(alpha = violin_alpha, linewidth = violin_lw, width = violin_width,
                trim = violin_trim, adjust = violin_adjust) +
    geom_jitter(position = position_jitter(width = pt_jitter, height = 0),
                size = pt_size, alpha = pt_alpha, shape = 16, stroke = 0) +
    geom_boxplot(width = box_width, alpha = box_alpha, outlier.shape = NA,
                 linewidth = box_lw, colour = "black") +
    stat_summary(fun = mean, geom = "point", colour = mean_col, fill = mean_col, size = mean_size) +
    stat_pvalue_manual(pv, label = "p.signif", y.position = "y.position",
                       tip.length = sig_tip, bracket.size = sig_bracket, size = ts_signif, color = "black") +
    scale_fill_manual(values = col) + scale_colour_manual(values = col) +
    scale_y_continuous(expand = expansion(mult = c(0.03, 0))) +
    coord_cartesian(ylim = c(yr[1] - 0.03 * r, ytop), clip = "off") +
    labs(x = NULL, y = lab[[metric]]) +
    theme(text = element_text(family = font, colour = "black"),
          axis.text = element_text(size = ts_axis, colour = "black"),
          axis.text.x = element_text(size = ts_xtick),
          axis.title = element_text(size = ts_title, colour = "black"),
          axis.ticks = element_blank(), axis.line = element_line(colour = axis_line_col),
          panel.grid = element_blank(),
          panel.background = element_rect(fill = "transparent", colour = NA),
          plot.background = element_rect(fill = "transparent", colour = NA),
          legend.position = "none", plot.margin = margin(8, 12, 4, 8))
}

P <- wrap_plots(lapply(names(lab), panel), ncol = ncol_layout)

save_both <- function(p, name, w, h) {
  if (!dir.exists(out_dir)) dir.create(out_dir, recursive = TRUE)
  ggsave(file.path(out_dir, paste0(name, ".pdf")), p, device = cairo_pdf, width = w, height = h, bg = "transparent")
  ggsave(file.path(out_dir, paste0(name, ".svg")), p, device = svglite::svglite, width = w, height = h, bg = "transparent")
}
save_both(P, out_name, fig_w, fig_h)
cat("Salvati:", paste0(out_name, c(".pdf", ".svg")), "\n")
