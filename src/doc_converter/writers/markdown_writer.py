"""doc_converter.writers.markdown_writer —— Markdown 写入器。

约定：
  * 图片保存到 ``<输出名>_assets/`` 并以相对路径引用；
  * 附件同样落盘并以链接形式引用；
  * 流程图输出为 Mermaid 代码块（GitHub 可直接渲染）；
  * 表格使用 GFM 管道表格，单元格内的 ``|`` 被转义。
"""

from __future__ import annotations

from pathlib import Path

from ..assets import AssetStore
from ..errors import WriteError
from ..model import (Attachment, Block, CodeBlock, Document, FlowchartBlock,
                     Heading, HorizontalRule, ImageBlock, ImageInline, Inline,
                     LineBreak, Link, ListBlock, ListItem, Paragraph,
                     QuoteBlock, TableBlock, TableCell, Text)
from ..utils import needs_title_header, sanitize_filename
from .base import BaseWriter, register_writer


def _escape_md(content: str, in_table: bool = False) -> str:
    """仅在可能触发 Markdown 语法的位置转义，兼顾可读性。"""
    out: list[str] = []
    length = len(content)
    for i, ch in enumerate(content):
        prev = content[i - 1] if i else ""
        nxt = content[i + 1] if i + 1 < length else ""
        at_start = i == 0 or prev == "\n"
        escape = False
        if ch in "\\`*_[]<>":
            escape = True
        elif ch == "|" and in_table:
            escape = True
        elif ch == "~" and in_table:
            escape = True
        elif ch in "#" and at_start:
            escape = True
        elif ch in "-+" and at_start:
            escape = True
        elif ch in "!." and at_start and nxt not in (" ",):
            # 避免误伤 "1.5"、"e.g." 等常见文本，仅行首紧邻非空格时转义
            if ch == "!" or (ch == "." and not prev.isdigit()):
                escape = True
        out.append("\\" + ch if escape else ch)
    return "".join(out)


@register_writer
class MarkdownWriter(BaseWriter):
    extension = "md"

    def write(self, doc: Document, out_path: Path, assets: AssetStore) -> Path:
        lines: list[str] = []
        if needs_title_header(doc):
            lines.append(f"# {doc.title}")
            lines.append("")
        for block in doc.blocks:
            self._render_block(block, lines, assets)
        text = "\n".join(lines).rstrip() + "\n"
        try:
            out_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise WriteError(str(out_path), str(exc))
        return out_path

    # ------------------------------------------------------------------ 块级
    def _render_block(self, block: Block, lines: list[str],
                      assets: AssetStore) -> None:
        if isinstance(block, Heading):
            lines.append(f"{'#' * block.level} {self._inlines(block.children, assets)}")
            lines.append("")
        elif isinstance(block, Paragraph):
            lines.append(self._inlines(block.children, assets))
            lines.append("")
        elif isinstance(block, ListBlock):
            self._render_list(block, lines, assets)
        elif isinstance(block, TableBlock):
            self._render_table(block, lines, assets)
        elif isinstance(block, ImageBlock):
            ref = self._image_ref(block, assets)
            lines.append(f"![{block.alt or ''}]({ref})")
            if block.caption:
                lines.append("")
                lines.append(f"*{block.caption}*")
            lines.append("")
        elif isinstance(block, CodeBlock):
            lang = block.lang or ""
            fence = "```" if "```" not in block.text else "~~~"
            lines.append(f"{fence}{lang}")
            lines.append(block.text.rstrip("\n"))
            lines.append(fence)
            lines.append("")
        elif isinstance(block, QuoteBlock):
            inner = self._inlines(block.children, assets)
            lines.append(f"> {inner}")
            lines.append("")
        elif isinstance(block, FlowchartBlock):
            lines.extend(self._mermaid(block))
            lines.append("")
        elif isinstance(block, Attachment):
            ref = self._attachment_ref(block, assets)
            lines.append(f"📎 [{block.name}]({ref})")
            lines.append("")
        elif isinstance(block, HorizontalRule):
            lines.append("---")
            lines.append("")

    def _render_list(self, block: ListBlock, lines: list[str],
                     assets: AssetStore) -> None:
        # 按 level 分组连续输出，保持 Markdown 嵌套可读性
        by_level: dict[int, list[ListItem]] = {}
        for item in block.items:
            by_level.setdefault(item.level, []).append(item)
        counter = block.start
        for level in sorted(by_level):
            for item in by_level[level]:
                indent = "  " * level
                text = self._inlines(item.children, assets)
                if block.ordered:
                    lines.append(f"{indent}{counter}. {text}")
                    counter += 1
                else:
                    lines.append(f"{indent}- {text}")
        lines.append("")

    def _render_table(self, block: TableBlock, lines: list[str],
                      assets: AssetStore) -> None:
        if not block.rows:
            return
        width = max(len(r) for r in block.rows)
        rows = [r + [TableCell()] * (width - len(r)) for r in block.rows]

        def cell_text(cell: TableCell, header: bool = False) -> str:
            children = cell.children
            if header:  # 表头无需重复强调（管道表格本身已表达表头语义）
                from dataclasses import replace
                children = [replace(c, bold=False, italic=False, underline=False)
                            if isinstance(c, Text) else c for c in children]
            return self._inlines(children, assets, in_table=True) or " "

        header = rows[0] if block.header else [TableCell(children=[Text(text=" ")])
                                               for _ in range(width)]
        lines.append("| " + " | ".join(cell_text(c, header=True) for c in header) + " |")
        lines.append("|" + "|".join([" --- "] * width) + "|")
        body = rows[1:] if block.header else rows
        for row in body:
            lines.append("| " + " | ".join(cell_text(c) for c in row) + " |")
        lines.append("")

    # ------------------------------------------------------------------ 行内
    def _inlines(self, inlines: list[Inline], assets: AssetStore,
                 in_table: bool = False) -> str:
        parts: list[str] = []
        for inline in inlines:
            if isinstance(inline, Text):
                parts.append(self._text(inline, in_table=in_table))
            elif isinstance(inline, Link):
                label = self._inlines(inline.children, assets, in_table)
                parts.append(f"[{label}]({inline.target})")
            elif isinstance(inline, ImageInline):
                parts.append(f"![{inline.alt or ''}]({self._image_ref(inline, assets)})")
            elif isinstance(inline, LineBreak):
                parts.append("  \n" if not in_table else "<br>")
        return "".join(parts)

    @staticmethod
    def _text(text: Text, in_table: bool = False) -> str:
        content = text.text
        if text.code:
            fence = "``" if "`" in content else "`"
            return f"{fence}{content}{fence}"
        result = _escape_md(content, in_table=in_table)
        if text.bold:
            result = f"**{result}**"
        if text.italic:
            result = f"*{result}*"
        if text.strike:
            result = f"~~{result}~~"
        if text.underline:  # Markdown 无下划线语法，保留内容（降级）
            result = f"<u>{result}</u>"
        return result

    # ------------------------------------------------------------------ 资源
    @staticmethod
    def _image_ref(image, assets: AssetStore) -> str:
        try:
            asset = assets.resolve_image(image.src, image.data, image.mime,
                                         hint=image.alt or "image")
            return asset.ref
        except ValueError:
            return image.src or ""

    @staticmethod
    def _attachment_ref(att: Attachment, assets: AssetStore) -> str:
        if att.data is None:
            return att.name
        asset = assets.add_attachment(att.name, att.data, att.mime)
        return asset.ref

    # ------------------------------------------------------------------ 流程图
    @staticmethod
    def _mermaid(block: FlowchartBlock) -> list[str]:
        lines = ["```mermaid", "graph TD"]
        for i, node in enumerate(block.nodes):
            lines.append(f'    N{i}["{node}"]')
        edges = block.edges
        if not edges and len(block.nodes) > 1:
            edges = [(block.nodes[i], block.nodes[i + 1])
                     for i in range(len(block.nodes) - 1)]
        index = {node: i for i, node in enumerate(block.nodes)}
        for a, b in edges:
            ia, ib = index.get(a), index.get(b)
            if ia is None or ib is None:
                continue
            lines.append(f"    N{ia} --> N{ib}")
        lines.append("```")
        return lines
