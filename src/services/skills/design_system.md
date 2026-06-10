# Survey Intelligence — Report Design System (shared by HTML + PPTX builders)

Both deliverables are sold to advertising clients under the AIM "Survey Intelligence"
brand. They MUST look like two renditions of the same designed document: same palette,
same typography logic, same section order, same persona names/colors/numbers, and the
SAME chart images.

## Brand tokens (use these exact values)

| Token | Hex | Use |
| --- | --- | --- |
| ivory (bg) | #FAF8F3 | page/slide background |
| panel | #FFFFFF | cards / content panels |
| hairline | #E6E1D5 | borders, dividers, table rules |
| obsidian (text) | #1A1C20 | primary text |
| muted | #70747C | secondary text, captions, axis labels |
| gold | #D4AF37 | accents, rules, highlights (never body text) |
| gold-deep | #B8902A | gradient depth, chart accent |
| gold-ink | #8A6B16 | gold-colored TEXT on light surfaces (readable) |
| gold-soft | #D4AF37 at ~14% opacity | soft fills behind highlights |

## Persona accent palette (assign in order, persona 1 → 6)

1. #B8902A (deep gold)  2. #2A7F7F (teal)  3. #8E3B46 (burgundy)
4. #2C4770 (navy)       5. #4A7043 (forest) 6. #6B4A7C (plum)

Each persona keeps ITS color everywhere: section/card accents in HTML, slide header
band in PPTX, and its chart bars. Never reassign between formats.

## Typography

- Display/headings: serif — HTML: `Georgia, 'Times New Roman', serif`; PPTX: `Georgia`.
- Body/labels: sans — HTML: `'Segoe UI', -apple-system, Arial, sans-serif`; PPTX: `Calibri`.
- Hierarchy: report title > section title > persona name > label (small caps / letter-spaced,
  muted) > body. Numbers that matter (sizes, percentages) render LARGE in serif, gold-ink
  or persona color.

## Canonical document structure (same order in both formats)

1. **Cover** — report title, survey name, date, "AIM · Survey Intelligence" credit,
   one-line scope, gold accent rule.
2. **Methodology** — data source; the STEP-0 filter counts (started / removed by status /
   removed by exclude / final N) as a small table; how segments were derived; honest
   limitations line.
3. **Audience overview** — total N, the persona roster: every persona with name, tagline,
   size count + % (the % bar/donut uses the persona's color). Sizes MUST sum to ≤ 100%.
4. **One section/slide per persona** (same order as the roster). Fields, always all six:
   - Name + one-line tagline
   - SIZE — count and % of filtered respondents
   - DEMOGRAPHICS — defining profile with supporting numbers
   - BEHAVIORS — vs the overall population
   - CONTENT PREFERENCES — media/platforms/content
   - AD RECEPTIVITY — receptivity + concrete placement implication (channel, format, angle)
5. **Implications for ad placement** — cross-persona summary table: persona × (reach, best
   channel, format, message angle).
6. **Closing** — one-paragraph takeaway + small-print methodology recap.

## Inputs you read (never invent data)

- `work/personas.json` — every number, name, tagline, bullet, and implication you render.
  Fields: `report_title`, `survey_name`, `date`, `methodology` (`started`, `removed_status`,
  `removed_exclude`, `final_n`, `approach`, `limitations`), `personas[]` (`name`, `tagline`,
  `size_count`, `size_pct`, `color` hex, `demographics[]`, `behaviors[]`, `content[]`,
  `ad_receptivity[]`, `placement` {`channel`,`format`,`angle`}, `charts[]` filenames),
  `overview_chart`, `implications_summary`.
- `work/charts/*.png` — pre-rendered, already in the correct style. Embed them as-is
  (HTML: base64 data URIs; PPTX: insert the PNG files). Do NOT regenerate or restyle charts.

## Quality bar

- Every claim shown carries its number from personas.json. No lorem, no placeholders.
- Consistent spacing rhythm; generous whitespace; no walls of text — bullets of ≤ 14 words.
- It must read as a premium consultancy deliverable a client pays for.
