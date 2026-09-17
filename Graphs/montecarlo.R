# ============================================================
# montecarlo.R  --  previsione di distorsione (error budget) via Monte Carlo, in R.
# Legge results_clean.csv (set con outlier rimossi; coerente con montecarlo_distortion.py).
# Produce 4 figure (PDF+SVG, sovrascrive): mc_dist, mc_sigma_sweep, mc_contrib, mc_exceed.
#   install.packages(c("ggplot2","svglite"))
# UNITA' scale-free: tutti gli errori sono relativi (%).
# ============================================================
library(ggplot2)

## ============================================================
## PARAMETRI + STILE  ---- modifica SOLO qui ----
## ============================================================
infile <- "results_clean.csv"; out_dir <- "."
N <- 20000; seed <- 42

# ScaleE: calibrazione a 2 punti, rumore di posizionamento gaussiano sigma (px), lunghezza L (px)
sigma_px <- 3
L_px     <- NA            # NA = mediana di bbox_gt_major_mm dai dati ; altrimenti es. 200
# PrE: dai risultati Exp. C (errore d'area di stampa). E' minuscolo per costruzione:
#   nel contributo (mc_contrib) risultera' ~0 perche' la sua varianza e' trascurabile
#   rispetto a SegE. NON e' un bug: e' il risultato (la segmentazione domina). Se vuoi
#   che pesi di piu', aumenta pre_sd, ma e' un'assunzione da giustificare con i dati.
pre_mean <- 0.0069; pre_sd <- 0.0010

sweep_sigma <- seq(0.5, 5, by = 0.25)   # mc_sigma_sweep (come immagine 4)
sweep_ymax  <- 40        # mc_sigma_sweep: tetto asse y (taglia solo i punti di coda dalla vista). NA = auto
n_pts_dist  <- 1200      # punti mostrati per back-end (subsample, per non sovraffollare)
n_pts_sweep <- 350       # punti mostrati per sigma

font <- "Arial"; ts_axis <- 14; ts_title <- 16; ts_xtick <- 13
backend_order <- c("SAM","GrabCut","Watershed")
col <- c(SAM = "#0072B2", GrabCut = "#E69F00", Watershed = "#009E73")
col_src <- c(SegE = "#444444", ScaleE = "#1B9E77", PrE = "#E69F00")   # fonti (mc_contrib)
teal <- "#1B9E77"        # tinta sweep (come immagine 4)
pre_floor <- 1.2         # mc_contrib: altezza simbolica minima della barra PrE (solo visiva; l'etichetta mostra il valore vero ~0)

violin_width <- 0.9; violin_alpha <- 0.30; violin_lw <- 0.2; violin_adjust <- 0.6
box_width <- 0.14; box_alpha <- 0.28; box_lw <- 0.2
pt_size <- 1.3; pt_alpha <- 0.35
mean_col <- "#8B0000"; mean_size <- 1.6
## ============================================================

set.seed(seed)
stopifnot(file.exists(infile))
d <- read.csv(infile, stringsAsFactors = FALSE)
d <- d[d$status == "ok", ]
d$backend <- factor(d$backend, levels = backend_order)
d$signed_seg <- (d$n_auto_px - d$n_gt_px) / d$n_gt_px      # errore d'area relativo CON segno

L <- if (is.na(L_px)) stats::median(d$bbox_gt_major_mm, na.rm = TRUE) else L_px

# perturbazione a 2 punti -> errore relativo d'area da scala
scale_area_err <- function(n, sigma, L) {
  nx1 <- rnorm(n, 0, sigma); ny1 <- rnorm(n, 0, sigma)
  nx2 <- rnorm(n, 0, sigma); ny2 <- rnorm(n, 0, sigma)
  lenp <- sqrt((L + nx2 - nx1)^2 + (ny2 - ny1)^2)
  (L / lenp)^2 - 1
}

# ---- simulazione totale per back-end ----
sim <- list()
for (b in backend_order) {
  ss <- d$signed_seg[d$backend == b]
  es  <- sample(ss, N, replace = TRUE)
  esc <- scale_area_err(N, sigma_px, L)
  ep  <- rnorm(N, pre_mean, pre_sd)
  total <- (1 + es) * (1 + esc) * (1 + ep) - 1
  sim[[b]] <- list(es = es, esc = esc, ep = ep, total = total)
}

theme_thesis <- function() theme(
  text = element_text(family = font, colour = "black"),
  axis.text = element_text(size = ts_axis, colour = "black"),
  axis.text.x = element_text(size = ts_xtick),
  axis.title = element_text(size = ts_title, colour = "black"),
  axis.ticks = element_blank(), axis.line = element_line(colour = "grey50"),
  panel.grid = element_blank(),
  panel.background = element_rect(fill = "transparent", colour = NA),
  plot.background = element_rect(fill = "transparent", colour = NA),
  legend.position = "top", legend.title = element_blank())

save_both <- function(p, name, w, h) {
  if (!dir.exists(out_dir)) dir.create(out_dir, recursive = TRUE)
  ggsave(file.path(out_dir, paste0(name, ".pdf")), p, device = cairo_pdf, width = w, height = h, bg = "transparent")
  ggsave(file.path(out_dir, paste0(name, ".svg")), p, device = svglite::svglite, width = w, height = h, bg = "transparent")
}

## ---- mc_dist: distorsione totale per back-end (violino + punti visibili) ----
dist_df <- do.call(rbind, lapply(backend_order, function(b) {
  v <- abs(sim[[b]]$total) * 100
  data.frame(backend = b, value = v[sample.int(length(v), n_pts_dist)])
}))
dist_df$backend <- factor(dist_df$backend, levels = backend_order)
p_dist <- ggplot(dist_df, aes(backend, value, fill = backend, colour = backend)) +
  geom_violin(alpha = violin_alpha, linewidth = violin_lw, width = violin_width, trim = TRUE, adjust = violin_adjust) +
  geom_jitter(position = position_jitter(width = 0.07, height = 0), size = pt_size, alpha = pt_alpha, shape = 16, stroke = 0) +
  geom_boxplot(width = box_width, alpha = box_alpha, outlier.shape = NA, linewidth = box_lw, colour = "black") +
  stat_summary(fun = mean, geom = "point", colour = mean_col, fill = mean_col, size = mean_size) +
  scale_fill_manual(values = col) + scale_colour_manual(values = col) +
  scale_y_continuous(expand = expansion(mult = c(0.02, 0.04))) +
  labs(x = NULL, y = "Total area distortion / %") + theme_thesis() + theme(legend.position = "none")
save_both(p_dist, "mc_dist", 6.2, 5.0)

## ---- mc_sigma_sweep: sensitivita' di scala (stile immagine 4, valori assoluti) ----
sweep <- do.call(rbind, lapply(sweep_sigma, function(sg) {
  a <- abs(scale_area_err(N, sg, L)) * 100
  data.frame(sigma = sg, area = a)
}))
medv <- tapply(sweep$area, sweep$sigma, median)
lov  <- tapply(sweep$area, sweep$sigma, quantile, probs = 0.05)
hiv  <- tapply(sweep$area, sweep$sigma, quantile, probs = 0.95)
S <- data.frame(sigma = as.numeric(names(medv)), med = as.numeric(medv),
                lo = as.numeric(lov), hi = as.numeric(hiv))
sweep_pts <- do.call(rbind, lapply(sweep_sigma, function(sg) {
  a <- sweep$area[sweep$sigma == sg]
  data.frame(sigma = sg, area = a[sample.int(length(a), n_pts_sweep)])
}))
p_sweep <- ggplot() +
  geom_ribbon(data = S, aes(sigma, ymin = lo, ymax = hi), fill = teal, alpha = 0.18) +
  geom_jitter(data = sweep_pts, aes(sigma, area), width = 0.06, height = 0,
              colour = teal, alpha = 0.18, size = 0.7, shape = 16, stroke = 0) +
  geom_line(data = S, aes(sigma, med), colour = teal, linewidth = 0.9) +
  geom_point(data = S, aes(sigma, med), colour = teal, size = 1.4) +
  scale_y_continuous(expand = expansion(mult = c(0.02, 0.04))) +
  coord_cartesian(ylim = c(0, sweep_ymax)) +
  labs(x = "Placement error \u03c3 / px", y = "Area error / %") + theme_thesis()
save_both(p_sweep, "mc_sigma_sweep", 6.6, 4.6)

## ---- mc_contrib: contributo di varianza di prima approssimazione per fonte ----
contrib <- do.call(rbind, lapply(backend_order, function(b) {
  s <- sim[[b]]; mes <- mean(s$es); msc <- mean(s$esc); mep <- mean(s$ep)
  v_seg   <- var((1 + s$es)  * (1 + msc)   * (1 + mep)   - 1)
  v_scale <- var((1 + mes)   * (1 + s$esc) * (1 + mep)   - 1)
  v_pre   <- var((1 + mes)   * (1 + msc)   * (1 + s$ep)  - 1)
  tot <- v_seg + v_scale + v_pre
  data.frame(backend = b,
             source = c("SegE", "ScaleE", "PrE"),
             pct = c(v_seg, v_scale, v_pre) / tot * 100)
}))
contrib$backend <- factor(contrib$backend, levels = backend_order)
contrib$source  <- factor(contrib$source, levels = c("SegE", "ScaleE", "PrE"))
contrib$pct_draw <- pmax(contrib$pct, pre_floor)   # barra simbolica per PrE ~0 (l'etichetta resta il valore vero)
p_contrib <- ggplot(contrib, aes(backend, pct_draw, fill = source, group = source)) +
  geom_col(position = position_dodge(width = 0.8), width = 0.72, colour = "white", linewidth = 0.2) +
  geom_text(aes(y = pct_draw, label = sprintf("%.2f", pct)), position = position_dodge(width = 0.8),
            vjust = -0.3, size = 3.2, family = font) +
  scale_fill_manual(values = col_src) +
  scale_y_continuous(expand = expansion(mult = c(0, 0.08))) +
  labs(x = NULL, y = "Variance contribution / %") + theme_thesis()
save_both(p_contrib, "mc_contrib", 6.6, 5.0)

## ---- mc_exceed: P(|distorsione| > soglia) per back-end (punti + linea di tendenza) ----
thr <- seq(0, 30, by = 0.5)
exc <- do.call(rbind, lapply(backend_order, function(b) {
  v <- abs(sim[[b]]$total) * 100
  data.frame(backend = b, thr = thr, p = sapply(thr, function(t) mean(v > t)))
}))
exc$backend <- factor(exc$backend, levels = backend_order)
p_exceed <- ggplot(exc, aes(thr, p, colour = backend)) +
  geom_line(linewidth = 0.8) +
  geom_point(size = 1.2, shape = 16, stroke = 0) +
  scale_colour_manual(values = col, name = NULL) +
  scale_y_continuous(expand = expansion(mult = c(0, 0.04))) +
  labs(x = "Threshold / %", y = "Exceedance probability / -") + theme_thesis()
save_both(p_exceed, "mc_exceed", 6.6, 4.8)

cat("Salvati: mc_dist, mc_sigma_sweep, mc_contrib, mc_exceed (.pdf e .svg)\n")
cat(sprintf("L = %.1f px, sigma = %.1f px, PrE ~ N(%.4f, %.4f), N = %d\n", L, sigma_px, pre_mean, pre_sd, N))
