"""doc_converter.writers.markdown_writer —— Markdown 写入器。

约定：
  * 图片保存到 ``<输出名>_assets/`` 并以相对路径引用；
  * 附件同样落盘并以链接形式引用；
  * 流程图优先输出原始 Mermaid 源码（来源为 Mermaid 时无损），
    否则由 IR 生成 Mermaid 代码块；
  * 表格使用 GFM 管道表格，单元格内的 ``|`` 被转义、换行转为 ``<br>``；
  * 列表按层级正确缩进，有序列表逐级重新编号。
"""

from __future__ import annotations

import re
from pathlib import Path

from ..assets import AssetStore
from ..errors import WriteError
from ..model import (Attachment, Block, CodeBlock, Document, FlowchartBlock,
                     Heading, HorizontalRule, ImageBlock, ImageInline, Inline,
                     LineBreak, Link, ListBlock, ListItem, Paragraph,
                     QuoteBlock, TableBlock, TableCell, Text)
from ..utils import needs_title_header
from .base import BaseWriter, register_writer

_MD_SPECIAL = set("\\`*_[]<>")
_BLANK_RE = re.compile(r"\n{3,}")


def _escape_md(content: str, in_table: bool = False) -> str:
    """转义可能触发 Markdown 语法的字符，同时保持可读性。"""
    out: list[str] = []
    length = len(content)
    for i, ch in enumerate(content):
        prev = content[i - 1] if i else ""
        nxt = content[i + 1] if i + 1 < length else ""
        at_start = i == 0 or prev == "\n"
        escape = False
        if ch in _MD_SPECIAL:
            escape = True
        elif ch == "|" and in_table:
            escape = True
        elif ch == "~" and in_table:
            escape = True
        elif ch == "#" and at_start:
            escape = True
        elif ch == "-" and at_start:
            escape = True
        elif ch == "+" and at_start:
            escape = True
        elif ch == "!" and at_start and nxt not in (" ",):
            escape = True
        elif ch == "." and at_start and not prev.isdigit() and nxt not in (" ",):
            escape = True
        out.append("\\" + ch if escape else ch)
    return "".join(out)


@register_writer
class MarkdownWriter(BaseWriter):
    extension = "md"

    def write(self, doc: Document, out_path: Path, assets: AssetStore) -> Path:
        lines: list[str] = []
        if needs_title_header(doc):
            lines.append(f"# {_escape_md(doc.title or '')}")
            lines.append("")
        for block in doc.blocks:
            self._render_block(block, lines, assets)
        text = "\n".join(lines)
        text = _BLANK_RE.sub("\n\n", text).rstrip() + "\n"
        try:
            out_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise WriteError(str(out_path), str(exc))
        return out_path

    # ------------------------------------------------------------------ 块级
    def _render_block(self, block: Block, lines: list[str],
                      assets: AssetStore) -> None:
        if isinstance(block, Heading):
            level = max(1, min(6, block.level))
            lines.append(f"{'#' * level} {self._inlines(block.children, assets)}")
            lines.append("")
        elif isinstance(block, Paragraph):
            content = self._inlines(block.children, assets)
            if content.strip():
                lines.append(content)
                lines.append("")
        elif isinstance(block, ListBlock):
            self._render_list(block, lines, assets)
        elif isinstance(block, TableBlock):
            self._render_table(block, lines, assets)
        elif isinstance(block, ImageBlock):
            self._render_image_block(block, lines, assets)
        elif isinstance(block, CodeBlock):
            self._render_code(block, lines)
        elif isinstance(block, QuoteBlock):
            self._render_quote(block, lines, assets)
        elif isinstance(block, FlowchartBlock):
            self._render_flowchart(block, lines)
        elif isinstance(block, Attachment):
            ref = self._attachment_ref(block, assets)
            label = _escape_md(block.name)
            lines.append(f"📎 [{label}]({_link_target(ref)})")
            lines.append("")
        elif isinstance(block, HorizontalRule):
            lines.append("---")
            lines.append("")

    # ------------------------------------------------------------------ 列表
    def _render_list(self, block: ListBlock, lines: list[str],
                     assets: AssetStore, base_indent: int = 0,
                     top: bool = True) -> None:
        counters: dict[int, int] = {}
        for item in block.items:
            level = base_indent + max(0, item.level)
            indent = "    " * level
            text = self._inlines(item.children, assets).strip() or " "
            if block.ordered:
                default_start = block.start if level == base_indent else 1
                counters[level] = counters.get(level, default_start)
                number = counters[level]
                counters[level] += 1
                for key in [key for key in counters if key > level]:
                    del counters[key]
                lines.append(f"{indent}{number}. {text}")
            else:
                counters.pop(level, None)
                lines.append(f"{indent}- {text}")
            for child in item.blocks:
                if isinstance(child, ListBlock):
                    self._render_list(child, lines, assets,
                                      base_indent=level + 1, top=False)
                elif isinstance(child, Paragraph):
                    content = self._inlines(child.children, assets).strip()
                    if content:
                        lines.append(f"{'    ' * (level + 1)}{content}")
                elif isinstance(child, CodeBlock):
                    self._render_code(child, lines)
        if top:
            lines.append("")

    # ------------------------------------------------------------------ 表格
    def _render_table(self, block: TableBlock, lines: list[str],
                      assets: AssetStore) -> None:
        rows = _expand_cells(block.rows)
        if not rows:
            return
        width = max(len(row) for row in rows)
        rows = [row + [TableCell()] * (width - len(row)) for row in rows]

        def cell_text(cell: TableCell, header: bool = False) -> str:
            children = cell.children
            if header:  # 表头无需重复强调（管道表格本身已表达表头语义）
                from dataclasses import replace
                children = [replace(child, bold=False, italic=False, underline=False,
                                    strike=False)
                            if isinstance(child, Text) else child
                            for child in children]
            text = self._inlines(children, assets, in_table=True).strip()
            return text.replace("\n", "<br>") or " "

        if block.header:
            header = rows[0]
            body = rows[1:]
        else:
            header = [TableCell(children=[Text(text=" ")]) for _ in range(width)]
            body = rows
        lines.append("| " + " | ".join(cell_text(c, header=True) for c in header) + " |")
        lines.append("| " + " | ".join(["---"] * width) + " |")
        for row in body:
            lines.append("| " + " | ".join(cell_text(c) for c in row) + " |")
        lines.append("")
        if block.caption:
            lines.append(f"*{_escape_md(block.caption)}*")
            lines.append("")

    # ------------------------------------------------------------------ 代码
    @staticmethod
    def _render_code(block: CodeBlock, lines: list[str]) -> None:
        text = block.text.rstrip("\n")
        runs = re.findall(r"`+", text)
        longest = max((len(run) for run in runs), default=0)
        fence = "`" * max(3, longest + 1)
        lang = block.lang or ""
        lines.append(f"{fence}{lang}")
        lines.append(text)
        lines.append(fence)
        lines.append("")

    # ------------------------------------------------------------------ 引用
    def _render_quote(self, block: QuoteBlock, lines: list[str],
                      assets: AssetStore) -> None:
        inner: list[str] = []
        if block.blocks:
            for child in block.blocks:
                self._render_block(child, inner, assets)
        else:
            inner.append(self._inlines(block.children, assets))
            inner.append("")
        for line in "\n".join(inner).rstrip().split("\n"):
            if line.strip():
                lines.append(f"> {line}")
            else:
                lines.append(">")
        lines.append("")

    # ------------------------------------------------------------------ 图片
    def _render_image_block(self, image: ImageBlock, lines: list[str],
                            assets: AssetStore) -> None:
        ref = self._image_ref(image, assets)
        alt = _escape_md(image.alt or "", in_table=False).replace("\n", " ")
        lines.append(f"![{alt}]({_link_target(ref)})")
        if image.caption:
            lines.append("")
            lines.append(f"*{_escape_md(image.caption)}*")
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
                title = f' "{_escape_md(inline.title)}"' if inline.title else ""
                parts.append(f"[{label}]({_link_target(inline.target)}{title})")
            elif isinstance(inline, ImageInline):
                alt = _escape_md(inline.alt or "").replace("\n", " ")
                parts.append(f"![{alt}]({_link_target(self._image_ref(inline, assets))})")
            elif isinstance(inline, LineBreak):
                parts.append("<br>" if in_table else "  \n")
        return "".join(parts)

    @staticmethod
    def _text(text: Text, in_table: bool = False) -> str:
        content = text.text
        if text.code:
            if in_table:
                content = content.replace("|", "\\|")
            fence = "``" if "`" in content else "`"
            padded = f" {content} " if content.startswith("`") or content.endswith("`") else content
            return f"{fence}{padded}{fence}"
        result = _escape_md(content, in_table=in_table)
        if text.bold:
            result = f"**{result}**"
        if text.italic:
            result = f"*{result}*"
        if text.strike:
            result = f"~~{result}~~"
        if text.underline:  # Markdown 无下划线语法，降级为 HTML 标签
            result = f"<u>{result}</u>"
        if text.superscript:
            result = f"<sup>{result}</sup>"
        if text.subscript:
            result = f"<sub>{result}</sub>"
        return result

    # ------------------------------------------------------------------ 资源
    @staticmethod
    def _image_ref(image, assets: AssetStore) -> str:
        asset = assets.resolve_image(image.src, image.data, image.mime,
                                     hint=image.alt or "image")
        return asset.ref or image.src

    @staticmethod
    def _attachment_ref(att: Attachment, assets: AssetStore) -> str:
        if att.data is None:
            return att.name
        return assets.add_attachment(att.name, att.data, att.mime).ref

    # ------------------------------------------------------------------ 流程图
    @staticmethod
    def _render_flowchart(block: FlowchartBlock, lines: list[str]) -> None:
        if block.source and "graph" in block.source.split("\n")[0].lower():
            source = block.source.rstrip("\n")
        elif block.source and "flowchart" in block.source.split("\n")[0].lower():
            source = block.source.rstrip("\n")
        else:
            index = {node: i for i, node in enumerate(block.nodes)}
            mermaid = ["graph TD"]
            for i, node in enumerate(block.nodes):
                mermaid.append(f'    N{i}["{node}"]')
            edges = block.edges or [(block.nodes[i], block.nodes[i + 1])
                                    for i in range(len(block.nodes) - 1)]
            for a, b in edges:
                if a in index and b in index:
                    mermaid.append(f"    N{index[a]} --> N{index[b]}")
            source = "\n".join(mermaid)
        lines.append("```mermaid")
        lines.append(source)
        lines.append("```")
        lines.append("")


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _link_target(url: str) -> str:
    """含空格/括号的链接目标用尖括号包裹，避免解析歧义。"""
    if not url:
        return ""
    if re.search(r"[\s()<>]", url) and not url.startswith("<"):
        return f"<{url}>"
    return url


def _expand_cells(rows: list[list[TableCell]]) -> list[list[TableCell]]:
    """把 colspan 展开为等宽网格（GFM 不支持单元格合并）。"""
    grid: list[list[TableCell]] = []
    for row in rows:
        cells: list[TableCell] = []
        for cell in row:
            cells.append(cell)
            for _ in range(max(1, cell.colspan) - 1):
                cells.append(TableCell())
        grid.append(cells)
    return grid
