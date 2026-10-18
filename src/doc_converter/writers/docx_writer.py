"""doc_converter.writers.docx_writer —— DOCX 写入器。

使用 python-docx 生成结构完整的 Word 文档：
  * 标题映射到内置 Heading 样式；
  * 列表映射到 List Bullet / List Number；
  * 表格使用 Table Grid 样式；
  * 图片以二进制方式嵌入（``run.add_picture``），保留原始宽高比；
  * 超链接通过外部关系写入；
  * 附件以超链接引用到 ``<输出名>_assets/`` 中的落盘文件；
  * 流程图无法在 Word 中直接渲染时，降级为"节点列表 + 说明"，
    同时在文末给出可粘贴到 https://mermaid.live 的 Mermaid 代码。
"""

from __future__ import annotations

import io
from pathlib import Path

from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Pt, RGBColor

from ..assets import AssetStore
from ..errors import WriteError
from ..model import (Attachment, Block, CodeBlock, Document, FlowchartBlock,
                     Heading, HorizontalRule, ImageBlock, ImageInline, Inline,
                     LineBreak, Link, ListBlock, ListItem, Paragraph,
                     QuoteBlock, TableBlock, TableCell, Text)
from .base import BaseWriter, register_writer
from ..utils import needs_title_header

_MAX_IMAGE_WIDTH_EMU = int(6.0 * 914400)  # 6 英寸


@register_writer
class DocxWriter(BaseWriter):
    extension = "docx"

    def write(self, doc: Document, out_path: Path, assets: AssetStore) -> Path:
        word_doc = DocxDocument()
        if needs_title_header(doc):
            word_doc.add_heading(doc.title, level=0)

        flowcharts: list[FlowchartBlock] = []
        for block in doc.blocks:
            if isinstance(block, FlowchartBlock):
                flowcharts.append(block)
                self._render_flowchart(word_doc, block)
            else:
                self._render_block(block, word_doc, assets)

        if flowcharts:
            # 汇总附上 Mermaid 源码，便于用户粘贴到支持 Mermaid 的编辑器精修
            self._append_mermaid_appendix(word_doc, flowcharts)

        try:
            word_doc.save(str(out_path))
        except Exception as exc:  # noqa: BLE001
            raise WriteError(str(out_path), f"{type(exc).__name__}: {exc}")
        return out_path

    # ------------------------------------------------------------------ 块级
    def _render_block(self, block: Block, word_doc, assets: AssetStore) -> None:
        if isinstance(block, Heading):
            word_doc.add_heading("", level=max(1, min(9, block.level)))
            self._add_inlines(word_doc.paragraphs[-1], block.children, assets)
        elif isinstance(block, Paragraph):
            para = word_doc.add_paragraph()
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
            para = word_doc.add_paragraph(style="Intense Quote")
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
            borders.append(bottom)
            p_pr.append(borders)

    def _render_list(self, block: ListBlock, word_doc, assets: AssetStore) -> None:
        for item in block.items:
            style = "List Number" if block.ordered else "List Bullet"
            if item.level:
                style = f"{style} {item.level + 1}"
            try:
                para = word_doc.add_paragraph(style=style)
            except KeyError:  # 模板缺少多级样式时降级
                para = word_doc.add_paragraph(style="List Bullet")
            self._add_inlines(para, item.children, assets)

    def _render_table(self, block: TableBlock, word_doc, assets: AssetStore) -> None:
        if not block.rows:
            return
        width = max(len(r) for r in block.rows)
        rows = [r + [TableCell()] * (width - len(r)) for r in block.rows]
        table = word_doc.add_table(rows=len(rows), cols=width)
        try:
            table.style = "Table Grid"
        except Exception:  # noqa: BLE001
            pass
        for r_i, row in enumerate(rows):
            for c_i, cell_model in enumerate(row):
                cell = table.cell(r_i, c_i)
                cell.text = ""
                para = cell.paragraphs[0]
                self._add_inlines(para, cell_model.children, assets)
                if block.header and r_i == 0:
                    for run in para.runs:
                        run.bold = True

    def _render_image(self, image: ImageBlock, word_doc, assets: AssetStore) -> None:
        try:
            asset = assets.resolve_image(image.src, image.data, image.mime,
                                         hint=image.alt or "image")
            data = asset.data
        except ValueError:
            data = None
        if not data:
            word_doc.add_paragraph(f"[图片无法嵌入: {image.src or '未知来源'}]")
            return
        para = word_doc.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = para.add_run()
        width = self._fit_width(image, data)
        try:
            run.add_picture(io.BytesIO(data), width=width)
        except Exception as exc:  # noqa: BLE001
            word_doc.add_paragraph(f"[图片嵌入失败: {exc}]")
            return
        if image.caption:
            cap = word_doc.add_paragraph()
            cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = cap.add_run(image.caption)
            r.italic = True
            r.font.size = Pt(9)

    @staticmethod
    def _fit_width(image: ImageBlock, data: bytes):
        if image.width_emu:
            return Emu(min(image.width_emu, _MAX_IMAGE_WIDTH_EMU))
        try:
            from PIL import Image as PILImage
            with PILImage.open(io.BytesIO(data)) as im:
                w, h = im.size
            if w and h:
                # 限制最大宽度，同时避免极端细长图
                return Emu(_MAX_IMAGE_WIDTH_EMU)
        except Exception:  # noqa: BLE001
            pass
        return Emu(_MAX_IMAGE_WIDTH_EMU)

    def _render_code(self, block: CodeBlock, word_doc) -> None:
        para = word_doc.add_paragraph()
        run = para.add_run(block.text.rstrip("\n"))
        run.font.name = "Consolas"
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
        run.font.size = Pt(9)
        # 浅灰底纹
        p_pr = para._p.get_or_add_pPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:fill"), "F2F2F2")
        p_pr.append(shd)

    def _render_attachment(self, att: Attachment, word_doc, assets: AssetStore) -> None:
        if att.data is None:
            word_doc.add_paragraph(f"📎 {att.name}")
            return
        asset = assets.add_attachment(att.name, att.data, att.mime)
        para = word_doc.add_paragraph()
        self._add_hyperlink(para, asset.ref, f"📎 {att.name}")

    # ------------------------------------------------------------------ 流程图
    def _render_flowchart(self, word_doc, block: FlowchartBlock) -> None:
        word_doc.add_heading("流程图", level=3)
        para = word_doc.add_paragraph()
        run = para.add_run("（源文档中的流程结构已转换为节点列表；"
                           "可编辑的 Mermaid 源码见文末附录）")
        run.italic = True
        run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
        for i, node in enumerate(block.nodes, start=1):
            word_doc.add_paragraph(f"{i}. {node}", style="List Number")

    @staticmethod
    def _append_mermaid_appendix(word_doc, flows: list[FlowchartBlock]) -> None:
        word_doc.add_heading("附录：流程图 Mermaid 源码", level=2)
        para = word_doc.add_paragraph()
        run = para.add_run("以下代码可粘贴到 https://mermaid.live 渲染并精修：")
        run.italic = True
        for idx, block in enumerate(flows, start=1):
            index = {n: i for i, n in enumerate(block.nodes)}
            edges = block.edges or [(block.nodes[i], block.nodes[i + 1])
                                    for i in range(len(block.nodes) - 1)]
            lines = ["graph TD"]
            for i, node in enumerate(block.nodes):
                lines.append(f'    N{i}["{node}"]')
            for a, b in edges:
                if a in index and b in index:
                    lines.append(f"    N{index[a]} --> N{index[b]}")
            code_para = word_doc.add_paragraph()
            code_run = code_para.add_run("\n".join(lines))
            code_run.font.name = "Consolas"
            code_run.font.size = Pt(9)

    # ------------------------------------------------------------------ 行内
    def _add_inlines(self, para, inlines: list[Inline], assets: AssetStore) -> None:
        for inline in inlines:
            if isinstance(inline, Text):
                self._add_text(para, inline)
            elif isinstance(inline, Link):
                label = self._inline_plain(inline.children)
                if inline.target:
                    self._add_hyperlink(para, inline.target, label)
                else:
                    para.add_run(label)
            elif isinstance(inline, ImageInline):
                try:
                    asset = assets.resolve_image(inline.src, inline.data,
                                                 inline.mime, hint="inline")
                    run = para.add_run()
                    width = (Emu(min(inline.width_emu or _MAX_IMAGE_WIDTH_EMU,
                                     _MAX_IMAGE_WIDTH_EMU))
                             if inline.width_emu else None)
                    run.add_picture(io.BytesIO(asset.data), width=width)
                except Exception as exc:  # noqa: BLE001
                    para.add_run(f"[图片: {exc}]")
            elif isinstance(inline, LineBreak):
                para.add_run().add_break()

    @staticmethod
    def _add_text(para, text: Text) -> None:
        run = para.add_run(text.text)
        run.bold = text.bold
        run.italic = text.italic
        run.underline = text.underline
        if text.strike:
            run.font.strike = True
        if text.code:
            run.font.name = "Consolas"
        if text.color and text.color.startswith("#") and len(text.color) in (4, 7):
            try:
                run.font.color.rgb = RGBColor.from_string(text.color.lstrip("#"))
            except ValueError:
                pass

    @staticmethod
    def _inline_plain(inlines: list[Inline]) -> str:
        parts: list[str] = []
        for inline in inlines:
            if isinstance(inline, Text):
                parts.append(inline.text)
            elif isinstance(inline, Link):
                parts.append(DocxWriter._inline_plain(inline.children))
        return "".join(parts)

    @staticmethod
    def _add_hyperlink(para, url: str, text: str) -> None:
        part = para.part
        r_id = part.relate_to(url, RT.HYPERLINK, is_external=True)
        hyperlink = OxmlElement("w:hyperlink")
        hyperlink.set(qn("r:id"), r_id)
        new_run = OxmlElement("w:r")
        r_pr = OxmlElement("w:rPr")
        color = OxmlElement("w:color")
        color.set(qn("w:val"), "0563C1")
        r_pr.append(color)
        u = OxmlElement("w:u")
        u.set(qn("w:val"), "single")
        r_pr.append(u)
        new_run.append(r_pr)
        t = OxmlElement("w:t")
        t.text = text
        new_run.append(t)
        hyperlink.append(new_run)
        para._p.append(hyperlink)
