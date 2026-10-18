"""doc_converter.readers.markdown_reader —— Markdown 读取器。

基于 markdown-it-py 的 gfm-like 预设（支持表格、删除线等 GFM 语法），
将 token 流转换为统一 IR；Mermaid 代码块转换为 ``FlowchartBlock``。
"""

from __future__ import annotations

from pathlib import Path

from markdown_it import MarkdownIt

from ..model import (CodeBlock, Document, FlowchartBlock, Heading,
                     HorizontalRule, ImageInline, LineBreak, Link, ListBlock,
                     ListItem, Paragraph, QuoteBlock, TableBlock, TableCell,
                     Text)
from ..utils import is_data_uri, is_url, parse_data_uri
from .base import BaseReader, register_reader


@register_reader
class MarkdownReader(BaseReader):
    extensions = ("md", "markdown", "mdown")

    def __init__(self) -> None:
        self.base_dir = Path(".")

    def read(self, path: Path, doc: Document) -> Document:
        self.base_dir = path.parent
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = path.read_text(encoding="gbk")
                doc.warn("Markdown: 源文件非 UTF-8 编码，已按 GBK 解码")
            except Exception as exc:  # noqa: BLE001
                raise self._fail(path, exc)
        except Exception as exc:  # noqa: BLE001
            raise self._fail(path, exc)

        md = MarkdownIt("gfm-like")
        try:
            md.parse("https://example.com")  # 探测 linkify 可选依赖
        except ModuleNotFoundError:
            md.disable("linkify")
            doc.warn("Markdown: 未安装 linkify-it-py，裸 URL 不会自动转换为链接"
                     "（可执行 pip install linkify-it-py 启用）")
        except Exception:  # noqa: BLE001
            md.disable("linkify")
        tokens = md.parse(text)
        blocks, _ = self._parse_blocks(tokens, 0, doc)
        doc.blocks.extend(blocks)
        if doc.is_empty():
            from ..errors import EmptyDocumentError
            raise EmptyDocumentError(str(path))
        return doc

    # ------------------------------------------------------------------ 块级
    def _parse_blocks(self, tokens, i: int, doc: Document):
        blocks = []
        while i < len(tokens):
            tok = tokens[i]
            t = tok.type
            if t == "heading_open":
                level = int(tok.tag[1])
                inlines = self._inlines(tokens[i + 1].children)
                blocks.append(Heading(level=level, children=inlines))
                i += 3
            elif t == "paragraph_open":
                inlines = self._inlines(tokens[i + 1].children)
                blocks.append(Paragraph(children=inlines))
                i += 3
            elif t == "fence":
                lang = (tok.info or "").strip().split()[0] if tok.info else None
                if lang in ("mermaid", "flow", "graph"):
                    blocks.append(self._mermaid_to_flow(tok.content))
                    doc.warn("Markdown: Mermaid 代码块已转换为流程结构（IR）")
                else:
                    blocks.append(CodeBlock(text=tok.content.rstrip("\n"), lang=lang))
                i += 1
            elif t == "code_block":
                blocks.append(CodeBlock(text=tok.content.rstrip("\n")))
                i += 1
            elif t == "hr":
                blocks.append(HorizontalRule())
                i += 1
            elif t == "blockquote_open":
                children, i = self._parse_quote(tokens, i)
                blocks.append(QuoteBlock(children=children))
            elif t in ("bullet_list_open", "ordered_list_open"):
                block, i = self._parse_list(tokens, i)
                blocks.append(block)
            elif t == "table_open":
                block, i = self._parse_table(tokens, i)
                blocks.append(block)
            else:
                i += 1
        return blocks, i

    def _parse_quote(self, tokens, i: int):
        children: list = []
        depth = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok.type == "blockquote_open":
                depth += 1
            elif tok.type == "blockquote_close":
                depth -= 1
                if depth == 0:
                    return children, i + 1
            elif tok.type == "inline":
                children.extend(self._inlines(tok.children))
            i += 1
        return children, i

    def _parse_list(self, tokens, i: int) -> tuple[ListBlock, int]:
        ordered = tokens[i].type == "ordered_list_open"
        try:
            start = int(tokens[i].attrGet("start") or 1)
        except (TypeError, ValueError):
            start = 1
        items: list[ListItem] = []
        i += 1
        close_type = "ordered_list_close" if ordered else "bullet_list_close"
        while i < len(tokens) and tokens[i].type != close_type:
            if tokens[i].type == "list_item_open":
                i += 1
                children: list = []
                while i < len(tokens) and tokens[i].type != "list_item_close":
                    tok = tokens[i]
                    if tok.type == "paragraph_open":
                        children.extend(self._inlines(tokens[i + 1].children))
                        i += 3
                    elif tok.type == "inline":
                        children.extend(self._inlines(tok.children))
                        i += 1
                    elif tok.type in ("bullet_list_open", "ordered_list_open"):
                        nested, i = self._parse_list(tokens, i)
                        for item in nested.items:
                            items.append(ListItem(children=item.children,
                                                  level=item.level + 1))
                    else:
                        i += 1
                i += 1  # 跳过 list_item_close
                items.append(ListItem(children=children))
            else:
                i += 1
        return ListBlock(ordered=ordered, items=items, start=start), i + 1

    def _parse_table(self, tokens, i: int) -> tuple[TableBlock, int]:
        rows: list[list[TableCell]] = []
        current: list[TableCell] | None = None
        while i < len(tokens):
            t = tokens[i].type
            if t == "tr_open":
                current = []
            elif t == "tr_close":
                if current:
                    rows.append(current)
                current = None
            elif t in ("th_open", "td_open"):
                inlines = self._inlines(tokens[i + 1].children)
                assert current is not None
                current.append(TableCell(children=inlines))
                i += 3
                continue
            elif t == "table_close":
                return TableBlock(rows=rows, header=True), i + 1
            i += 1
        return TableBlock(rows=rows, header=True), i

    # ------------------------------------------------------------------ 行内
    def _inlines(self, tokens) -> list:
        out: list = []
        style = dict(bold=False, italic=False, strike=False, code=False)
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            t = tok.type
            if t == "text":
                out.append(Text(text=tok.content, **style))
            elif t == "code_inline":
                out.append(Text(text=tok.content, code=True))
            elif t in ("softbreak", "hardbreak"):
                out.append(LineBreak())
            elif t == "image":
                src = tok.attrGet("src") or ""
                data, mime = self._load_image(src)
                out.append(ImageInline(
                    src=src,
                    alt=tok.children[0].content if tok.children else "",
                    data=data,
                    mime=mime,
                ))
            elif t == "link_open":
                target = tok.attrGet("href") or ""
                title = tok.attrGet("title")
                inner, i = self._collect_until(tokens, i + 1, "link_close", "link_open")
                out.append(Link(target=target, children=self._inlines(inner),
                                title=title))
                continue
            elif t == "strong_open":
                style = {**style, "bold": True}
            elif t == "strong_close":
                style = {**style, "bold": False}
            elif t == "em_open":
                style = {**style, "italic": True}
            elif t == "em_close":
                style = {**style, "italic": False}
            elif t == "s_open":
                style = {**style, "strike": True}
            elif t == "s_close":
                style = {**style, "strike": False}
            i += 1
        return out

    @staticmethod
    def _collect_until(tokens, i: int, close_type: str, nesting_type: str):
        """收集直到配对的闭合 token，支持同类型嵌套。返回 (内部 tokens, 下一个索引)。"""
        depth = 1
        inner: list = []
        while i < len(tokens):
            tok = tokens[i]
            if tok.type == nesting_type:
                depth += 1
            elif tok.type == close_type:
                depth -= 1
                if depth == 0:
                    return inner, i + 1
            inner.append(tok)
            i += 1
        return inner, i

    # ------------------------------------------------------------------ 资源
    _MIME_BY_EXT = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
        ".svg": "image/svg+xml", ".tiff": "image/tiff",
    }

    def _load_image(self, src: str) -> tuple[bytes | None, str]:
        """读取图片字节：本地相对路径基于源文件目录解析。"""
        if not src or is_url(src):
            return None, "image/png"
        if is_data_uri(src):
            data, mime = parse_data_uri(src)
            return data, mime
        try:
            candidate = (self.base_dir / src).resolve()
            if candidate.is_file():
                data = candidate.read_bytes()
                mime = self._MIME_BY_EXT.get(candidate.suffix.lower(), "image/png")
                return data, mime
        except OSError:
            return None, "image/png"
        return None, "image/png"

    # ------------------------------------------------------------------ Mermaid
    @staticmethod
    def _mermaid_to_flow(source: str) -> FlowchartBlock:
        import re
        nodes: list[str] = []
        edges: list[tuple[str, str]] = []
        # 匹配 A["label"]-->B["label"] / A-->B / A---B 等边定义
        edge_re = re.compile(
            r'([A-Za-z0-9_\-\u4e00-\u9fff]+)'
            r'(?:\[([^\]]*)\])?'
            r'\s*(?:-->|---|==>|-.->|--)\s*'
            r'([A-Za-z0-9_\-\u4e00-\u9fff]+)'
            r'(?:\[([^\]]*)\])?'
        )
        labels: dict[str, str] = {}
        for m in edge_re.finditer(source):
            a, a_label, b, b_label = m.groups()
            if a_label:
                labels[a] = a_label.strip('"\'')
            if b_label:
                labels[b] = b_label.strip('"\'')
            edges.append((labels.get(a, a), labels.get(b, b)))
        for node_id, label in labels.items():
            if label and label not in nodes:
                nodes.append(label)
        if not nodes:  # 无法解析时保留原始文本作为单一节点（降级）
            lines = [ln.strip() for ln in source.splitlines() if ln.strip()]
            nodes = lines or ["(空流程图)"]
        return FlowchartBlock(nodes=nodes, edges=edges)
