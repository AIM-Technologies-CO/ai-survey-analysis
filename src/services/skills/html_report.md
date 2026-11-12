# Skill: build `report.html` (self-contained, AIM house style)

You produce ONE file: `report.html`, mirroring the AIM template look of the deck.

## Hard requirements

- Fully self-contained: inline `<style>` only, every image embedded as a base64
  `data:image/png;base64,...` URI. ZERO external requests (no webfonts, CDNs, scripts).
- Build with a Python script (e.g. `work/build_html.py`) that reads `work/personas.json`,
  base64-encodes `work/charts/*.png`, and writes the HTML. Never hand-paste numbers.
- Brand images available in the run dir: `template/aim_logo_white.png` (white AIM logo),
  `template/lightbulb.png` (takeaway icon). Embed them as base64 too.

## Responsive layout rules (mandatory; the last web version overflowed)

Chart PNGs are 1200-2400px wide. Without these rules they blow the grid open and spill off
the page. Put this CSS in every report:

```css
img { max-width: 100%; height: auto; display: block; }
.chart img { width: 100%; }
.grid, [class*=grid] { align-items: start; }          /* no stretched half-empty cards */
.grid > *, [class*=grid] > *, .card { min-width: 0; } /* grid children may shrink */
.card { overflow: hidden; }
```
Small icons (lightbulb, logo) keep an explicit width. Nothing may be wider than its card or the
page at 1280px or at 390px (phone).

## Layout spec

- `body`: background #CAEDFC, text #001847, font `Helvetica, 'Helvetica Neue', Arial,
  sans-serif`, 15px/1.55; content column `max-width: 1100px` centered, 24px side padding.
- **Cover**: full-width band with a purple-to-teal gradient
  (`linear-gradient(100deg, #2B2FD8 0%, #6A11E8 45%, #7A1FF0 60%, #1F8FE0 85%, #19E3B5 100%)`),
  white AIM logo centered, title in `Avenir, 'Avenir Next', Helvetica, sans-serif` 900 weight
  ~56px white, the survey name bold 24px between two thin white rules, plain subtitle, date,
  and "Driven by Data. Delivered by AI." in light blue (#D1ECFA).
- **Section pages** (light): h2 Helvetica 26px bold navy; one muted italic subtitle line;
  white cards (`border: 1px solid #001847; border-radius: 8px`) with an optional navy header
  strip (white uppercase bold 12px + a #9CC9E8 % after it); highlighted numbers in
  `<strong style="color:#E60000">`; each section ends with a **takeaway bar**: a 3px navy top
  rule, the lightbulb icon (28px) and one sentence with bold keywords.
- **Stat cards**: white card, big red number (28px bold) + navy line.
- **Audience overview**: 100% stacked bar (persona colors, largest first, then a light-grey
  `not_profiled` "Not profiled" segment so it reaches 100%; every segment
  labelled: inside if wide enough, otherwise above); persona cards in a responsive grid with
  FULL taglines (no `text-overflow: ellipsis`, no `line-clamp`).
- **Persona sections**: name as h2 with a persona-color left border (6px); `profile` as the
  ONLY subtitle (no tagline repeat); size badge right-aligned (`{pct}%` in persona color + count). The persona's own
  `sections` as cards whose header strip shows the section `title` (bullets with a `base` smaller
  than the persona size get a small muted "(n=37)"); charts in cards with the
  chart `title` as header and `how_to_read` under the image (12px muted). `profile_footnote` as
  a 12px muted footnote. Two columns ≥ 900px, one column below.
- **Summary table**: navy header row, white rows with #9CC9E8 rules, persona names bold in their
  color, columns exactly as given; `source_note` under it.
- **What this suggests** (only if `recommendations`): a navy (#001847) panel, numbered circles
  (white outline), sky (#8FD3F4) headings, white "Based on: ..." lines.
- **Closing**: the gradient band again with "Thank you" (Avenir 900, white).
- Never write "Survey Intelligence". Print: `print-color-adjust: exact`; `break-inside: avoid`
  on cards. Arabic content: `dir="auto"`.

## QA before finishing

0. `python check_report.py html report.html` must print PASS. It loads the page in a real
   browser at 1280px, reports anything wider than its card or the page, truncated text, and
   missing image rules, and saves screenshots `work/qa/html-NN.png`. Open them with Read and
   look for overflow, overlaps and empty stretched cards. Fix and re-run until PASS.

1. Re-open the file: size > 50 KB, every persona name present.
2. Zero `http://` / `https://`; every `<img>` src starts with `data:image/png;base64,`.
3. No "…", `text-overflow: ellipsis` or `line-clamp`; no "Survey Intelligence"; no em/en dashes.
4. Tokens `#CAEDFC`, `#001847`, `#E60000` appear in the CSS.
