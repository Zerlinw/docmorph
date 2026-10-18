"""doc_converter.readers.docx_reader —— DOCX 读取器。

覆盖：标题/正文样式、列表、表格、超链接、行内图片、块级图片、
组合形状（流程图 SmartArt / 文本框+连接线）、OLE 嵌入附件。
对无法在 IR 中表达的元素给出降级警告。
"""

from __future__ import annotations

import posixpath
from pathlib import Path

from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph
from docx.text.run import Run

from ..model import (Attachment, CodeBlock, Document, FlowchartBlock, Heading,
                     HorizontalRule, ImageBlock, ImageInline, LineBreak, Link,
                     ListBlock, ListItem, Paragraph, QuoteBlock, TableBlock,
                     TableCell, Text)
from ..utils import guess_ext, is_data_uri, parse_data_uri
from .base import BaseReader, register_reader

# python-docx 的 nsmap 未包含 VML 命名空间，这里直接使用字面量
_VML_NS = "urn:schemas-microsoft-com:vml"
_VML_IMAGEDATA = "{%s}imagedata" % _VML_NS
_WPG_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
_WPS_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
_WPG_WGP = "{%s}wgp" % _WPG_NS
_WPS_WSP = "{%s}wsp" % _WPS_NS
_WPS_TXBX = "{%s}txbx" % _WPS_NS
_O_NS = "urn:schemas-microsoft-com:office:office"
_O_OLEOBJECT = "{%s}OLEObject" % _O_NS
_VML_SHAPE = "{%s}shape" % _VML_NS


class _Ctx:
    """读取上下文：持有 python-docx 部件，用于解析图片关系。"""

    def __init__(self, part, doc: Document):
        self.part = part
        self.doc = doc

    def image_blob(self, rId: str) -> tuple[bytes, str] | None:
        try:
            rel = self.part.rels[rId]
            if rel.is_external:
                return None
            part = rel.target_part
            return part.blob, part.partname
        except KeyError:
            return None


@register_reader
class DocxReader(BaseReader):
    extensions = ("docx", "doc")

    def __init__(self) -> None:
        self._body_proxy = None

    def read(self, path: Path, doc: Document) -> Document:
        try:
            docx = DocxDocument(str(path))
        except Exception as exc:  # noqa: BLE001
            raise self._fail(path, exc)
        ctx = _Ctx(docx.part, doc)
        self._body_proxy = docx._body  # 提供 .part 的父对象，供样式查询

        attachments: list[Attachment] = []
        self._extract_embedded_files(ctx, attachments)
        self._walk_body(ctx, docx.element.body, doc)
        # 附件统一放到文档末尾，避免打断正文阅读顺序
        doc.blocks.extend(attachments)
        self._extract_title(docx, doc)

        if doc.is_empty():
            from ..errors import EmptyDocumentError
            raise EmptyDocumentError(str(path))
        return doc

    # ------------------------------------------------------------------ 正文
    def _walk_body(self, ctx: _Ctx, body, doc: Document) -> None:
        pending_list: ListBlock | None = None

        def flush_list():
            nonlocal pending_list
            if pending_list is not None:
                doc.blocks.append(pending_list)
                pending_list = None

        for child in body.iterchildren():
            if child.tag == qn("w:p"):
                para = DocxParagraph(child, self._body_proxy)
                style = (para.style.name if para.style is not None else "") or ""
                level = _heading_level(style)

                if style.lower().startswith("list"):
                    ordered = "number" in style.lower()
                    lvl = _list_level(style)
                    inlines = self._paragraph_inlines(ctx, para)
                    if pending_list is None or pending_list.ordered != ordered:
                        flush_list()
                        pending_list = ListBlock(ordered=ordered)
                    pending_list.items.append(ListItem(children=inlines, level=lvl))
                    continue

                flush_list()
                # 等宽字体段落识别为代码块（DOCX 无专门代码样式）
                if _is_monospace_paragraph(para) and para.text.strip():
                    doc.blocks.append(CodeBlock(text=para.text))
                    continue
                inlines = self._paragraph_inlines(ctx, para)
                # 段落内嵌的组合形状（流程图）优先作为独立块输出
                flow = _flowchart_from_paragraph(ctx, para)
                if flow is not None:
                    doc.blocks.append(flow)
                    if inlines:
                        doc.blocks.append(Paragraph(children=inlines))
                    continue
                if level:
                    doc.blocks.append(Heading(level=level, children=inlines))
                elif inlines:
                    doc.blocks.append(Paragraph(children=inlines))
            elif child.tag == qn("w:tbl"):
                flush_list()
                doc.blocks.append(
                    self._read_table(ctx, DocxTable(child, self._body_proxy)))
            else:
                # w:sectPr 等结构节点直接忽略
                continue
        flush_list()

    # ------------------------------------------------------------------ 段落
    def _paragraph_inlines(self, ctx: _Ctx, para: DocxParagraph) -> list:
        inlines: list = []
        for el in para._p.iterchildren():
            if el.tag == qn("w:r"):
                inlines.extend(self._run_inlines(ctx, el))
            elif el.tag == qn("w:hyperlink"):
                rId = el.get(qn("r:id"))
                target = ""
                if rId and rId in ctx.part.rels:
                    target = ctx.part.rels[rId].target_ref
                children: list = []
                for r in el.findall(qn("w:r")):
                    children.extend(self._run_inlines(ctx, r))
                if target:
                    inlines.append(Link(target=target, children=children))
                else:
                    inlines.extend(children)
            elif el.tag in (qn("w:bookmarkStart"), qn("w:bookmarkEnd")):
                continue
        return inlines

    def _run_inlines(self, ctx: _Ctx, r_el) -> list:
        run = Run(r_el, None)
        out: list = []

        # 1) 图片（DrawingML a:blip 或 VML v:imagedata）
        for blip in r_el.findall(".//" + qn("a:blip")):
            rId = blip.get(qn("r:embed"))
            if not rId:
                continue
            blob = ctx.image_blob(rId)
            if blob is None:
                ctx.doc.warn("DOCX: 跳过一个无法解析的图片关系（可能是外部链接）")
                continue
            data, partname = blob
            mime = _mime_from_partname(partname, data)
            width, height = _drawing_extent(r_el)
            out.append(ImageInline(src=partname, alt="", data=data, mime=mime,
                                   width_emu=width, height_emu=height))
        for imagedata in r_el.findall(".//" + _VML_IMAGEDATA):
            rId = imagedata.get(qn("r:id"))
            if not rId:
                continue
            blob = ctx.image_blob(rId)
            if blob is None:
                continue
            data, partname = blob
            out.append(ImageInline(src=partname, alt="", data=data,
                                   mime=_mime_from_partname(partname, data)))

        # 2) 组合形状（流程图）由段落级处理，这里只取普通文本
        if r_el.find(".//" + _WPG_WGP) is not None:
            return out

        text = "".join(t.text or "" for t in r_el.findall(".//" + qn("w:t")))
        # python-docx 会把 \n 原样写入 w:t，这里转换为软换行
        if "\n" in text:
            parts = text.split("\n")
            for i, part in enumerate(parts):
                if part:
                    out.append(Text(text=part, **_run_style(run)))
                if i < len(parts) - 1:
                    out.append(LineBreak())
            return out
        if r_el.find(".//" + qn("w:br")) is not None and text:
            out.append(Text(text=text, **_run_style(run)))
            out.append(LineBreak())
            return out
        if text:
            out.append(Text(text=text, **_run_style(run)))
        return out

    # ------------------------------------------------------------------ 表格
    def _read_table(self, ctx: _Ctx, table: DocxTable) -> TableBlock:
        rows: list[list[TableCell]] = []
        for r_i, row in enumerate(table.rows):
            cells: list[TableCell] = []
            for cell in row.cells:
                inlines: list = []
                for para in cell.paragraphs:
                    inlines.extend(self._paragraph_inlines(ctx, para))
                cells.append(TableCell(children=inlines))
            rows.append(cells)
        header = bool(rows) and _looks_like_header(table)
        if not header and rows:
            ctx.doc.warn("DOCX: 表格未检测到表头样式，已按无表头表格处理")
        return TableBlock(rows=rows, header=header)

    # ------------------------------------------------------------------ 附件
    def _extract_embedded_files(self, ctx: _Ctx, out: list[Attachment]) -> None:
        try:
            rels = list(ctx.part.rels.values())
        except Exception:  # noqa: BLE001
            return
        for rel in rels:
            reltype = rel.reltype.lower()
            is_embedding = (
                rel.reltype.endswith("/oleObject")
                or rel.reltype.endswith("/package")
                or "embeddings" in rel.target_ref.lower()
            )
            if not is_embedding or rel.is_external:
                continue
            try:
                part = rel.target_part
                name = posixpath.basename(str(part.partname))
                display = _ole_display_name(ctx, rel.rId) or name
                out.append(Attachment(
                    name=display,
                    data=part.blob,
                    mime=_mime_from_partname(name, part.blob),
                ))
            except Exception as exc:  # noqa: BLE001
                ctx.doc.warn(f"DOCX: 提取嵌入附件失败 ({rel.rId}): {exc}")

    # ------------------------------------------------------------------ 标题
    @staticmethod
    def _extract_title(docx, doc: Document) -> None:
        try:
            props = docx.core_properties
            if props.title:
                doc.title = props.title
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

_MONOSPACE_FONTS = {"consolas", "courier new", "courier", "monospace",
                    "menlo", "monaco", "source code pro", "cascadia code"}


def _is_monospace_paragraph(para: DocxParagraph) -> bool:
    """整段均为等宽字体时视为代码块。"""
    runs = para.runs
    if not runs:
        return False
    for run in runs:
        name = (run.font.name or "").strip().lower()
        if name and name not in _MONOSPACE_FONTS:
            return False
        if not name:
            return False
    return True


def _heading_level(style: str) -> int | None:
    s = style.strip().lower()
    if s in ("title",):
        return 1
    if s.startswith("heading"):
        digits = "".join(ch for ch in style if ch.isdigit())
        try:
            return max(1, min(6, int(digits)))
        except ValueError:
            return 1
    if s in ("subtitle",):
        return 2
    return None


def _list_level(style: str) -> int:
    digits = "".join(ch for ch in style if ch.isdigit())
    return max(0, int(digits) - 1) if digits else 0


def _run_style(run: Run) -> dict:
    color = None
    try:
        if run.font.color and run.font.color.rgb:
            color = str(run.font.color.rgb)
    except Exception:  # noqa: BLE001
        pass
    return dict(
        bold=bool(run.bold),
        italic=bool(run.italic),
        underline=bool(run.underline),
        strike=bool(run.font.strike),
        code=False,
        color=color,
    )


def _mime_from_partname(partname: str, data: bytes) -> str:
    ext = Path(str(partname)).suffix.lower().lstrip(".")
    mapping = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "bmp": "image/bmp", "tiff": "image/tiff",
        "webp": "image/webp", "emf": "image/x-emf", "wmf": "image/x-wmf",
        "svg": "image/svg+xml",
    }
    if ext in mapping:
        return mapping[ext]
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "application/octet-stream"


def _drawing_extent(r_el) -> tuple[int | None, int | None]:
    extent = r_el.find(".//" + qn("wp:extent"))
    if extent is None:
        return None, None
    try:
        return int(extent.get("cx")), int(extent.get("cy"))
    except (TypeError, ValueError):
        return None, None


def _looks_like_header(table: DocxTable) -> bool:
    try:
        style = (table.style.name if table.style else "") or ""
    except Exception:  # noqa: BLE001
        return False
    if "list" in style.lower():
        return False
    # 启发式：首行所有单元格非空且样式加粗
    try:
        first = table.rows[0]
        texts = [c.text.strip() for c in first.cells]
        if texts and all(texts):
            bolds = []
            for c in first.cells:
                bolds.append(any(r.bold for p in c.paragraphs for r in p.runs))
            return any(bolds)
    except Exception:  # noqa: BLE001
        pass
    return False


def _ole_display_name(ctx: _Ctx, rId: str) -> str | None:
    """从 OLE 对象的 XML 中尽量取出显示名。"""
    try:
        xml = ctx.part.element
    except Exception:  # noqa: BLE001
        return None
    ole_objects = xml.findall(".//" + _O_OLEOBJECT)
    for obj in ole_objects:
        if obj.get(qn("r:id")) != rId:
            continue
        # 邻近的 v:shape / wp:docPr 常带有可读名称
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


def _flowchart_from_paragraph(ctx: _Ctx, para: DocxParagraph) -> FlowchartBlock | None:
    """识别段落中的组合形状（流程图），抽取各形状文本。

    DOCX 流程图通常由 ``wpg:wgp`` 组合 + ``wps:wsp`` 形状 +
    ``wps:cxnSp`` 连接线构成。连接关系无法从二进制可靠恢复时，
    按顺序连边作为降级策略，并记录警告。
    """
    p_el = para._p
    groups = p_el.findall(".//" + _WPG_WGP)
    shapes = p_el.findall(".//" + _WPS_WSP)
    if not groups and not shapes:
        return None
    nodes: list[str] = []
    for txbx in p_el.findall(".//" + _WPS_TXBX):
        parts = [t.text or "" for t in txbx.findall(".//" + qn("w:t"))]
        label = " ".join(p for p in parts if p).strip()
        if label:
            nodes.append(label)
    if not nodes:
        return None
    edges = [(nodes[i], nodes[i + 1]) for i in range(len(nodes) - 1)]
    ctx.doc.warn(
        "DOCX: 检测到流程图/组合形状，已提取 %d 个节点文本；"
        "连接关系无法从文件可靠恢复，已按顺序连边（降级处理）" % len(nodes)
    )
    return FlowchartBlock(nodes=nodes, edges=edges)
