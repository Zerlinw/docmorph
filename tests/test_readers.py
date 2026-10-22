"""读取器的针对性测试：还原真实文档中的结构。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from doc_converter.model import (Attachment, CodeBlock, Document, FlowchartBlock,
                                 Heading, ImageBlock, ImageInline, Link,
                                 ListBlock, Paragraph, QuoteBlock, TableBlock,
                                 Text)
from doc_converter.readers.docx_reader import DocxReader
from doc_converter.readers.html_reader import HtmlReader
from doc_converter.readers.markdown_reader import MarkdownReader
from doc_converter.readers.pdf_reader import PdfReader


def _read(reader, path: Path) -> Document:
    doc = Document()
    reader.read(path, doc)
    return doc


def _text(inlines) -> str:
    parts = []
    for inline in inlines:
        if isinstance(inline, Text):
            parts.append(inline.text)
        elif isinstance(inline, Link):
            parts.append(_text(inline.children))
    return "".join(parts)


def _blocks(doc: Document, kind) -> list:
    return [block for block in doc.blocks if isinstance(block, kind)]


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

def test_docx_localized_styles_and_title(rich_docx_file):
    doc = _read(DocxReader(), rich_docx_file)
    headings = _blocks(doc, Heading)
    assert headings[0].level == 1
    assert _text(headings[0].children) == "Word 结构测试"
    assert doc.title == "Word 结构测试"
    # Title 存在时 Heading 1 下移为二级标题
    assert headings[1].level == 2
    assert _text(headings[1].children) == "编号列表"
    assert doc.metadata.get("author") == "测试作者"
    assert doc.metadata.get("subject") == "自动化测试"


def test_docx_numbering_and_bullets(rich_docx_file):
    doc = _read(DocxReader(), rich_docx_file)
    lists = _blocks(doc, ListBlock)
    assert lists, "应识别出列表"
    ordered = [block for block in lists if block.ordered]
    bullets = [block for block in lists if not block.ordered]
    assert ordered and ordered[0].items[0].level == 0
    assert any(item.level == 1 for item in ordered[0].items)
    assert bullets and _text(bullets[0].items[0].children) == "符号第一项"


def test_docx_content_control_and_revision(rich_docx_file):
    doc = _read(DocxReader(), rich_docx_file)
    text = "\n".join(_text(block.children) for block in doc.blocks
                     if isinstance(block, Paragraph))
    assert "内容控件中的文字" in text
    assert "修订前插入的文字" in text


def test_docx_text_box_not_flowchart(rich_docx_file):
    doc = _read(DocxReader(), rich_docx_file)
    assert not _blocks(doc, FlowchartBlock), "单个文本框不应被当作流程图"
    text = "\n".join(_text(block.children) for block in doc.blocks
                     if isinstance(block, Paragraph))
    assert "这是一个文本框" in text
    assert any("文本框" in warning for warning in doc.warnings)


def test_docx_merged_table(rich_docx_file):
    doc = _read(DocxReader(), rich_docx_file)
    tables = _blocks(doc, TableBlock)
    assert len(tables) == 1
    table = tables[0]
    assert table.header is True
    assert table.rows[0][0].colspan == 2
    assert table.rows[0][1].rowspan == 2
    assert _text(table.rows[0][0].children) == "合并表头"


def test_docx_image_hyperlink_attachment(rich_docx_file):
    doc = _read(DocxReader(), rich_docx_file)
    images = [block for block in doc.blocks
              if isinstance(block, ImageBlock)]
    images += [inline for block in doc.blocks if isinstance(block, Paragraph)
               for inline in block.children if isinstance(inline, ImageInline)]
    assert images, "应读取到图片"
    assert any(image.data for image in images)
    links = [inline for block in doc.blocks if isinstance(block, Paragraph)
             for inline in block.children if isinstance(inline, Link)]
    assert any(link.target.startswith("https://example.com") for link in links)
    attachments = _blocks(doc, Attachment)
    assert attachments and attachments[0].data == b"name,value\nsample,1\n"


def test_docx_code_block(rich_docx_file):
    doc = _read(DocxReader(), rich_docx_file)
    codes = _blocks(doc, CodeBlock)
    assert any("def hello()" in code.text for code in codes)


def test_docx_without_title_style_keeps_heading_levels(workdir, png_bytes):
    from docx import Document as WordDocument

    path = workdir / "plain.docx"
    word = WordDocument()
    word.add_heading("一级标题", level=1)
    word.add_heading("二级标题", level=2)
    word.save(str(path))
    doc = _read(DocxReader(), path)
    headings = _blocks(doc, Heading)
    assert [heading.level for heading in headings] == [1, 2]


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def test_html_nested_list_and_spans(rich_html_file):
    doc = _read(HtmlReader(), rich_html_file)
    lists = _blocks(doc, ListBlock)
    ordered = next(block for block in lists if block.ordered)
    assert ordered.items[1].blocks, "嵌套列表应挂在父项内部"
    nested = ordered.items[1].blocks[0]
    assert isinstance(nested, ListBlock) and not nested.ordered
    assert [_text(item.children) for item in nested.items] == ["子项 A", "子项 B"]
    tables = _blocks(doc, TableBlock)
    assert tables[0].caption == "数据表"
    assert tables[0].rows[0][1].colspan == 2


def test_html_loose_text_definition_and_figure(workdir):
    path = workdir / "loose.html"
    path.write_text(
        '<!DOCTYPE html><html><body><div>裸文本 <b>加粗</b></div>'
        '<dl><dt>术语</dt><dd>定义</dd></dl>'
        '<figure><img src="missing.png" alt="图" width="120">'
        '<figcaption>题注</figcaption></figure>'
        '<pre><code class="language-py">line1\nline2</code></pre>'
        '</body></html>', encoding="utf-8")
    doc = _read(HtmlReader(), path)
    text = "\n".join(_text(block.children) for block in doc.blocks
                     if isinstance(block, Paragraph))
    assert "裸文本" in text and "加粗" in text
    assert "术语" in text and "定义" in text
    figures = _blocks(doc, ImageBlock)
    assert figures and figures[0].alt == "图"
    assert figures[0].width_px == 120
    assert figures[0].caption == "题注"
    codes = _blocks(doc, CodeBlock)
    assert codes[0].text == "line1\nline2"
    assert codes[0].lang == "py"


def test_html_metadata(rich_html_file):
    doc = _read(HtmlReader(), rich_html_file)
    assert doc.title == "富文本示例"
    assert doc.metadata.get("author") == "测试作者"
    assert doc.metadata.get("lang") == "zh-CN"


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def test_markdown_nested_lists_and_title(rich_md_file):
    doc = _read(MarkdownReader(), rich_md_file)
    assert doc.title == "富文本示例"
    assert doc.metadata.get("author") == "测试作者"
    lists = _blocks(doc, ListBlock)
    ordered = next(block for block in lists if block.ordered)
    assert ordered.start == 1
    assert ordered.items[1].blocks, "嵌套列表应挂在父项内部"
    assert _text(ordered.items[2].children) == "第三步"


def test_markdown_mermaid_and_code(rich_md_file):
    doc = _read(MarkdownReader(), rich_md_file)
    flows = _blocks(doc, FlowchartBlock)
    assert flows and flows[0].nodes == ["开始", "处理", "结束"]
    assert flows[0].source.startswith("graph TD")
    codes = _blocks(doc, CodeBlock)
    assert any(code.lang == "python" for code in codes)


def test_markdown_inline_html_and_ordered_start(workdir):
    path = workdir / "inline.md"
    path.write_text(
        "文本 <u>下划线</u> 与 <sup>上标</sup>。\n\n"
        "5. 第五项\n6. 第六项\n", encoding="utf-8")
    doc = _read(MarkdownReader(), path)
    paragraph = next(block for block in doc.blocks if isinstance(block, Paragraph))
    styles = {inline.text.strip(): inline for inline in paragraph.children
              if isinstance(inline, Text) and inline.text.strip()}
    assert styles["下划线"].underline is True
    assert styles["上标"].superscript is True
    ordered = next(block for block in doc.blocks if isinstance(block, ListBlock))
    assert ordered.start == 5


def test_markdown_table_escaping(workdir):
    path = workdir / "table.md"
    path.write_text(
        "| 名称 | 说明 |\n| --- | --- |\n| A | 含 \\| 管道 |\n", encoding="utf-8")
    doc = _read(MarkdownReader(), path)
    table = _blocks(doc, TableBlock)[0]
    assert _text(table.rows[1][1].children) == "含 | 管道"


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def test_pdf_paragraphs_headings_tables(rich_pdf_file):
    doc = _read(PdfReader(), rich_pdf_file)
    assert _blocks(doc, Heading), "应识别出标题"
    assert _blocks(doc, TableBlock), "应重建出表格"
    assert _blocks(doc, ListBlock), "应识别出列表"
    assert any("启发式" in warning for warning in doc.warnings)


def test_pdf_metadata_and_page_count(rich_pdf_file):
    doc = _read(PdfReader(), rich_pdf_file)
    assert doc.metadata.get("creator") or doc.metadata.get("producer")
    assert len(doc.blocks) > 5


def test_pdf_header_footer_removal(workdir):
    """构造多页 PDF，页眉/页脚为重复文字，应被剔除。"""
    html = workdir / "header.html"
    body = "".join(f"<h2>章节 {index}</h2>" +
                   "".join(f"<p>正文段落 {index}-{line}，用于撑高页面。</p>"
                           for line in range(18))
                   for index in range(1, 4))
    html.write_text(
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>页眉页脚</title></head><body>"
        f"{body}</body></html>", encoding="utf-8")
    from doc_converter import convert

    pdf = workdir / "header.pdf"
    convert(html, target="pdf", output=pdf)
    doc = _read(PdfReader(), pdf)
    text = "\n".join(_text(block.children) for block in doc.blocks
                     if isinstance(block, Paragraph))
    assert "正文段落" in text
