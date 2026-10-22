"""doc_converter.readers.docx_reader —— DOCX 读取器。

覆盖：
  * 标题（内置样式 / 本地化样式名 / outlineLvl）、正文、对齐；
  * 列表（``w:numPr`` 编号定义、项目符号/编号、多级缩进、样式式列表）；
  * 表格（gridSpan 合并列、vMerge 合并行、嵌套表格展平）；
  * 超链接（外链与书签锚点）、行内图片、块级图片、题注；
  * 组合形状（流程图）/ 文本框；
  * 内容控件 ``w:sdt``、修订插入 ``w:ins``、域 ``w:fldSimple``；
  * OLE / package 嵌入附件。

对无法在 IR 中表达的元素给出降级警告，绝不静默丢内容。
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.styles.style import StyleFactory

from ..model import (Attachment, CodeBlock, Document, FlowchartBlock, Heading,
                     HorizontalRule, ImageBlock, ImageInline, LineBreak, Link,
                     ListBlock, ListItem, Paragraph, QuoteBlock, TableBlock,
                     TableCell, Text)
from ..utils import is_data_uri, parse_data_uri
from .base import BaseReader, register_reader

# python-docx 的 nsmap 未包含 VML / DrawingML 扩展命名空间，这里直接使用字面量
_VML_NS = "urn:schemas-microsoft-com:vml"
_VML_IMAGEDATA = "{%s}imagedata" % _VML_NS
_VML_SHAPE = "{%s}shape" % _VML_NS
_WPG_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
_WPG_WGP = "{%s}wgp" % _WPG_NS
_WPS_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
_WPS_WSP = "{%s}wsp" % _WPS_NS
_WPS_TXBX = "{%s}txbx" % _WPS_NS
_WPS_CXNSP = "{%s}cxnSp" % _WPS_NS
_O_NS = "urn:schemas-microsoft-com:office:office"
_O_OLEOBJECT = "{%s}OLEObject" % _O_NS

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _w(tag: str) -> str:
    return "{%s}%s" % (_W, tag)


_HEADING_NAME_RE = re.compile(
    r"^(?:heading|titre|überschrift|标题|見出し|제목)\s*([1-9])?$", re.IGNORECASE)
_LIST_BULLET_RE = re.compile(
    r"(?:list\s*bullet|listbullet|项目符号|箇条書き|글머리)", re.IGNORECASE)
_LIST_NUMBER_RE = re.compile(
    r"(?:list\s*number|listnumber|编号列表|段落编号|番号付き|번호)", re.IGNORECASE)
_MONOSPACE_STYLES = {
    "html preformatted", "preformatted text", "code", "source code", "代码",
    "代码块", "plain text",
}


class _Ctx:
    """读取上下文：持有 python-docx 部件，用于解析图片关系与编号定义。"""

    def __init__(self, docx, doc: Document):
        self.docx = docx
        self.part = docx.part
        self.doc = doc
        self._numbering: _Numbering | None = None
        self._styles: dict[str, object] = {}

    def style(self, para):
        """把段落的 styleId 解析为 Style 对象（兼容本地化样式名与自定义 ID）。"""
        style_id = para.style            # CT_P.style -> w:pStyle/@w:val
        if not style_id:
            return None
        if style_id in self._styles:
            return self._styles[style_id]
        style = None
        try:
            element = self.docx.styles.element
            style_elm = element.get_by_id(style_id)
            if style_elm is None:
                # 部分生成器（WPS / LibreOffice）会把 styleId 本地化或改成纯数字
                style_elm = element.get_by_name(style_id)
            if style_elm is not None:
                style = StyleFactory(style_elm)
        except Exception:  # noqa: BLE001 —— 样式不存在时按无样式处理
            style = None
        self._styles[style_id] = style
        return style

    def image_blob(self, rId: str) -> tuple[bytes, str] | None:
        try:
            rel = self.part.rels[rId]
            if rel.is_external:
                return None
            target = rel.target_part
            return target.blob, str(target.partname)
        except Exception:  # noqa: BLE001
            return None

    @property
    def numbering(self) -> _Numbering | None:
        if self._numbering is None:
            element = None
            try:
                element = self.part.numbering_part.element
            except Exception:  # noqa: BLE001 —— 无编号部件
                # 兜底：在包内查找 numbering.xml
                try:
                    for part in self.part.package.iter_parts():
                        if str(part.partname).endswith("numbering.xml"):
                            element = part.element
                            break
                except Exception:  # noqa: BLE001
                    element = None
            self._numbering = _Numbering(element) if element is not None else _Numbering(None)
        return self._numbering


class _Numbering:
    """``word/numbering.xml`` 的轻量解析：numId -> (ilvl -> numFmt)。"""

    def __init__(self, element) -> None:
        self._formats: dict[tuple[int, int], str] = {}
        self._start: dict[tuple[int, int], int] = {}
        if element is None:
            return
        abstracts: dict[int, dict[int, tuple[str, int]]] = {}
        for absn in element.findall(_w("abstractNum")):
            try:
                aid = int(absn.get(_w("abstractNumId")))
            except (TypeError, ValueError):
                continue
            levels: dict[int, tuple[str, int]] = {}
            for lvl in absn.findall(_w("lvl")):
                try:
                    ilvl = int(lvl.get(_w("ilvl")) or 0)
                except (TypeError, ValueError):
                    continue
                fmt_el = lvl.find(_w("numFmt"))
                start_el = lvl.find(_w("start"))
                fmt = (fmt_el.get(_w("val")) if fmt_el is not None else "decimal") or "decimal"
                try:
                    start = int(start_el.get(_w("val"))) if start_el is not None else 1
                except (TypeError, ValueError):
                    start = 1
                levels[ilvl] = (fmt, start)
            abstracts[aid] = levels
        for num in element.findall(_w("num")):
            try:
                nid = int(num.get(_w("numId")))
            except (TypeError, ValueError):
                continue
            abs_el = num.find(_w("abstractNumId"))
            if abs_el is None:
                continue
            try:
                aid = int(abs_el.get(_w("val")))
            except (TypeError, ValueError):
                continue
            for ilvl, (fmt, start) in abstracts.get(aid, {}).items():
                self._formats[(nid, ilvl)] = fmt
                self._start[(nid, ilvl)] = start
            # lvlOverride 可覆盖起始编号
            for override in num.findall(_w("lvlOverride")):
                try:
                    ilvl = int(override.get(_w("ilvl")) or 0)
                except (TypeError, ValueError):
                    continue
                start_el = override.find(_w("startOverride"))
                if start_el is not None:
                    try:
                        self._start[(nid, ilvl)] = int(start_el.get(_w("val")))
                    except (TypeError, ValueError):
                        pass

    def is_ordered(self, num_id: int | None, ilvl: int) -> bool:
        if num_id is None:
            return False
        fmt = self._formats.get((num_id, ilvl))
        if fmt is None:
            # 未命中时回退到 0 级定义（有些文档只定义 0 级）
            fmt = self._formats.get((num_id, 0), "bullet")
        return fmt not in ("bullet", "none", "图片", "picture")

    def start(self, num_id: int | None, ilvl: int) -> int:
        if num_id is None:
            return 1
        return self._start.get((num_id, ilvl), 1)


@register_reader
class DocxReader(BaseReader):
    extensions = ("docx",)

    def __init__(self) -> None:
        self._body_proxy = None
        self._doc: Document | None = None
        self._heading_offset = 0

    def read(self, path: Path, doc: Document) -> Document:
        try:
            docx = DocxDocument(str(path))
        except Exception as exc:  # noqa: BLE001
            raise self._fail(path, exc)
        self._doc = doc
        ctx = _Ctx(docx, doc)
        self._body_proxy = docx._body
        # Word 文档常用 Title 样式作文档标题，此时 Heading 1 实际是二级结构
        self._heading_offset = 1 if _document_has_title_style(ctx, docx.element.body) else 0

        attachments: list[Attachment] = []
        self._extract_embedded_files(ctx, attachments)
        self._walk_elements(ctx, docx.element.body, doc)
        # 附件统一放到文档末尾，避免打断正文阅读顺序
        doc.blocks.extend(attachments)
        self._extract_metadata(docx, doc)

        if doc.is_empty():
            from ..errors import EmptyDocumentError
            raise EmptyDocumentError(str(path))
        return doc

    # ------------------------------------------------------------------ 正文
    def _walk_elements(self, ctx: _Ctx, container, doc: Document) -> None:
        """把容器内的段落/表格按文档顺序线性化后统一处理。"""
        pending_list: ListBlock | None = None

        def flush_list():
            nonlocal pending_list
            if pending_list is not None and pending_list.items:
                doc.blocks.append(pending_list)
            pending_list = None

        for element in _iter_block_elements(container):
            if element.tag == _w("tbl"):
                flush_list()
                doc.blocks.append(self._read_table(ctx, element))
                continue

            para = element
            style = ctx.style(para)
            heading_level = _heading_level(para, style, self._heading_offset)

            # ---- 列表（标题优先，避免自动编号标题被误判为列表） ----
            if heading_level is None:
                list_info = self._list_info(ctx, para, style)
                if list_info is not None:
                    ordered, level, start = list_info
                    inlines = self._paragraph_inlines(ctx, para)
                    if pending_list is None or pending_list.ordered != ordered \
                            or pending_list.start != start:
                        flush_list()
                        pending_list = ListBlock(ordered=ordered, start=start)
                    pending_list.items.append(ListItem(children=inlines, level=level))
                    continue

            flush_list()

            # ---- 文本框 / 流程图 ----
            shape_texts = _shape_texts(para)
            if shape_texts:
                if len(shape_texts) >= 2:
                    nodes = shape_texts
                    doc.warn(
                        "DOCX: 检测到流程图/组合形状，已提取 %d 个节点文本；"
                        "连接关系无法从文件可靠恢复，已按顺序连边（降级处理）" % len(nodes))
                    doc.blocks.append(FlowchartBlock(
                        nodes=nodes,
                        edges=[(nodes[i], nodes[i + 1]) for i in range(len(nodes) - 1)],
                    ))
                else:
                    doc.warn("DOCX: 文本框已转换为普通段落")
                    if shape_texts[0].strip():
                        doc.blocks.append(
                            Paragraph(children=[Text(text=shape_texts[0].strip())]))
                # 段落自身可能还有形状之外的文字
                inlines = self._paragraph_inlines(ctx, para)
                if inlines:
                    doc.blocks.append(Paragraph(children=inlines))
                continue

            if heading_level is not None:
                inlines = self._paragraph_inlines(ctx, para)
                if inlines:
                    doc.blocks.append(Heading(level=heading_level, children=inlines))
                continue

            inlines = self._paragraph_inlines(ctx, para)
            if not inlines:
                continue

            # 整段仅有等宽字体 -> 代码块（DOCX 无专门代码样式）
            if _is_monospace_paragraph(para, style) and not _has_image(inlines):
                doc.blocks.append(CodeBlock(text=_inlines_to_text(inlines)))
                continue

            # 整段仅有一张图片 -> 块级图片
            if len(inlines) == 1 and isinstance(inlines[0], ImageInline):
                image = inlines[0]
                doc.blocks.append(ImageBlock(
                    src=image.src, alt=image.alt, data=image.data, mime=image.mime,
                    width_emu=image.width_emu, height_emu=image.height_emu))
                continue

            doc.blocks.append(Paragraph(children=inlines, align=_paragraph_align(para)))
        flush_list()

    # ------------------------------------------------------------------ 列表
    def _list_info(self, ctx: _Ctx, para, style) -> tuple[bool, int, int] | None:
        num_pr = para.find(_w("pPr") + "/" + _w("numPr"))
        num_id = None
        level = 0
        if num_pr is not None:
            num_id_el = num_pr.find(_w("numId"))
            ilvl_el = num_pr.find(_w("ilvl"))
            if num_id_el is not None:
                try:
                    num_id = int(num_id_el.get(_w("val")))
                except (TypeError, ValueError):
                    num_id = None
            if ilvl_el is not None:
                try:
                    level = max(0, int(ilvl_el.get(_w("val"))))
                except (TypeError, ValueError):
                    level = 0
        if num_id is None and style is not None:
            style_p_pr = style.element.find(_w("pPr"))
            if style_p_pr is not None:
                style_num = style_p_pr.find(_w("numPr"))
                if style_num is not None:
                    num_id_el = style_num.find(_w("numId"))
                    if num_id_el is not None:
                        try:
                            num_id = int(num_id_el.get(_w("val")))
                        except (TypeError, ValueError):
                            num_id = None
                    ilvl_el = style_num.find(_w("ilvl"))
                    if ilvl_el is not None:
                        try:
                            level = max(0, int(ilvl_el.get(_w("val"))))
                        except (TypeError, ValueError):
                            level = 0

        if num_id is not None and num_id != 0:
            numbering = ctx.numbering
            ordered = numbering.is_ordered(num_id, level) if numbering else False
            start = numbering.start(num_id, level) if numbering else 1
            return ordered, level, start

        # 样式式列表（List Bullet / List Number / 项目符号 …）
        style_id = (style.style_id if style is not None else "") or ""
        style_name = (style.name if style is not None else "") or ""
        probe = f"{style_id} {style_name}"
        if _LIST_BULLET_RE.search(probe):
            return False, _style_level(style_id or style_name), 1
        if _LIST_NUMBER_RE.search(probe):
            return True, _style_level(style_id or style_name), 1
        return None

    # ------------------------------------------------------------------ 段落
    def _paragraph_inlines(self, ctx: _Ctx, para) -> list:
        inlines: list = []
        for el in para.iterchildren():
            tag = el.tag
            if tag == _w("r"):
                inlines.extend(self._run_inlines(ctx, el))
            elif tag == _w("hyperlink"):
                inlines.extend(self._hyperlink_inlines(ctx, el))
            elif tag in (_w("ins"), _w("smartTag"), _w("customXml"), _w("moveTo"),
                         _w("fldSimple")):
                inlines.extend(self._paragraph_inlines(ctx, el))
            elif tag == _w("sdt"):
                content = el.find(_w("sdtContent"))
                if content is not None:
                    inlines.extend(self._paragraph_inlines(ctx, content))
            elif tag in (_w("del"), _w("moveFrom")):
                continue
            elif tag in (_w("bookmarkStart"), _w("bookmarkEnd"), _w("proofErr")):
                continue
        return inlines

    def _hyperlink_inlines(self, ctx: _Ctx, element) -> list:
        rId = element.get(qn("r:id"))
        target = ""
        if rId and rId in ctx.part.rels:
            target = ctx.part.rels[rId].target_ref
        if not target:
            anchor = element.get(_w("anchor"))
            if anchor:
                target = f"#{anchor}"
        children = self._paragraph_inlines(ctx, element)
        if not children:
            return []
        if target:
            return [Link(target=target, children=children)]
        return children

    def _run_inlines(self, ctx: _Ctx, r_el) -> list:
        out: list = []
        style = _run_style(r_el)

        # 1) DrawingML 图片（含替代文字与显示尺寸）
        for drawing in r_el.findall(qn("w:drawing")):
            extent = drawing.find(".//" + qn("wp:extent"))
            width = height = None
            if extent is not None:
                try:
                    width, height = int(extent.get("cx")), int(extent.get("cy"))
                except (TypeError, ValueError):
                    width = height = None
            doc_pr = drawing.find(".//" + qn("wp:docPr"))
            alt = (doc_pr.get("descr") or doc_pr.get("title") or "") if doc_pr is not None else ""
            for blip in drawing.findall(".//" + qn("a:blip")):
                rId = blip.get(qn("r:embed")) or blip.get(qn("r:link"))
                if not rId:
                    continue
                blob = ctx.image_blob(rId)
                if blob is None:
                    ctx.doc.warn("DOCX: 跳过一个无法解析的图片关系（可能是外部链接）")
                    continue
                data, partname = blob
                out.append(ImageInline(src=partname, alt=alt.strip(), data=data,
                                       mime=_mime_from_partname(partname, data),
                                       width_emu=width, height_emu=height))

        # 2) VML 图片（旧版 Word / 兼容模式）
        for pict in r_el.findall(qn("w:pict")):
            for imagedata in pict.findall(".//" + _VML_IMAGEDATA):
                rId = imagedata.get(qn("r:id"))
                if not rId:
                    continue
                blob = ctx.image_blob(rId)
                if blob is None:
                    continue
                data, partname = blob
                out.append(ImageInline(src=partname, alt="", data=data,
                                       mime=_mime_from_partname(partname, data)))

        # 3) 组合形状（流程图）由段落级处理，这里不再重复取文本
        if r_el.find(".//" + _WPG_WGP) is not None or r_el.find(".//" + _WPS_TXBX) is not None:
            return out

        # 4) 普通文本 / 制表符 / 换行（只取直接子节点，避免文本框内文字重复）
        buffer: list[str] = []

        def flush():
            if buffer:
                text = "".join(buffer)
                buffer.clear()
                _append_text(out, text, style)

        for child in r_el.iterchildren():
            tag = child.tag
            if tag == _w("t"):
                buffer.append(child.text or "")
            elif tag == _w("tab"):
                buffer.append("\t")
            elif tag in (_w("br"), _w("cr")):
                flush()
                out.append(LineBreak())
            elif tag == _w("noBreakHyphen"):
                buffer.append("-")
            elif tag == _w("softHyphen"):
                continue
            elif tag == _w("sym"):
                try:
                    buffer.append(chr(int(child.get(_w("char")), 16)))
                except (TypeError, ValueError):
                    continue
        flush()
        return out

    # ------------------------------------------------------------------ 表格
    def _read_table(self, ctx: _Ctx, tbl) -> TableBlock:
        doc = ctx.doc
        rows: list[list[TableCell]] = []
        # (row_index, grid_column) -> 已放置的单元格，用于 vMerge 延续
        placed: dict[tuple[int, int], TableCell] = {}

        trs = tbl.findall(_w("tr"))
        for r_i, tr in enumerate(trs):
            row: list[TableCell] = []
            col = 0
            for tc in tr.findall(_w("tc")):
                tc_pr = tc.find(_w("tcPr"))
                span = 1
                vmerge_value = None
                if tc_pr is not None:
                    span_el = tc_pr.find(_w("gridSpan"))
                    if span_el is not None:
                        try:
                            span = max(1, int(span_el.get(_w("val"))))
                        except (TypeError, ValueError):
                            span = 1
                    vmerge = tc_pr.find(_w("vMerge"))
                    if vmerge is not None:
                        vmerge_value = vmerge.get(_w("val")) or "continue"

                if vmerge_value == "continue":
                    above = _find_cell_above(placed, r_i, col)
                    if above is not None:
                        above.rowspan += 1
                    col += span
                    continue

                cell = TableCell(children=self._cell_inlines(ctx, tc), colspan=span)
                placed[(r_i, col)] = cell
                row.append(cell)
                col += span
            if row:
                rows.append(row)

        if not rows:
            doc.warn("DOCX: 遇到空表格，已跳过")
            return TableBlock(rows=[], header=False)

        header = _table_has_header_row(trs)
        if not header:
            doc.warn("DOCX: 表格未检测到表头样式，已按无表头表格处理")
        return TableBlock(rows=rows, header=header)

    def _cell_inlines(self, ctx: _Ctx, tc) -> list:
        out: list = []
        for child in tc.iterchildren():
            if child.tag == _w("p"):
                inlines = self._paragraph_inlines(ctx, child)
                if inlines:
                    if out:
                        out.append(LineBreak())
                    out.extend(inlines)
            elif child.tag == _w("tbl"):
                ctx.doc.warn("DOCX: 嵌套表格已展平为单元格文本")
                if out:
                    out.append(LineBreak())
                out.append(Text(text=_table_plain_text(child)))
            elif child.tag == _w("sdt"):
                content = child.find(_w("sdtContent"))
                if content is not None:
                    for nested in content.findall(_w("p")):
                        inlines = self._paragraph_inlines(ctx, nested)
                        if inlines:
                            if out:
                                out.append(LineBreak())
                            out.extend(inlines)
        return out

    # ------------------------------------------------------------------ 附件
    def _extract_embedded_files(self, ctx: _Ctx, out: list[Attachment]) -> None:
        try:
            rels = list(ctx.part.rels.values())
        except Exception:  # noqa: BLE001
            return
        seen: set[str] = set()
        for rel in rels:
            reltype = (rel.reltype or "").lower()
            is_embedding = (
                reltype.endswith("/oleobject")
                or reltype.endswith("/package")
                or "embeddings" in (rel.target_ref or "").lower()
            )
            if not is_embedding or rel.is_external:
                continue
            try:
                part = rel.target_part
                partname = str(part.partname)
                if partname in seen:
                    continue
                seen.add(partname)
                name = posixpath.basename(partname)
                display = _ole_display_name(ctx, rel.rId) or name
                out.append(Attachment(
                    name=display,
                    data=part.blob,
                    mime=_mime_from_partname(name, part.blob),
                ))
            except Exception as exc:  # noqa: BLE001
                ctx.doc.warn(f"DOCX: 提取嵌入附件失败 ({rel.rId}): {exc}")

    # ------------------------------------------------------------------ 元数据
    @staticmethod
    def _extract_metadata(docx, doc: Document) -> None:
        props = None
        try:
            props = docx.core_properties
        except Exception:  # noqa: BLE001
            return
        if props is None:
            return
        mapping = {
            "title": getattr(props, "title", None),
            "author": getattr(props, "author", None),
            "subject": getattr(props, "subject", None),
            "keywords": getattr(props, "keywords", None),
            "category": getattr(props, "category", None),
            "comments": getattr(props, "comments", None),
            "last_modified_by": getattr(props, "last_modified_by", None),
        }
        for key, value in mapping.items():
            if value:
                doc.metadata[key] = str(value)
        try:
            if props.created:
                doc.metadata["created"] = props.created.isoformat(timespec="seconds")
            if props.modified:
                doc.metadata["modified"] = props.modified.isoformat(timespec="seconds")
        except Exception:  # noqa: BLE001
            pass

        # 文档标题：优先首个一级标题，其次 core properties.title
        first_h1 = next(
            (b for b in doc.blocks if isinstance(b, Heading) and b.level == 1), None)
        if first_h1 is not None:
            doc.title = _inlines_to_text(first_h1.children).strip() or None
        elif doc.metadata.get("title"):
            doc.title = doc.metadata["title"].strip() or None


# ---------------------------------------------------------------------------
# 元素遍历与样式解析
# ---------------------------------------------------------------------------

_SKIP_BODY_TAGS = {
    _w("sectPr"), _w("bookmarkStart"), _w("bookmarkEnd"), _w("proofErr"),
    _w("del"), _w("moveFrom"), _w("commentRangeStart"), _w("commentRangeEnd"),
    _w("tblPr"), _w("tblGrid"),
}


def _iter_block_elements(container):
    """按文档顺序产出段落 / 表格元素，递归展开内容控件与修订插入。"""
    for child in container.iterchildren():
        tag = child.tag
        if tag in (_w("p"), _w("tbl")):
            yield child
        elif tag == _w("sdt"):
            content = child.find(_w("sdtContent"))
            if content is not None:
                yield from _iter_block_elements(content)
        elif tag in (_w("ins"), _w("moveTo"), _w("customXml"), _w("smartTag")):
            yield from _iter_block_elements(child)
        elif tag == _w("altChunk"):
            continue
        elif tag in _SKIP_BODY_TAGS:
            continue


def _document_has_title_style(ctx: _Ctx, body) -> bool:
    """判断文档开头是否使用 Title（文档标题）样式。"""
    checked = 0
    for element in _iter_block_elements(body):
        if element.tag != _w("p"):
            continue
        style = ctx.style(element)
        names: list[str] = []
        if style is not None:
            names = [(style.style_id or "").lower(), (style.name or "").lower()]
        names.append((element.style or "").lower())
        if any(name in ("title", "标题", "document title") for name in names):
            return True
        checked += 1
        if checked >= 8:
            break
    return False


def _heading_level(para, style, offset: int = 0) -> int | None:
    """标题识别：样式 ID / 本地化样式名 / outlineLvl 多级兜底。

    ``offset`` 用于文档存在 Title 样式时的层级下移（Title 视为一级标题）。
    """
    candidates = [para.style or ""]
    if style is not None:
        candidates.extend([style.style_id or "", style.name or ""])
        base = style
        depth = 0
        while base is not None and depth < 4:
            base = base.base_style
            if base is not None:
                candidates.extend([base.style_id or "", base.name or ""])
            depth += 1

    for text in candidates:
        text = text.strip()
        if not text:
            continue
        lower = text.lower()
        if lower in ("title", "标题"):
            return 1
        if lower in ("subtitle", "副标题"):
            return min(6, 2 + offset)
        match = _HEADING_NAME_RE.match(text)
        if match:
            digits = match.group(1)
            return min(6, (int(digits) if digits else 1) + offset)
        if lower.startswith("heading") or lower.startswith("标题"):
            digits = "".join(ch for ch in text if ch.isdigit())
            return min(6, (int(digits) if digits else 1) + offset)

    level = _outline_level(para, style)
    if level is not None and offset:
        level = min(6, level + offset)
    return level


def _outline_level(para, style) -> int | None:
    for element in (_p_pr(para), *(_style_p_prs(style))):
        if element is None:
            continue
        outline = element.find(_w("outlineLvl"))
        if outline is not None:
            try:
                return max(1, min(6, int(outline.get(_w("val"))) + 1))
            except (TypeError, ValueError):
                continue
    return None


def _p_pr(para):
    return para.find(_w("pPr"))


def _style_p_prs(style):
    seen = []
    base = style
    depth = 0
    while base is not None and depth < 4:
        try:
            seen.append(base.element.find(_w("pPr")))
        except Exception:  # noqa: BLE001
            break
        base = base.base_style
        depth += 1
    return seen


def _paragraph_align(para) -> str | None:
    p_pr = _p_pr(para)
    if p_pr is None:
        return None
    jc = p_pr.find(_w("jc"))
    if jc is None:
        return None
    value = (jc.get(_w("val")) or "").lower()
    return {"center": "center", "right": "right", "end": "right",
            "both": "justify", "distribute": "justify"}.get(value, "left")


def _style_level(text: str) -> int:
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return 0
    return max(0, min(8, int(digits) - 1))


def _run_style(r_el) -> dict:
    r_pr = r_el.find(_w("rPr"))
    bold = italic = underline = strike = superscript = subscript = False
    color = None
    monospace = False
    if r_pr is not None:
        bold = _toggle(r_pr, "b")
        italic = _toggle(r_pr, "i")
        u = r_pr.find(_w("u"))
        if u is not None:
            underline = (u.get(_w("val")) or "single") not in ("none", "0", "false")
        strike = _toggle(r_pr, "strike") or _toggle(r_pr, "dstrike")
        color_el = r_pr.find(_w("color"))
        if color_el is not None:
            value = (color_el.get(_w("val")) or "").strip()
            if value and value.lower() != "auto":
                color = value if value.startswith("#") else f"#{value}"
        vert = r_pr.find(_w("vertAlign"))
        if vert is not None:
            val = (vert.get(_w("val")) or "").lower()
            superscript = val == "superscript"
            subscript = val == "subscript"
        fonts = r_pr.find(_w("rFonts"))
        if fonts is not None:
            names = [fonts.get(_w(key)) or "" for key in
                     ("ascii", "hAnsi", "cs", "eastAsia")]
            monospace = any(name.strip().lower() in _MONOSPACE_FONTS for name in names)
    return dict(bold=bold, italic=italic, underline=underline, strike=strike,
                code=False, color=color, superscript=superscript,
                subscript=subscript, monospace=monospace)


def _toggle(r_pr, tag: str) -> bool:
    el = r_pr.find(_w(tag))
    if el is None:
        return False
    return (el.get(_w("val")) or "true").lower() not in ("0", "false", "off")


_MONOSPACE_FONTS = {"consolas", "courier new", "courier", "monospace", "menlo",
                    "monaco", "source code pro", "cascadia code", "cascadia mono",
                    "lucida console", "sarasa mono sc", "fira code", "jetbrains mono"}


def _append_text(out: list, text: str, style: dict) -> None:
    if not text:
        return
    style = {**style}
    style["code"] = bool(style.get("monospace"))
    style.pop("monospace", None)
    if "\n" in text:
        parts = text.split("\n")
        for i, part in enumerate(parts):
            if part:
                out.append(Text(text=part, **style))
            if i < len(parts) - 1:
                out.append(LineBreak())
        return
    out.append(Text(text=text, **style))


def _has_image(inlines: list) -> bool:
    return any(isinstance(inline, ImageInline) for inline in inlines)


def _inlines_to_text(inlines: list) -> str:
    parts: list[str] = []
    for inline in inlines:
        if isinstance(inline, Text):
            parts.append(inline.text)
        elif isinstance(inline, Link):
            parts.append(_inlines_to_text(inline.children))
        elif isinstance(inline, LineBreak):
            parts.append("\n")
    return "".join(parts)


def _is_monospace_paragraph(para, style) -> bool:
    para_text = "".join(t.text or "" for t in para.iter(_w("t")))
    if not para_text.strip():
        return False
    style_name = ((style.name if style is not None else "") or "").lower()
    if style_name in _MONOSPACE_STYLES:
        return True
    runs = para.findall(_w("r"))
    if not runs:
        return False
    for run in runs:
        text = "".join(t.text or "" for t in run.findall(_w("t")))
        if not text.strip():
            continue
        r_pr = run.find(_w("rPr"))
        fonts = r_pr.find(_w("rFonts")) if r_pr is not None else None
        if fonts is None:
            return False
        names = [fonts.get(_w(key)) or "" for key in ("ascii", "hAnsi", "eastAsia")]
        if not any(name.strip().lower() in _MONOSPACE_FONTS for name in names):
            return False
    return True


def _shape_texts(para) -> list[str]:
    """提取段落中组合形状/文本框内的文本（按出现顺序）。"""
    texts: list[str] = []
    for txbx in para.findall(".//" + _WPS_TXBX):
        parts = [t.text or "" for t in txbx.findall(".//" + _w("t"))]
        label = "\n".join(p for p in parts if p).strip()
        if label:
            texts.append(label)
    return texts


def _table_has_header_row(trs) -> bool:
    if not trs:
        return False
    first = trs[0]
    tr_pr = first.find(_w("trPr"))
    if tr_pr is not None and tr_pr.find(_w("tblHeader")) is not None:
        return True
    # 启发式：首行单元格全部非空且至少一个加粗
    cells = first.findall(_w("tc"))
    if not cells:
        return False
    has_text = True
    bold_found = False
    for tc in cells:
        text = "".join(t.text or "" for t in tc.iter(_w("t"))).strip()
        if not text:
            has_text = False
            break
        for r_pr in tc.iter(_w("rPr")):
            if _toggle(r_pr, "b"):
                bold_found = True
                break
    return has_text and bold_found


def _find_cell_above(placed: dict, row: int, col: int) -> TableCell | None:
    for r in range(row - 1, -1, -1):
        cell = placed.get((r, col))
        if cell is not None:
            return cell
    return None


def _table_plain_text(tbl) -> str:
    rows = []
    for tr in tbl.findall(_w("tr")):
        cells = [("".join(t.text or "" for t in tc.iter(_w("t")))).strip()
                 for tc in tr.findall(_w("tc"))]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _mime_from_partname(partname: str, data: bytes) -> str:
    ext = Path(str(partname)).suffix.lower().lstrip(".")
    mapping = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "bmp": "image/bmp", "tiff": "image/tiff",
        "tif": "image/tiff", "webp": "image/webp", "emf": "image/x-emf",
        "wmf": "image/x-wmf", "svg": "image/svg+xml",
    }
    if ext in mapping:
        return mapping[ext]
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _ole_display_name(ctx: _Ctx, rId: str) -> str | None:
    """从 OLE 对象的 XML 中尽量取出显示名。"""
    try:
        xml = ctx.part.element
    except Exception:  # noqa: BLE001
        return None
    for obj in xml.findall(".//" + _O_OLEOBJECT):
        if obj.get(qn("r:id")) != rId:
            continue
        parent = obj.getparent()
        while parent is not None:
            for shape in parent.findall(".//" + _VML_SHAPE):
                name = shape.get("name")
                if name:
                    return name
            for docpr in parent.findall(".//" + qn("wp:docPr")):
                name = docpr.get("name")
                if name:
                    return name
            parent = parent.getparent()
    return None
