# Skill: build `report.pptx` (client-ready deck)

You produce ONE file: `report.pptx` in the working directory, following the shared
design system exactly. Build it with a Python script (e.g. `work/build_pptx.py`) using
**python-pptx**, reading every value from `work/personas.json` and inserting the
pre-rendered `work/charts/*.png`.

## Deck setup

- 16:9: `prs.slide_width = Inches(13.333)`, `prs.slide_height = Inches(7.5)`.
- Use the blank layout (`prs.slide_layouts[6]`) and draw everything explicitly —
  full-bleed ivory background rectangle (#FAF8F3) FIRST on every slide, no default
  placeholders.
- Colors via `RGBColor.from_string("D4AF37")` etc. Fonts: Georgia for headings/numbers,
  Calibri for body. Set font on every run (python-pptx does not inherit reliably).

## Slide-by-slide spec (order fixed)

1. **Title** — gold band (full width, 0.18in) at top; report title Georgia 40pt obsidian,
   centered upper third; survey name · date Calibri 16pt muted below; bottom-right
   "AIM · Survey Intelligence" Calibri 11pt gold-ink.
2. **Methodology** — title 26pt; left half: the filter funnel as 4 stacked rows
   (label Calibri 13pt muted + number Georgia 24pt obsidian, thin hairline between);
   right half: approach text box 13pt with 1.15 line spacing; limitations 10pt italic
   muted at bottom.
3. **Audience overview** — title 26pt; persona roster as horizontal color-coded bars:
   one row per persona — swatch square in persona color, name Georgia 15pt, tagline
   Calibri 11pt muted, right-aligned `{count} · {pct}%` Georgia 16pt in persona color;
   bar widths proportional to pct. If `overview_chart` exists, place it right half instead
   and keep roster left.
4. **One slide per persona** — header band: persona-color rectangle (full width, 0.6in)
   with name Georgia 22pt WHITE + tagline Calibri 12pt white at 80% opacity; SIZE badge
   top-right inside band (`{pct}% · {count}`) Georgia 16pt white.
   Body: LEFT column (55%) = four blocks (DEMOGRAPHICS / BEHAVIORS / CONTENT / AD
   RECEPTIVITY): small-caps Calibri 11pt label in persona color, bullets Calibri 12.5pt
   obsidian (≤ 4 bullets each, keep the numbers). RIGHT column (45%) = the persona's
   chart PNG(s), max 2 stacked, captions 10pt muted.
   Footer strip: gold-soft rectangle, gold-ink Calibri 12pt:
   "Reach them: {channel} · {format} · {angle}".
5. **Implications** — title 26pt; table (python-pptx table): header row small-caps muted
   on hairline fill; rows = persona (name in its color) × reach / best channel / format /
   message angle; Calibri 12pt; alternate row shading white/ivory.
6. **Closing** — centered takeaway Georgia 20pt on ivory with gold rule above; recap
   Calibri 10pt muted bottom.

## Practical rules

- Wrap text boxes: `tf.word_wrap = True`; size boxes generously — clipped text is a defect.
- Keep margins ≥ 0.5in; align columns on a consistent grid (e.g. left margin 0.6in).
- Insert chart images by file path with explicit width; never stretch beyond natural
  aspect ratio.

## QA before finishing

1. Re-open with python-pptx: assert slide count == 5 + len(personas) and the file > 30 KB.
2. Walk every shape's text frame: assert no empty persona fields and every persona name
   appears on its slide.
3. If a build error occurs (it happens with tables), fix and re-run — the script must
   end exit-0.
