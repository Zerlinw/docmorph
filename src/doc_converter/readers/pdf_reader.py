"""doc_converter.readers.pdf_reader —— PDF 读取器。

PDF 是版式驱动格式，没有语义结构，本读取器采用启发式重建：
  * 依据字符字号聚类推断标题层级；
  * 依据行距与边界框聚合段落；
  * ``page.find_tables()`` 还原表格（无线框表格可能失败，降级为文本）；
  * 图片按 bbox 裁剪导出为 PNG；
  * 注释中的 URI 还原超链接；
  * 通过 pypdf 提取 EmbeddedFile 附件。

所有启发式判断都会向 ``Document.warnings`` 记录降级说明。
"""

from __future__ import annotations

import io
import statistics
from pathlib import Path

import pdfplumber

from ..model import (Attachment, Document, Heading, ImageBlock, Link,
                     Paragraph, TableBlock, TableCell, Text)
from .base import BaseReader, register_reader

_HEADING_RATIO_L1 = 1.6
_HEADING_RATIO_L2 = 1.25


@register_reader
class PdfReader(BaseReader):
    extensions = ("pdf",)

    def read(self, path: Path, doc: Document) -> Document:
        try:
            pdf = pdfplumber.open(str(path))
        except Exception as exc:  # noqa: BLE001
            raise self._fail(path, exc)
        try:
            with pdf:
                doc.warn("PDF 为版式格式，标题层级/段落结构为启发式重建，可能与原文有差异")
                for pno, page in enumerate(pdf.pages, start=1):
                    self._read_page(page, pno, doc)
        except Exception as exc:  # noqa: BLE001
            raise self._fail(path, exc)

        self._extract_attachments(path, doc)
        if doc.is_empty():
            from ..errors import EmptyDocumentError
            raise EmptyDocumentError(str(path))
        return doc

    # ------------------------------------------------------------------ 页面
    def _read_page(self, page, pno: int, doc: Document) -> None:
        try:
            lines = page.extract_text_lines() or []
        except Exception:  # noqa: BLE001
            lines = []
        try:
            tables = page.find_tables()
        except Exception:  # noqa: BLE001
            tables = []
        if not tables:
            # 无框线表格：改用文本对齐策略重试（启发式，可能不精确）
            try:
                tables = page.find_tables({
                    "vertical_strategy": "text",
                    "horizontal_strategy": "text",
                })
                if tables:
                    doc.warn(f"PDF 第 {pno} 页: 表格无框线，已按文本对齐启发式重建")
            except Exception:  # noqa: BLE001
                tables = []

        sizes = [c.get("size", 0) for c in page.chars if c.get("size")]
        body_size = statistics.median(sizes) if sizes else 0.0

        table_bboxes = [tuple(t.bbox) for t in tables]

        def inside_table(bbox) -> bool:
            x0, top, x1, bottom = bbox
            for tx0, ttop, tx1, tbottom in table_bboxes:
                if x0 >= tx0 - 2 and x1 <= tx1 + 2 and top >= ttop - 2 and bottom <= tbottom + 2:
                    return True
            return False

        # ---- 图片（先收集，后按纵向位置排序插入） ----
        items: list[tuple[float, object]] = []
        seen_imgs: set[tuple] = set()

        for img in page.images:
            bbox = (img.get("x0"), img.get("top"), img.get("x1"), img.get("bottom"))
            if None in bbox:
                continue
            key = tuple(round(v, 1) for v in bbox)
            if key in seen_imgs:
                continue
            seen_imgs.add(key)
            data = self._crop_image(page, bbox)
            if data:
                items.append((bbox[1], ImageBlock(src=f"page{pno}-image",
                                                  alt=f"第 {pno} 页图片",
                                                  data=data, mime="image/png")))

        # ---- 表格 ----
        for t in tables:
            try:
                raw_rows = t.extract()
            except Exception:  # noqa: BLE001
                doc.warn(f"PDF 第 {pno} 页: 一个表格解析失败，已降级跳过")
                continue
            rows = [[TableCell(children=[Text(text=(cell or "").strip())])
                     for cell in row] for row in raw_rows if row]
            if rows:
                items.append((t.bbox[1], TableBlock(rows=rows, header=True)))
                doc.warn(f"PDF 第 {pno} 页: 表格由文本位置重建，单元格边界可能与原文存在偏差")

        # ---- 文本行 -> 标题/段落 ----
        para_lines: list[str] = []
        para_top = 0.0

        def flush_para():
            nonlocal para_lines, para_top
            if para_lines:
                text = _join_lines(para_lines)
                if text.strip():
                    items.append((para_top, Paragraph(children=[Text(text=text.strip())])))
                para_lines = []

        for line in lines:
            bbox = (line.get("x0"), line.get("top"), line.get("x1"), line.get("bottom"))
            if bbox[0] is not None and inside_table(bbox):
                continue
            text = (line.get("text") or "").strip()
            if not text:
                flush_para()
                continue
            chars = line.get("chars") or []
            max_size = max((c.get("size", 0) for c in chars), default=0.0)
            level = _heading_level(max_size, body_size)
            if level:
                flush_para()
                items.append((bbox[1] if bbox[1] is not None else 0.0,
                              Heading(level=level, children=[Text(text=text)])))
            else:
                if not para_lines:
                    para_top = bbox[1] if bbox[1] is not None else 0.0
                para_lines.append(text)
        flush_para()

        # ---- 超链接注释（位置未知，统一在页面内容后列出） ----
        try:
            annots = page.annots or []
        except Exception:  # noqa: BLE001
            annots = []
        link_uris = [a.get("uri") for a in annots if a.get("uri")]

        for top, block in sorted(items, key=lambda it: it[0]):
            doc.blocks.append(block)

        if link_uris:
            doc.warn("PDF: 检测到 %d 个链接注释，锚定文本位置未知，已在页面末尾列出"
                     % len(link_uris))
            for uri in link_uris:
                doc.blocks.append(Paragraph(children=[
                    Link(target=uri, children=[Text(text=f"[链接] {uri}")])]))

    # ------------------------------------------------------------------ 图片
    @staticmethod
    def _crop_image(page, bbox) -> bytes | None:
        try:
            x0, top, x1, bottom = bbox
            if x1 - x0 < 2 or bottom - top < 2:
                return None
            cropped = page.crop((x0, top, x1, bottom))
            pil_image = cropped.to_image(resolution=150).original
            buf = io.BytesIO()
            pil_image.convert("RGB").save(buf, format="PNG")
            return buf.getvalue()
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ 附件
    @staticmethod
    def _extract_attachments(path: Path, doc: Document) -> None:
        try:
            from pypdf import PdfReader
        except ImportError:  # pragma: no cover
            doc.warn("PDF: 未安装 pypdf，跳过嵌入附件提取")
            return
        try:
            reader = PdfReader(str(path))
        except Exception as exc:  # noqa: BLE001
            doc.warn(f"PDF: 读取嵌入附件失败: {exc}")
            return
        attachments = getattr(reader, "attachments", None) or {}
        for name, contents in attachments.items():
            for idx, data in enumerate(contents):
                doc.blocks.append(Attachment(
                    name=name or f"attachment-{idx}",
                    data=bytes(data),
                    mime="application/octet-stream",
                ))
        if attachments:
            doc.warn("PDF: 已提取 %d 个嵌入附件" %
                     sum(len(v) for v in attachments.values()))


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _heading_level(size: float, body_size: float) -> int | None:
    if not size or not body_size:
        return None
    ratio = size / body_size
    if ratio >= _HEADING_RATIO_L1:
        return 1
    if ratio >= _HEADING_RATIO_L2:
        return 2
    return None


_CJK_RANGES = (
    ("\u4e00", "\u9fff"), ("\u3000", "\u303f"), ("\uff00", "\uffef"),
    ("\u3040", "\u30ff"),
)


def _is_cjk(ch: str) -> bool:
    return any(lo <= ch <= hi for lo, hi in _CJK_RANGES)


def _join_lines(lines: list[str]) -> str:
    """中日韩文本行直接相接，西文行间补空格。"""
    parts: list[str] = []
    for i, line in enumerate(lines):
        if i and parts and not (_is_cjk(parts[-1][-1]) and _is_cjk(line[0])):
            parts.append(" ")
        parts.append(line)
    return "".join(parts)
