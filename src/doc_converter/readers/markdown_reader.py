"""doc_converter.readers.markdown_reader —— Markdown 读取器。

基于 markdown-it-py 的 gfm-like 预设（表格、删除线、裸 URL 自动链接），
把 token 流转换为统一 IR。额外能力：

  * 嵌套列表顺序与层级正确（父项 -> 子项）；
  * 内嵌 HTML（``<u>``、``<img>``、``<table>`` …）交给 HTML 读取器解析；
  * Mermaid 代码块转换为 ``FlowchartBlock`` 并保留原始源码；
  * YAML front matter 解析为文档元数据；
  * 软换行按中英文分别处理（中文不额外插入空格）。
"""

from __future__ import annotations

import re
from pathlib import Path

from markdown_it import MarkdownIt

from ..model import (Block, CodeBlock, Document, FlowchartBlock, Heading,
                     HorizontalRule, ImageInline, LineBreak, Link, ListBlock,
                     ListItem, Paragraph, QuoteBlock, TableBlock, TableCell,
                     Text)
from ..utils import inlines_plain_text, is_data_uri, is_url, parse_data_uri, resolve_local_path
from .base import BaseReader, register_reader
from .html_reader import HtmlReader

_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.S)
_FRONT_MATTER_KEYS = {"title", "author", "date", "description", "keywords",
                      "subject", "lang", "language", "tags"}
_HTML_STYLE_TAGS = {
    "u": "underline", "s": "strike", "del": "strike", "strike": "strike",
    "sup": "superscript", "sub": "subscript", "strong": "bold", "b": "bold",
    "em": "italic", "i": "italic",
}


@register_reader
class MarkdownReader(BaseReader):
    extensions = ("md", "markdown", "mdown", "mkd", "mkdn")

    def __init__(self) -> None:
        self.base_dir = Path(".")
        self._html = HtmlReader()
        self._html.base_dir = self.base_dir

    def read(self, path: Path, doc: Document) -> Document:
        self.base_dir = path.parent
        self._html.base_dir = self.base_dir
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = path.read_text(encoding="gbk")
                doc.warn("Markdown: 源文件非 UTF-8 编码，已按 GBK 解码")
            except Exception as exc:  # noqa: BLE001
                raise self._fail(path, exc)
        except Exception as exc:  # noqa: BLE001
            raise self._fail(path, exc)

        text = self._strip_front_matter(text, doc)

        md = MarkdownIt("gfm-like")
        try:
            md.parse("https://example.com")  # 探测 linkify 可选依赖
        except ModuleNotFoundError:
            md.disable("linkify")
            doc.warn("Markdown: 未安装 linkify-it-py，裸 URL 不会自动转换为链接"
                     "（可执行 pip install linkify-it-py 启用）")
        except Exception:  # noqa: BLE001
            md.disable("linkify")

        self._md = md
        tokens = md.parse(text)
        blocks, _ = self._parse_blocks(tokens, 0, doc)
        doc.blocks.extend(blocks)
        self._set_title(doc)
        if doc.is_empty():
            from ..errors import EmptyDocumentError
            raise EmptyDocumentError(str(path))
        return doc

    # ------------------------------------------------------------------ 前置
    def _strip_front_matter(self, text: str, doc: Document) -> str:
        match = _FRONT_MATTER_RE.match(text)
        if not match:
            return text
        body = match.group(1)
        lines = [line for line in body.splitlines() if line.strip()]
        if not lines:
            return text
        keys = set()
        for line in lines:
            if line.lstrip().startswith("#"):
                continue
            key, sep, value = line.partition(":")
            if not sep:
                return text            # 不像 YAML，保留原文
            keys.add(key.strip().lower())
            if key.strip().lower() in _FRONT_MATTER_KEYS and value.strip():
                doc.metadata[key.strip().lower()] = value.strip().strip("'\"")
        if not keys & _FRONT_MATTER_KEYS:
            return text
        return text[match.end():]

    def _set_title(self, doc: Document) -> None:
        # 正文一级标题优先作为显示标题；front matter 的 title 只作元数据
        for block in doc.blocks:
            if isinstance(block, Heading) and block.level == 1:
                text = inlines_plain_text(block.children).strip()
                if text:
                    doc.title = text
                return
        if not doc.title and doc.metadata.get("title"):
            doc.title = doc.metadata["title"].strip() or None

    # ------------------------------------------------------------------ 块级
    def _parse_blocks(self, tokens, i: int, doc: Document):
        blocks: list[Block] = []
        while i < len(tokens):
            tok = tokens[i]
            t = tok.type
            if t == "heading_open":
                level = int(tok.tag[1])
                inlines = self._inlines(tokens[i + 1].children)
                if inlines:
                    blocks.append(Heading(level=level, children=inlines))
                i += 3
            elif t == "paragraph_open":
                inlines = self._inlines(tokens[i + 1].children)
                if inlines:
                    blocks.append(Paragraph(children=inlines))
                i += 3
            elif t == "fence":
                blocks.append(self._read_fence(tok, doc))
                i += 1
            elif t == "code_block":
                blocks.append(CodeBlock(text=tok.content.rstrip("\n")))
                i += 1
            elif t == "hr":
                blocks.append(HorizontalRule())
                i += 1
            elif t == "blockquote_open":
                quote, i = self._parse_quote(tokens, i, doc)
                blocks.append(quote)
            elif t in ("bullet_list_open", "ordered_list_open"):
                block, i = self._parse_list(tokens, i)
                if block.items:
                    blocks.append(block)
            elif t == "table_open":
                block, i = self._parse_table(tokens, i)
                if block.rows:
                    blocks.append(block)
            elif t in ("html_block", "html_inline"):
                fragments = self._html.parse_fragment(tok.content, self.base_dir, doc)
                blocks.extend(fragments)
                i += 1
            elif t == "inline":
                # 兜底：未匹配到容器的行内内容，避免静默丢弃
                inlines = self._inlines(tok.children)
                if inlines:
                    blocks.append(Paragraph(children=inlines))
                i += 1
            else:
                i += 1
        return blocks, i

    def _read_fence(self, tok, doc: Document) -> Block:
        info = (tok.info or "").strip()
        lang = info.split()[0].lower() if info else None
        if lang in ("mermaid", "flow", "graph") or lang is None and \
                re.match(r"\s*(graph|flowchart)\b", tok.content or ""):
            flow = _parse_mermaid(tok.content)
            if flow is not None:
                doc.warn("Markdown: Mermaid 代码块已转换为流程结构（IR）")
                return flow
        return CodeBlock(text=tok.content.rstrip("\n"), lang=lang)

    def _parse_quote(self, tokens, i: int, doc: Document):
        depth = 0
        j = i
        while j < len(tokens):
            if tokens[j].type == "blockquote_open":
                depth += 1
            elif tokens[j].type == "blockquote_close":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        inner_tokens = tokens[i + 1:j]
        inner_blocks, _ = self._parse_blocks(inner_tokens, 0, doc)
        children: list = []
        for block in inner_blocks:
            if isinstance(block, (Paragraph, Heading, QuoteBlock)) and block.children:
                children.extend(block.children)
            if children:
                break
        if not children:
            # 纯列表等结构：用其文本占位，保证引用内容不为空
            for block in inner_blocks:
                if isinstance(block, ListBlock):
                    for item in block.items:
                        children.extend(item.children)
                    if children:
                        break
        return QuoteBlock(children=children, blocks=inner_blocks), j + 1

    def _parse_list(self, tokens, i: int, base_level: int = 0):
        ordered = tokens[i].type == "ordered_list_open"
        try:
            start = int(tokens[i].attrGet("start") or 1)
        except (TypeError, ValueError):
            start = 1
        items: list[ListItem] = []
        i += 1
        close_type = "ordered_list_close" if ordered else "bullet_list_close"
        while i < len(tokens) and tokens[i].type != close_type:
            tok = tokens[i]
            if tok.type == "list_item_open":
                i += 1
                children: list = []
                nested: list[ListBlock] = []
                while i < len(tokens) and tokens[i].type != "list_item_close":
                    inner = tokens[i]
                    t = inner.type
                    if t == "paragraph_open":
                        children.extend(self._inlines(tokens[i + 1].children))
                        i += 3
                    elif t == "inline":
                        children.extend(self._inlines(inner.children))
                        i += 1
                    elif t in ("bullet_list_open", "ordered_list_open"):
                        nested_block, i = self._parse_list(tokens, i, base_level + 1)
                        nested.append(nested_block)
                    elif t == "fence":
                        if children:
                            children.append(LineBreak())
                        children.append(Text(text=inner.content.rstrip("\n"), code=True))
                        i += 1
                    elif t == "blockquote_open":
                        quote, i = self._parse_quote(tokens, i, Document())
                        children.extend(quote.children)
                        if quote.blocks:
                            if children:
                                children.append(LineBreak())
                            children.extend(inlines_from_blocks(quote.blocks))
                    elif t == "html_block":
                        children.extend(self._html.parse_inline_fragment(
                            inner.content, self.base_dir))
                        i += 1
                    else:
                        i += 1
                i += 1  # 跳过 list_item_close
                item = ListItem(children=children, level=base_level)
                items.append(item)
                # 子列表挂在父项内部，保持类型与嵌套顺序
                for nested_block in nested:
                    item.blocks.append(nested_block)
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
                inlines: list = []
                if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
                    inlines = self._inlines(tokens[i + 1].children)
                if current is not None:
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
        style = dict(bold=False, italic=False, underline=False, strike=False,
                     code=False, color=None)
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            t = tok.type
            if t == "text":
                out.append(Text(text=tok.content, **style))
            elif t == "code_inline":
                out.append(Text(text=tok.content, code=True, bold=style["bold"],
                                italic=style["italic"], strike=style["strike"]))
            elif t == "softbreak":
                prev = _last_char(out)
                nxt = _next_char(tokens, i + 1)
                if prev and nxt and _is_cjk(prev) and _is_cjk(nxt):
                    pass                      # 中文换行不补空格
                elif prev and prev in " \t":
                    pass
                else:
                    out.append(Text(text=" "))
            elif t == "hardbreak":
                out.append(LineBreak())
            elif t == "image":
                src = tok.attrGet("src") or ""
                alt = tok.content or ""
                data, mime = self._load_image(src)
                out.append(ImageInline(src=src, alt=alt, data=data, mime=mime))
            elif t == "link_open":
                target = tok.attrGet("href") or ""
                title = tok.attrGet("title")
                inner, i = _collect_until(tokens, i + 1, "link_close", "link_open")
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
            elif t in ("html_inline",):
                self._apply_inline_html(tok.content, out, style)
                style = dict(style)
            elif t == "html_block":
                fragments = self._html.parse_fragment(tok.content, self.base_dir)
                for block in fragments:
                    if isinstance(block, (Paragraph, Heading, QuoteBlock)):
                        out.extend(block.children)
            i += 1
        return _merge_text(out)

    def _apply_inline_html(self, content: str, out: list, style: dict) -> None:
        """处理内联 HTML：成对样式标签累积状态，其它标签交给 HTML 解析器。"""
        content = (content or "").strip()
        match = re.match(r"^<\s*(/?)\s*([A-Za-z][A-Za-z0-9]*)([^>]*)>$", content)
        if not match:
            out.extend(self._html.parse_inline_fragment(content, self.base_dir))
            return
        closing = bool(match.group(1))
        name = match.group(2).lower()
        if name == "br":
            out.append(LineBreak())
            return
        if name in ("img", "picture", "input"):
            out.extend(self._html.parse_inline_fragment(content, self.base_dir))
            return
        key = _HTML_STYLE_TAGS.get(name)
        if key:
            style[key] = not closing

    # ------------------------------------------------------------------ 资源
    _MIME_BY_EXT = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
        ".svg": "image/svg+xml", ".tif": "image/tiff", ".tiff": "image/tiff",
        ".ico": "image/x-icon", ".avif": "image/avif",
    }

    def _load_image(self, src: str) -> tuple[bytes | None, str]:
        """读取图片字节：本地相对路径基于源文件目录解析。"""
        if not src or is_url(src):
            return None, "image/png"
        if is_data_uri(src):
            try:
                return parse_data_uri(src)
            except Exception:  # noqa: BLE001
                return None, "image/png"
        path = resolve_local_path(src, self.base_dir)
        if path is not None:
            try:
                return path.read_bytes(), self._MIME_BY_EXT.get(
                    path.suffix.lower(), "image/png")
            except OSError:
                return None, "image/png"
        return None, "image/png"


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

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


def _merge_text(inlines: list) -> list:
    merged: list = []
    for inline in inlines:
        if merged and isinstance(inline, Text) and isinstance(merged[-1], Text):
            prev = merged[-1]
            if (prev.bold, prev.italic, prev.underline, prev.strike, prev.code,
                    prev.color, prev.superscript, prev.subscript) == \
                    (inline.bold, inline.italic, inline.underline, inline.strike,
                     inline.code, inline.color, inline.superscript,
                     inline.subscript):
                prev.text += inline.text
                continue
        merged.append(inline)
    return merged


def _last_char(inlines: list) -> str:
    for inline in reversed(inlines):
        if isinstance(inline, Text) and inline.text:
            return inline.text[-1]
        if isinstance(inline, Link):
            char = _last_char(inline.children)
            if char:
                return char
    return ""


def _next_char(tokens, i: int) -> str:
    while i < len(tokens):
        tok = tokens[i]
        if tok.type in ("text", "code_inline") and tok.content:
            return tok.content[0]
        if tok.type in ("strong_open", "em_open", "s_open", "link_open"):
            i += 1
            continue
        break
    return ""


_CJK_RANGES = (("\u4e00", "\u9fff"), ("\u3000", "\u303f"), ("\uff00", "\uffef"),
               ("\u3040", "\u30ff"), ("\uac00", "\ud7af"))


def _is_cjk(ch: str) -> bool:
    return any(lo <= ch <= hi for lo, hi in _CJK_RANGES)


def inlines_from_blocks(blocks: list[Block]) -> list:
    out: list = []
    for block in blocks:
        if isinstance(block, (Paragraph, Heading, QuoteBlock)):
            out.extend(block.children)
        elif isinstance(block, ListBlock):
            for item in block.items:
                out.extend(item.children)
        elif isinstance(block, CodeBlock):
            out.append(Text(text=block.text, code=True))
    return out


# ---------------------------------------------------------------------------
# Mermaid 解析
# ---------------------------------------------------------------------------

_ARROW_SPLIT_RE = re.compile(
    r"\s*(-\.->|==>|-->|---|--x|--o|--)\s*(?:\|([^|]*)\|)?\s*")
_EDGE_LABEL_RE = re.compile(r"--\s+([^|>]+?)\s+-->")
_NODE_RE = re.compile(
    r"^(?P<id>[A-Za-z0-9_\-\u4e00-\u9fff]+)\s*"
    r"(?:(?P<square>\[[^\]]*\])|(?P<round>\([^)]*\))|(?P<curly>\{[^}]*\})|"
    r"(?P<asym>>[^\]]*\]))?\s*$")
_DECL_RE = re.compile(
    r"^(?:graph|flowchart)\s+(?:TD|TB|BT|RL|LR)?\s*$", re.IGNORECASE)


def _parse_mermaid(source: str) -> FlowchartBlock | None:
    """把 Mermaid graph/flowchart 源码解析为节点与边（尽力而为）。"""
    if not source or not source.strip():
        return None
    lines = source.replace("\r\n", "\n").split("\n")
    first = lines[0].strip()
    if not re.match(r"^(?:graph|flowchart)\b", first, re.IGNORECASE) and not any(
            _ARROW_SPLIT_RE.search(line) for line in lines if line.strip()):
        return None

    labels: dict[str, str] = {}
    order: list[str] = []
    edges: list[tuple[str, str]] = []

    def register(node_id: str, label: str | None) -> str:
        if node_id not in labels:
            labels[node_id] = (label or node_id).strip().strip('"\'')
            order.append(node_id)
        elif label:
            labels[node_id] = label.strip().strip('"\'')
        return labels[node_id]

    def parse_chunk(chunk: str) -> str | None:
        chunk = chunk.strip().strip(";")
        if not chunk:
            return None
        match = _NODE_RE.match(chunk)
        if not match:
            return None
        node_id = match.group("id")
        label = None
        for key in ("square", "round", "curly", "asym"):
            raw = match.group(key)
            if raw:
                label = raw[1:-1]
                break
        register(node_id, label)
        return node_id

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("%%"):
            continue
        if _DECL_RE.match(line):
            continue
        if re.match(r"^(?:subgraph|end\b|classDef\b|class\b|style\b|linkStyle\b|"
                    r"click\b|direction\b)", line, re.IGNORECASE):
            continue
        line = _EDGE_LABEL_RE.sub(lambda m: f"-->|{m.group(1).strip()}|", line)
        parts = _ARROW_SPLIT_RE.split(line)
        # parts: [chunk, arrow, label, chunk, arrow, label, chunk, ...]
        chunks: list[str] = []
        current: str | None = None
        for index, part in enumerate(parts):
            if index % 3 == 0:
                node = parse_chunk(part)
                if node:
                    if current and chunks:
                        edges.append((current, node))
                    chunks.append(node)
                    current = node
            else:
                continue
    if not order:
        return None
    nodes = [labels[node_id] for node_id in order]
    named_edges = [(labels.get(a, a), labels.get(b, b)) for a, b in edges]
    # 去重，保持顺序
    seen: set[tuple[str, str]] = set()
    unique_edges = []
    for edge in named_edges:
        if edge[0] == edge[1] or edge in seen:
            continue
        seen.add(edge)
        unique_edges.append(edge)
    if not unique_edges and len(nodes) > 1:
        unique_edges = [(nodes[i], nodes[i + 1]) for i in range(len(nodes) - 1)]
    return FlowchartBlock(nodes=nodes, edges=unique_edges, source=source,
                          directed=True)
