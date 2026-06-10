# Survey Intelligence — Report Design System (shared by HTML + PPTX builders)

Both deliverables follow the **AIM dark editorial** report language (the house style of
AIM's flagship client decks): a deep-navy canvas, luminous accent colors, serif display
typography with italic emphasis, and disciplined slide/section "chrome". They MUST look
like two renditions of the same designed document: same palette, same type logic, same
section order, same persona names/colors/numbers, and the SAME chart images.

## Core tokens (use these exact values)

| Token | Hex | Use |
| --- | --- | --- |
| canvas | #0A0E27 | page / slide background (deep navy) |
| panel | #121A4A | cards, chart panels |
| panel-alt | #18225A | hover/alternate fills, table stripes |
| text | #E8ECF4 | primary text |
| dim | #A9B1CE | secondary text |
| muted | #8A93B0 | captions, labels, axis text, footers |
| rule | #1E285E | hairlines, borders, gridlines |
| accent | #00FF96 | AIM signal green — eyebrows, key numbers, emphasis |
| accent-2 | #00B4D0 | cyan — secondary accent |
| warn | #FFD166 | cautions |
| bad | #FF4D6D | negative callouts |

## Persona accent palette (assign in order, persona 1 → 6)

1. #00FF96 (green)  2. #B37BFF (purple)  3. #00D4F5 (cyan)
4. #FFD166 (amber)  5. #FF6EC4 (pink)    6. #2BA8FF (blue)

Each persona keeps ITS color everywhere: card stripes and name accents in HTML, slide
stripes/labels in PPTX, and its chart bars. Text placed ON a persona-color fill is
always canvas (#0A0E27) for contrast. Never reassign colors between formats.

## Typography

- Display/headings: **Georgia** (serif). Emphasis words/lines in *italic* — often in
  accent or persona color (e.g. cover second line, persona names).
- Body/labels: HTML `'Segoe UI', Calibri, Arial, sans-serif`; PPTX **Calibri**.
- Arabic strings: PPTX font **Tahoma** (shapes Arabic correctly); HTML add `dir="auto"`.
- EYEBROWS / LABELS: 9-11px/pt uppercase, letter-spaced, bold, muted or accent.
- Big numbers in Georgia, large (24-28pt), text or accent color.

## Section/slide "chrome" (every content section/slide)

- Thin top hairline (rule color) across the content width.
- **Eyebrow** top-left: `0N · SECTION NAME` — small caps, accent green, bold.
- Top-right running header, muted small: `AIM TECHNOLOGIES · SURVEY INTELLIGENCE · {date}`.
- **Title**: Georgia ~30pt/28px, text color.
- **Kicker** under the title: one italic muted line stating the section's takeaway in
  plain words (write a real insight, not a label).
- Bottom hairline + footer: left `AIM Technologies · Confidential` · right `0N / Section`
  — 8pt muted.

## Canonical document structure (same order in both formats)

1. **Cover** — canvas bg; accent green bar down the LEFT edge; "AIM TECHNOLOGIES" small
   green bold + "Survey Intelligence · Audience Segmentation" muted; then a two-line
   display title: line 1 Georgia ~64-72pt text color, line 2 Georgia *italic* in accent
   green (e.g. "Inside the / Audience"); muted subtitle naming the survey + date; a row
   of persona chips (panel cards: persona-color dot + name + size %); bottom meta strip
   (hairline + 4 columns: PREPARED BY / SURVEY / RESPONDENTS / PERIOD — label 8pt muted
   bold over value 11pt text bold).
2. **01 · Methodology** — the filter funnel (started → removed by status → removed by
   exclude → final N) as KPI stat cards or a ledger with big Georgia numbers; approach
   paragraph; limitations in italic muted.
3. **02 · Audience overview** — a manual **100% stacked size bar** (one row, segments in
   persona colors, inline % labels in canvas-colored bold text) + the persona roster:
   per persona a panel card with persona-color TOP stripe (≈4px/0.06in), name Georgia
   italic in persona color, tagline muted, size count + % as a big Georgia figure;
   include the overview chart image.
4. **03+ · One section/slide per persona** (roster order). All six fields, always:
   - Name + one-line tagline
   - SIZE — count and % of filtered respondents
   - DEMOGRAPHICS — defining profile with numbers
   - BEHAVIORS — vs the overall population
   - CONTENT PREFERENCES — media/platforms/content
   - AD RECEPTIVITY — receptivity + placement implication (channel, format, angle)
5. **Implications for ad placement** — cross-persona table: persona × (reach, best
   channel, format, message angle); header row small caps muted; persona names in their
   colors; row stripes panel/panel-alt.
6. **Closing** — one-paragraph takeaway (Georgia, with one italic accent phrase) +
   small-print recap.

## Inputs you read (never invent data)

- `work/personas.json` — every number, name, tagline, bullet, and implication you render.
  Fields: `report_title`, `survey_name`, `date`, `methodology` (`started`, `removed_status`,
  `removed_exclude`, `final_n`, `approach`, `limitations`), `personas[]` (`name`, `tagline`,
  `size_count`, `size_pct`, `color` hex, `demographics[]`, `behaviors[]`, `content[]`,
  `ad_receptivity[]`, `placement` {`channel`,`format`,`angle`}, `charts[]` filenames),
  `overview_chart`, `implications_summary`.
- `work/charts/*.png` — pre-rendered dark-theme charts (panel background, persona colors).
  Embed as-is (HTML: base64 data URIs; PPTX: insert the PNGs). Do NOT regenerate or restyle.

## Quality bar

- Every claim carries its number from personas.json. No lorem, no placeholders.
- Dark theme everywhere — no white flashes; charts sit on panel-colored cards so they blend.
- Generous whitespace, strict alignment grid, bullets ≤ 14 words.
- It must read like AIM's flagship client deck: premium, editorial, confident.
