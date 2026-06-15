# Skill: build `report.pptx` (AIM dark editorial deck)

You produce ONE file: `report.pptx`, via a Python script (e.g. `work/build_pptx.py`)
using **python-pptx**, reading every value from `work/personas.json` and inserting the
pre-rendered `work/charts/*.png`. Follow the shared design system exactly.

## Deck setup

- 16:9: `prs.slide_width = Inches(13.333)`, `prs.slide_height = Inches(7.5)`.
- Blank layout (`prs.slide_layouts[6]`); paint a full-bleed canvas rectangle
  (#0A0E27) FIRST on every slide; draw everything explicitly, no placeholders.
- Disable shape shadows: `shp.shadow.inherit = False` on every rect you draw.
- Colors via `RGBColor(0x0A, 0x0E, 0x27)` etc. Fonts: Georgia (display) / Calibri
  (body) / Tahoma for Arabic strings; set font name+size+color on EVERY run.
- Write small helpers first (`add_rect`, `add_text`, `add_hairline`) and reuse them.

## Slide chrome (every content slide)

- Hairline (#1E285E, 0.5pt) at y=0.42in, x=0.55 → width 12.23in.
- Eyebrow at (0.55, 0.18): `0N · SECTION` Calibri 9pt bold #00FF96 uppercase.
- Running header top-right: `AIM TECHNOLOGIES · SURVEY INTELLIGENCE · {date}` Calibri
  8.5pt #8A93B0 right-aligned.
- Title at (0.55, 0.58): Georgia 30pt #E8ECF4.
- Kicker at (0.55, 1.38): Calibri 11.5pt italic #8A93B0 — one-line real takeaway.
- Bottom hairline at y=7.15; footer left `AIM Technologies · Confidential`, right
  `0N / Section name` — Calibri 8pt #8A93B0.
- Content area: y ≈ 1.95 → 7.0, x margins 0.55in.

## Slide-by-slide spec (order fixed)

1. **Cover** — canvas bg; accent bar: rect (0, 0, 0.22in, 7.5in) #00FF96.
   At x=0.75: "AIM TECHNOLOGIES" Calibri 11pt bold #00FF96 (y=0.5);
   "Survey Intelligence · Audience Segmentation" Calibri 10pt #8A93B0 (y=0.85).
   Display title two lines: line 1 (y≈2.35) Georgia 64pt #E8ECF4; line 2 (y≈3.25)
   Georgia 76pt ITALIC #00FF96 (derive a 2-3 word editorial title from report_title,
   e.g. "Inside the / Audience").
   Subtitle (y≈4.55) Calibri 14pt #8A93B0: survey name · period · final N.
   Persona chips row (y≈5.9): per persona a panel card (#121A4A, border #1E285E,
   ~2.2×0.62in): persona-color dot (0.18in oval) + name Calibri 13pt bold #E8ECF4 +
   `{pct}%` Calibri 9pt #8A93B0.
   Meta strip (y≈6.8): hairline, then 4 columns (PREPARED BY / SURVEY / RESPONDENTS /
   PERIOD): label Calibri 8pt bold #8A93B0 over value Calibri 11pt bold #E8ECF4.
2. **01 · Methodology** — chrome; 4 KPI stat cards in a row (panel #121A4A, border
   #1E285E, accent TOP stripe 0.08in #00FF96): label Calibri 9pt bold muted, number
   Georgia 28pt #E8ECF4 (final N in #00FF96), caption 8.5pt #8A93B0 — for started /
   removed by status / removed by exclude / final N. Below: approach text box Calibri
   12pt #A9B1CE (line_spacing 1.3); limitations Calibri 10pt italic #8A93B0.
3. **02 · Audience overview** — chrome; manual 100% stacked size bar (full content
   width, 0.55in tall): background track panel+rule border, segments = persona colors
   with inline `{pct}%` Calibri 11pt bold in CANVAS color (#0A0E27) centered (only if
   share ≥ 6%); legend chips below (0.16in swatch + name Calibri 9.5pt #A9B1CE).
   Then the roster: per persona a panel card with persona-color top stripe — name
   Georgia 14pt italic in persona color, tagline Calibri 9pt muted, `{pct}% · {count}`
   Georgia 16pt #E8ECF4. Place the overview chart PNG right half if it fits.
3b. **Audience shift** (ONLY if personas.json has `waves`) — chrome; insert BETWEEN Audience
   overview and the persona slides (eyebrow numbers after it shift by one). A `shifts_summary`
   table: header `PERSONA | {wave1}% | … | {waveN}% | Δ PTS | WHAT IT MEANS` — one % column per
   wave in time order (small-caps muted on panel-alt); body rows Calibri 11pt, persona names in
   their colors; the Δ cell is the signed first-to-last change with ▲ #00FF96 (rising) /
   ▼ #FF4D6D (falling) / muted dash (stable); one-line headline takeaway as the kicker. (The
   grouped wave `overview_chart` already sits on the Audience overview slide.)
4. **One slide per persona** — chrome with eyebrow `0N · PERSONA`; title = persona name
   Georgia 30pt in the PERSONA's color, kicker = tagline italic.
   SIZE badge top-right of content: `{pct}%` Georgia 26pt persona color + `{count}
   respondents` Calibri 9pt muted.
   If `wave_sizes` is present, add a wave-shift line under the badge — the % series across all
   waves with the net delta: `Wave 1 {pct}% · … · Wave N {pct}%  (Δ +X.X ▲)` Calibri 10pt colored
   by direction (#00FF96 up / #FF4D6D down), with the persona's `shift` sentence as a 9pt #8A93B0 line.
   LEFT column (~55%): four blocks — label Calibri 10pt bold small-caps in persona
   color (DEMOGRAPHICS / BEHAVIORS / CONTENT PREFERENCES / AD RECEPTIVITY), bullets
   Calibri 12pt #E8ECF4, line_spacing 1.25, ≤ 4 bullets each, keep the numbers; thin
   hairline between blocks. RIGHT column (~45%): persona chart PNG(s) on panel cards
   (draw the panel rect, inset the image), captions 9pt #8A93B0, max 2 stacked.
   Footer strip above the bottom hairline: panel-alt rect with persona-color left edge
   (0.05in): `Reach them: {channel} · {format} · {angle}` Calibri 12pt #E8ECF4 with
   the three values bold.
5. **Implications** — chrome; table: header row small-caps Calibri 9pt bold #8A93B0 on
   panel-alt fill; body rows Calibri 11pt #E8ECF4, persona names in their colors,
   alternating panel/panel-alt fills, borders #1E285E. (If python-pptx table styling
   fights you, draw it manually with rects + text boxes — often cleaner here.)
6. **Closing** — chrome-less canvas slide: centered takeaway Georgia 22pt #E8ECF4 with
   one italic #00FF96 phrase; hairline; recap Calibri 9pt #8A93B0 at bottom.

## Practical rules

- `tf.word_wrap = True`; margins of text frames to 0; size boxes generously — clipped
  text is a defect. Keep a consistent grid (left 0.55in, gutters ≥ 0.12in).
- Insert chart images with explicit width, never stretch beyond natural aspect.
- Text on persona-color fills is always #0A0E27 (canvas) bold.
- Arabic strings: font Tahoma.

## QA before finishing

1. Re-open with python-pptx: assert slide count == 5 + len(personas), plus 1 more when
   personas.json has a `waves` field (the Audience shift slide). File > 30 KB.
2. Walk every text frame: every persona name appears on its slide; no empty fields.
3. The build script must end exit-0 (fix and re-run on any error).
