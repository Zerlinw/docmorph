"""doc_converter.writers.docx_writer —— DOCX 写入器。

使用 python-docx 生成结构完整、可在 Word / WPS 中继续编辑的文档：
  * 标题映射到内置 Heading 样式，Title 单独处理；
  * 列表使用真正的 Word 编号（``w:numPr``），每个列表独立编号、支持多级缩进；
  * 表格使用 Table Grid 样式，保留合并单元格（colspan / rowspan）与表头重复；
  * 图片以二进制方式嵌入，保留原始宽高比并限制最大宽度，写入替代文字；
  * 超链接通过外部关系写入，书签锚点使用内部链接；
  * 附件落盘到 ``<输出名>_assets/`` 并以超链接关联；
  * 流程图降级为"节点框 + 箭头"，并在文末附上可粘贴到 mermaid.live 的源码；
  * 页面默认 A4，写入作者/主题等文档属性。
"""

from __future__ import annotations

import io
from pathlib import Path

from docx import Document as DocxDocument
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Inches, Mm, Pt, RGBColor

from ..assets import AssetStore, image_size_px, is_supported_by_docx
from ..errors import WriteError
from ..model import (Attachment, Block, CodeBlock, Document, FlowchartBlock,
                     Heading, HorizontalRule, ImageBlock, ImageInline, Inline,
                     LineBreak, Link, ListBlock, ListItem, Paragraph,
                     QuoteBlock, TableBlock, TableCell, Text)
from ..utils import needs_title_header
from .base import BaseWriter, register_writer

_MAX_IMAGE_WIDTH_EMU = int(6.2 * 914400)      # 6.2 英寸，适配 A4 正文宽度
_DEFAULT_IMAGE_DPI = 96

_ALIGN_MAP = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}


@register_writer
class DocxWriter(BaseWriter):
    extension = "docx"

    def write(self, doc: Document, out_path: Path, assets: AssetStore) -> Path:
        word_doc = DocxDocument()
        self._setup_document(word_doc, doc)

        if needs_title_header(doc):
            title_para = word_doc.add_heading("", level=0)
            title_para.add_run(doc.title or "")
        elif doc.title:
            word_doc.core_properties.title = doc.title

        flowcharts: list[FlowchartBlock] = []
        for block in doc.blocks:
            if isinstance(block, FlowchartBlock):
                flowcharts.append(block)
                self._render_flowchart(word_doc, block)
            else:
                self._render_block(block, word_doc, assets)

        if flowcharts:
            self._append_mermaid_appendix(word_doc, flowcharts)

        try:
            word_doc.save(str(out_path))
        except Exception as exc:  # noqa: BLE001
            raise WriteError(str(out_path), f"{type(exc).__name__}: {exc}")
        return out_path

    # ------------------------------------------------------------------ 初始化
    @staticmethod
    def _setup_document(word_doc, doc: Document) -> None:
        # 页面：A4 + 常规页边距
        try:
            section = word_doc.sections[0]
            section.orientation = WD_ORIENT.PORTRAIT
            section.page_width = Mm(210)
            section.page_height = Mm(297)
            for attr in ("left_margin", "right_margin"):
                setattr(section, attr, Mm(25))
            for attr in ("top_margin", "bottom_margin"):
                setattr(section, attr, Mm(25))
        except Exception:  # noqa: BLE001
            pass

        # 正文字体：西文 Calibri + 中文宋体，10.5pt
        try:
            normal = word_doc.styles["Normal"]
            normal.font.name = "Calibri"
            normal.font.size = Pt(10.5)
            r_pr = normal.element.get_or_add_rPr()
            r_fonts = r_pr.get_or_add_rFonts()
            r_fonts.set(qn("w:eastAsia"), "宋体")
            normal.paragraph_format.space_after = Pt(6)
            normal.paragraph_format.line_spacing = 1.25
        except Exception:  # noqa: BLE001
            pass

        # 文档属性
        props = word_doc.core_properties
        metadata = doc.metadata or {}
        try:
            props.title = doc.title or metadata.get("title") or ""
            props.author = metadata.get("author") or ""
            props.subject = metadata.get("subject") or ""
            props.keywords = metadata.get("keywords") or ""
            props.comments = metadata.get("description") or metadata.get("comments") or ""
            props.category = metadata.get("category") or ""
            props.last_modified_by = metadata.get("last_modified_by") or ""
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ 块级
    def _render_block(self, block: Block, word_doc, assets: AssetStore) -> None:
        if isinstance(block, Heading):
            para = word_doc.add_heading("", level=max(1, min(9, block.level)))
            self._add_inlines(para, block.children, assets)
        elif isinstance(block, Paragraph):
            para = word_doc.add_paragraph()
            if block.align in _ALIGN_MAP:
                para.alignment = _ALIGN_MAP[block.align]
            self._add_inlines(para, block.children, assets)
        elif isinstance(block, ListBlock):
            self._render_list(block, word_doc, assets)
        elif isinstance(block, TableBlock):
            self._render_table(block, word_doc, assets)
        elif isinstance(block, ImageBlock):
            self._render_image(block, word_doc, assets)
        elif isinstance(block, CodeBlock):
            self._render_code(block, word_doc)
        elif isinstance(block, QuoteBlock):
            if block.blocks:
                for child in block.blocks:
                    self._render_quote_block(child, word_doc, assets)
            else:
                para = word_doc.add_paragraph(style=self._quote_style(word_doc))
                self._add_inlines(para, block.children, assets)
        elif isinstance(block, Attachment):
            self._render_attachment(block, word_doc, assets)
        elif isinstance(block, HorizontalRule):
            para = word_doc.add_paragraph()
            p_pr = para._p.get_or_add_pPr()
            borders = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            bottom.set(qn("w:val"), "single")
            bottom.set(qn("w:sz"), "6")
            bottom.set(qn("w:space"), "1")
            bottom.set(qn("w:color"), "CCCCCC")
            borders.append(bottom)
            p_pr.append(borders)

    def _render_quote_block(self, block: Block, word_doc, assets: AssetStore) -> None:
        style = self._quote_style(word_doc)
        if isinstance(block, Paragraph):
            para = word_doc.add_paragraph(style=style)
            self._add_inlines(para, block.children, assets)
        elif isinstance(block, Heading):
            para = word_doc.add_heading("", level=max(1, min(9, block.level + 1)))
            self._add_inlines(para, block.children, assets)
        elif isinstance(block, ListBlock):
            self._render_list(block, word_doc, assets, style=style)
        elif isinstance(block, CodeBlock):
            self._render_code(block, word_doc, style=style)
        else:
            self._render_block(block, word_doc, assets)

    @staticmethod
    def _quote_style(word_doc) -> str | None:
        for name in ("Intense Quote", "Quote"):
            try:
                word_doc.styles[name]
                return name
            except KeyError:
                continue
        return None

    # ------------------------------------------------------------------ 列表
    def _render_list(self, block: ListBlock, word_doc, assets: AssetStore,
                     style: str | None = None, base_level: int = 0) -> None:
        numbering = _NumberingFactory(word_doc, ordered=block.ordered)
        num_id = numbering.create(start=block.start)
        number = block.start
        for item in block.items:
            level = max(0, min(8, base_level + item.level))
            if style:
                para = word_doc.add_paragraph(style=style)
            elif num_id is not None:
                para = word_doc.add_paragraph(style="List Paragraph")
            else:
                # 兜底：模板缺少编号定义时使用文本项目符号
                para = word_doc.add_paragraph()
                marker = f"{number}." if block.ordered else "•"
                number += 1
                para.add_run(f"{marker} ")
                para.paragraph_format.left_indent = Pt(18 * (level + 1))
            if num_id is not None:
                p_pr = para._p.get_or_add_pPr()
                num_pr = p_pr.get_or_add_numPr()
                num_pr.get_or_add_ilvl().val = level
                num_pr.get_or_add_numId().val = num_id
            self._add_inlines(para, item.children, assets)
            for child in item.blocks:
                if isinstance(child, ListBlock):
                    self._render_list(child, word_doc, assets, style=style,
                                      base_level=level + 1)
                else:
                    self._render_block(child, word_doc, assets)

    # ------------------------------------------------------------------ 表格
    def _render_table(self, block: TableBlock, word_doc, assets: AssetStore) -> None:
        if not block.rows:
            return
        width = max(sum(max(1, cell.colspan) for cell in row) for row in block.rows)
        if width <= 0:
            return
        table = word_doc.add_table(rows=len(block.rows), cols=width)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        try:
            table.style = "Table Grid"
        except Exception:  # noqa: BLE001
            pass

        occupied = [[False] * width for _ in range(len(block.rows))]
        for r_i, row in enumerate(block.rows):
            col = 0
            for cell_model in row:
                while col < width and occupied[r_i][col]:
                    col += 1
                if col >= width:
                    break
                colspan = max(1, min(cell_model.colspan, width - col))
                rowspan = max(1, min(cell_model.rowspan, len(block.rows) - r_i))
                target = table.cell(r_i, col)
                if colspan > 1 or rowspan > 1:
                    try:
                        target = target.merge(
                            table.cell(r_i + rowspan - 1, col + colspan - 1))
                    except Exception:  # noqa: BLE001
                        pass
                for rr in range(r_i, r_i + rowspan):
                    for cc in range(col, col + colspan):
                        if rr < len(occupied) and cc < width:
                            occupied[rr][cc] = True
                self._fill_table_cell(target, cell_model, assets,
                                      header=block.header and r_i == 0)
                col += colspan

        # 表头跨页重复
        if block.header:
            try:
                tr_pr = table.rows[0]._tr.get_or_add_trPr()
                tr_pr.append(OxmlElement("w:tblHeader"))
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _fill_table_cell(cell, model: TableCell, assets: AssetStore,
                         header: bool = False) -> None:
        # merge() 后单元格可能已包含内容，先清空
        for paragraph in list(cell.paragraphs)[1:]:
            paragraph._p.getparent().remove(paragraph._p)
        para = cell.paragraphs[0]
        for run in list(para.runs):
            run._element.getparent().remove(run._element)
        inline_writer = _InlineWriter()
        inline_writer.add(para, model.children, assets)
        if header:
            for run in para.runs:
                run.bold = True

    # ------------------------------------------------------------------ 图片
    def _render_image(self, image: ImageBlock, word_doc, assets: AssetStore) -> None:
        asset = assets.resolve_image(image.src, image.data, image.mime,
                                     hint=image.alt or "image")
        data = asset.data
        if not data:
            word_doc.add_paragraph(f"[图片无法嵌入: {image.src or asset.error or '未知来源'}]")
            return
        para = word_doc.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if not self._add_picture(para, data, image, asset.mime):
            return
        if image.caption:
            cap = word_doc.add_paragraph()
            cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = cap.add_run(image.caption)
            run.italic = True
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(0x59, 0x63, 0x6E)

    @staticmethod
    def _add_picture(para, data: bytes, image, mime: str) -> bool:
        if not is_supported_by_docx(mime) and not _looks_like_raster(data):
            para.add_run(f"[图片格式 {mime} 暂不支持嵌入 Word，已跳过]")
            return False
        width = _picture_width(data, image)
        try:
            run = para.add_run()
            shape = run.add_picture(io.BytesIO(data), width=width)
            alt = (image.alt or "").strip()
            if alt:
                try:
                    shape._inline.docPr.set("descr", alt)
                except Exception:  # noqa: BLE001
                    pass
            return True
        except Exception as exc:  # noqa: BLE001
            para.add_run(f"[图片嵌入失败: {exc}]")
            return False

    def _render_code(self, block: CodeBlock, word_doc, style: str | None = None) -> None:
        para = word_doc.add_paragraph(style=style)
        run = para.add_run(block.text.rstrip("\n") or " ")
        run.font.name = "Consolas"
        run.font.size = Pt(9)
        try:
            run._element.get_or_add_rPr().get_or_add_rFonts().set(
                qn("w:eastAsia"), "Consolas")
        except Exception:  # noqa: BLE001
            pass
        p_pr = para._p.get_or_add_pPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:fill"), "F6F8FA")
        p_pr.append(shd)
        borders = OxmlElement("w:pBdr")
        for edge in ("top", "left", "bottom", "right"):
            element = OxmlElement(f"w:{edge}")
            element.set(qn("w:val"), "single")
            element.set(qn("w:sz"), "4")
            element.set(qn("w:space"), "4")
            element.set(qn("w:color"), "E1E4E8")
            borders.append(element)
        p_pr.append(borders)

    def _render_attachment(self, att: Attachment, word_doc, assets: AssetStore) -> None:
        if att.data is None:
            word_doc.add_paragraph(f"📎 {att.name}")
            return
        asset = assets.add_attachment(att.name, att.data, att.mime)
        para = word_doc.add_paragraph()
        self._add_hyperlink(para, asset.ref, f"📎 {att.name}")

    # ------------------------------------------------------------------ 流程图
    def _render_flowchart(self, word_doc, block: FlowchartBlock) -> None:
        if block.title:
            para = word_doc.add_paragraph()
            run = para.add_run(block.title)
            run.bold = True
        for index, node in enumerate(block.nodes):
            para = word_doc.add_paragraph()
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            para.paragraph_format.space_before = Pt(4)
            para.paragraph_format.space_after = Pt(4)
            _apply_box(para)
            para.add_run(node)
            if index < len(block.nodes) - 1:
                arrow = word_doc.add_paragraph()
                arrow.alignment = WD_ALIGN_PARAGRAPH.CENTER
                arrow.add_run("↓").font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    @staticmethod
    def _append_mermaid_appendix(word_doc, flows: list[FlowchartBlock]) -> None:
        word_doc.add_heading("附录：流程图 Mermaid 源码", level=2)
        intro = word_doc.add_paragraph()
        run = intro.add_run("以下代码可粘贴到 https://mermaid.live 渲染并精修：")
        run.italic = True
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
        for block in flows:
            if block.source and block.source.strip():
                source = block.source.strip()
            else:
                index = {node: i for i, node in enumerate(block.nodes)}
                edges = block.edges or [(block.nodes[i], block.nodes[i + 1])
                                        for i in range(len(block.nodes) - 1)]
                lines = ["graph TD"]
                for i, node in enumerate(block.nodes):
                    lines.append(f'    N{i}["{node}"]')
                for a, b in edges:
                    if a in index and b in index:
                        lines.append(f"    N{index[a]} --> N{index[b]}")
                source = "\n".join(lines)
            code_para = word_doc.add_paragraph()
            _apply_box(code_para, fill="F6F8FA")
            code_run = code_para.add_run(source)
            code_run.font.name = "Consolas"
            code_run.font.size = Pt(9)

    # ------------------------------------------------------------------ 行内
    def _add_inlines(self, para, inlines: list[Inline], assets: AssetStore) -> None:
        _InlineWriter().add(para, inlines, assets)

    @staticmethod
    def _add_hyperlink(para, url: str, text: str) -> None:
        hyperlink = OxmlElement("w:hyperlink")
        if url.startswith("#"):
            hyperlink.set(qn("w:anchor"), url[1:])
        else:
            try:
                r_id = para.part.relate_to(url, RT.HYPERLINK, is_external=True)
            except Exception:  # noqa: BLE001
                para.add_run(text)
                return
            hyperlink.set(qn("r:id"), r_id)
        new_run = OxmlElement("w:r")
        r_pr = OxmlElement("w:rPr")
        try:
            r_style = OxmlElement("w:rStyle")
            r_style.set(qn("w:val"), "Hyperlink")
            r_pr.append(r_style)
        except Exception:  # noqa: BLE001
            pass
        color = OxmlElement("w:color")
        color.set(qn("w:val"), "0563C1")
        r_pr.append(color)
        u = OxmlElement("w:u")
        u.set(qn("w:val"), "single")
        r_pr.append(u)
        new_run.append(r_pr)
        for index, chunk in enumerate(str(text).split("\n")):
            if index:
                new_run.append(OxmlElement("w:br"))
            t = OxmlElement("w:t")
            t.text = chunk
            t.set(qn("xml:space"), "preserve")
            new_run.append(t)
        hyperlink.append(new_run)
        para._p.append(hyperlink)


class _InlineWriter:
    """把行内 IR 写入 python-docx 段落（供正文与表格单元格复用）。"""

    def add(self, para, inlines: list[Inline], assets: AssetStore) -> None:
        for inline in inlines:
            if isinstance(inline, Text):
                self._add_text(para, inline)
            elif isinstance(inline, Link):
                label = _inline_plain(inline.children)
                if inline.target:
                    DocxWriter._add_hyperlink(para, inline.target, label)
                else:
                    para.add_run(label)
            elif isinstance(inline, ImageInline):
                self._add_image(para, inline, assets)
            elif isinstance(inline, LineBreak):
                para.add_run().add_break()

    @staticmethod
    def _add_text(para, text: Text) -> None:
        chunks = str(text.text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
        run = None
        for index, chunk in enumerate(chunks):
            if index:
                para.add_run().add_break()
            if not chunk:
                continue
            run = para.add_run(chunk)
            run.bold = text.bold
            run.italic = text.italic
            run.underline = text.underline
            if text.strike:
                run.font.strike = True
            if text.superscript:
                run.font.superscript = True
            if text.subscript:
                run.font.subscript = True
            if text.code:
                run.font.name = "Consolas"
                try:
                    run._element.get_or_add_rPr().get_or_add_rFonts().set(
                        qn("w:eastAsia"), "Consolas")
                except Exception:  # noqa: BLE001
                    pass
            color = _parse_color(text.color)
            if color is not None:
                run.font.color.rgb = color

    @staticmethod
    def _add_image(para, inline: ImageInline, assets: AssetStore) -> None:
        asset = assets.resolve_image(inline.src, inline.data, inline.mime,
                                     hint=inline.alt or "inline")
        if not asset.data:
            para.add_run(f"[图片无法嵌入: {inline.src or asset.error or '未知来源'}]")
            return
        if not is_supported_by_docx(asset.mime) and not _looks_like_raster(asset.data):
            para.add_run(f"[图片格式 {asset.mime} 暂不支持嵌入 Word，已跳过]")
            return
        width = _picture_width(asset.data, inline)
        try:
            run = para.add_run()
            shape = run.add_picture(io.BytesIO(asset.data), width=width)
            if inline.alt:
                try:
                    shape._inline.docPr.set("descr", inline.alt)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            para.add_run(f"[图片嵌入失败: {exc}]")


# ---------------------------------------------------------------------------
# 编号工厂
# ---------------------------------------------------------------------------

class _NumberingFactory:
    """为每个列表创建独立编号实例，保证有序列表从头编号。"""

    def __init__(self, word_doc, ordered: bool) -> None:
        self.ordered = ordered
        self._word_doc = word_doc
        self._numbering = None
        self._abstract_id = None
        self._next_id = None
        try:
            part = word_doc.part.numbering_part
            self._numbering = part.element
            self._next_id = self._max_num_id() + 1
            self._abstract_id = self._resolve_abstract()
        except Exception:  # noqa: BLE001
            self._numbering = None

    def create(self, start: int = 1) -> int | None:
        if self._numbering is None or self._abstract_id is None:
            return None
        num_id = self._next_id
        self._next_id += 1
        num = OxmlElement("w:num")
        num.set(qn("w:numId"), str(num_id))
        abstract = OxmlElement("w:abstractNumId")
        abstract.set(qn("w:val"), str(self._abstract_id))
        num.append(abstract)
        for level in range(9):
            override = OxmlElement("w:lvlOverride")
            override.set(qn("w:ilvl"), str(level))
            start_element = OxmlElement("w:startOverride")
            start_element.set(qn("w:val"), str(start if level == 0 else 1))
            override.append(start_element)
            num.append(override)
        self._numbering.append(num)
        return num_id

    def _max_num_id(self) -> int:
        values = []
        for num in self._numbering.findall(qn("w:num")):
            try:
                values.append(int(num.get(qn("w:numId"))))
            except (TypeError, ValueError):
                continue
        return max(values, default=0)

    def _resolve_abstract(self) -> int | None:
        # 1) 从内置样式（List Number / List Bullet）的编号定义解析
        style_name = "List Number" if self.ordered else "List Bullet"
        style_num_id = None
        try:
            style = self._word_doc.styles[style_name]
            p_pr = style.element.find(qn("w:pPr"))
            num_pr = p_pr.find(qn("w:numPr")) if p_pr is not None else None
            num_id_el = num_pr.find(qn("w:numId")) if num_pr is not None else None
            if num_id_el is not None:
                style_num_id = _int_or_none(num_id_el.get(qn("w:val")))
        except Exception:  # noqa: BLE001
            style_num_id = None

        if style_num_id is not None:
            for num in self._numbering.findall(qn("w:num")):
                if _int_or_none(num.get(qn("w:numId"))) != style_num_id:
                    continue
                abstract = num.find(qn("w:abstractNumId"))
                if abstract is not None:
                    return _int_or_none(abstract.get(qn("w:val")))

        # 2) 回退：找第一个与列表类型匹配的 abstractNum
        wanted = "decimal" if self.ordered else "bullet"
        for abstract in self._numbering.findall(qn("w:abstractNum")):
            lvl = abstract.find(qn("w:lvl"))
            if lvl is None:
                continue
            fmt = lvl.find(qn("w:numFmt"))
            if fmt is not None and fmt.get(qn("w:val")) == wanted:
                return _int_or_none(abstract.get(qn("w:abstractNumId")))
        return None


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 样式辅助
# ---------------------------------------------------------------------------

def _apply_box(para, fill: str = "F2F6FC") -> None:
    p_pr = para._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    for edge in ("top", "left", "bottom", "right"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), "6")
        element.set(qn("w:space"), "4")
        element.set(qn("w:color"), "8CA3C7")
        borders.append(element)
    p_pr.append(borders)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), fill)
    p_pr.append(shd)


def _picture_width(data: bytes, image) -> Emu | None:
    """计算图片显示宽度：优先原始 EMU，其次像素（96dpi），最后按原始尺寸。"""
    if getattr(image, "width_emu", None):
        return Emu(min(int(image.width_emu), _MAX_IMAGE_WIDTH_EMU))
    width_px = getattr(image, "width_px", None)
    if not width_px:
        size = image_size_px(data)
        width_px = size[0] if size else None
    if width_px:
        inches = min(width_px / _DEFAULT_IMAGE_DPI, _MAX_IMAGE_WIDTH_EMU / 914400)
        if inches >= 0.2:
            return Inches(inches)
    return Emu(_MAX_IMAGE_WIDTH_EMU)


def _looks_like_raster(data: bytes) -> bool:
    if len(data) < 12:
        return False
    return (data[:8] == b"\x89PNG\r\n\x1a\n"
            or data[:3] == b"\xff\xd8\xff"
            or data[:6] in (b"GIF87a", b"GIF89a")
            or data[:2] == b"BM"
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
            or data[:4] in (b"II*\x00", b"MM\x00*"))


def _parse_color(value: str | None) -> RGBColor | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        from PIL import ImageColor
        rgb = ImageColor.getrgb(text)
        return RGBColor(rgb[0], rgb[1], rgb[2])
    except Exception:  # noqa: BLE001
        pass
    if text.startswith("#") and len(text) in (4, 7):
        try:
            return RGBColor.from_string(text.lstrip("#"))
        except ValueError:
            return None
    return None


def _inline_plain(inlines: list[Inline]) -> str:
    parts: list[str] = []
    for inline in inlines:
        if isinstance(inline, Text):
            parts.append(inline.text)
        elif isinstance(inline, Link):
            parts.append(_inline_plain(inline.children))
        elif isinstance(inline, LineBreak):
            parts.append("\n")
        elif isinstance(inline, ImageInline):
            parts.append(inline.alt or "")
    return "".join(parts)
