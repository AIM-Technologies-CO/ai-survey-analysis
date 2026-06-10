# Skill: build `report.html` (self-contained client report)

You produce ONE file: `report.html` in the working directory, following the shared
design system exactly.

## Hard requirements

- Fully self-contained: inline `<style>` only, every chart embedded as a base64
  `data:image/png;base64,...` URI. ZERO external requests (no fonts, CDNs, scripts).
  It must render perfectly offline and attached to an email.
- Build it with a small Python script (e.g. `work/build_html.py`) that reads
  `work/personas.json`, base64-encodes `work/charts/*.png`, and writes the final HTML —
  don't hand-paste numbers.

## Layout spec

- `body`: ivory background, obsidian text, sans body font, `max-width: 1040px` centered
  content column, base font 15px/1.6.
- **Cover header**: white panel, 4px gold top rule; report title in serif 34px; under it
  survey name · date · "AIM · Survey Intelligence" in muted small caps.
- **Section titles**: serif 22px with a thin hairline underline and a short gold tick on
  the left.
- **Methodology**: the filter funnel as a 4-row table (label left, big serif number right);
  approach paragraph; limitations in 12px muted italic.
- **Audience overview**: roster of persona chips — each a white card with the persona-color
  left border (4px), name in serif, tagline muted, and a horizontal % bar in the persona
  color with the count/% as a large serif figure. Then the overview chart image full-width.
- **Persona sections**: one white card per persona:
  - Header band: persona-color 4px left border; name serif 24px in the persona color
    (darken if needed for contrast), tagline muted; SIZE figure top-right (serif 28px).
  - Two-column grid (single column under 720px): left = DEMOGRAPHICS + BEHAVIORS,
    right = CONTENT PREFERENCES + AD RECEPTIVITY. Each block: small-caps muted label,
    then tight bullets; every bullet keeps its number visible (wrap the % in
    `<strong>` obsidian).
  - Persona charts below the grid, side by side (max 2 per row), each with a muted caption.
  - Placement callout: gold-soft background strip, gold-ink text:
    "Reach them: {channel} · {format} · {angle}".
- **Implications**: full-width table, hairline rules, header row in small caps muted,
  first column = persona name in its color.
- **Closing**: centered, serif, on ivory; small-print recap 11px muted.
- Print-friendly: `@media print { ... }` keep colors (`-webkit-print-color-adjust: exact`),
  avoid breaking cards across pages (`break-inside: avoid`).

## QA before finishing

1. `python -c` re-open the file: assert size > 50 KB and it contains every persona name.
2. Confirm zero occurrences of `http://` or `https://` in the HTML (fonts/links aside —
   there should be none at all).
3. Confirm every `<img>` src starts with `data:image/png;base64,`.
