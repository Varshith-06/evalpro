"""Build the SIH 2026 idea-submission deck from the official template.

The deck is generated **from the provided template file itself** rather than
from a blank presentation, so the slide master, layouts, theme, colour scheme,
the SIH banner artwork, the footer bar, the team-name oval, and the slide
dimensions are byte-identical to what the portal supplied. Only text content and
the geometry of the content text boxes change.

Format decisions, and the reasoning:

* **Six slides.** The template ships seven and its own final slide says to keep
  the deck to six including the title page, and that the instructions slide may
  be deleted before upload. So it is deleted.
* **The idea-details pointers are preserved.** The template forbids changing
  them, so every pointer ("Detailed explanation of the proposed solution",
  "Potential challenges and risks", and so on) stays, verbatim, as the bold
  underlined sub-heading it already is. The team's content goes underneath it.
* **Typefaces are the template's.** Garamond for the SIH banner, Times New Roman
  for slide titles, Arial for all body text, with the template's Wingdings and
  Arial bullet characters and its ``tx2`` heading colour.
* **Body point sizes are reduced from the template's 28pt placeholders.** Real
  content does not fit at 28pt in a box 1.5 inches tall. The typeface, colour,
  bullet glyphs, alignment, and every other formatting property are unchanged.

Run with:  python scripts/build_presentation.py
"""
from __future__ import annotations

import copy
import shutil
import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "SIH2026-IDEA-Presentation-Format.pptx"
OUTPUT = REPO / "presentation" / "SIH2026-EvalPro-Idea-Presentation.pptx"

# Template palette, read from the file rather than guessed.
HEADING_BLUE = RGBColor(0x1F, 0x49, 0x7D)   # theme tx2
BODY_BLACK = RGBColor(0x1A, 0x1A, 0x1A)
ACCENT_BLUE = RGBColor(0x00, 0x70, 0xC0)    # the footer bar colour
LAYER_FILL = RGBColor(0x1F, 0x49, 0x7D)
LAYER_FILL_ALT = RGBColor(0x4F, 0x81, 0xBD)  # theme accent1
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

ARIAL = "Arial"
TIMES = "Times New Roman"


# ==========================================================================
# Low-level helpers
# ==========================================================================
def set_bullet(paragraph, char: str | None, font: str = ARIAL, indent_in: float = 0.0) -> None:
    """Set (or clear) a paragraph's bullet glyph.

    python-pptx has no API for this, so it is done on the XML directly using
    exactly the ``buFont``/``buChar`` pairs the template already uses.
    """
    p_pr = paragraph._p.get_or_add_pPr()
    for tag in ("a:buNone", "a:buChar", "a:buAutoNum", "a:buFont"):
        for node in p_pr.findall(qn(tag)):
            p_pr.remove(node)

    marl = int(Inches(indent_in + (0.24 if char else 0.0)))
    p_pr.set("marL", str(marl))
    p_pr.set("indent", str(-int(Inches(0.24)) if char else "0"))

    if char is None:
        p_pr.append(p_pr.makeelement(qn("a:buNone"), {}))
        return

    bu_font = p_pr.makeelement(qn("a:buFont"), {"typeface": font, "pitchFamily": "34", "charset": "0"})
    bu_char = p_pr.makeelement(qn("a:buChar"), {"char": char})
    p_pr.append(bu_font)
    p_pr.append(bu_char)


def set_line_spacing(paragraph, spacing: float, before: float = 0.0, after: float = 0.0) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    for tag in ("a:lnSpc", "a:spcBef", "a:spcAft"):
        for node in p_pr.findall(qn(tag)):
            p_pr.remove(node)
    ln = p_pr.makeelement(qn("a:lnSpc"), {})
    pct = ln.makeelement(qn("a:spcPct"), {"val": str(int(spacing * 100000))})
    ln.append(pct)
    p_pr.insert(0, ln)
    for tag, value in (("a:spcBef", before), ("a:spcAft", after)):
        if value <= 0:
            continue
        node = p_pr.makeelement(qn(tag), {})
        pts = node.makeelement(qn("a:spcPts"), {"val": str(int(value * 100))})
        node.append(pts)
        p_pr.append(node)


def style_run(run, size: float, bold: bool = False, colour: RGBColor = BODY_BLACK,
              font: str = ARIAL, underline: bool = False, italic: bool = False) -> None:
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.underline = underline
    run.font.color.rgb = colour
    # Match the template, which sets the complex-script typeface alongside latin.
    r_pr = run._r.get_or_add_rPr()
    for tag in ("a:cs", "a:ea"):
        for node in r_pr.findall(qn(tag)):
            r_pr.remove(node)
    r_pr.append(r_pr.makeelement(qn("a:cs"), {"typeface": font, "pitchFamily": "34", "charset": "0"}))


def clear_text(shape) -> None:
    frame = shape.text_frame
    frame.clear()
    first = frame.paragraphs[0]
    for run in list(first.runs):
        first._p.remove(run._r)


def add_line(frame, text: str, *, size: float, bold: bool = False,
             colour: RGBColor = BODY_BLACK, bullet: str | None = None,
             indent: float = 0.0, underline: bool = False, spacing: float = 1.0,
             before: float = 0.0, after: float = 0.0, first: bool = False,
             align=PP_ALIGN.LEFT, font: str = ARIAL, bullet_font: str = ARIAL):
    paragraph = frame.paragraphs[0] if first else frame.add_paragraph()
    paragraph.alignment = align
    run = paragraph.add_run()
    run.text = text
    style_run(run, size, bold=bold, colour=colour, underline=underline, font=font)
    set_bullet(paragraph, bullet, font=bullet_font, indent_in=indent)
    set_line_spacing(paragraph, spacing, before, after)
    return paragraph


def drop_empty_paragraphs(frame) -> None:
    """Remove paragraphs with no runs.

    Some of the template's boxes carry a list style that suppresses the bullet
    on the very first paragraph. Building every paragraph the same way and then
    dropping the leftover empty one is more reliable than special-casing which
    box behaves how.
    """
    body = frame._txBody
    for paragraph in list(frame.paragraphs):
        if not paragraph.runs:
            body.remove(paragraph._p)
    if not frame.paragraphs:
        body.append(body.makeelement(qn("a:p"), {}))


def place(shape, left: float, top: float, width: float, height: float) -> None:
    shape.left, shape.top, shape.width, shape.height = (
        Inches(left), Inches(top), Inches(width), Inches(height),
    )


def find(slide, name_fragment: str):
    for shape in slide.shapes:
        if name_fragment.lower() in (shape.name or "").lower():
            return shape
    raise KeyError(f"{name_fragment!r} not found on slide")


def set_autofit_off(shape) -> None:
    """The template's boxes carry ``spAutoFit``; with denser content we size the
    box ourselves, so the tag is removed rather than fought with."""
    body = shape.text_frame._txBody.find(qn("a:bodyPr"))
    if body is None:
        return
    for tag in ("a:spAutoFit", "a:normAutofit"):
        for node in body.findall(qn(tag)):
            body.remove(node)
    body.set("wrap", "square")
    for attribute, value in (("lIns", "45720"), ("rIns", "45720"), ("tIns", "22860"), ("bIns", "22860")):
        body.set(attribute, value)


def delete_slide(prs, index: int) -> None:
    slide_id_list = prs.slides._sldIdLst
    entries = list(slide_id_list)
    entry = entries[index]
    prs.part.drop_rel(entry.rId)
    slide_id_list.remove(entry)


# ==========================================================================
# Slide content
# ==========================================================================
TEAM = {
    # Fields the portal issues. Left as obvious placeholders to be filled in
    # before upload rather than invented here.
    "ps_id": "[Problem Statement ID from the SIH portal]",
    "ps_title": "Automated Programming Lab Evaluation Platform",
    "theme": "Smart Education",
    "category": "Software",
    "team_id": "[Team ID from the SIH portal]",
    "team_name": "[Team Name as registered on the portal]",
}

IDEA_TITLE = "EvalPro - Programming Labs That Mark Themselves, and Explain Why"


def build_title_slide(slide) -> None:
    box = find(slide, "TextBox 9")
    set_autofit_off(box)
    place(box, 0.36, 2.10, 6.20, 5.05)
    clear_text(box)
    frame = box.text_frame

    fields = [
        ("Problem Statement ID – ", TEAM["ps_id"]),
        ("Problem Statement Title – ", TEAM["ps_title"]),
        ("Theme – ", TEAM["theme"]),
        ("PS Category – ", TEAM["category"]),
        ("Team ID – ", TEAM["team_id"]),
        ("Team Name (Registered on portal) – ", TEAM["team_name"]),
    ]
    for index, (label, value) in enumerate(fields):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = PP_ALIGN.JUSTIFY
        label_run = paragraph.add_run()
        label_run.text = label
        style_run(label_run, 15, bold=True, colour=BODY_BLACK)
        value_run = paragraph.add_run()
        value_run.text = value
        style_run(value_run, 15, bold=False, colour=HEADING_BLUE)
        set_bullet(paragraph, "•")
        set_line_spacing(paragraph, 1.15, before=0, after=12)

    # The template's own subtitle placeholder reads "TITLE PAGE"; it stays.
    subtitle = find(slide, "Subtitle 3")
    for paragraph in subtitle.text_frame.paragraphs:
        for run in paragraph.runs:
            if run.text.strip().upper() == "TITLE PAGE":
                run.text = "TITLE PAGE"


def build_solution_slide(slide) -> None:
    title = find(slide, "Title 1")
    clear_text(title)
    add_line(
        title.text_frame, IDEA_TITLE, size=27, bold=True, colour=HEADING_BLUE,
        font=TIMES, first=True, align=PP_ALIGN.LEFT,
    )
    # The template's team-name oval sits at x 0.36-1.73, so the title starts
    # clear of it rather than running underneath.
    place(title, 1.88, 0.10, 8.55, 1.00)

    box = find(slide, "TextBox 8")
    set_autofit_off(box)
    place(box, 0.36, 1.26, 12.58, 5.50)
    clear_text(box)
    frame = box.text_frame

    add_line(
        frame,
        "Proposed Solution (Describe your Idea/Solution/Prototype)",
        size=20, bold=True, colour=HEADING_BLUE, underline=True,
        bullet="v", bullet_font="Wingdings", first=True, after=10,
    )

    sections = [
        (
            "Detailed explanation of the proposed solution",
            [
                "A student submits their lab program. The platform runs it, marks it, and shows them "
                "exactly why they got each mark.",
                "It does not stop at a number. Each mark is linked to a topic - loops, recursion, "
                "error handling - so the student sees which topic they are weak on, not just which lab.",
                "Marks from every lab add up over the semester into a clear picture of what each "
                "student actually understands.",
            ],
        ),
        (
            "How it addresses the problem",
            [
                "Collects the academic information: labs, submissions and class lists, straight from "
                "the college's existing Moodle or Google Classroom.",
                "Analyses it four ways: each submission, each student over time, each question, and "
                "the class as a whole.",
                "Gives everyone a next step: what a student should revise, what a teacher should "
                "explain again, which students need support.",
                "Shows it on one simple screen per person - one for students, one for teachers, one "
                "for the department.",
            ],
        ),
        (
            "Innovation and uniqueness of the solution",
            [
                "A missing bracket costs two marks, not the whole lab. We find the smallest fix that "
                "makes the program run, then mark the rest of the work properly.",
                "The answer key never goes near the student's program, so it cannot be copied, "
                "guessed or hard-coded.",
                "It also marks the question paper. If the strongest students all fail one part, the "
                "question was probably unclear, and the teacher is told.",
                "When it is not sure, it says so and hands the submission to the teacher instead of "
                "guessing.",
            ],
        ),
    ]
    _render_sections(frame, sections, heading_size=17, body_size=14)


def build_technical_slide(slide) -> None:
    box = find(slide, "TextBox 8")
    set_autofit_off(box)
    place(box, 0.36, 3.62, 12.58, 3.20)
    clear_text(box)
    frame = box.text_frame

    add_line(
        frame,
        "Technologies to be used (e.g. programming languages, frameworks, hardware)",
        size=16, bold=True, colour=HEADING_BLUE, underline=True,
        bullet="v", bullet_font="Wingdings", first=True, after=5,
    )
    for text in [
        "Python and FastAPI on the server. A plain web interface that works in any browser, with "
        "nothing to install on a student's machine.",
        "Student programs run inside a locked-down sandbox, so nothing they submit can touch the "
        "college's systems or see anyone else's work.",
        "Well-established methods do the marking: standard program analysis, the same plagiarism "
        "technique universities already use, and a well-known model for tracking what a learner knows.",
        "Runs on an ordinary computer. No expensive hardware, no paid AI service needed to mark a "
        "submission.",
    ]:
        add_line(frame, text, size=13.5, bullet="•", indent=0.12, spacing=0.95, after=3,
                 align=PP_ALIGN.JUSTIFY)

    add_line(
        frame,
        "Methodology and process for implementation (Flow Charts/Images/ working prototype)",
        size=16, bold=True, colour=HEADING_BLUE, underline=True,
        bullet="v", bullet_font="Wingdings", before=10, after=5,
    )
    for text in [
        "The teacher writes the lab question in ordinary English. The platform reads it and works "
        "out what it can check - and shows the teacher that list before anything is published.",
        "Working prototype today: a full class of 24 students across four labs, marked from start "
        "to finish, with every screen above already built.",
    ]:
        add_line(frame, text, size=13.5, bullet="•", indent=0.12, spacing=0.95, after=3,
                 align=PP_ALIGN.JUSTIFY)

    _draw_architecture(slide)


def build_feasibility_slide(slide) -> None:
    box = find(slide, "TextBox 8")
    set_autofit_off(box)
    place(box, 0.36, 1.26, 12.58, 4.66)
    clear_text(box)
    frame = box.text_frame

    sections = [
        (
            "Analysis of the feasibility of the idea",
            [
                "It already works. Nothing here waits on a research breakthrough.",
                "It runs on a normal laptop or a small college server, and setting up one lab takes "
                "a teacher about ten minutes.",
                "It fits into the tools a college already uses, so marks go back into the existing "
                "gradebook automatically.",
            ],
        ),
        (
            "Potential challenges and risks",
            [
                "Student programs are run on our machine, and some of them will be badly behaved.",
                "Automatic marking can get things wrong, and a wrong mark destroys trust quickly.",
                "Wrongly accusing a student of copying is the most damaging mistake the system could make.",
                "Teachers will abandon anything that costs them more time than it saves.",
            ],
        ),
        (
            "Strategies for overcoming these challenges",
            [
                "Every program runs sealed off from everything else, and is thrown away afterwards.",
                "The system knows when it is unsure and sends those submissions to the teacher. "
                "Students can question any mark and get a human answer.",
                "For copying, it only shows the overlapping lines side by side. The judgement stays "
                "with the teacher - the platform never accuses anyone.",
                "Set-up is ten minutes, marks flow back into the existing gradebook, and every "
                "correction a teacher makes teaches the system to do better next time.",
            ],
        ),
    ]
    _render_sections(frame, sections, heading_size=17, body_size=14)

    metric_strip(slide, [
        ("Already built", "a full class of 24 students and four labs, marked end to end"),
        ("~10 minutes", "for a teacher to set up one lab"),
        ("No extra cost", "runs on ordinary hardware, no paid AI service to mark work"),
    ])


def build_impact_slide(slide) -> None:
    box = find(slide, "TextBox 8")
    set_autofit_off(box)
    place(box, 0.36, 1.26, 12.58, 5.50)
    clear_text(box)
    frame = box.text_frame

    sections = [
        (
            "Potential impact on the target audience",
            [
                "Students stop guessing why they lost marks. Instead of \"72/100\" they see \"your "
                "program crashes when the list is empty\" - and which topic to revise because of it.",
                "Teachers get their marking hours back, and get told what the class did not "
                "understand while there is still time to teach it again.",
                "Heads of department see how the course is performing against its stated outcomes, "
                "live, instead of reconstructing it at the end of the year.",
            ],
        ),
        (
            "Benefits of the solution (social, economic, environmental, etc.)",
            [
                "Fairer marking. Every student is marked to the same standard, every mark comes with "
                "a reason, and any student can question any mark.",
                "Time and cost saved. Marking a lab of sixty students goes from an evening's work to "
                "reviewing the handful the system was unsure about.",
                "Accreditation reporting for NBA and NAAC, which takes weeks of manual work today, "
                "becomes a report that is always up to date.",
                "Weaker students are spotted early and offered help, rather than discovered at the "
                "end of the semester when nothing can be done.",
            ],
        ),
    ]
    _render_sections(frame, sections, heading_size=17, body_size=15)


def build_references_slide(slide) -> None:
    box = find(slide, "TextBox 8")
    set_autofit_off(box)
    place(box, 0.36, 1.26, 12.58, 4.66)
    clear_text(box)
    frame = box.text_frame

    add_line(
        frame, "Details / Links of the reference and research work",
        size=17, bold=True, colour=HEADING_BLUE, underline=True,
        bullet="v", bullet_font="Wingdings", after=9,
    )

    groups = [
        ("Checking for copied code", [
            "Schleimer, Wilkerson & Aiken, \"Winnowing: Local Algorithms for Document "
            "Fingerprinting\", ACM SIGMOD 2003 - the method behind MOSS, used by universities "
            "worldwide.",
        ]),
        ("Tracking what a student knows", [
            "Corbett & Anderson, \"Knowledge Tracing\", User Modeling and User-Adapted Interaction, "
            "1994 - the standard model for estimating mastery from performance.",
            "Ebel & Frisbie, Essentials of Educational Measurement - the classical way to tell a "
            "good exam question from a bad one.",
        ]),
        ("Marking programs automatically", [
            "Gulwani, Radicek & Zuleger, \"Automated Clustering and Program Repair for Introductory "
            "Programming Assignments\", PLDI 2018 - the basis for giving partial credit instead of "
            "a zero.",
            "tree-sitter - the parser used to understand student code even when it does not compile.",
        ]),
        ("Running untrusted code safely", [
            "Agache et al., \"Firecracker: Lightweight Virtualization\", USENIX NSDI 2020.",
        ]),
        ("Standards we build to", [
            "1EdTech Learning Tools Interoperability 1.3 - how the platform plugs into Moodle and "
            "Google Classroom.",
            "National Board of Accreditation (India) - the CO-PO attainment rules the reports follow.",
        ]),
    ]
    for heading, entries in groups:
        add_line(frame, heading, size=14, bold=True, colour=ACCENT_BLUE,
                 bullet="•", indent=0.0, spacing=0.95, before=5, after=2)
        for entry in entries:
            add_line(frame, entry, size=12.5, bullet="–", indent=0.30, spacing=0.95, after=3)
    drop_empty_paragraphs(frame)

    footer = slide.shapes.add_textbox(Inches(0.34), Inches(6.08), Inches(12.66), Inches(0.62))
    footer.fill.solid()
    footer.fill.fore_color.rgb = RGBColor(0xEC, 0xF2, 0xF9)
    footer.line.color.rgb = LAYER_FILL_ALT
    footer.line.width = Pt(0.75)
    set_autofit_off(footer)
    clear_text(footer)
    footer.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    add_line(
        footer.text_frame,
        "Working prototype and source code: github.com/Varshith-06/evalpro",
        size=13, bold=True, colour=LAYER_FILL, bullet=None, align=PP_ALIGN.CENTER,
        first=True, spacing=0.95,
    )


def metric_strip(slide, items: list[tuple[str, str]], top: float = 6.02, height: float = 0.76) -> None:
    """A row of measured figures along the foot of a slide.

    Judges read numbers before prose, and the numbers here are measured on the
    working prototype rather than projected, so they are given the space to be
    read on their own.
    """
    left = Inches(0.34)
    count = max(1, len(items))
    gap = Inches(0.24)
    width = Emu(int((Inches(12.66) - gap * (count - 1)) / count))
    for index, (figure, caption) in enumerate(items):
        x = Emu(int(left) + index * (int(width) + int(gap)))
        box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(top), width, Inches(height))
        box.fill.solid()
        box.fill.fore_color.rgb = RGBColor(0xEC, 0xF2, 0xF9)
        box.line.color.rgb = LAYER_FILL_ALT
        box.line.width = Pt(0.75)
        box.shadow.inherit = False
        set_autofit_off(box)
        clear_text(box)
        frame = box.text_frame
        frame.word_wrap = True
        frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        add_line(frame, figure, size=16, bold=True, colour=LAYER_FILL, bullet=None,
                 align=PP_ALIGN.CENTER, first=True, spacing=0.9)
        add_line(frame, caption, size=10, colour=BODY_BLACK, bullet=None,
                 align=PP_ALIGN.CENTER, spacing=0.88)


def _render_sections(frame, sections, heading_size: float, body_size: float) -> None:
    """Pointer heading (preserved verbatim from the template) then the content."""
    for heading, bullets in sections:
        add_line(
            frame, heading, size=heading_size, bold=True, colour=HEADING_BLUE,
            underline=True, bullet="v", bullet_font="Wingdings",
            before=7, after=3, align=PP_ALIGN.LEFT,
        )
        for text in bullets:
            add_line(
                frame, text, size=body_size, bullet="•", indent=0.12,
                spacing=0.95, after=2, align=PP_ALIGN.JUSTIFY,
            )


# ==========================================================================
# Architecture diagram, drawn as native shapes
# ==========================================================================
LAYERS = [
    ("1.  COLLECT", "lab questions, submissions\nand class lists, from the\ncollege's existing system"),
    ("2.  MARK", "run the program safely,\ncheck it against the rubric,\nexplain every mark"),
    ("3.  UNDERSTAND", "build a picture of which\ntopics each student\nhas actually got"),
    ("4.  ACT", "tell students what to revise,\ntell teachers what to reteach,\nreport on the course"),
]


def _draw_architecture(slide) -> None:
    top = Inches(1.22)
    height = Inches(1.62)
    left = Inches(0.36)
    width = Inches(2.86)
    gap = Inches(0.32)

    for index, (title, body) in enumerate(LAYERS):
        x = Emu(int(left) + index * (int(width) + int(gap)))
        box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, top, width, height)
        box.fill.solid()
        box.fill.fore_color.rgb = LAYER_FILL if index % 2 == 0 else LAYER_FILL_ALT
        box.line.color.rgb = WHITE
        box.line.width = Pt(1)
        box.shadow.inherit = False

        frame = box.text_frame
        frame.word_wrap = True
        frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        set_autofit_off(box)
        clear_text(box)
        add_line(frame, title, size=10.5, bold=True, colour=WHITE,
                 bullet=None, align=PP_ALIGN.CENTER, first=True, spacing=0.95, after=3)
        add_line(frame, body, size=8.5, colour=WHITE, bullet=None,
                 align=PP_ALIGN.CENTER, spacing=0.9)

        if index < len(LAYERS) - 1:
            arrow_left = Emu(int(x) + int(width))
            connector = slide.shapes.add_connector(
                MSO_CONNECTOR.STRAIGHT, arrow_left, Emu(int(top) + int(height) // 2),
                Emu(int(arrow_left) + int(gap)), Emu(int(top) + int(height) // 2),
            )
            connector.line.color.rgb = HEADING_BLUE
            connector.line.width = Pt(2.25)
            line = connector.line._get_or_add_ln()
            tail = line.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"})
            line.append(tail)

    caption = slide.shapes.add_textbox(left, Emu(int(top) + int(height) + int(Inches(0.06))),
                                       Inches(12.55), Inches(0.44))
    set_autofit_off(caption)
    clear_text(caption)
    add_line(
        caption.text_frame,
        "Every mark is tied to a topic. That one link is what turns a pile of lab marks into a picture "
        "of what a student actually understands.",
        size=11, bold=True, colour=HEADING_BLUE, bullet=None, first=True,
        align=PP_ALIGN.CENTER, spacing=0.95,
    )


# ==========================================================================
# Entry point
# ==========================================================================
def main() -> int:
    if not TEMPLATE.exists():
        print(f"template not found: {TEMPLATE}", file=sys.stderr)
        return 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TEMPLATE, OUTPUT)

    prs = Presentation(str(OUTPUT))
    slides = list(prs.slides)

    build_title_slide(slides[0])
    build_solution_slide(slides[1])
    build_technical_slide(slides[2])
    build_feasibility_slide(slides[3])
    build_impact_slide(slides[4])
    build_references_slide(slides[5])

    # The template's own instructions slide says the deck must be six slides
    # including the title page, and that this slide may be deleted before upload.
    delete_slide(prs, 6)

    _set_team_name(prs)
    prs.save(str(OUTPUT))

    print(f"wrote {OUTPUT.relative_to(REPO)} — {len(prs.slides.__iter__.__self__._sldIdLst)} slides")
    return 0


def _set_team_name(prs) -> None:
    """The oval in the top-left of every content slide reads 'Your Team Name'."""
    for slide in prs.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    if run.text.strip() == "Your Team Name":
                        run.text = TEAM["team_name"] if not TEAM["team_name"].startswith("[") else "Your Team Name"


if __name__ == "__main__":
    raise SystemExit(main())
