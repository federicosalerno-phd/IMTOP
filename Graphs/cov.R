# ============================================================
# cov_rain.R  --  Coverage under/over: raincloud (mezzo-violino + pioggia + box), landscape
# Mezzi-violini speculari (under apre a sinistra, over a destra), pioggia interna, box IQR.
# Legge results_clean.csv.  Salva: cov_rain.pdf + cov_rain.svg
#   install.packages(c("ggplot2","ggpubr","svglite"))   # nessun pacchetto extra per i violini
# ============================================================
library(ggplot2); library(ggpubr)

## ============================================================
## STILE  ---- modifica SOLO qui ----
## ============================================================
infile <- "results_clean.csv"; out_name <- "cov_rain"; out_dir <- "."
fig_w <- 9.4; fig_h <- 4.6; font <- "Arial"          # landscape
ts_axis <- 13; ts_title <- 15; ts_xtick <- 13; ts_signif <- 4.3; ts_legend <- 12

backend_order <- c("SAM","GrabCut","Watershed")
col_under <- "#D55E00"; col_over <- "#0072B2"; mean_col <- "#8B0000"; mean_size <- 2.4

off       <- 0.24      # distanza under/over dal centro del back-end
cloud_w   <- 0.20      # larghezza del mezzo-violino
cloud_a   <- 0.50      # opacita' violino
rain_gap  <- 0.04; rain_w <- 0.12        # posizione/larghezza della pioggia (interna)
pt_size   <- 2.0; pt_alpha <- 0.6
boxw      <- 0.05; box_lw <- 0.6           # mezza-larghezza box IQR
y_max     <- 26; y_breaks <- c(0,5,10,15,20)
sig_bracket <- 0.25; sig_tip <- 0.012; sig_pad <- 0.06; sig_toppad <- 0.07
axis_line_col <- "grey50"; show_signif <- TRUE
ylab <- "Coverage / %"
lab_under <- "under-coverage (exposed wound)"; lab_over <- "over-coverage (excess skin)"
## ============================================================

stopifnot(file.exists(infile))
d <- read.csv(infile, stringsAsFactors = FALSE); d <- d[d$status == "ok", ]
d$backend <- factor(d$backend, levels = backend_order)
L <- rbind(
  data.frame(backend = d$backend, value = d$under_cov_frac * 100, type = lab_under),
  data.frame(backend = d$backend, value = d$over_cov_frac  * 100, type = lab_over))
L <- L[is.finite(L$value), ]
L$type <- factor(L$type, levels = c(lab_under, lab_over))
xc <- setNames(seq_along(backend_order), backend_order)
set.seed(1)

polys <- list(); rain <- list(); boxes <- list(); k <- 1
for (b in backend_order) for (tp in c(lab_under, lab_over)) {
  v <- L$value[L$backend == b & L$type == tp]; v <- v[is.finite(v)]
  dir <- if (tp == lab_under) -1 else 1
  c0  <- xc[[b]] + dir * off
  dd  <- density(v, from = 0, to = y_max, n = 200)
  dx  <- dd$y / max(dd$y) * cloud_w
  polys[[k]] <- data.frame(grp = k, type = tp,
                           x = c(c0, c0 + dir * dx, c0), y = c(0, dd$x, y_max))   # violino esterno
  rx <- c0 - dir * (rain_gap + runif(length(v), 0, rain_w))                       # pioggia interna
  rain[[k]] <- data.frame(type = tp, x = rx, y = v)
  q <- quantile(v, c(.25,.5,.75))
  boxes[[k]] <- data.frame(type = tp, c0 = c0, q1 = q[1], med = q[2], q3 = q[3], mean = mean(v))
  k <- k + 1
}
polys <- do.call(rbind, polys); rain <- do.call(rbind, rain); boxes <- do.call(rbind, boxes)
polys$type <- factor(polys$type, levels = c(lab_under, lab_over))
rain$type  <- factor(rain$type,  levels = c(lab_under, lab_over))

stars <- function(p) ifelse(is.na(p), "ns", ifelse(p < 1e-4, "****", ifelse(p < 1e-3, "***",
                                                                            ifelse(p < 1e-2, "**", ifelse(p < 5e-2, "*", "ns")))))
pv <- do.call(rbind, lapply(backend_order, function(b) {
  u <- d$under_cov_frac[d$backend == b] * 100; o <- d$over_cov_frac[d$backend == b] * 100
  m <- is.finite(u) & is.finite(o)
  p <- tryCatch(suppressWarnings(wilcox.test(u[m], o[m], paired = TRUE)$p.value), error = function(e) NA)
  data.frame(xmin = xc[[b]] - off, xmax = xc[[b]] + off, p.signif = stars(p),
             y.position = y_max - 2.2)
}))

g <- ggplot() +
  geom_polygon(data = polys, aes(x, y, group = grp, fill = type), colour = NA, alpha = cloud_a) +
  geom_point(data = rain, aes(x, y, colour = type), size = pt_size, alpha = pt_alpha, shape = 16, stroke = 0) +
  geom_rect(data = boxes, aes(xmin = c0 - boxw, xmax = c0 + boxw, ymin = q1, ymax = q3, colour = type),
            fill = "white", linewidth = box_lw, inherit.aes = FALSE) +
  geom_segment(data = boxes, aes(x = c0 - boxw, xend = c0 + boxw, y = med, yend = med),
               colour = "black", linewidth = box_lw, inherit.aes = FALSE) +
  geom_point(data = boxes, aes(x = c0, y = mean), colour = mean_col, size = mean_size, inherit.aes = FALSE) +
  scale_fill_manual(values = c(col_under, col_over), name = NULL) +
  scale_colour_manual(values = c(col_under, col_over), guide = "none") +
  scale_x_continuous(breaks = xc, labels = backend_order) +
  scale_y_continuous(breaks = y_breaks) +
  coord_cartesian(xlim = c(0.5, length(backend_order) + 0.5), ylim = c(0, y_max), clip = "off") +
  labs(x = NULL, y = ylab) +
  theme(text = element_text(family = font, colour = "black"),
        axis.text = element_text(size = ts_axis, colour = "black"),
        axis.text.x = element_text(size = ts_xtick),
        axis.title = element_text(size = ts_title, colour = "black"),
        axis.ticks = element_blank(), axis.line = element_line(colour = axis_line_col),
        panel.grid = element_blank(),
        panel.background = element_rect(fill = "transparent", colour = NA),
        plot.background = element_rect(fill = "transparent", colour = NA),
        legend.position = "top", legend.text = element_text(size = ts_legend))

if (show_signif)
  g <- g + ggpubr::geom_bracket(inherit.aes = FALSE,
                                xmin = pv$xmin, xmax = pv$xmax, label = pv$p.signif, y.position = pv$y.position,
                                tip.length = sig_tip, size = sig_bracket, label.size = ts_signif, color = "black")

save_both <- function(p, name, w, h) {
  if (!dir.exists(out_dir)) dir.create(out_dir, recursive = TRUE)
  ggsave(file.path(out_dir, paste0(name, ".pdf")), p, device = cairo_pdf, width = w, height = h, bg = "transparent")
  ggsave(file.path(out_dir, paste0(name, ".svg")), p, device = svglite::svglite, width = w, height = h, bg = "transparent")
}
save_both(g, out_name, fig_w, fig_h)
cat("Salvati:", paste0(out_name, c(".pdf", ".svg")), "\n")