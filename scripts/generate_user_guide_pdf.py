"""Render the Markdown user guide into a polished Chinese PDF."""

from __future__ import annotations

import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "USER_GUIDE.md"
SCREENSHOT = ROOT / "docs" / "images" / "workbench.png"
OUTPUT = ROOT / "output" / "pdf" / "SZU-Course-Help-User-Guide.pdf"

PAGE_WIDTH, PAGE_HEIGHT = A4
BRAND = colors.HexColor("#1D55D0")
INK = colors.HexColor("#172033")
MUTED = colors.HexColor("#647086")
LINE = colors.HexColor("#DDE3EC")
SURFACE = colors.HexColor("#F4F7FB")
WARNING = colors.HexColor("#A76A02")


def register_fonts() -> tuple[str, str]:
    regular_candidates = (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    )
    bold_candidates = (
        Path("C:/Windows/Fonts/msyhbd.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    )
    regular = next((path for path in regular_candidates if path.exists()), None)
    bold = next((path for path in bold_candidates if path.exists()), regular)
    if regular is None or bold is None:
        raise RuntimeError("未找到可用于生成中文 PDF 的字体")
    pdfmetrics.registerFont(TTFont("GuideSans", str(regular)))
    pdfmetrics.registerFont(TTFont("GuideSansBold", str(bold)))
    pdfmetrics.registerFontFamily(
        "GuideSans",
        normal="GuideSans",
        bold="GuideSansBold",
        italic="GuideSans",
        boldItalic="GuideSansBold",
    )
    return "GuideSans", "GuideSansBold"


def inline_markup(value: str, font_name: str) -> str:
    placeholders: list[str] = []

    def preserve_code(match: re.Match[str]) -> str:
        placeholders.append(html.escape(match.group(1)))
        return f"@@CODE{len(placeholders) - 1}@@"

    value = re.sub(r"`([^`]+)`", preserve_code, value)
    value = html.escape(value)
    value = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        r'<link href="\2" color="#1D55D0">\1</link>',
        value,
    )
    value = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", value)
    for index, code in enumerate(placeholders):
        value = value.replace(
            f"@@CODE{index}@@",
            f'<font name="{font_name}" color="#1D55D0">{code}</font>',
        )
    return value


def build_styles(font_name: str, bold_font: str) -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    return {
        "body": ParagraphStyle(
            "GuideBody",
            parent=sample["BodyText"],
            fontName=font_name,
            fontSize=10.2,
            leading=16.5,
            textColor=INK,
            wordWrap="CJK",
            spaceAfter=7,
        ),
        "h2": ParagraphStyle(
            "GuideH2",
            parent=sample["Heading2"],
            fontName=bold_font,
            fontSize=17,
            leading=24,
            textColor=INK,
            wordWrap="CJK",
            spaceBefore=16,
            spaceAfter=8,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "GuideH3",
            parent=sample["Heading3"],
            fontName=bold_font,
            fontSize=12.5,
            leading=19,
            textColor=BRAND,
            wordWrap="CJK",
            spaceBefore=11,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "bullet": ParagraphStyle(
            "GuideBullet",
            parent=sample["BodyText"],
            fontName=font_name,
            fontSize=10.2,
            leading=16,
            leftIndent=14,
            firstLineIndent=-10,
            textColor=INK,
            wordWrap="CJK",
            spaceAfter=4,
        ),
        "quote": ParagraphStyle(
            "GuideQuote",
            parent=sample["BodyText"],
            fontName=font_name,
            fontSize=9.8,
            leading=15.5,
            textColor=colors.HexColor("#714B00"),
            wordWrap="CJK",
        ),
        "code": ParagraphStyle(
            "GuideCode",
            parent=sample["Code"],
            fontName=font_name,
            fontSize=8.7,
            leading=13,
            textColor=colors.HexColor("#26324A"),
            wordWrap="CJK",
        ),
        "cover_title": ParagraphStyle(
            "CoverTitle",
            parent=sample["Title"],
            fontName=bold_font,
            fontSize=30,
            leading=39,
            alignment=TA_CENTER,
            textColor=INK,
        ),
        "cover_subtitle": ParagraphStyle(
            "CoverSubtitle",
            parent=sample["BodyText"],
            fontName=font_name,
            fontSize=12,
            leading=19,
            alignment=TA_CENTER,
            textColor=MUTED,
        ),
        "cover_meta": ParagraphStyle(
            "CoverMeta",
            parent=sample["BodyText"],
            fontName=font_name,
            fontSize=9.5,
            leading=15,
            alignment=TA_CENTER,
            textColor=MUTED,
        ),
    }


def draw_page(canvas, doc) -> None:
    canvas.saveState()
    if doc.page > 1:
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(18 * mm, PAGE_HEIGHT - 15 * mm, PAGE_WIDTH - 18 * mm, PAGE_HEIGHT - 15 * mm)
        canvas.setFont("GuideSans", 8.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, PAGE_HEIGHT - 11.5 * mm, "深大抢课助手 3.2 使用手册")
        canvas.drawRightString(PAGE_WIDTH - 18 * mm, 10 * mm, f"第 {doc.page} 页")
    canvas.restoreState()


def add_cover(story: list, styles: dict[str, ParagraphStyle]) -> None:
    story.append(Spacer(1, 14 * mm))
    story.append(Paragraph("深大抢课助手 3.2", styles["cover_title"]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("跨平台 Release 使用手册", styles["cover_subtitle"]))
    story.append(Paragraph("无需 Python · 完整解压 · 双击平台启动脚本", styles["cover_subtitle"]))
    story.append(Spacer(1, 10 * mm))

    image = Image(str(SCREENSHOT))
    image.drawWidth = 174 * mm
    image.drawHeight = image.drawWidth * 900 / 1440
    story.append(image)
    story.append(Spacer(1, 8 * mm))

    note = Table(
        [
            [
                Paragraph(
                    "本手册截图中的学号已脱敏。请勿分享真实学号、密码、Cookie、Card Key 或 PEM 密钥。",
                    styles["quote"],
                )
            ]
        ],
        colWidths=[166 * mm],
    )
    note.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF7E6")),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#E7C77E")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(note)
    story.append(Spacer(1, 7 * mm))
    story.append(Paragraph("版本 3.2.0 · MIT License · Weeye · Misakait", styles["cover_meta"]))
    story.append(Paragraph("github.com/Weeye-hua/SZU-Course-Help", styles["cover_meta"]))
    story.append(PageBreak())


def flush_paragraph(
    story: list,
    lines: list[str],
    styles: dict[str, ParagraphStyle],
    font_name: str,
) -> None:
    if not lines:
        return
    text = " ".join(line.strip() for line in lines).strip()
    if text:
        story.append(Paragraph(inline_markup(text, font_name), styles["body"]))
    lines.clear()


def markdown_story(
    markdown: str,
    styles: dict[str, ParagraphStyle],
    font_name: str,
) -> list:
    story: list = []
    paragraph_lines: list[str] = []
    code_lines: list[str] = []
    in_code = False
    started = False

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if line.startswith("# "):
            continue
        if not started:
            if line.startswith("## "):
                started = True
            else:
                continue

        if line.startswith("```"):
            flush_paragraph(story, paragraph_lines, styles, font_name)
            if in_code:
                code_text = "<br/>".join(html.escape(item or " ") for item in code_lines)
                box = Table(
                    [[Paragraph(code_text, styles["code"])]],
                    colWidths=[164 * mm],
                )
                box.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
                            ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                            ("LEFTPADDING", (0, 0), (-1, -1), 9),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                            ("TOPPADDING", (0, 0), (-1, -1), 7),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                        ]
                    )
                )
                story.extend([box, Spacer(1, 5)])
                code_lines.clear()
            in_code = not in_code
            continue

        if in_code:
            code_lines.append(line)
            continue

        if not line.strip():
            flush_paragraph(story, paragraph_lines, styles, font_name)
            continue

        if line.startswith("## "):
            flush_paragraph(story, paragraph_lines, styles, font_name)
            title = inline_markup(line[3:].strip(), font_name)
            story.append(
                KeepTogether(
                    [
                        Spacer(1, 4),
                        HRFlowable(width="100%", thickness=1.2, color=BRAND, spaceAfter=6),
                        Paragraph(title, styles["h2"]),
                    ]
                )
            )
            continue

        if line.startswith("### "):
            flush_paragraph(story, paragraph_lines, styles, font_name)
            story.append(Paragraph(inline_markup(line[4:].strip(), font_name), styles["h3"]))
            continue

        if line.startswith("> "):
            flush_paragraph(story, paragraph_lines, styles, font_name)
            quote = Table(
                [[Paragraph(inline_markup(line[2:].strip(), font_name), styles["quote"])]],
                colWidths=[164 * mm],
            )
            quote.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF7E6")),
                        ("LINEBEFORE", (0, 0), (0, -1), 3, WARNING),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                        ("TOPPADDING", (0, 0), (-1, -1), 7),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ]
                )
            )
            story.extend([quote, Spacer(1, 5)])
            continue

        bullet = re.match(r"^-\s+(.+)$", line)
        numbered = re.match(r"^(\d+)\.\s+(.+)$", line)
        if bullet or numbered:
            flush_paragraph(story, paragraph_lines, styles, font_name)
            prefix = "●" if bullet else f"{numbered.group(1)}."
            content = bullet.group(1) if bullet else numbered.group(2)
            story.append(
                Paragraph(
                    f"{prefix}&nbsp;&nbsp;{inline_markup(content, font_name)}",
                    styles["bullet"],
                )
            )
            continue

        if line == "---":
            flush_paragraph(story, paragraph_lines, styles, font_name)
            # The Markdown footer repeats metadata already present on the cover.
            # Omitting it keeps the PDF from producing a nearly empty final page.
            break

        paragraph_lines.append(line.removesuffix("  "))

    flush_paragraph(story, paragraph_lines, styles, font_name)
    return story


def main() -> None:
    font_name, bold_font = register_fonts()
    styles = build_styles(font_name, bold_font)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    markdown = SOURCE.read_text(encoding="utf-8")

    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=19 * mm,
        bottomMargin=17 * mm,
        title="深大抢课助手 3.2 使用手册",
        author="Weeye · Misakait",
        subject="SZU Course Help cross-platform release user guide",
    )
    story: list = []
    add_cover(story, styles)
    story.extend(markdown_story(markdown, styles, font_name))
    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    print(f"Generated {OUTPUT}")


if __name__ == "__main__":
    main()
