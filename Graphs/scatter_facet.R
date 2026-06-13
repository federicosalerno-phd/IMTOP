# ============================================================
# scatter_facet.R  --  RQ2: Patch area error vs Dice, per back-end
# Scatter + LOESS liscio + rho di Spearman annotato. results_full.csv (status ok).
#   install.packages(c("ggplot2","svglite"))
# ============================================================
library(ggplot2)

## ============================================================
## STILE / PARAMETRI  ---- modifica SOLO qui ----
## ============================================================
infile  <- "results_full.csv"; out_name <- "scatter_facet"; out_dir <- "."
fig_w   <- 10; fig_h <- 3.8; font <- "Arial"

# --- trend ---
sm_method <- "loess"   # "loess" (consigliato) | "lm" (retta) | "gam"
sm_span   <- 0.5      # solo per loess: piu' alto = piu' liscio (0.6-1.0)
sm_se     <- TRUE     # TRUE per mostrare la banda di confidenza

# --- punti ---
pt_size  <- 2.5       # dimensione pallini (era ~1). Alza per ingrandirli
pt_alpha <- 0.8
line_lw  <- 0.7        # spessore linea di tendenza

# --- etichetta rho ---
show_rho <- TRUE
x_rho <- 0.985; y_rho <- 29; ts_rho <- 4.2; rho_col <- "grey25"

# --- colori (Okabe-Ito): punti chiari, linea scura ---
cols  <- c(SAM="#0072B2", GrabCut="#E69F00", Watershed="#009E73")
darks <- c(SAM="#024E7A", GrabCut="#8A6200", Watershed="#00684D")

# --- assi ---
y_min <- 0; y_max <- 30; y_breaks <- seq(0, 30, 5)
x_min <- 0.82; x_max <- 1.00; x_breaks <- c(0.85, 0.90, 0.95, 1.00)
ts_strip <- 12; ts_axis <- 12; ts_tick <- 10; axis_col <- "grey50"
## ============================================================

d <- read.csv(infile, stringsAsFactors = FALSE)
d <- d[d$status == "ok", c("backend", "dice", "patch_area_err_pct")]
d$backend <- factor(d$backend, levels = names(cols))

## etichette rho di Spearman per ogni pannello
lab <- do.call(rbind, lapply(levels(d$backend), function(b) {
  s <- d[d$backend == b, ]
  r <- cor(s$dice, s$patch_area_err_pct, method = "spearman")
  data.frame(backend = b, dice = x_rho, patch_area_err_pct = y_rho,
             lab = sprintf("rho == %.2f", r))
}))
lab$backend <- factor(lab$backend, levels = names(cols))

## smoother per ogni back-end (un layer a colore scuro per pannello)
sm <- function(b) geom_smooth(data = d[d$backend == b, ],
                              method = sm_method, span = sm_span, se = sm_se,
                              colour = darks[[b]], linewidth = line_lw)

g <- ggplot(d, aes(dice, patch_area_err_pct)) +
  geom_point(aes(colour = backend), alpha = pt_alpha, size = pt_size,
             shape = 16, stroke = 0) +
  sm("SAM") + sm("GrabCut") + sm("Watershed") +
  {if (show_rho) geom_text(data = lab, aes(label = lab), parse = TRUE,
                           hjust = 1, vjust = 1, size = ts_rho, colour = rho_col,
                           inherit.aes = TRUE)} +
  scale_colour_manual(values = cols, guide = "none") +
  facet_wrap(~ backend) +
  scale_x_continuous(breaks = x_breaks) +
  scale_y_continuous(breaks = y_breaks) +
  coord_cartesian(xlim = c(x_min, x_max), ylim = c(y_min, y_max)) +
  labs(x = "Dice / -", y = "Patch area error / %") +
  theme(text = element_text(family = font, colour = "black"),
        strip.text = element_text(size = ts_strip, face = "bold"),
        strip.background = element_blank(),
        axis.title = element_text(size = ts_axis, colour = "black"),
        axis.text  = element_text(size = ts_tick, colour = "black"),
        axis.ticks = element_line(colour = axis_col),
        axis.line  = element_line(colour = axis_col),
        panel.grid = element_blank(),
        panel.spacing = unit(1, "lines"),
        panel.background = element_rect(fill = "transparent", colour = NA),
        plot.background  = element_rect(fill = "transparent", colour = NA))

save_both <- function(p, name, w, h) {
  ggsave(file.path(out_dir, paste0(name, ".pdf")), p, device = cairo_pdf,
         width = w, height = h, bg = "transparent")
  ggsave(file.path(out_dir, paste0(name, ".svg")), p, device = svglite::svglite,
         width = w, height = h, bg = "transparent")
}
save_both(g, out_name, fig_w, fig_h)
cat("Salvati:", paste0(out_name, c(".pdf", ".svg")), "\n")
