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
  content does not fit at 28pt in a box 1.5 inches tall. The typeface, colour
  and bullet glyphs are unchanged.
* **Body text is left-aligned, not justified.** The placeholders are justified,
  which suits the long paragraphs they were written for and ruins short
  bullets: justification stretches a one-line bullet across the full slide and
  opens rivers of white space between the words. Ragged-right keeps word
  spacing even, which is what reads well from the back of a room.

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

IDEA_TITLE = "EvalPro - Programming Labs That Mark Themselves"


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
        # Left, not justified: these lines are short enough that justification
        # only opens gaps between the words.
        paragraph.alignment = PP_ALIGN.LEFT
        label_run = paragraph.add_run()
        label_run.text = label
        style_run(label_run, 15.5, bold=True, colour=BODY_BLACK)
        value_run = paragraph.add_run()
        value_run.text = value
        style_run(value_run, 15.5, bold=False, colour=HEADING_BLUE)
        set_bullet(paragraph, "•")
        set_line_spacing(paragraph, 1.12, before=0, after=14)

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
        title.text_frame, IDEA_TITLE, size=24, bold=True, colour=HEADING_BLUE,
        font=TIMES, first=True, align=PP_ALIGN.LEFT,
    )
    # The template's team-name oval sits at x 0.36-1.73, so the title starts
    # clear of it rather than running underneath.
    place(title, 1.88, 0.22, 8.72, 0.80)

    box = find(slide, "TextBox 8")
    set_autofit_off(box)
    place(box, 0.36, 1.30, 12.58, 5.46)
    clear_text(box)
    frame = box.text_frame

    add_line(
        frame,
        "Proposed Solution (Describe your Idea/Solution/Prototype)",
        size=20, bold=True, colour=HEADING_BLUE, underline=True,
        bullet="v", bullet_font="Wingdings", first=True, after=11,
    )

    sections = [
        (
            "Detailed explanation of the proposed solution",
            [
                "A student submits their lab program. It is run, marked, and every mark is explained.",
                "Each mark is tied to a topic, so a student sees which topic is weak - not just which lab.",
                "Those marks add up across the semester into a picture of what each student understands.",
            ],
        ),
        (
            "How it addresses the problem",
            [
                "Collects labs, submissions and class lists from the college's existing system.",
                "Analyses each submission, each student, each question and the whole class.",
                "Tells students what to revise, and teachers what to explain again.",
            ],
        ),
        (
            "Innovation and uniqueness of the solution",
            [
                "It marks the thinking, not just the output - a right idea with one wrong line still scores.",
                "The answer key never touches the student's program, so it cannot be copied.",
                "It marks the question paper too - unclear questions are flagged to the teacher.",
                "When it is unsure, it says so and hands the work to a human.",
            ],
        ),
    ]
    _render_sections(frame, sections, heading_size=18, body_size=16,
                     section_gap=20, bullet_gap=6, line=1.12)


def build_technical_slide(slide) -> None:
    box = find(slide, "TextBox 8")
    set_autofit_off(box)
    place(box, 0.36, 3.62, 12.58, 3.18)
    clear_text(box)
    frame = box.text_frame

    add_line(
        frame,
        "Technologies to be used (e.g. programming languages, frameworks, hardware)",
        size=15, bold=True, colour=HEADING_BLUE, underline=True,
        bullet="v", bullet_font="Wingdings", first=True, after=6,
    )
    for text in [
        "Python and FastAPI, with a plain web interface that runs in any browser.",
        "Student programs run inside a sealed sandbox, away from college systems.",
        "Proven methods for marking, plagiarism checking and tracking what a learner knows.",
        "Ordinary hardware - no GPU, and no paid AI service to mark a submission.",
    ]:
        add_line(frame, text, size=13.5, bullet="•", indent=0.12, spacing=1.06, after=4)

    add_line(
        frame,
        "Methodology and process for implementation (Flow Charts/Images/ working prototype)",
        size=15, bold=True, colour=HEADING_BLUE, underline=True,
        bullet="v", bullet_font="Wingdings", before=11, after=6,
    )
    for text in [
        "The teacher writes the question in plain English; the platform shows what it will check "
        "before anything is published.",
        "Working prototype: a class of 24 students across four labs, marked end to end.",
    ]:
        add_line(frame, text, size=13.5, bullet="•", indent=0.12, spacing=1.06, after=4)

    _draw_architecture(slide)


def build_feasibility_slide(slide) -> None:
    box = find(slide, "TextBox 8")
    set_autofit_off(box)
    place(box, 0.36, 1.30, 12.58, 4.60)
    clear_text(box)
    frame = box.text_frame

    sections = [
        (
            "Analysis of the feasibility of the idea",
            [
                "It already works. Nothing here waits on a research breakthrough.",
                "Runs on a normal laptop or a small college server.",
                "Fits the tools a college already uses, so marks return to the existing gradebook.",
            ],
        ),
        (
            "Potential challenges and risks",
            [
                "Running students' programs safely on our own machine.",
                "A wrong automatic mark loses a class's trust very quickly.",
                "Wrongly accusing a student of copying.",
                "Teachers abandoning anything that costs more time than it saves.",
            ],
        ),
        (
            "Strategies for overcoming these challenges",
            [
                "Every program runs sealed off from everything else, then is destroyed.",
                "Unsure work goes to the teacher, and any student can question any mark.",
                "For copying we show the matching lines only. The teacher decides.",
                "Ten-minute setup, and every correction a teacher makes trains the system.",
            ],
        ),
    ]
    _render_sections(frame, sections, heading_size=16, body_size=13.5,
                     section_gap=13, bullet_gap=4, line=1.06)

    metric_strip(slide, [
        ("Already built", "a class of 24 students and four labs, marked end to end"),
        ("~10 minutes", "for a teacher to set up one lab"),
        ("No extra cost", "ordinary hardware, no paid AI service"),
    ])


def build_impact_slide(slide) -> None:
    box = find(slide, "TextBox 8")
    set_autofit_off(box)
    place(box, 0.36, 1.52, 12.58, 5.24)
    clear_text(box)
    frame = box.text_frame

    sections = [
        (
            "Potential impact on the target audience",
            [
                "Students see “your program crashes on an empty list” instead of “72/100”.",
                "Teachers get their marking hours back, and hear what the class missed in time to "
                "teach it again.",
                "Departments get NBA and NAAC outcome reports that are always up to date.",
            ],
        ),
        (
            "Benefits of the solution (social, economic, environmental, etc.)",
            [
                "Fairer - one standard for everyone, every mark has a reason, every mark can be "
                "questioned.",
                "Cheaper - an evening of marking becomes reviewing the few cases the system flagged.",
                "Earlier - struggling students are found while something can still be done about it.",
                "Lasting - what a class got wrong this year improves how it is taught next year.",
            ],
        ),
    ]
    _render_sections(frame, sections, heading_size=20, body_size=17.5,
                     section_gap=34, bullet_gap=13, line=1.18)


def build_references_slide(slide) -> None:
    box = find(slide, "TextBox 8")
    set_autofit_off(box)
    place(box, 0.36, 1.30, 12.58, 4.60)
    clear_text(box)
    frame = box.text_frame

    add_line(
        frame, "Details / Links of the reference and research work",
        size=16, bold=True, colour=HEADING_BLUE, underline=True,
        bullet="v", bullet_font="Wingdings", after=10,
    )

    groups = [
        ("Checking for copied code", [
            "Schleimer, Wilkerson & Aiken, “Winnowing: Local Algorithms for Document "
            "Fingerprinting”, ACM SIGMOD 2003 - the method behind MOSS.",
        ]),
        ("Tracking what a student knows", [
            "Corbett & Anderson, “Knowledge Tracing”, User Modeling and User-Adapted "
            "Interaction, 1994.",
            "Ebel & Frisbie, Essentials of Educational Measurement - telling a good exam question "
            "from a bad one.",
        ]),
        ("Marking programs automatically", [
            "Gulwani, Radicek & Zuleger, “Automated Clustering and Program Repair for "
            "Introductory Programming Assignments”, PLDI 2018.",
            "tree-sitter - the parser used to read student code even when it does not compile.",
        ]),
        ("Running untrusted code, and the standards we build to", [
            "Agache et al., “Firecracker: Lightweight Virtualization”, USENIX NSDI 2020.",
            "1EdTech LTI 1.3 for Moodle and Google Classroom; NBA (India) for the CO-PO reports.",
        ]),
    ]
    for heading, entries in groups:
        add_line(frame, heading, size=15, bold=True, colour=ACCENT_BLUE,
                 bullet="•", indent=0.0, spacing=1.06, before=13, after=5)
        for entry in entries:
            add_line(frame, entry, size=13, bullet="–", indent=0.30, spacing=1.08, after=5)
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
        first=True, spacing=1.0,
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
                 align=PP_ALIGN.CENTER, first=True, spacing=1.0)
        add_line(frame, caption, size=10, colour=BODY_BLACK, bullet=None,
                 align=PP_ALIGN.CENTER, spacing=1.04)


def _render_sections(
    frame,
    sections,
    heading_size: float,
    body_size: float,
    section_gap: float = 14.0,
    bullet_gap: float = 5.0,
    line: float = 1.08,
) -> None:
    """Pointer heading (preserved verbatim from the template) then the content.

    Body text is left-aligned, not justified. The template's placeholders are
    justified, which is fine for the long paragraphs they contain and wrong for
    short bullets: justification stretches a one-line bullet across the full
    slide width and opens rivers of white space between words. Ragged-right
    keeps the word spacing even, which is what actually reads well from the back
    of a room.
    """
    for heading, bullets in sections:
        add_line(
            frame, heading, size=heading_size, bold=True, colour=HEADING_BLUE,
            underline=True, bullet="v", bullet_font="Wingdings",
            before=section_gap, after=6, align=PP_ALIGN.LEFT, spacing=1.0,
        )
        for text in bullets:
            add_line(
                frame, text, size=body_size, bullet="•", indent=0.12,
                spacing=line, after=bullet_gap, align=PP_ALIGN.LEFT,
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
        add_line(frame, title, size=11, bold=True, colour=WHITE,
                 bullet=None, align=PP_ALIGN.CENTER, first=True, spacing=1.0, after=4)
        add_line(frame, body, size=9.5, colour=WHITE, bullet=None,
                 align=PP_ALIGN.CENTER, spacing=1.06)

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
        size=11.5, bold=True, colour=HEADING_BLUE, bullet=None, first=True,
        align=PP_ALIGN.CENTER, spacing=1.04,
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
