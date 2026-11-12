"""Build the slim AIM deck template used by the segmentation deck builder.

Source: the AIM house deck "VF Flex & Fakka Users - Survey Summary.pptx".
Keeps: the cover slide, the Thank-you slide, and the three layouts the builder uses
(AIM_LIGHT = light-blue content slide, DEFAULT = navy slide, 2_Custom Layout = gradient
cover). Drops the example content slides and every other layout, and extracts the small
brand icons (lightbulb for the takeaway bar, white AIM logo for navy slides).

Usage:  python scripts/build_aim_template.py "<source deck>.pptx"
Output: src/services/skills/template/{aim_template.pptx, lightbulb.png, aim_logo_white.png}
"""

from __future__ import annotations

import sys
from pathlib import Path

from pptx import Presentation

OUT = Path(__file__).resolve().parent.parent / "src" / "services" / "skills" / "template"
KEEP_LAYOUTS = {(0, "AIM_LIGHT"), (0, "DEFAULT"), (1, "2_Custom Layout")}


def main(src: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    prs = Presentation(src)
    slides = list(prs.slides)

    # brand icons from the example slides (before they are deleted)
    def pic(slide, name):
        return next(sh for sh in slide.shapes if sh.shape_type == 13 and sh.name == name)
    (OUT / "lightbulb.png").write_bytes(pic(slides[1], "Image 0").image.blob)
    (OUT / "aim_logo_white.png").write_bytes(pic(slides[3], "Image 0").image.blob)

    # keep only cover (first) + thank-you (last)
    sld_ids = prs.slides._sldIdLst
    for sld in list(sld_ids)[1:-1]:
        prs.part.drop_rel(sld.rId)
        sld_ids.remove(sld)

    # prune unused layouts
    for mi, master in enumerate(prs.slide_masters):
        for layout in list(master.slide_layouts):
            if (mi, layout.name) not in KEEP_LAYOUTS and not layout.used_by_slides:
                master.slide_layouts.remove(layout)

    # neutral cover text (the builder overwrites it per run)
    for sh in prs.slides[0].shapes:
        if not sh.has_text_frame:
            continue
        t = sh.text_frame.text
        if t.startswith("Vodafone"):
            p0, p1 = sh.text_frame.paragraphs[0], sh.text_frame.paragraphs[1]
            p0.runs[0].text = "{{SURVEY_NAME}}"
            for r in p0.runs[1:]:
                r.text = ""
            p1.runs[0].text = "{{SUBTITLE}}"
            for r in p1.runs[1:]:
                r.text = ""
        elif t.strip() == "Research Report":
            sh.text_frame.paragraphs[0].runs[0].text = "{{TITLE}}"
        elif "2026" in t:
            sh.text_frame.paragraphs[0].runs[0].text = "{{DATE}}"
            for r in sh.text_frame.paragraphs[0].runs[1:]:
                r.text = ""

    out = OUT / "aim_template.pptx"
    prs.save(out)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main(sys.argv[1])
