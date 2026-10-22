"""doc_converter.readers.pdf_reader —— PDF 读取器。

PDF 是版式驱动格式，没有语义结构，本读取器采用多轮启发式重建：

  * 依据字号聚类 + 粗体比例推断标题层级；
  * 依据行距、缩进与句末标点聚合段落；
  * 识别项目符号 / 编号行，重建列表（含层级与起始编号）；
  * ``find_tables()`` 还原表格，并用"多行多列 + 非空比例"过滤误报；
  * 图片优先提取原始流，失败时按 bbox 裁剪导出为 PNG；
  * 去除跨页重复的页眉 / 页脚 / 页码；
  * 注释中的 URI 还原超链接；
  * 通过 pypdf 提取文档属性与 EmbeddedFile 附件。

所有启发式判断都会向 ``Document.warnings`` 记录降级说明。
"""

from __future__ import annotations

import io
import re
import statistics
from pathlib import Path

import pdfplumber

from ..model import (Attachment, CodeBlock, Document, Heading, ImageBlock,
                     Link, ListBlock, ListItem, Paragraph, TableBlock,
                     TableCell, Text)
from .base import BaseReader, register_reader

_HEADING_SIZE_RATIO = 1.12
_HEADING_BOLD_RATIO = 0.6
_MAX_HEADING_LEVEL = 4

_BULLET_RE = re.compile(r"^\s*(?:[•●○◦◆◇■□▪▫‣⁃∙·]|[-–—*])\s+")
_ORDERED_RE = re.compile(
    r"^\s*(?:\(?(\d{1,3})[.)、）]|([一二三四五六七八九十]{1,3})[、.)）]|(\d{1,3})\s+)")
_CJK_RANGES = (("\u4e00", "\u9fff"), ("\u3000", "\u303f"), ("\uff00", "\uffef"),
               ("\u3040", "\u30ff"), ("\uac00", "\ud7af"))
_PAGE_NUMBER_RE = re.compile(
    r"^(?:[-–—\s]*\d{1,4}[-–—\s]*|第\s*\d{1,4}\s*页(?:\s*[/／]\s*\d{1,4})?"
    r"|page\s+\d{1,4}(?:\s+of\s+\d{1,4})?|-\s*\d+\s*-)$", re.IGNORECASE)


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
                pages = [self._page_lines(page) for page in pdf.pages]
                heights = [float(page.height or 0) for page in pdf.pages]
                skipped = _repeated_marginal_text(pages, heights)
                body_size = _body_font_size(pages)
                heading_map = _heading_size_map(pages, body_size)
                for index, page in enumerate(pdf.pages):
                    self._read_page(page, index + 1, pages[index], doc,
                                    body_size, heading_map, skipped)
        except Exception as exc:  # noqa: BLE001
            raise self._fail(path, exc)

        self._extract_attachments(path, doc)
        self._extract_metadata(path, doc)
        if doc.title is None:
            first_h1 = next((b for b in doc.blocks
                             if isinstance(b, Heading) and b.level == 1), None)
            if first_h1 is not None:
                doc.title = _inlines_text(first_h1.children).strip() or None
            elif doc.metadata.get("title"):
                doc.title = doc.metadata["title"].strip() or None
        if doc.is_empty():
            from ..errors import EmptyDocumentError
            raise EmptyDocumentError(str(path))
        return doc

    # ------------------------------------------------------------------ 行提取
    @staticmethod
    def _page_lines(page) -> list[dict]:
        try:
            raw_lines = page.extract_text_lines() or []
        except Exception:  # noqa: BLE001
            raw_lines = []
        lines: list[dict] = []
        for raw in raw_lines:
            text = (raw.get("text") or "").strip()
            if not text:
                continue
            chars = raw.get("chars") or []
            sizes = [float(c.get("size") or 0) for c in chars if c.get("size")]
            size = max(sizes) if sizes else 0.0
            bold = 0
            mono = 0
            for char in chars:
                font = (char.get("fontname") or "").lower()
                if "bold" in font or "black" in font or "heavy" in font \
                        or "黑体" in font or "semibold" in font:
                    bold += 1
                if "mono" in font or "courier" in font or "consolas" in font \
                        or "code" in font:
                    mono += 1
            bold_ratio = bold / len(chars) if chars else 0.0
            mono_ratio = mono / len(chars) if chars else 0.0
            bbox = (raw.get("x0"), raw.get("top"), raw.get("x1"), raw.get("bottom"))
            if None in bbox:
                continue
            lines.append(dict(text=text, size=size, bold=bold_ratio,
                              mono=mono_ratio, bbox=bbox))
        lines.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
        return lines

    # ------------------------------------------------------------------ 页面
    def _read_page(self, page, pno: int, lines: list[dict], doc: Document,
                   body_size: float, heading_map: dict[float, int],
                   skipped: set[str]) -> None:
        tables = self._find_tables(page, pno, doc)
        table_boxes = [bbox for bbox, _block in tables]

        def inside_table(bbox) -> bool:
            x0, top, x1, bottom = bbox
            cx, cy = (x0 + x1) / 2, (top + bottom) / 2
            for tx0, ttop, tx1, tbottom in table_boxes:
                if tx0 - 2 <= cx <= tx1 + 2 and ttop - 2 <= cy <= tbottom + 2:
                    return True
            return False

        items: list[tuple[float, object]] = []

        # ---- 图片 ----
        seen_images: set[tuple] = set()
        for img in page.images:
            bbox = (img.get("x0"), img.get("top"), img.get("x1"), img.get("bottom"))
            if None in bbox:
                continue
            if bbox[2] - bbox[0] < 4 or bbox[3] - bbox[1] < 4:
                continue
            if inside_table(bbox):
                continue
            key = tuple(round(float(v), 1) for v in bbox)
            if key in seen_images:
                continue
            seen_images.add(key)
            data = self._image_bytes(page, img, bbox)
            if data:
                items.append((bbox[1], ImageBlock(src=f"page{pno}-image",
                                                  alt=f"第 {pno} 页图片",
                                                  data=data, mime="image/png")))

        # ---- 表格 ----
        for bbox, table_block in tables:
            items.append((bbox[1], table_block))

        # ---- 正文 ----
        page_lines = [line for line in lines
                      if line["text"] not in skipped
                      and not _PAGE_NUMBER_RE.match(line["text"].strip())
                      and not inside_table(line["bbox"])]
        items.extend(self._blocks_from_lines(page_lines, pno, doc, body_size,
                                             heading_map))

        for top, block in sorted(items, key=lambda item: item[0]):
            doc.blocks.append(block)

        # ---- 链接注释 ----
        try:
            annots = page.annots or []
        except Exception:  # noqa: BLE001
            annots = []
        link_uris = [a.get("uri") for a in annots if a.get("uri")]
        if link_uris:
            doc.warn("PDF: 检测到 %d 个链接注释，锚定文本位置未知，已在页面末尾列出"
                     % len(link_uris))
            for uri in link_uris:
                doc.blocks.append(Paragraph(children=[
                    Link(target=uri, children=[Text(text=f"[链接] {uri}")])]))

    # ------------------------------------------------------------------ 表格
    def _find_tables(self, page, pno: int, doc: Document):
        """依次尝试：框线表格 -> 文本对齐表格 -> 自定义列对齐检测。"""
        results: list[tuple[tuple, TableBlock]] = []
        try:
            found = page.find_tables()
        except Exception:  # noqa: BLE001
            found = []
        for table in found:
            if not _table_bbox_sane(table.bbox, page):
                continue
            block = self._table_block(table, pno, doc)
            if block is not None:
                results.append((tuple(table.bbox), block))
        if results:
            return results

        try:
            found = page.find_tables({
                "vertical_strategy": "text",
                "horizontal_strategy": "text",
            })
        except Exception:  # noqa: BLE001
            found = []
        # 文本对齐策略容易把整页正文误判为一张大表；只有"紧凑"的结果才采信
        for table in found:
            if not _table_bbox_sane(table.bbox, page):
                continue
            if not _compact_table_candidate(table.bbox, page, len(table.rows)):
                continue
            block = self._table_block(table, pno, doc)
            if block is not None:
                results.append((tuple(table.bbox), block))
        if results:
            doc.warn(f"PDF 第 {pno} 页: 表格无框线，已按文本对齐启发式重建")
            return results

        for bbox, block in _layout_tables(page):
            results.append((bbox, block))
        if results:
            doc.warn(f"PDF 第 {pno} 页: 表格无框线，已按列对齐启发式重建")
        return results

    def _table_block(self, table, pno: int, doc: Document) -> TableBlock | None:
        try:
            raw_rows = table.extract()
        except Exception:  # noqa: BLE001
            doc.warn(f"PDF 第 {pno} 页: 一个表格解析失败，已降级跳过")
            return None
        rows = [[TableCell(children=[Text(text=(cell or "").strip())])
                 for cell in row] for row in raw_rows if row]
        rows = [row for row in rows if any(_cell_text(cell) for cell in row)]
        if not rows:
            return None
        # 表头行必须有多列有效内容，否则多半是把整页文字识别成了表格
        first_row_non_empty = sum(1 for cell in rows[0] if _cell_text(cell))
        if len(rows[0]) < 2 or first_row_non_empty < 2:
            return None
        if not _table_is_meaningful(rows):
            return None
        header = _row_looks_like_header(rows[0])
        doc.warn(f"PDF 第 {pno} 页: 表格由文本位置重建，单元格边界可能与原文存在偏差")
        return TableBlock(rows=rows, header=header)

    # ------------------------------------------------------------------ 段落
    def _blocks_from_lines(self, lines: list[dict], pno: int, doc: Document,
                           body_size: float,
                           heading_map: dict[float, int]) -> list[tuple[float, object]]:
        items: list[tuple[float, object]] = []
        if not lines:
            return items
        heights = [line["bbox"][3] - line["bbox"][1] for line in lines]
        median_height = statistics.median(heights) if heights else 12.0
        page_left = min(line["bbox"][0] for line in lines)
        indent_step = max(10.0, (body_size or 10.0) * 1.6)

        paragraph: list[dict] = []
        paragraph_top: float | None = None
        list_stack: list[tuple[int, ListBlock]] = []
        list_top: float | None = None
        code_lines: list[str] = []
        code_top: float | None = None

        def flush_code() -> None:
            nonlocal code_lines, code_top
            if code_lines:
                text = "\n".join(code_lines).rstrip()
                if text.strip():
                    items.append((code_top or 0.0, CodeBlock(text=text)))
            code_lines = []
            code_top = None

        def flush_paragraph() -> None:
            nonlocal paragraph
            if not paragraph:
                return
            text = _join_lines([line["text"] for line in paragraph])
            paragraph = []
            if text.strip():
                items.append((paragraph_top or 0.0,
                              Paragraph(children=[Text(text=text.strip())])))

        def flush_list() -> None:
            nonlocal list_stack, list_top
            if list_stack:
                items.append((list_top or 0.0, list_stack[0][1]))
            list_stack = []
            list_top = None

        def flush_all() -> None:
            flush_paragraph()
            flush_code()
            flush_list()

        def get_list_block(level: int, ordered: bool, start: int,
                           top: float) -> ListBlock:
            nonlocal list_stack, list_top
            while list_stack and level < list_stack[-1][0]:
                list_stack.pop()
            if list_stack and level == list_stack[-1][0]:
                if list_stack[-1][1].ordered == ordered:
                    return list_stack[-1][1]
                # 同级但类型不同：顶层列表必须先落盘，不能丢弃
                if len(list_stack) == 1:
                    items.append((list_top or top, list_stack[0][1]))
                    list_stack = []
                    list_top = None
                else:
                    list_stack.pop()
            if list_stack and list_stack[-1][1].items:
                block = ListBlock(ordered=ordered, start=start)
                list_stack[-1][1].items[-1].blocks.append(block)
                list_stack.append((level, block))
                return block
            block = ListBlock(ordered=ordered, start=start)
            if list_top is None:
                list_top = top
            list_stack.append((level, block))
            return block

        for line in lines:
            text = line["text"]
            size = line["size"]
            bbox = line["bbox"]
            level = _heading_level_for(size, body_size, heading_map)
            heading = False
            if level is not None and len(text) <= 120:
                heading = True
            elif (line["bold"] >= _HEADING_BOLD_RATIO and body_size > 0
                  and size >= body_size * 1.02 and len(text) <= 40
                  and not re.search(r"[。！？.!?:：;；]$", text)):
                heading = True
                level = _MAX_HEADING_LEVEL
            if heading:
                flush_all()
                items.append((bbox[1], Heading(level=level or 1,
                                               children=[Text(text=text)])))
                continue

            # 等宽字体连续行 -> 代码块
            if line.get("mono", 0.0) >= 0.6 and len(text) <= 400:
                flush_paragraph()
                flush_list()
                if code_top is None:
                    code_top = bbox[1]
                code_lines.append(text)
                continue
            flush_code()

            list_info = _match_list_item(text)
            if list_info is not None:
                flush_paragraph()
                ordered, first_number, marker_length = list_info
                item_text = text[marker_length:].strip()
                indent = max(0.0, bbox[0] - page_left)
                item_level = int(min(6, round(indent / indent_step)))
                block = get_list_block(item_level, ordered,
                                       first_number or 1, bbox[1])
                block.items.append(ListItem(children=[Text(text=item_text)]))
                continue

            # 列表项的续行（未以标记开头、行距较小）
            if list_stack and list_stack[-1][1].items:
                gap = bbox[1] - _prev_bottom(lines, line)
                if gap <= median_height * 0.9:
                    last = list_stack[-1][1].items[-1]
                    if last.children and isinstance(last.children[-1], Text):
                        last.children[-1].text = _join_lines(
                            [last.children[-1].text, text])
                    else:
                        last.children.append(Text(text=text))
                    continue
            flush_list()

            # 段落切分：行距过大，或首行缩进且上一行以句末标点结束
            new_paragraph = False
            if paragraph:
                prev = paragraph[-1]
                gap = bbox[1] - prev["bbox"][3]
                if gap > median_height * 1.55:
                    new_paragraph = True
                elif (bbox[0] - paragraph[0]["bbox"][0] > indent_step * 0.6
                      and re.search(r"[。！？.!?:：;；]$", prev["text"])):
                    new_paragraph = True
                elif (paragraph[0]["bbox"][0] - bbox[0] > indent_step * 0.8
                      and len(paragraph) >= 2):
                    new_paragraph = True
            if new_paragraph:
                flush_paragraph()
            if not paragraph:
                paragraph_top = bbox[1]
            paragraph.append(line)

        flush_paragraph()
        flush_code()
        flush_list()
        return items

    # ------------------------------------------------------------------ 图片
    @staticmethod
    def _image_bytes(page, image: dict, bbox) -> bytes | None:
        stream = image.get("stream")
        if stream is not None:
            try:
                data = stream.get_data()
                if _is_viewable_image(data):
                    return _to_png(data)
            except Exception:  # noqa: BLE001
                pass
        try:
            x0, top, x1, bottom = bbox
            cropped = page.crop((x0, top, x1, bottom))
            pil_image = cropped.to_image(resolution=150).original
            buffer = io.BytesIO()
            pil_image.convert("RGB").save(buffer, format="PNG")
            return buffer.getvalue()
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ 附件/元数据
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

    @staticmethod
    def _extract_metadata(path: Path, doc: Document) -> None:
        try:
            from pypdf import PdfReader
        except ImportError:  # pragma: no cover
            return
        try:
            info = PdfReader(str(path)).metadata or {}
        except Exception:  # noqa: BLE001
            return
        mapping = {"/Title": "title", "/Author": "author",
                   "/Subject": "subject", "/Keywords": "keywords",
                   "/Creator": "creator", "/Producer": "producer"}
        for key, name in mapping.items():
            try:
                value = info.get(key)
            except Exception:  # noqa: BLE001
                continue
            if value:
                text = str(value).strip()
                if text:
                    doc.metadata[name] = text


# ---------------------------------------------------------------------------
# 启发式工具
# ---------------------------------------------------------------------------

def _body_font_size(pages: list[list[dict]]) -> float:
    sizes: list[float] = []
    for lines in pages:
        for line in lines:
            if line["size"]:
                sizes.append(round(line["size"], 1))
    if not sizes:
        return 0.0
    try:
        return float(statistics.median(sizes))
    except statistics.StatisticsError:  # pragma: no cover
        return 0.0


def _heading_size_map(pages: list[list[dict]], body_size: float) -> dict[float, int]:
    if not body_size:
        return {}
    raw_sizes = set()
    for lines in pages:
        for line in lines:
            if line["size"] >= body_size * _HEADING_SIZE_RATIO:
                raw_sizes.add(round(line["size"], 1))
    if not raw_sizes:
        return {}
    clustered: list[float] = []
    for size in sorted(raw_sizes, reverse=True):
        if not clustered or abs(size - clustered[-1]) > 1.0:
            clustered.append(size)
    return {size: min(index + 1, _MAX_HEADING_LEVEL)
            for index, size in enumerate(clustered)}


def _heading_level_for(size: float, body_size: float,
                       heading_map: dict[float, int]) -> int | None:
    if not size or not body_size:
        return None
    rounded = round(size, 1)
    if rounded in heading_map:
        return heading_map[rounded]
    for candidate, level in heading_map.items():
        if abs(candidate - rounded) <= 0.6:
            return level
    if size >= body_size * _HEADING_SIZE_RATIO:
        return 1
    return None


def _match_list_item(text: str) -> tuple[bool, int, int] | None:
    match = _BULLET_RE.match(text)
    if match:
        return False, 1, match.end()
    match = _ORDERED_RE.match(text)
    if match:
        first = match.group(1) or match.group(3)
        try:
            number = int(first) if first else 1
        except ValueError:
            number = 1
        return True, max(1, number), match.end()
    return None


def _prev_bottom(lines: list[dict], current: dict) -> float:
    try:
        index = lines.index(current)
    except ValueError:
        return current["bbox"][1]
    if index == 0:
        return current["bbox"][1]
    return lines[index - 1]["bbox"][3]


def _repeated_marginal_text(pages: list[list[dict]],
                            heights: list[float]) -> set[str]:
    """找出在多页页眉/页脚区域重复出现的文字，作为噪声剔除。"""
    if len(pages) < 2:
        return set()
    counts: dict[str, int] = {}
    for lines, height in zip(pages, heights):
        if not height:
            continue
        seen: set[str] = set()
        for line in lines:
            relative = line["bbox"][1] / height
            if relative >= 0.08 and relative <= 0.92:
                continue
            key = re.sub(r"\d+", "#", line["text"].strip())
            if key and key not in seen:
                seen.add(key)
                counts[key] = counts.get(key, 0) + 1
    threshold = max(2, int(len(pages) * 0.6 + 0.999))
    repeated = {text for text, count in counts.items() if count >= threshold}
    return repeated


_CELL_GAP = 14.0          # 同一行内两个单元格的最小水平间距
_COLUMN_TOLERANCE = 22.0  # 同一列起始位置允许的漂移


def _table_bbox_sane(bbox, page) -> bool:
    """排除超出页面范围、或几乎覆盖整页的伪表格。"""
    try:
        x0, top, x1, bottom = [float(value) for value in bbox]
        width = float(page.width or 0)
        height = float(page.height or 0)
    except (TypeError, ValueError):
        return False
    if width <= 0 or height <= 0:
        return True
    if x0 < -2 or top < -2 or x1 > width + 2 or bottom > height + 2:
        return False
    if (x1 - x0) * (bottom - top) > width * height * 0.92:
        return False
    return True


def _compact_table_candidate(bbox, page, row_count: int) -> bool:
    """文本对齐策略的可信度校验：表格不应覆盖大半个页面或包含过多行。"""
    try:
        x0, top, x1, bottom = [float(value) for value in bbox]
        height = float(page.height or 0)
    except (TypeError, ValueError):
        return False
    if height <= 0:
        return True
    if bottom - top > height * 0.55:
        return False
    if row_count > 20:
        return False
    return True


def _layout_tables(page) -> list[tuple[tuple, TableBlock]]:
    """无框线表格的列对齐检测：按行分组、按间距切分单元格并校验列一致性。"""
    try:
        words = page.extract_words(x_tolerance=2, y_tolerance=3,
                                   keep_blank_chars=False)
    except Exception:  # noqa: BLE001
        return []
    if not words:
        return []
    rows = _group_words_rows(words)
    regions: list[list[dict]] = []
    current: list[dict] = []
    for row in rows:
        cells = _split_row(row["words"])
        if _cells_tabular(cells):
            current.append({"top": row["top"], "bottom": row["bottom"],
                            "cells": cells})
        else:
            if len(current) >= 2:
                regions.append(current)
            current = []
    if len(current) >= 2:
        regions.append(current)

    tables: list[tuple[tuple, TableBlock]] = []
    for region in regions:
        if not _columns_consistent(region):
            continue
        cell_rows: list[list[TableCell]] = []
        for row in region:
            cell_rows.append([TableCell(children=[Text(text=cell[2])])
                              for cell in row["cells"]])
        width = max(len(row) for row in cell_rows)
        if width < 2:
            continue
        cell_rows = [row + [TableCell()] * (width - len(row)) for row in cell_rows]
        xs0 = [cell[0] for row in region for cell in row["cells"]]
        xs1 = [cell[1] for row in region for cell in row["cells"]]
        bbox = (min(xs0), region[0]["top"], max(xs1), region[-1]["bottom"])
        tables.append((bbox, TableBlock(rows=cell_rows, header=True)))
    return tables


def _group_words_rows(words: list[dict]) -> list[dict]:
    ordered = sorted(words, key=lambda word: (round(word["top"], 1), word["x0"]))
    rows: list[dict] = []
    for word in ordered:
        top = float(word["top"])
        bottom = float(word["bottom"])
        if rows and abs(top - rows[-1]["top"]) <= max(2.0, (bottom - top) * 0.4):
            rows[-1]["words"].append(word)
            rows[-1]["top"] = min(rows[-1]["top"], top)
            rows[-1]["bottom"] = max(rows[-1]["bottom"], bottom)
        else:
            rows.append({"top": top, "bottom": bottom, "words": [word]})
    for row in rows:
        row["words"].sort(key=lambda word: word["x0"])
    return rows


def _split_row(words: list[dict]) -> list[tuple[float, float, str]]:
    if not words:
        return []
    groups: list[list[dict]] = [[words[0]]]
    for word in words[1:]:
        if word["x0"] - groups[-1][-1]["x1"] > _CELL_GAP:
            groups.append([word])
        else:
            groups[-1].append(word)
    return [(group[0]["x0"], group[-1]["x1"],
             _join_words([word["text"] for word in group]))
            for group in groups]


def _join_words(words: list[str]) -> str:
    text = ""
    for word in words:
        if not text:
            text = word
        elif text and word and _is_cjk(text[-1]) and _is_cjk(word[0]):
            text += word
        else:
            text += " " + word
    return text


def _cells_tabular(cells: list[tuple[float, float, str]]) -> bool:
    non_empty = [cell for cell in cells if cell[2].strip()]
    if len(non_empty) < 2:
        return False
    return not any(len(cell[2]) > 120 for cell in cells)


def _columns_consistent(region: list[dict]) -> bool:
    counts = [len(row["cells"]) for row in region]
    common = max(set(counts), key=counts.count)
    if common < 2:
        return False
    if counts.count(common) < max(2, int(len(region) * 0.6)):
        return False
    for column in range(common):
        starts = [row["cells"][column][0] for row in region
                  if len(row["cells"]) > column]
        if len(starts) < 2:
            return False
        if max(starts) - min(starts) > _COLUMN_TOLERANCE:
            return False
    return True


def _table_is_meaningful(rows: list[list[TableCell]]) -> bool:
    if len(rows) < 2:
        return False
    width = max(len(row) for row in rows)
    if width < 2:
        return False
    total = sum(len(row) for row in rows)
    non_empty = sum(1 for row in rows for cell in row if _cell_text(cell))
    if total == 0 or non_empty / total < 0.4:
        return False
    non_empty_columns = 0
    for column in range(width):
        values = [row[column] for row in rows if column < len(row)]
        if sum(1 for cell in values if _cell_text(cell)) >= 2:
            non_empty_columns += 1
    return non_empty_columns >= 2


def _row_looks_like_header(row: list[TableCell]) -> bool:
    texts = [_cell_text(cell) for cell in row]
    return bool(texts) and all(texts) and len(texts) >= 1 and not any(
        len(text) > 40 for text in texts)


def _cell_text(cell: TableCell) -> str:
    return _inlines_text(cell.children).strip()


def _inlines_text(inlines) -> str:
    parts = []
    for inline in inlines:
        if isinstance(inline, Text):
            parts.append(inline.text)
        elif isinstance(inline, Link):
            parts.append(_inlines_text(inline.children))
    return "".join(parts)


def _is_cjk(ch: str) -> bool:
    return any(lo <= ch <= hi for lo, hi in _CJK_RANGES)


def _join_lines(lines: list[str]) -> str:
    """中日韩文本行直接相接，西文行间补空格；英文断词连字符会被合并。"""
    parts: list[str] = []
    for index, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        if not parts:
            parts.append(line)
            continue
        previous = parts[-1]
        if previous.endswith("-") and len(previous) > 1 and line[:1].islower():
            parts[-1] = previous[:-1] + line
            continue
        if previous and line and _is_cjk(previous[-1]) and _is_cjk(line[0]):
            parts[-1] = previous + line
        else:
            parts[-1] = previous + " " + line
    return "".join(parts)


def _is_viewable_image(data: bytes) -> bool:
    if not data or len(data) < 12:
        return False
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return False


def _to_png(data: bytes) -> bytes:
    """统一转 PNG，避免上层按 MIME 猜测格式出错。"""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            buffer = io.BytesIO()
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGB")
            image.save(buffer, format="PNG")
            return buffer.getvalue()
    except Exception:  # noqa: BLE001
        return data
