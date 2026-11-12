# Skill: build `report.pptx` on the AIM house template

You produce ONE file: `report.pptx`, via a Python script (e.g. `work/build_pptx.py`) that
reads every value from `work/personas.json` and inserts `work/charts/*.png`.

## Build on the real template, with the provided helpers (mandatory)

The run directory contains `aim_deck.py` and `template/aim_template.pptx` (the AIM master:
gradient cover, Thank-you slide, light and navy layouts, fonts, logo, lightbulb icon).
Start your script with:

```python
import sys; sys.path.insert(0, ".")
from aim_deck import *          # open_template, set_cover, light_slide, dark_slide, card, ...
prs = open_template()
set_cover(prs, title="Audience Segmentation", survey_name=..., subtitle=..., date="November 2026")
...
finish(prs, "report.pptx")      # moves the Thank-you slide to the end and saves
```

Read `aim_deck.py` first (it is short). Helpers:
- `light_slide(prs, title)` light-blue content slide with the template title (Helvetica 22 bold navy).
- `subtitle(slide, line)` one muted italic line under the title (y≈0.92).
- `card(slide, x, y, w, h, header=None, header_pct=None)` white rounded card, navy border,
  optional navy header strip; RETURNS the inner content box (x, y, w, h) to place text/images in.
- `stat_card(slide, x, y, w, h, number, line)` big red number + navy explanation.
- `text(slide, x, y, w, h, paragraphs, size=11, bullets=False, ...)`; markup: `**bold**`,
  `!!57%!!` (red bold number), `~~(n=37)~~` (small muted, for bases).
- `est_height(paragraphs, w, size, bullets=...)` estimated inches a text needs: use it to size
  boxes and stack them so nothing collides.
- `picture_fit(slide, path, x, y, max_w, max_h)` image scaled to fit inside the box.
- `footnote(slide, line, y=6.45)`, `takeaway_bar(slide, line)` (every light slide ends with one).
- `dark_slide(prs, title)` + `numbered_item(slide, n, y, heading, bullets)` for recommendations.
- Tokens: NAVY, RED, MUTED, WHITE, BAR_DARK, BAR_LIGHT, PERSONA_COLORS, TEXT_ON[color].

Do NOT draw your own backgrounds, logos or cover: they come from the template. Use the
template fonts only (Helvetica on content slides; the cover keeps its Avenir fonts).

## Geometry (16:9, inches)

- Title band y 0.32-0.94; subtitle y≈0.92; content area y 1.3 → 6.35; footnote y≈6.45;
  takeaway bar from y=6.74 (owned by `takeaway_bar`). x from 0.55 to 12.78; gutters 0.2.
- Three cards in a row: w=3.93 at x = 0.55, 4.68, 8.81 (the template grid).
  Two cards: w=6.02 at x = 0.55, 6.76.

## Space and size rules (checked by `check_report.py pptx`)

- Charts are drawn at their slot size (see the chart style). Place them with `picture_fit`
  into a box of exactly that size; a chart shown below 80% of its drawn size FAILS the check.
  Slots: single persona chart 5.8 x 4.0in; two stacked 5.8 x 1.65in each; overview 12.0 x 1.9in.
- No half-empty slides: on every light slide the content must reach down to about y=6.3in.
  Make cards taller (distribute the free height across them), use more spacing, or a larger
  chart; never leave the lower third empty. Text stays at its normal size (no giant fonts).
- Bullet bases: when a bullet's `base` is smaller than the persona's `size_count` (question asked
  only of some people), append ` ~~(n={base})~~` to the bullet. Same in key findings when the
  base is not the full sample.

## Slides (order fixed; see the design system for content)

1. Cover via `set_cover`: title = 2-3 words ("Audience Segmentation") so it keeps the template's
   full size; the theme goes in the plain subtitle; survey name; "Month YYYY".
2. Key findings (light): `stat_card` row for the top 3 findings (y 1.3, h≈1.9; number = the key
   figure, line = the rest of the finding WITHOUT repeating that number), remaining findings as
   bullets in a `card` that extends to y≈6.3; `takeaway_bar`. The low-base list is NOT here
   (it lives on the methodology slide only).
3. Methodology (light): 4 `stat_card`s (w≈2.9, h≈1.2) + approach/limitations card and the
   "groups too small to read" card, both extending to y≈6.3.
4. Audience overview (light): stacked size bar (y 1.3, 0.5in tall) built from `rect`s across
   12.2in, personas largest first, then a final light-grey (#B8C4D6) `not_profiled` segment
   labelled "Not profiled {pct}%" (navy text) when its count > 0, so the bar always reaches 100%.
   Labels use the FULL persona name + % (shrink to 9pt rather than abbreviate; if it still does
   not fit, put the % only and rely on the cards). Label inside with TEXT_ON color if the segment is ≥ 0.6in wide, otherwise a navy 9pt label
   above it. Then one `card` per persona (y≈2.0, h≈1.5: name in persona color bold 12pt; FULL
   tagline 9.5pt; `{pct}% · {count}` 14pt bold); never truncate. Then the overview chart in a
   card (y≈3.75 to 6.3, header "Audience sizes") at its 12.0 x 1.9in slot, and the
   `not_profiled.reason` as a footnote.
5. Persona slides (light, size order): `light_slide(name)`, then ONE subtitle line =
   `profile` (do NOT also print the tagline here; the tagline lives on the overview cards).
   Size badge at top-right (x≈10.3, y≈0.32: `{pct}%` 22pt bold in the persona color +
   "{count} respondents" 9pt muted). LEFT (x 0.55, w 6.02, y 1.3 → 6.3): the 2-3 `sections` as
   `card(header=section.title)`, stacked with 0.15in gaps and the free height shared between
   them so the column reaches y≈6.3. Key figures in bullets in red (`!!44%!!`), bases muted.
   RIGHT (x 6.76, w 6.02, y 1.3 → 6.3): the persona's ONE chart card (header = chart.title,
   image at its 5.8 x 4.0 slot, `how_to_read` 8.5pt muted under it).
   `footnote(profile_footnote)`, `takeaway_bar(...)`.
   EXACTLY ONE slide per persona. Never a "(continued)" slide, never repeat a bullet on another
   slide. If it does not fit, tighten the wording.
6. Summary table (light): manual table from `rect` + `text`: navy header row (white bold 10pt),
   white rows separated by #9CC9E8 hairlines, persona names bold in their color, body 11pt;
   share the free height between rows so the table reaches y≈6.2; `source_note` via
   `footnote`; `takeaway_bar`.
7. What this suggests (navy, only if `recommendations`): `dark_slide` + `numbered_item` per item
   (heading = text, bullet = "Based on: ..."), spaced evenly from y 1.1 to 6.6.
8. `finish(prs, "report.pptx")` (Thank-you slide stays last).

## QA before finishing (all mandatory)

1. `python check_report.py pptx report.pptx` must print PASS (no overlaps, nothing off-slide,
   no box too small for its text, no "…"). Fix the script and re-run until PASS.
2. `python check_report.py render report.pptx`, then open EVERY `work/qa/slide-N.png` with Read
   and look for overlaps, clipped text, cut-off charts. Fix and re-check.
3. Every persona name appears on its slide; the build script exits 0.
