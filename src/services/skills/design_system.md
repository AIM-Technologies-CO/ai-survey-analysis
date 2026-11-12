# Audience Segmentation — Report Design System (shared by HTML + PPTX builders)

Both deliverables follow the **AIM house template** (the AIM "Research Report" deck):
- a **gradient cover** (purple to teal, AIM logo, "Driven by Data. Delivered by AI.") and a
  gradient **Thank-you** closing slide,
- **light-blue content pages** (#CAEDFC) with bold navy titles, white rounded cards with a
  thin navy border, navy card header strips, **key numbers in AIM red**, and a bottom
  **takeaway bar** (navy rule + lightbulb icon + one bold-keyword sentence),
- **navy pages** (#001847) for recommendations, with numbered circles, sky-blue headings and
  the white AIM logo bottom-right.

The HTML and PPTX must look like two renditions of the same document: same palette, fonts,
section order, persona names/colors/numbers, and the SAME chart images.

## Tokens (exact values from the template)

| Token | Hex | Use |
| --- | --- | --- |
| bg-light | #CAEDFC | content page background |
| navy | #001847 | titles, body text, card borders, card header strips, navy pages |
| navy-2 | #0B2E5C | small uppercase labels inside cards |
| red | #E60000 | highlighted numbers ONLY (e.g. "57%" at the start of a stat) |
| white | #FFFFFF | cards |
| muted | #5A6B80 | notes, captions, footnotes, "how to read" lines |
| head-pct | #9CC9E8 | the % that follows a card header ("FOOD & DRINKS 72%") |
| sky | #8FD3F4 | headings on navy pages |
| bar-dark / bar-light | #2F5597 / #5B9BD5 | chart series (two-group comparisons) |

## Fonts (the template's own)

- Content pages: **Helvetica** everywhere (titles bold 22pt, body 10-12pt). Fallback stack in
  HTML: `Helvetica, 'Helvetica Neue', Arial, sans-serif`.
- Cover: **Avenir Black** (title), **Avenir** (survey name, bold), **Avenir Light** (date),
  **Avenir Next Medium** (tagline). HTML: `Avenir, 'Avenir Next', 'Nunito Sans', Helvetica, sans-serif`.
- Arabic strings: Tahoma (PPTX) / `dir="auto"` (HTML).

## Persona palette (personas are sorted largest first; assign in order)

1. #2F5597  2. #5B9BD5  3. #7B3FE4  4. #12A594  5. #E0A100  6. #0B2E5C
Text on a persona fill: white on 1/3/4/6, navy on 2/5.

## Document structure (same order in both formats)

1. **Cover** (gradient): title (e.g. "Audience Segmentation"), survey name, one-line plain
   subtitle, month + year. Never write "Survey Intelligence" anywhere.
2. **Key findings** (light): 3 stat cards in a row (red number + navy line) for the strongest
   `key_findings`, the rest as bullets in a white card; low-base note as a muted footnote:
   "Not read (fewer than 30 respondents): ...". Takeaway bar = the single most important finding.
3. **Methodology** (light): 4 stat cards (started / removed status / removed exclude / final N);
   approach + limitations in a white card (limitations include the 30-respondent rule).
4. **Audience overview** (light): 100% stacked size bar in persona colors, LARGEST FIRST, then a
   light-grey (#B8C4D6) "Not profiled" segment from `not_profiled` so it always reaches 100%; every
   segment labelled (inside if wide enough, otherwise above it); one white card per persona in
   the same order with its name in its color, FULL tagline (never truncated, never "…") and
   `{pct}% · {count}`; overview chart if it fits.
5. **One page per persona** (light, size order): title = persona name; ONE subtitle line = `profile`
   (qualitative, no numbers; the tagline is NOT repeated here); size badge top-right (`{pct}%` in persona color + count). The
   persona's 2-3 `sections` as white cards with a navy header strip carrying the section's OWN
   `title` (never fixed labels); EXACTLY one page per persona, nothing repeated on another page;
   bullets with key numbers in red; when a bullet's `base` is smaller
   than the persona size, show it after the bullet in small muted text "(n=37)". Charts in white cards with the
   chart `title` as card header and the `how_to_read` line under the image in muted 8pt.
   `profile_footnote` as a muted footnote above the takeaway bar. Takeaway bar = what makes
   this persona different, in one sentence with **bold** keywords.
6. **Summary table** (light): `summary_table.title`; columns EXACTLY as given; navy header row
   with white text; white rows with thin #9CC9E8 rules; persona names bold in their color;
   `source_note` as muted footnote.
7. **What this suggests** (navy, ONLY if `recommendations`): numbered items, sky heading = the
   recommendation, white bullet = "Based on: ...".
8. **Thank-you** (template closing slide, unchanged).

## Wave-over-wave mode (ONLY when personas.json has a top-level `waves` field)

- Cover subtitle names every wave and its N in order.
- Audience overview: `overview_chart` is the grouped wave chart; subtitle states the biggest shift.
- NEW light page "Audience shift" right after the overview: `shifts_summary` table
  `PERSONA | {wave1}% | … | Δ PTS | WHAT IT MEANS`; Δ with ▲ #12A594 rising, ▼ #E60000 falling,
  muted dash when stable.
- Each persona page gains a wave-shift line from `wave_sizes` with the net delta.

## Inputs you read (never invent data)

- `work/personas.json`: `not_profiled` {count, pct, label, reason}, `report_title`, `survey_name`, `date`, `client_focus`, `methodology`,
  `key_findings[]` ({text, base}), `low_base_notes[]`, `personas[]` (`name`, `tagline`,
  `size_count`, `size_pct`, `color`, `profile`, `profile_footnote`, `sections[]` {title,
  bullets[{text, base}]}, `charts[]` {file, title, how_to_read}), `overview_chart`,
  `summary_table` {title, columns, rows, source_note}, optional `recommendations[]`
  {text, based_on}. Wave mode adds `waves`, per-persona `wave_sizes` + `shift`, `shifts_summary`.
- Render the `text` of claims. Show `base` as a small muted "(n=..)" ONLY when it is smaller than the
  group it describes (a question asked of a subset); otherwise it stays in personas.json.
- `work/charts/*.png`: pre-rendered charts in the AIM chart style. Embed as-is.

## Layout rules (the #1 client complaint was overlapping and cut-off content)

- NOTHING may overlap: no text over a chart, no card over the takeaway bar, nothing over a footer.
- NOTHING may be cut off; never truncate with "…". If it does not fit: fewer words, a bigger
  box, or a continuation page. Never below 9pt body text.
- Images always fit inside their card keeping aspect ratio, at (close to) the size they were drawn.
- No half-empty pages: content fills the page down to just above the footnote/takeaway bar.

## Writing

- Plain, simple English; short sentences; numbers carry the meaning.
- Never use em dashes (—) or en dashes (–); use commas, colons, periods or parentheses.
