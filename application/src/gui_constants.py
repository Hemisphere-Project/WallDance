"""Shared GUI palette and layout constants.

Single source for the recurring color tuples and layout magic numbers in
gui.py / gui_builder.py. Pixel values are unscaled — wrap them in
gui_builder.scaled() at the use site.

Theme RGBA tuples (button/badge state themes in gui_builder.setup_theme
and the top bar) are intentionally not migrated here yet; they belong to
a future theming pass.
"""

# --- Text / accent palette (R, G, B) ---
# Greys, brightest to faintest
TEXT_NORMAL = (180, 180, 180)   # body text
TEXT_MUTED = (150, 150, 150)    # secondary / explanatory text
TEXT_DIM = (120, 120, 120)      # de-emphasized text
TEXT_HINT = (100, 100, 100)     # placeholders / inactive hints
TEXT_FAINT = (80, 80, 80)       # faint stat labels / separators

# Accents
HEADING_GREEN = (120, 200, 140)  # section/dialog headings
OK_GREEN = (120, 255, 120)       # healthy status (FPS ok, connected)
BRIGHT_GREEN = (100, 255, 100)   # strong positive emphasis
PALE_GREEN = (140, 180, 140)     # subdued positive
WARN_AMBER = (255, 200, 100)     # warnings / toasts
WARN_ORANGE = (255, 180, 80)     # stronger warnings (stale, fallback)
ERROR_SOFT = (255, 120, 120)     # soft error text
ALERT_RED = (255, 80, 80)        # alerts / failing status

# --- Layout (unscaled px) ---
CONTROL_PANEL_WIDTH = 370

# Default viewport geometry (app.py may pass explicit DPI-scaled values)
VIEWPORT_BASE_W = 1340
VIEWPORT_BASE_H = 900
VIEWPORT_MIN = 900

# Toast anchor: top-left of the preview area, just below the top bar
TOAST_POS = (15, 38)

# _recompute_layout(): horizontal padding around the video panel
# (left 6 + right 6 + window padding + gap) and vertical overhead
# (bars + paddings) — margin once bars are measured, fallback before
# the first frame has rendered them.
LAYOUT_H_PAD = 28
# Vertical overhead beyond the measured bars: window padding + inter-row item
# spacing for the stacked rows (top bar, phase rail, alerts strip, drawer bar,
# bottom bar). Bumped when the rail/alerts/drawer rows were added so the middle
# shrinks enough to keep the whole window inside the viewport (no scrollbar).
LAYOUT_V_MARGIN = 120
LAYOUT_V_FALLBACK = 240
