"""12 种转换组合的端到端验证。"""

from __future__ import annotations

import zipfile

import pytest

from doc_converter import SUPPORTED_FORMATS, convert
from doc_converter.errors import ConversionError


def _read_md(result) -> str:
    return result.output.read_text(encoding="utf-8")


def _read_html(result) -> str:
    return result.output.read_text(encoding="utf-8")


def _pdf_text(path) -> str:
    import pdfplumber

    with pdfplumber.open(str(path)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def assert_markdown(result, source_format: str) -> None:
    text = _read_md(result)
    assert result.output.exists() and result.output.suffix == ".md"
    assert len(text) > 20
    expected = "Word 结构测试" if source_format == "docx" else "富文本示例"
    if source_format != "pdf":
        assert expected in text
        assert "|" in text                     # 表格
        assert "```" in text                   # 代码块或 Mermaid
        assert "![" in text or result.assets   # 图片
    else:
        assert len(text.splitlines()) >= 5


def assert_html(result, source_format: str) -> None:
    text = _read_html(result)
    assert "<!DOCTYPE html>" in text
    assert "<body>" in text
    assert "doc-meta" not in text              # 不再注入说明横幅
    assert "<table>" in text
    if source_format != "pdf":
        expected = "Word 结构测试" if source_format == "docx" else "富文本示例"
        assert expected in text
        assert "<h1" in text
        assert "<img" in text
        assert "<ul" in text or "<ol" in text


def assert_docx(result) -> None:
    assert result.output.exists() and result.output.suffix == ".docx"
    with zipfile.ZipFile(result.output) as archive:
        names = archive.namelist()
        document = archive.read("word/document.xml").decode("utf-8")
        assert "word/document.xml" in names
        if result.source_format in ("html", "md"):
            assert any(name.startswith("word/media/") for name in names)
    from docx import Document

    word = Document(str(result.output))
    assert len(word.paragraphs) >= 3


def assert_pdf(result) -> None:
    assert result.output.exists()
    assert result.output.stat().st_size > 1500
    with open(result.output, "rb") as handle:
        assert handle.read(5) == b"%PDF-"
    assert result.engine in ("chromium", "weasyprint", "xhtml2pdf")
    text = _pdf_text(result.output)
    assert "富文本示例" in text or len(text) > 40


# ---------------------------------------------------------------------------
# Markdown 源
# ---------------------------------------------------------------------------

def test_md_to_html(rich_md_file, tmp_path):
    result = convert(rich_md_file, target="html", output=tmp_path)
    assert_html(result, "md")


def test_md_to_docx(rich_md_file, tmp_path):
    result = convert(rich_md_file, target="docx", output=tmp_path)
    assert_docx(result)


def test_md_to_pdf(rich_md_file, tmp_path):
    result = convert(rich_md_file, target="pdf", output=tmp_path)
    assert_pdf(result)


# ---------------------------------------------------------------------------
# HTML 源
# ---------------------------------------------------------------------------

def test_html_to_md(rich_html_file, tmp_path):
    result = convert(rich_html_file, target="md", output=tmp_path)
    assert_markdown(result, "html")
    text = _read_md(result)
    assert "**加粗**" in text
    assert "子项 A" in text
    assert "| 名称 |" in text


def test_html_to_docx(rich_html_file, tmp_path):
    result = convert(rich_html_file, target="docx", output=tmp_path)
    assert_docx(result)


def test_html_to_pdf(rich_html_file, tmp_path):
    result = convert(rich_html_file, target="pdf", output=tmp_path)
    assert_pdf(result)


# ---------------------------------------------------------------------------
# DOCX 源
# ---------------------------------------------------------------------------

def test_docx_to_md(rich_docx_file, tmp_path):
    result = convert(rich_docx_file, target="md", output=tmp_path)
    assert_markdown(result, "docx")
    text = _read_md(result)
    assert "# Word 结构测试" in text
    assert "## 编号列表" in text          # Title 视为一级标题，Heading1 降为二级
    assert "编号第一项" in text
    assert "项目符号" in text and "符号第一项" in text
    assert "内容控件中的文字" in text
    assert "插入的文字" in text
    assert "合并表头" in text
    assert result.assets or "![" in text


def test_docx_to_html(rich_docx_file, tmp_path):
    result = convert(rich_docx_file, target="html", output=tmp_path)
    assert_html(result, "docx")
    text = _read_html(result)
    assert 'colspan="2"' in text
    assert 'rowspan="2"' in text
    assert "<code" in text or "Consolas" in text


def test_docx_to_pdf(rich_docx_file, tmp_path):
    result = convert(rich_docx_file, target="pdf", output=tmp_path)
    assert_pdf(result)


# ---------------------------------------------------------------------------
# PDF 源（启发式重建）
# ---------------------------------------------------------------------------

def test_pdf_to_md(rich_pdf_file, tmp_path):
    result = convert(rich_pdf_file, target="md", output=tmp_path)
    assert_markdown(result, "pdf")
    assert result.warnings, "PDF 转换应给出启发式重建告警"


def test_pdf_to_html(rich_pdf_file, tmp_path):
    result = convert(rich_pdf_file, target="html", output=tmp_path)
    assert result.output.exists()
    text = _read_html(result)
    assert "<body>" in text
    assert "富文本示例" in text
    assert "<table>" in text


def test_pdf_to_docx(rich_pdf_file, tmp_path):
    result = convert(rich_pdf_file, target="docx", output=tmp_path)
    assert result.output.exists()
    assert result.output.stat().st_size > 2000


# ---------------------------------------------------------------------------
# 错误处理与输出路径
# ---------------------------------------------------------------------------

def test_same_format_raises(rich_md_file):
    with pytest.raises(ConversionError) as info:
        convert(rich_md_file, target="md")
    assert info.value.code == "same_format"


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConversionError) as info:
        convert(tmp_path / "missing.md", target="html")
    assert info.value.code == "file_not_found"


def test_unsupported_format_raises(rich_md_file):
    with pytest.raises(ConversionError) as info:
        convert(rich_md_file, target="rtf")
    assert info.value.code == "unsupported_format"


def test_directory_input_raises(tmp_path):
    with pytest.raises(ConversionError):
        convert(tmp_path, target="html")


def test_output_file_path(rich_md_file, tmp_path):
    destination = tmp_path / "custom-name.html"
    result = convert(rich_md_file, target="html", output=destination)
    assert result.output == destination
    assert destination.exists()


def test_output_directory_created(rich_md_file, tmp_path):
    directory = tmp_path / "nested" / "output"
    result = convert(rich_md_file, target="html", output=directory)
    assert result.output.parent == directory
    assert directory.is_dir()


def test_default_output_next_to_source(rich_md_file, tmp_path):
    import shutil

    source = tmp_path / "copy.md"
    shutil.copy(rich_md_file, source)
    (tmp_path / "diagram.png").write_bytes(
        (rich_md_file.parent / "diagram.png").read_bytes())
    result = convert(source, target="html")
    assert result.output == source.with_suffix(".html")
    assert result.output.exists()


def test_asset_mode_embed_falls_back_for_html(rich_md_file, tmp_path):
    result = convert(rich_md_file, target="html", output=tmp_path,
                     asset_mode="embed")
    assert result.output.exists()
    assert any("embed" in warning for warning in result.warnings)
    assert (tmp_path / "rich_assets").is_dir()


def test_supported_formats():
    assert set(SUPPORTED_FORMATS) == {"pdf", "docx", "html", "md"}
    assert len(SUPPORTED_FORMATS) ** 2 - len(SUPPORTED_FORMATS) == 12
