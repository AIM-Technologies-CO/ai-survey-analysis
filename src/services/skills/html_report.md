# Skill: build `report.html` (self-contained dark editorial report)

You produce ONE file: `report.html` in the working directory, following the shared
design system exactly (AIM dark editorial).

## Hard requirements

- Fully self-contained: inline `<style>` only, every chart embedded as a base64
  `data:image/png;base64,...` URI. ZERO external requests (no webfonts, CDNs, scripts).
  Renders perfectly offline / attached to an email.
- Build with a Python script (e.g. `work/build_html.py`) that reads `work/personas.json`,
  base64-encodes `work/charts/*.png`, and writes the HTML — never hand-paste numbers.

## Layout spec

- `body`: canvas #0A0E27 background, text #E8ECF4, sans body
  (`'Segoe UI', Calibri, Arial, sans-serif`), 15px/1.6; content column
  `max-width: 1080px` centered; headings `Georgia, 'Times New Roman', serif`.
- **Cover (hero)**: full-width band on canvas with a 6px accent-green bar fixed to the
  LEFT edge; "AIM TECHNOLOGIES" 11px bold letter-spaced in accent; under it
  "Survey Intelligence · Audience Segmentation" muted; then the two-line display title —
  line 1 Georgia 52px #E8ECF4, line 2 Georgia italic 60px #00FF96; muted subtitle
  (survey name · period · N); persona chip row (panel cards, persona dot + name +
  `{pct}%`); meta strip: top hairline, 4 columns of label-over-value
  (PREPARED BY / SURVEY / RESPONDENTS / PERIOD).
- **Section chrome** (each numbered section): top hairline #1E285E; eyebrow
  `0N · NAME` 10px bold letter-spaced #00FF96; floated-right running header
  `AIM TECHNOLOGIES · SURVEY INTELLIGENCE · {date}` 9px #8A93B0; title Georgia 28px;
  kicker italic 13px #8A93B0 — a real one-line takeaway.
- **Methodology**: 4 KPI cards in a row (panel bg, rule border, accent top stripe 4px):
  label 10px muted small-caps over a big Georgia 30px number (started / removed status /
  removed exclude / final N — final N's number in accent green); approach paragraph dim;
  limitations italic muted 12px.
- **Audience overview**: one full-width 100% stacked size bar (height ~44px, segments in
  persona colors, inline `{pct}%` labels in canvas-colored 12px bold, segment ≥ 6% only);
  persona roster grid (2-3 columns): panel cards with persona-color TOP stripe (4px),
  name Georgia italic 19px in persona color, tagline muted 12px, big `{pct}%` Georgia
  26px text-color + `{count} respondents` muted; then the overview chart image on a
  panel card.
- **Persona sections**: one per persona, full chrome with eyebrow `0N · PERSONA`:
  - Header row: persona-color dot (14px) + name Georgia italic 26px in persona color +
    tagline muted; right-aligned SIZE block (`{pct}%` Georgia 30px text + count muted).
  - Two-column grid (1fr 1fr; single column < 720px): four blocks (DEMOGRAPHICS /
    BEHAVIORS / CONTENT PREFERENCES / AD RECEPTIVITY) — block label 10px small-caps in
    the persona color, bullets 13.5px #E8ECF4 with numbers in `<strong>` accent or
    persona color; thin rule between blocks.
  - Charts on panel cards below (max 2 per row), captions 11px muted.
  - Placement callout: panel-alt strip with persona-color left border 3px:
    `Reach them: {channel} · {format} · {angle}` — 13px, channel/format/angle values bold.
- **Implications**: full-width table on panel; header small-caps muted; persona names in
  their colors; zebra rows panel/panel-alt; hairline rules only.
- **Closing**: centered Georgia 20px with one italic accent-green phrase; recap 11px muted.
- Footer of each section (or page footer): `AIM Technologies · Confidential` ·
  `0N / Section` 10px muted, separated by a hairline.
- Print: `@media print` with `-webkit-print-color-adjust: exact; print-color-adjust: exact;`
  and `break-inside: avoid` on cards (the dark theme must survive printing).
- Arabic content: wrap values in `dir="auto"`.

## QA before finishing

1. Re-open the file: assert size > 50 KB and every persona name present.
2. Zero occurrences of `http://` / `https://` anywhere in the HTML.
3. Every `<img>` src starts with `data:image/png;base64,`.
4. Spot-check tokens: `#0A0E27`, `#121A4A`, `#00FF96` all appear in the CSS.
