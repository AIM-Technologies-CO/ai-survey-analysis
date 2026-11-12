"""Deterministic QA gate for a segmentation run (copied into every run dir).

Usage (from the run directory):
  python check_report.py personas            # validate work/personas.json (run BEFORE the builders)
  python check_report.py pptx report.pptx    # layout check: off-slide, overlaps, overflowing text
  python check_report.py render report.pptx  # render slides to work/qa/slide-N.png for visual review
  python check_report.py html report.html    # web version: overflow/cut-off in a real browser + screenshots

Exit code 0 = pass, 1 = defects found (each printed on its own line).
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

MIN_BASE = 30
DASHES = "—–―‒"
# Fixed template labels the review rejected: section titles must describe the actual content.
BANNED_SECTION_TITLES = {
    "demographics", "behaviors", "behaviours", "content preferences", "content",
    "ad receptivity", "preferences", "attitudes", "media", "other",
}
MAX_NAME_WORDS = 4
MAX_TAGLINE_WORDS = 12
MAX_BULLET_WORDS = 24


def _words(s: str) -> int:
    return len(str(s or "").split())


def _walk_strings(obj, path="$"):
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{path}[{i}]")


def _check_claim(item, where: str, errs: list[str]) -> None:
    if not isinstance(item, dict) or not str(item.get("text", "")).strip():
        errs.append(f"{where}: must be an object {{text, base}} with non-empty text")
        return
    base = item.get("base")
    if not isinstance(base, int) or isinstance(base, bool):
        errs.append(f"{where}: 'base' must be an integer respondent count (got {base!r})")
    elif base < MIN_BASE:
        errs.append(f"{where}: base n={base} < {MIN_BASE}. LOW BASE: remove this claim "
                    f"(no conclusion may rest on fewer than {MIN_BASE} respondents)")
    if _words(item["text"]) > MAX_BULLET_WORDS:
        errs.append(f"{where}: {_words(item['text'])} words, max {MAX_BULLET_WORDS}; shorten it")


def check_personas(run_dir: Path) -> list[str]:
    p = run_dir / "work" / "personas.json"
    if not p.exists():
        return [f"{p} not found"]
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"personas.json is not valid JSON: {exc}"]
    errs: list[str] = []

    for k in ("report_title", "survey_name", "date", "methodology", "key_findings",
              "personas", "overview_chart", "summary_table"):
        if k not in d:
            errs.append(f"missing top-level field '{k}'")

    for i, kf in enumerate(d.get("key_findings") or []):
        _check_claim(kf, f"key_findings[{i}]", errs)
    if not d.get("key_findings"):
        errs.append("key_findings is empty: give 3-5 headline findings tied to client_focus")

    personas = d.get("personas") or []
    if not 2 <= len(personas) <= 6:
        errs.append(f"{len(personas)} personas; expected 3-6")
    sizes = [p.get("size_count", 0) for p in personas]
    if sizes != sorted(sizes, reverse=True):
        errs.append(f"personas must be sorted by size_count, largest first (got {sizes})")

    charts_dir = run_dir / "work" / "charts"
    for pi, per in enumerate(personas):
        w = f"personas[{pi}] '{per.get('name', '?')}'"
        if (per.get("size_count") or 0) < MIN_BASE:
            errs.append(f"{w}: size_count {per.get('size_count')} < {MIN_BASE}; a persona this small "
                        f"cannot be profiled. Merge it into another persona")
        if _words(per.get("name")) > MAX_NAME_WORDS:
            errs.append(f"{w}: name longer than {MAX_NAME_WORDS} words; use a plain, short name")
        if _words(per.get("tagline")) > MAX_TAGLINE_WORDS:
            errs.append(f"{w}: tagline longer than {MAX_TAGLINE_WORDS} words")
        for f in ("profile", "profile_footnote"):
            if not str(per.get(f, "")).strip():
                errs.append(f"{w}: missing '{f}'")
        if "demographics" in per:
            errs.append(f"{w}: 'demographics' is no longer a section; put it in profile + profile_footnote")
        secs = per.get("sections") or []
        if not 2 <= len(secs) <= 3:
            errs.append(f"{w}: {len(secs)} sections; expected 2-3")
        for si, s in enumerate(secs):
            sw = f"{w}.sections[{si}]"
            title = str(s.get("title", "")).strip()
            if not title:
                errs.append(f"{sw}: missing title")
            elif title.lower() in BANNED_SECTION_TITLES:
                errs.append(f"{sw}: generic title '{title}'. Write a title that says what the "
                            f"bullets are actually about (e.g. 'Where they buy coffee')")
            bullets = s.get("bullets") or []
            if not 1 <= len(bullets) <= 3:
                errs.append(f"{sw}: {len(bullets)} bullets; expected 1-3")
            for bi, b in enumerate(bullets):
                _check_claim(b, f"{sw}.bullets[{bi}]", errs)
        charts = per.get("charts") or []
        if len(charts) > 1:
            errs.append(f"{w}: {len(charts)} charts; max 1 per persona (its most differentiating one)")
        for ci, c in enumerate(charts):
            if not isinstance(c, dict) or not c.get("file"):
                errs.append(f"{w}.charts[{ci}]: must be {{file, title, how_to_read}}")
                continue
            if not (charts_dir / c["file"]).exists():
                errs.append(f"{w}.charts[{ci}]: work/charts/{c['file']} does not exist")
            if not str(c.get("how_to_read", "")).strip():
                errs.append(f"{w}.charts[{ci}]: missing 'how_to_read' (one plain line explaining the chart)")

    npf = d.get("not_profiled")
    final_n = (d.get("methodology") or {}).get("final_n")
    if not isinstance(npf, dict) or not isinstance(npf.get("count"), int):
        errs.append("missing 'not_profiled' {count, pct, label, reason} (use count 0 if everyone is in a persona)")
    elif isinstance(final_n, int) and sum(sizes) + npf["count"] != final_n:
        errs.append(f"persona sizes ({sum(sizes)}) + not_profiled ({npf['count']}) != final_n ({final_n}); "
                    f"the overview bar must add up to 100%")

    oc = d.get("overview_chart")
    if oc and not (charts_dir / oc).exists():
        errs.append(f"overview_chart work/charts/{oc} does not exist")

    st = d.get("summary_table") or {}
    cols = st.get("columns") or []
    rows = st.get("rows") or []
    if st:
        if len(cols) < 3:
            errs.append("summary_table.columns: need persona + size + at least one survey-based column")
        if not str(st.get("source_note", "")).strip():
            errs.append("summary_table.source_note: say which survey questions the columns come from")
        if len(rows) != len(personas):
            errs.append(f"summary_table.rows: {len(rows)} rows for {len(personas)} personas")
        for ri, r in enumerate(rows):
            if not isinstance(r, list) or len(r) != len(cols):
                errs.append(f"summary_table.rows[{ri}]: must be a list with one cell per column")

    for ri, rec in enumerate(d.get("recommendations") or []):
        if not str(rec.get("based_on", "")).strip():
            errs.append(f"recommendations[{ri}]: missing 'based_on' (the survey finding behind it)")

    for path, s in _walk_strings(d):
        if any(ch in s for ch in DASHES):
            errs.append(f"{path}: contains an em/en dash; rewrite with a comma, colon or period")
    return errs


# --------------------------------------------------------------------------- #
# PPTX layout check
# --------------------------------------------------------------------------- #
def _box(shp):
    return (shp.left or 0, shp.top or 0, (shp.left or 0) + (shp.width or 0), (shp.top or 0) + (shp.height or 0))


def _overlap(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0


def _text_overflows(shp) -> tuple[bool, float, float]:
    """Rough estimate: does the text need more height than the box gives it?"""
    from pptx.util import Emu
    tf = shp.text_frame
    box_w = Emu(shp.width or 0).pt - (Emu(tf.margin_left or 0).pt + Emu(tf.margin_right or 0).pt)
    box_h = Emu(shp.height or 0).pt
    if box_w <= 0 or box_h <= 0:
        return False, 0, box_h
    need = 0.0
    for para in tf.paragraphs:
        text = "".join(r.text for r in para.runs)
        size = next((r.font.size.pt for r in para.runs if r.font.size), None) or 18.0
        spacing = para.line_spacing if isinstance(para.line_spacing, float) else 1.0
        char_w = size * 0.5  # avg glyph width for Calibri/Georgia-class fonts
        lines = max(1, math.ceil(len(text) * char_w / box_w)) if text else 1
        need += lines * size * 1.2 * spacing
    return need > box_h * 1.08 + 2, need, box_h


def _ink_box(shp):
    """Estimated area the TEXT actually occupies (boxes are often wider than their text),
    so two wide-but-sparse text boxes on one line are not reported as overlapping."""
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Emu, Pt
    l, t, r, b = _box(shp)
    tf = shp.text_frame
    widest, height = 0.0, 0.0
    box_w = Emu(r - l).pt or 1
    align = None
    for para in tf.paragraphs:
        text = "".join(run.text for run in para.runs)
        size = next((run.font.size.pt for run in para.runs if run.font.size), None) or 18.0
        spacing = para.line_spacing if isinstance(para.line_spacing, float) else 1.0
        w = len(text) * size * 0.5
        lines = max(1, math.ceil(w / box_w)) if text else 1
        widest = max(widest, min(w, box_w))
        height += lines * size * 1.2 * spacing
        align = align or para.alignment
    w_emu, h_emu = int(Pt(widest)), int(Pt(height))
    if align == PP_ALIGN.RIGHT:
        l = max(l, r - w_emu)
    elif align == PP_ALIGN.CENTER:
        mid = (l + r) // 2
        l, r = max(l, mid - w_emu // 2), min(r, mid + w_emu // 2)
    else:
        r = min(r, l + w_emu)
    return (l, t, r, max(b, t + h_emu) if h_emu > (b - t) else t + h_emu)


def _chart_shrink(shp) -> float | None:
    """Displayed width / drawn width for chart images (>= 800px wide). None for icons/logos."""
    import io
    from PIL import Image
    from pptx.util import Emu
    try:
        with Image.open(io.BytesIO(shp.image.blob)) as im:
            px = im.size[0]
            dpi = (im.info.get("dpi") or (200, 200))[0] or 200
    except Exception:
        return None
    if px < 800:
        return None
    return Emu(shp.width or 0).inches / (px / float(dpi))


def check_pptx(pptx_path: Path) -> list[str]:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    from pptx.util import Emu
    prs = Presentation(str(pptx_path))
    W, H = prs.slide_width, prs.slide_height
    errs: list[str] = []

    # Repeated content: the same paragraph on two slides means a slide was padded by copying
    # (e.g. "(continued)" slides that repeat every bullet). Each statement appears once.
    where: dict[str, set[int]] = {}
    for si, slide in enumerate(prs.slides, start=1):
        if slide.slide_layout.name == "2_Custom Layout":
            continue
        for shp in slide.shapes:
            if not shp.has_text_frame:
                continue
            for para in shp.text_frame.paragraphs:
                t = re.sub(r"\s+", " ", para.text).strip()
                if len(t) >= 40:
                    where.setdefault(t, set()).add(si)
                if si and "(continued)" in t.lower():
                    errs.append(f"slide {si}: '(continued)' slide. Each persona gets exactly ONE slide; "
                                f"cut bullets or use one chart instead of splitting")
    for t, slides in where.items():
        if len(slides) > 1:
            errs.append(f"slides {sorted(slides)} repeat the same text: '{t[:60]}...'. Say it once")
    for si, slide in enumerate(prs.slides, start=1):
        # The AIM template's own cover / Thank-you slides (gradient layout) are designed
        # artwork: a full-bleed image under text by design. Only their filled-in text is ours.
        if slide.slide_layout.name == "2_Custom Layout":
            continue
        content = []  # shapes that carry text or images
        for shp in slide.shapes:
            l, t, r, b = _box(shp)
            name = shp.name
            has_text = shp.has_text_frame and shp.text_frame.text.strip() != ""
            is_pic = shp.shape_type == MSO_SHAPE_TYPE.PICTURE
            if (has_text or is_pic) and (l < -1000 or t < -1000 or r > W + 1000 or b > H + 1000):
                errs.append(f"slide {si}: '{name}' extends beyond the slide edge (cut off)")
            if has_text:
                over, need, have = _text_overflows(shp)
                if over:
                    snippet = shp.text_frame.text.strip().replace("\n", " ")[:50]
                    errs.append(f"slide {si}: text box '{snippet}...' needs ~{need:.0f}pt height but has "
                                f"{have:.0f}pt (will overflow/overlap). Enlarge the box or cut words")
                if "…" in shp.text_frame.text or shp.text_frame.text.rstrip().endswith("..."):
                    errs.append(f"slide {si}: truncated text with an ellipsis in '{name}'; show the full text")
            if is_pic:
                shrink = _chart_shrink(shp)
                if shrink is not None and shrink < 0.8:
                    errs.append(f"slide {si}: chart '{name}' is shown at {shrink:.0%} of the size it was drawn, "
                                f"so its text is too small to read. Re-draw it with figsize equal to its box "
                                f"(width {Emu(r - l).inches:.1f}in x height {Emu(b - t).inches:.1f}in)")
            if has_text or is_pic:
                content.append((shp, _ink_box(shp) if has_text else (l, t, r, b), has_text, is_pic))
        if slide.slide_layout.name == "AIM_LIGHT":
            # lowest content edge above the footnote/takeaway zone (y < 6.4in)
            body = [_box(s)[3] for s in slide.shapes
                    if Emu(_box(s)[1]).inches > 1.0 and Emu(_box(s)[1]).inches < 6.4
                    and Emu(_box(s)[3]).inches <= 6.45]
            if body and Emu(max(body)).inches < 4.9:
                errs.append(f"slide {si}: content ends at y={Emu(max(body)).inches:.1f}in, leaving the lower part "
                            f"of the slide empty. Size cards to fill the area (taller cards, larger charts, "
                            f"or more spacing), down to about y=6.3in")
        for i in range(len(content)):
            for j in range(i + 1, len(content)):
                a, ba, _, _ = content[i]
                c, bc, _, _ = content[j]
                ov = _overlap(ba, bc)
                smaller = min((ba[2] - ba[0]) * (ba[3] - ba[1]), (bc[2] - bc[0]) * (bc[3] - bc[1])) or 1
                if ov / smaller > 0.04:
                    ta = (a.text_frame.text.strip()[:30] if a.has_text_frame else a.name)
                    tc = (c.text_frame.text.strip()[:30] if c.has_text_frame else c.name)
                    errs.append(f"slide {si}: '{ta}' overlaps '{tc}' ({ov / smaller:.0%} of the smaller one)")
    return errs


_OVERFLOW_JS = r"""
<script>
window.addEventListener('load', () => setTimeout(() => {
  const out = [], W = document.documentElement.clientWidth;
  const desc = el => (el.tagName.toLowerCase() + (el.className ? '.' + String(el.className).split(' ')[0] : '')
                      + ' "' + (el.alt || el.textContent || '').trim().slice(0, 40) + '"');
  for (const el of document.body.querySelectorAll('*')) {
    if (el.id === '__qa') continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    if (r.right > W + 2) { out.push('wider than the page (' + Math.round(r.right) + 'px > ' + W + 'px): ' + desc(el)); continue; }
    const p = el.parentElement;
    if (p && p !== document.body) {
      const pr = p.getBoundingClientRect(), cs = getComputedStyle(p);
      if (r.right > pr.right + 2 && cs.overflowX === 'visible' && pr.width > 0)
        out.push('spills out of its container by ' + Math.round(r.right - pr.right) + 'px: ' + desc(el));
    }
    if (el.tagName === 'IMG' && el.naturalWidth > 600 && r.width < 200)
      out.push('image shown tiny (' + Math.round(r.width) + 'px): ' + desc(el));
    if (/…$/.test((el.childElementCount ? '' : el.textContent || '').trim())) out.push('truncated text: ' + desc(el));
  }
  const pre = document.createElement('pre'); pre.id = '__qa';
  pre.textContent = JSON.stringify([...new Set(out)].slice(0, 40));
  document.body.appendChild(pre);
}, 300));
</script>"""


def _chrome() -> str | None:
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        if shutil.which(name):
            return shutil.which(name)
    return None


def check_html(html_path: Path, run_dir: Path, width: int = 1280) -> list[str]:
    """Static checks + (if a headless Chrome exists) real-layout overflow detection and
    screenshots in work/qa/html-N.png for visual review."""
    errs: list[str] = []
    html = html_path.read_text(encoding="utf-8", errors="replace")
    if re.search(r"https?://", html):
        errs.append("report.html references an external URL (must be fully self-contained)")
    if re.search(r"text-overflow\s*:\s*ellipsis|line-clamp", html):
        errs.append("report.html truncates text (text-overflow: ellipsis / line-clamp)")
    if not re.search(r"img\s*[,{][^}]*max-width\s*:\s*100%", html) and "qa-img-fit" not in html:
        errs.append("report.html has no global `img { max-width: 100%; height: auto; }` rule; "
                    "large chart PNGs will overflow their cards")
    if "Survey Intelligence" in html:
        errs.append("report.html says 'Survey Intelligence'")
    chrome = _chrome()
    if not chrome:
        print("html: no headless Chrome here; layout overflow not measured (static checks only).")
        return errs
    qa = run_dir / "work" / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    probe = qa / "_probe.html"
    probe.write_text(html.replace("</body>", _OVERFLOW_JS + "</body>", 1), encoding="utf-8")
    flags = ["--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
             f"--window-size={width},900"]
    dom = subprocess.run([chrome, *flags, "--virtual-time-budget=4000", "--dump-dom", probe.as_uri()],
                         capture_output=True, text=True, timeout=180).stdout
    m = re.search(r'<pre id="__qa">(.*?)</pre>', dom, re.S)
    if m:
        import html as _h
        for item in json.loads(_h.unescape(m.group(1))):
            errs.append(f"report.html at {width}px: {item}")
    else:
        errs.append("report.html: layout probe did not run (page failed to load in Chrome)")
    # screenshots: one tall capture split into viewport-sized pages for Read
    from PIL import Image
    shot = qa / "_html_full.png"
    subprocess.run([chrome, *flags[:-1], f"--window-size={width},16000", "--virtual-time-budget=4000",
                    f"--screenshot={shot}", html_path.resolve().as_uri()], capture_output=True, timeout=180)
    for old in qa.glob("html-*.png"):
        old.unlink()
    if shot.exists():
        with Image.open(shot) as im:
            px = im.convert("RGB")
            # crop the trailing area that is just page background (any colour)
            from PIL import ImageChops
            bg = Image.new("RGB", px.size, px.getpixel((px.width - 1, px.height - 1)))
            bbox = ImageChops.difference(px, bg).getbbox()
            h = min(px.height, bbox[3] + 40) if bbox else px.height
            n = 0
            for top in range(0, h, 1100):
                n += 1
                px.crop((0, top, width, min(top + 1100, h))).save(qa / f"html-{n:02d}.png")
        shot.unlink()
        print(f"HTML screenshots: work/qa/html-01.png .. html-{n:02d}.png (open each with Read)")
    probe.unlink(missing_ok=True)
    return errs


def render(pptx_path: Path, run_dir: Path) -> list[str]:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice or not shutil.which("pdftoppm"):
        print("render: LibreOffice/pdftoppm not installed here; skipping visual render "
              "(rely on `check_report.py pptx`).")
        return []
    qa = run_dir / "work" / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(qa), str(pptx_path)],
                   check=True, capture_output=True, timeout=300)
    pdf = qa / (pptx_path.stem + ".pdf")
    for old in qa.glob("slide-*.png"):
        old.unlink()
    subprocess.run(["pdftoppm", "-png", "-r", "80", str(pdf), str(qa / "slide")], check=True, timeout=300)
    # The PDF is a client deliverable too (same pages as the deck).
    shutil.copyfile(pdf, run_dir / (pptx_path.stem + ".pdf"))
    print(f"PDF written: {pptx_path.stem}.pdf")
    pngs = sorted(qa.glob("slide-*.png"))
    print("Rendered slides (open each with Read and look for overlaps, cut-off or clipped text):")
    for p_ in pngs:
        print(f"  {p_.relative_to(run_dir)}")
    return []


def main(argv: list[str]) -> int:
    run_dir = Path.cwd()
    if len(argv) < 2 or argv[1] not in ("personas", "pptx", "render", "html"):
        print(__doc__)
        return 2
    mode = argv[1]
    if mode == "personas":
        errs = check_personas(run_dir)
    elif mode == "html":
        errs = check_html(Path(argv[2]) if len(argv) > 2 else run_dir / "report.html", run_dir)
    else:
        target = Path(argv[2]) if len(argv) > 2 else run_dir / "report.pptx"
        errs = check_pptx(target) if mode == "pptx" else render(target, run_dir)
    for e in errs:
        print(f"DEFECT: {e}")
    print(f"{mode}: {'PASS' if not errs else f'{len(errs)} defect(s)'}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
