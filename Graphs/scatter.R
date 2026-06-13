# ============================================================
# scatter.R  --  Fig. 9: Patch area error vs Dice (colori per back-end + marginali)
# Default: tutti i dati (RQ2 ha bisogno dei punti a basso Dice / alto errore).
# Salva: scatter.pdf + scatter.svg (sovrascrive).
#   install.packages(c("ggplot2","ggExtra","svglite"))
# ============================================================
library(ggplot2); library(ggExtra)

## ============================================================
## STILE  ---- modifica SOLO qui ----
## ============================================================
infile <- "results_full.csv"       # RQ2: dati completi.  Per soli puliti: "results_clean.csv"
out_name <- "scatter"; out_dir <- "."
fig_w <- 7.4; fig_h <- 6.0; font <- "Arial"
ts_axis <- 14; ts_legend <- 12

backend_order <- c("SAM","GrabCut","Watershed")
col <- c(SAM = "#0072B2", GrabCut = "#E69F00", Watershed = "#009E73")  # Okabe-Ito (come box)

pt_size <- 2.0; pt_alpha <- 0.75          # pallini: solo riempimento, niente contorno (shape 16)
fit_line <- TRUE; fit_lw <- 0.5; fit_lty <- "dashed"; fit_col <- "#444444"  # retta sottilissima
axis_line_col <- "grey50"

xlab <- "Dice / -"
ylab <- "Patch area error / %"
xlim <- c(NA, NA); ylim <- c(0, NA)       # es. ylim<-c(0,60) per tagliare la coda

# marginali: istogrammi, SOLO riempimento (niente contorno), stesso numero di bin, ben riscalati
marg_bins <- 40
marg_fill <- "#cfd4d9"                     # colore riempimento barre
marg_ratio <- 8                            # piu' grande = marginali piu' piccoli/corti (riscala)
## ============================================================

stopifnot(file.exists(infile))
d <- read.csv(infile, stringsAsFactors = FALSE)
d <- d[d$status == "ok" & !is.na(d$dice) & !is.na(d$patch_area_err_pct), ]
d$backend <- factor(d$backend, levels = backend_order)

g <- ggplot(d, aes(dice, patch_area_err_pct, colour = backend))
if (fit_line)
  g <- g + geom_smooth(method = "lm", formula = y ~ x, se = FALSE,
                       colour = fit_col, linewidth = fit_lw, linetype = fit_lty)
g <- g +
  geom_point(shape = 16, size = pt_size, alpha = pt_alpha) +   # shape 16 = pieno, nessun bordo
  scale_colour_manual(values = col, name = NULL) +
  labs(x = xlab, y = ylab) +                                   # nessuna scritta Spearman/linear
  coord_cartesian(xlim = if (all(is.na(xlim))) NULL else xlim,
                  ylim = if (all(is.na(ylim))) NULL else ylim) +
  theme(text = element_text(family = font, colour = "black"),
        axis.text = element_text(size = ts_axis, colour = "black"),
        axis.title = element_text(size = ts_axis + 2, colour = "black"),
        legend.position = c(0.99, 0.99), legend.justification = c(1, 1),
        legend.text = element_text(size = ts_legend),
        axis.ticks = element_blank(), axis.line = element_line(colour = axis_line_col),
        panel.grid = element_blank(),
        panel.background = element_rect(fill = "transparent", colour = NA),
        plot.background = element_rect(fill = "transparent", colour = NA))

# istogrammi marginali: stesso bins su entrambi, SOLO riempimento (outline = fill -> invisibile)
pm <- ggMarginal(g, type = "histogram", bins = marg_bins, size = marg_ratio,
                 fill = marg_fill, colour = marg_fill, groupColour = FALSE, groupFill = FALSE)

save_both <- function(p, name, w, h) {
  if (!dir.exists(out_dir)) dir.create(out_dir, recursive = TRUE)
  cairo_pdf(file.path(out_dir, paste0(name, ".pdf")), width = w, height = h); print(p); dev.off()
  svglite::svglite(file.path(out_dir, paste0(name, ".svg")), width = w, height = h); print(p); dev.off()
}
save_both(pm, out_name, fig_w, fig_h)
cat("Salvati:", paste0(out_name, c(".pdf", ".svg")), "\n")
