# ============================================================
# scale_sensitivity.R  --  Scale sensitivity (Fig. 10): |area error| vs sigma
# Monte-Carlo sweep della calibrazione a due punti. Legge results_all.csv SOLO
# per ricavare L = median(bbox_gt_major_mm). Salva: scale_sensitivity.pdf + .svg
#   install.packages(c("ggplot2","svglite"))
# ============================================================
library(ggplot2)

## ============================================================
## STILE / PARAMETRI  ---- modifica SOLO qui ----
## ============================================================
infile  <- "results_all.csv"; out_name <- "scale_sensitivity"; out_dir <- "."
fig_w   <- 4.8; fig_h <- 3.3; font <- "Arial"

# --- modello Monte-Carlo ---
seed       <- 42
L_override <- NA          # NA -> usa median(bbox_gt_major_mm); altrimenti px (es. 200)
sig_min    <- 0.5; sig_max <- 5.0; sig_step <- 0.25   # griglia sigma (px)
n_bin      <- 1400        # campioni per sigma (mediana + banda). Piu' basso = piu' spezzato
n_scat     <- 340         # punti disegnati per sigma (nuvola)
jit        <- 0.075       # jitter orizzontale dei punti (px)
band_lo    <- 5; band_hi <- 95   # percentili della banda

# --- estetica ---
teal       <- "#2A9D8F"   # nuvola + banda
teal_dark  <- "#15665B"   # linea di tendenza
pt_size    <- 0.9 ; pt_alpha <- 0.42          # punti verticali (alpha alto = piu' scuri)
band_alpha <- 0.16
line_lw    <- 0.8                              # spessore linea di tendenza
mk_size    <- 1.9 ; mk_stroke <- 0.4          # marker sulla linea
mk_edge    <- "white"                          # bordo dei marker
zero_line_col <- "grey45"
axis_line_col <- "grey50"

ts_title <- 15; ts_axis <- 13; ts_tick <- 12
y_min <- 0; y_max <- 26; y_breaks <- seq(0, 25, 5)
x_breaks <- 1:5
## ============================================================

set.seed(seed)

## --- L (lunghezza del riferimento/fiducial, px) ---
if (is.na(L_override)) {
  stopifnot(file.exists(infile))
  d <- read.csv(infile, stringsAsFactors = FALSE)
  d <- d[d$status == "ok", ]
  L <- median(d$bbox_gt_major_mm, na.rm = TRUE)
} else L <- L_override

## --- modello di propagazione della scala ---
sample_len <- function(n, sigma, L) {            # errore relativo di lunghezza
  n1x <- rnorm(n, 0, sigma); n1y <- rnorm(n, 0, sigma)
  n2x <- rnorm(n, 0, sigma); n2y <- rnorm(n, 0, sigma)
  dx <- L + (n2x - n1x); dy <- (n2y - n1y)
  sqrt(dx^2 + dy^2) / L - 1
}
len_to_area <- function(e) (1 + e)^2 - 1          # errore relativo d'area

sigmas <- seq(sig_min, sig_max, by = sig_step)
med <- p_lo <- p_hi <- numeric(length(sigmas))
scat <- data.frame(x = numeric(0), y = numeric(0))
for (k in seq_along(sigmas)) {
  s  <- sigmas[k]
  a  <- abs(len_to_area(sample_len(n_bin, s, L))) * 100   # |errore d'area| in %
  med[k] <- median(a)
  qs <- quantile(a, c(band_lo, band_hi) / 100)
  p_lo[k] <- qs[1]; p_hi[k] <- qs[2]
  idx <- sample.int(n_bin, min(n_scat, n_bin))
  scat <- rbind(scat, data.frame(x = s + runif(length(idx), -jit, jit), y = a[idx]))
}
n <- length(sigmas)
band <- data.frame(                                   # banda estesa di 'jit' ai due estremi:
  sigma = c(sig_min - jit, sigmas, sig_max + jit),    # il bordo verticale non taglia piu' i punti
  lo    = c(p_lo[1], p_lo, p_lo[n]),
  hi    = c(p_hi[1], p_hi, p_hi[n]))
line <- data.frame(sigma = sigmas, med = med)
scat <- scat[scat$y <= y_max, ]                        # scarto i punti oltre y_max (niente mezzi-pallini)

## --- plot ---
g <- ggplot() +
  geom_ribbon(data = band, aes(x = sigma, ymin = lo, ymax = hi),
              fill = teal, alpha = band_alpha) +
  geom_point(data = scat, aes(x, y), colour = teal, alpha = pt_alpha,
             size = pt_size, shape = 16, stroke = 0) +
  geom_line(data = line, aes(sigma, med), colour = teal_dark, linewidth = line_lw) +
  geom_point(data = line, aes(sigma, med), shape = 21, fill = teal_dark,
             colour = mk_edge, size = mk_size, stroke = mk_stroke) +
  geom_hline(yintercept = 0, colour = zero_line_col, linewidth = 0.3) +
  labs(title = "Scale sensitivity",
       x = expression("Placement error " * sigma * " / px"),
       y = "Area error / %") +
  scale_x_continuous(breaks = x_breaks) +
  scale_y_continuous(breaks = y_breaks, expand = expansion(mult = c(0, 0.02))) +
  coord_cartesian(xlim = c(sig_min - 0.25, sig_max + 0.25), ylim = c(y_min, y_max)) +
  theme(text = element_text(family = font, colour = "black"),
        plot.title = element_text(size = ts_title, face = "bold", colour = teal_dark, hjust = 0.5),
        axis.title = element_text(size = ts_axis, colour = "black"),
        axis.text  = element_text(size = ts_tick, colour = "black"),
        axis.ticks = element_line(colour = axis_line_col),
        axis.line  = element_line(colour = axis_line_col),
        panel.grid = element_blank(),
        panel.background = element_rect(fill = "transparent", colour = NA),
        plot.background  = element_rect(fill = "transparent", colour = NA))

## --- salvataggio (pdf vettoriale + svg), sfondo trasparente ---
save_both <- function(p, name, w, h) {
  if (!dir.exists(out_dir)) dir.create(out_dir, recursive = TRUE)
  ggsave(file.path(out_dir, paste0(name, ".pdf")), p, device = cairo_pdf,
         width = w, height = h, bg = "transparent")
  ggsave(file.path(out_dir, paste0(name, ".svg")), p, device = svglite::svglite,
         width = w, height = h, bg = "transparent")
}
save_both(g, out_name, fig_w, fig_h)
cat("Salvati:", paste0(out_name, c(".pdf", ".svg")),
    "| L =", round(L, 2), "px | mediana@sigma=3 =",
    round(med[which.min(abs(sigmas - 3))], 2), "%\n")
