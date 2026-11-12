"""AIM house-deck helpers (copied into every run dir; import with `from aim_deck import *`).

Builds slides on the real AIM template (template/aim_template.pptx, from the AIM
"Research Report" deck): gradient cover + Thank-you slide, light-blue AIM_LIGHT content
slides, navy DEFAULT slides. Use these helpers instead of drawing chrome by hand so every
deck matches the house style exactly.

Inline text markup accepted by every text helper:
  **bold**   -> bold navy
  !!57%!!    -> bold AIM red number (the template's highlight style)
  ~~(n=37)~~ -> small muted text (use for bases after a bullet)
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Emu, Inches, Pt

HERE = Path(__file__).resolve().parent
TEMPLATE_DIR = HERE / "template"

# ---- brand tokens (from the AIM template) ----
NAVY = "001847"        # titles, body text, card borders, header bars
NAVY_2 = "0B2E5C"      # small caps labels
RED = "E60000"         # highlighted numbers
BG_LIGHT = "CAEDFC"    # AIM_LIGHT slide background (set by the layout)
WHITE = "FFFFFF"
MUTED = "5A6B80"       # notes, captions
HEAD_PCT = "9CC9E8"    # % next to a card header
SKY = "8FD3F4"         # headings on navy slides
BAR_DARK = "2F5597"    # chart series 1
BAR_LIGHT = "5B9BD5"   # chart series 2
PERSONA_COLORS = ["2F5597", "5B9BD5", "7B3FE4", "12A594", "E0A100", "0B2E5C"]
TEXT_ON = {"2F5597": WHITE, "5B9BD5": NAVY, "7B3FE4": WHITE, "12A594": WHITE,
           "E0A100": NAVY, "0B2E5C": WHITE}

FONT = "Helvetica"           # content slides (template font)
FONT_COVER = "Avenir"        # cover (template font)
FONT_COVER_BLACK = "Avenir Black"

SLIDE_W, SLIDE_H = 13.333, 7.5
CONTENT_TOP, CONTENT_BOTTOM = 1.15, 6.6   # keep content inside; takeaway bar starts at 6.74
LEFT, RIGHT = 0.55, 12.78


def rgb(h: str) -> RGBColor:
    return RGBColor.from_string(h.lstrip("#").upper())


# --------------------------------------------------------------------------- #
# deck lifecycle
# --------------------------------------------------------------------------- #
def open_template() -> Presentation:
    prs = Presentation(str(TEMPLATE_DIR / "aim_template.pptx"))
    prs._aim_closing_id = prs.slides._sldIdLst[-1].id  # Thank-you slide stays last
    return prs


def _layout(prs, name):
    for m in prs.slide_masters:
        for l in m.slide_layouts:
            if l.name == name:
                return l
    raise KeyError(name)


def _new_slide(prs, layout_name):
    s = prs.slides.add_slide(_layout(prs, layout_name))
    for ph in list(s.placeholders):
        ph._element.getparent().remove(ph._element)
    return s


def finish(prs, out_path: str | Path) -> None:
    """Move the Thank-you slide to the end and save."""
    lst = prs.slides._sldIdLst
    closing = next(s for s in lst if s.id == prs._aim_closing_id)
    lst.remove(closing)
    lst.append(closing)
    prs.save(str(out_path))


# --------------------------------------------------------------------------- #
# text
# --------------------------------------------------------------------------- #
_TOKEN = re.compile(r"(\*\*.+?\*\*|!!.+?!!|~~.+?~~)")


def _add_runs(para, text, *, size, color=NAVY, bold=False, font=FONT, italic=False):
    for part in _TOKEN.split(str(text)):
        if not part:
            continue
        r = para.add_run()
        c, b = color, bold
        if part.startswith("**") and part.endswith("**"):
            part, b = part[2:-2], True
        elif part.startswith("!!") and part.endswith("!!"):
            part, b, c = part[2:-2], True, RED
        sz = size
        if part.startswith("~~") and part.endswith("~~"):
            part, c, sz = part[2:-2], MUTED, max(7, size - 2)
        r.text = part
        f = r.font
        f.name, f.size, f.bold, f.italic = font, Pt(sz), b, italic
        f.color.rgb = rgb(c)


def text(slide, x, y, w, h, paragraphs, *, size=11, color=NAVY, bold=False, font=FONT,
         align="left", anchor="top", bullets=False, spacing=1.1, italic=False, space_after=2):
    """Add a text box. `paragraphs` is a str or list of str (markup allowed)."""
    if isinstance(paragraphs, str):
        paragraphs = [paragraphs]
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE,
                          "bottom": MSO_ANCHOR.BOTTOM}[anchor]
    for i, ptxt in enumerate(paragraphs):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER,
                          "right": PP_ALIGN.RIGHT}[align]
        para.line_spacing = spacing
        para.space_after = Pt(space_after)
        _add_runs(para, ("•  " + ptxt) if bullets else ptxt, size=size, color=color,
                  bold=bold, font=font, italic=italic)
    return tb


def est_height(paragraphs, w, size=11, spacing=1.1, bullets=False) -> float:
    """Estimated inches needed for text in a box of width w (use it to stack boxes)."""
    if isinstance(paragraphs, str):
        paragraphs = [paragraphs]
    per_line = max(1, int(w * 72 / (size * 0.52)))
    lines = 0
    for p in paragraphs:
        n = len(re.sub(r"\*\*|!!|~~", "", p)) + (3 if bullets else 0)
        lines += max(1, math.ceil(n / per_line))
    return lines * size * 1.2 * spacing / 72 + len(paragraphs) * 2 / 72


# --------------------------------------------------------------------------- #
# shapes
# --------------------------------------------------------------------------- #
def rect(slide, x, y, w, h, fill=None, line=None, line_w=0.75, rounded=False):
    shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
                                 Inches(x), Inches(y), Inches(w), Inches(h))
    if rounded:
        shp.adjustments[0] = 0.06
    if fill:
        shp.fill.solid()
        shp.fill.fore_color.rgb = rgb(fill)
    else:
        shp.fill.background()
    if line:
        shp.line.color.rgb = rgb(line)
        shp.line.width = Pt(line_w)
    else:
        shp.line.fill.background()
    shp.shadow.inherit = False
    return shp


def picture_fit(slide, path, x, y, max_w, max_h, align="center"):
    """Insert an image scaled to fit inside (max_w x max_h), keeping aspect ratio."""
    with Image.open(path) as im:
        iw, ih = im.size
    scale = min(max_w / iw, max_h / ih)
    w, h = iw * scale, ih * scale
    dx = (max_w - w) / 2 if align == "center" else 0
    return slide.shapes.add_picture(str(path), Inches(x + dx), Inches(y + (max_h - h) / 2),
                                    Inches(w), Inches(h))


# --------------------------------------------------------------------------- #
# slide types
# --------------------------------------------------------------------------- #
def set_cover(prs, *, title, survey_name, subtitle, date):
    """Fill the gradient cover. title e.g. 'Audience Segmentation'."""
    vals = {"{{TITLE}}": title, "{{SURVEY_NAME}}": survey_name,
            "{{SUBTITLE}}": subtitle, "{{DATE}}": date}
    # Max font size so each line stays on ONE line in its template slot
    # (title slot 8.2in wide; survey-name/subtitle sit between the two rules, ~5.2in).
    fit = {"{{TITLE}}": (8.0, 53.33), "{{SURVEY_NAME}}": (5.2, 24.0), "{{SUBTITLE}}": (5.2, 16.0)}
    for sh in prs.slides[0].shapes:
        if sh.has_text_frame:
            for p in sh.text_frame.paragraphs:
                for r in p.runs:
                    for k, v in vals.items():
                        if k in r.text:
                            r.text = r.text.replace(k, v)
                            if k in fit:
                                width, max_pt = fit[k]
                                cur = r.font.size.pt if r.font.size else max_pt
                                r.font.size = Pt(min(cur, max_pt, width * 72 / (max(len(v), 1) * 0.56)))


def light_slide(prs, title):
    """AIM_LIGHT content slide (light-blue bg) with the template title style."""
    s = _new_slide(prs, "AIM_LIGHT")
    text(s, LEFT, 0.32, 12.3, 0.62, title, size=22, bold=True, color=NAVY, anchor="middle")
    return s


def subtitle(slide, line, y=0.92):
    """One plain line under the title (muted)."""
    return text(slide, LEFT, y, 12.2, 0.3, line, size=12, color=MUTED, italic=True)


def takeaway_bar(slide, line):
    """The template's bottom bar: navy rule + lightbulb + one takeaway line (markup ok)."""
    rect(slide, -0.02, 6.74, SLIDE_W + 0.04, 0.04, fill=NAVY)
    picture_fit(slide, TEMPLATE_DIR / "lightbulb.png", 0.14, 6.85, 0.42, 0.42)
    text(slide, 0.72, 6.8, 11.95, 0.62, line, size=10.5, color=NAVY, anchor="middle")


def footnote(slide, line, y=6.45):
    return text(slide, LEFT, y, 12.2, 0.25, line, size=8, color=MUTED)


def card(slide, x, y, w, h, header=None, header_pct=None, header_fill=NAVY):
    """White rounded card with navy border; optional navy header strip ('FOOD & DRINKS 72%').
    Returns the (x, y, w, h) of the free content area inside the card."""
    rect(slide, x, y, w, h, fill=WHITE, line=NAVY, rounded=True)
    if header is None:
        return (x + 0.12, y + 0.1, w - 0.24, h - 0.2)
    rect(slide, x, y, w, 0.32, fill=header_fill)
    tb = text(slide, x + 0.1, y + 0.01, w - 0.2, 0.3, "", size=10, bold=True, color=WHITE,
              anchor="middle", align="center")
    p = tb.text_frame.paragraphs[0]
    _add_runs(p, header.upper(), size=10, color=WHITE, bold=True)
    if header_pct:
        _add_runs(p, f"  {header_pct}", size=9, color=HEAD_PCT, bold=True)
    return (x + 0.12, y + 0.42, w - 0.24, h - 0.52)


def stat_card(slide, x, y, w, h, number, line):
    """Template KPI card: big red number + navy explanation."""
    rect(slide, x, y, w, h, fill=WHITE, line=NAVY, rounded=True)
    tb = text(slide, x + 0.12, y + 0.06, w - 0.24, h - 0.12, "", anchor="middle")
    p = tb.text_frame.paragraphs[0]
    _add_runs(p, f"{number} ", size=20, color=RED, bold=True)
    _add_runs(p, line, size=11, color=NAVY)
    return tb


def dark_slide(prs, title):
    """Navy slide (template DEFAULT layout) for recommendations / section breaks."""
    s = _new_slide(prs, "DEFAULT")
    rect(s, 0, 0, SLIDE_W, SLIDE_H, fill=NAVY)
    text(s, LEFT, 0.3, 12.3, 0.55, title, size=24, bold=True, color=WHITE, anchor="middle")
    picture_fit(s, TEMPLATE_DIR / "aim_logo_white.png", 11.75, 7.0, 1.1, 0.3)
    return s


def numbered_item(slide, n, y, heading, bullets, h=1.0):
    """Template recommendation row on a navy slide: numbered circle + sky heading + bullets."""
    c = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(0.62), Inches(y + 0.06), Inches(0.52), Inches(0.52))
    c.fill.solid(); c.fill.fore_color.rgb = rgb(NAVY)
    c.line.color.rgb = rgb(WHITE); c.line.width = Pt(1)
    c.shadow.inherit = False
    text(slide, 0.62, y + 0.06, 0.52, 0.52, str(n), size=16, color=WHITE, align="center", anchor="middle")
    text(slide, 1.35, y, 11.35, 0.3, heading, size=11.5, bold=True, color=SKY)
    text(slide, 1.35, y + 0.32, 11.35, h - 0.32, bullets, size=10, color=WHITE, bullets=True)
