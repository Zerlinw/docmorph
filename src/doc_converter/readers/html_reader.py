"""doc_converter.readers.html_reader —— HTML 读取器。

覆盖：标题层级、段落（含裸文本）、列表（含嵌套）、表格（colspan/rowspan）、
超链接、图片（本地相对路径 / data URI / 远程 URL / width 属性）、代码块、
引用（多段）、水平线、定义列表，以及指向非页面文件的附件链接。

HTML 的空白折叠规则会被还原（除 ``<pre>`` 外连续空白压缩为单个空格），
避免转换结果出现多余的换行。
"""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

from ..model import (Attachment, Block, CodeBlock, Document, FlowchartBlock,
                     Heading, HorizontalRule, ImageBlock, ImageInline, LineBreak,
                     Link, ListBlock, ListItem, Paragraph, QuoteBlock, TableBlock,
                     TableCell, Text)
from ..utils import (ext_of, inlines_plain_text, is_data_uri, is_url,
                     parse_data_uri, resolve_local_path)
from .base import BaseReader, register_reader

_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table",
               "pre", "blockquote", "hr", "div", "section", "article",
               "figure", "main", "header", "footer", "aside", "dl", "form",
               "fieldset", "figcaption", "address", "nav"}

_INLINE_TAGS = {"a", "b", "strong", "i", "em", "u", "s", "del", "strike",
                "code", "kbd", "samp", "span", "br", "img", "small", "sub",
                "sup", "mark", "abbr", "cite", "q", "time", "font", "label",
                "wbr", "picture"}

_ATTACHMENT_EXTS = {
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip", "rar", "7z",
    "tar", "gz", "csv", "json", "xml", "txt", "png", "jpg", "jpeg", "gif",
    "svg", "mp3", "mp4", "wav", "apk", "exe", "msi", "bin",
}

_WS_RE = re.compile(r"[ \t\r\n\f\v]+")
_MIME_BY_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
    ".svg": "image/svg+xml", ".tif": "image/tiff", ".tiff": "image/tiff",
    ".ico": "image/x-icon", ".avif": "image/avif",
}


@register_reader
class HtmlReader(BaseReader):
    extensions = ("html", "htm")

    def __init__(self) -> None:
        self.base_dir: Path = Path(".")

    def read(self, path: Path, doc: Document) -> Document:
        try:
            raw = path.read_bytes()
        except Exception as exc:  # noqa: BLE001
            raise self._fail(path, exc)
        self.base_dir = path.parent
        try:
            soup = BeautifulSoup(raw, "html.parser")
        except Exception as exc:  # noqa: BLE001
            raise self._fail(path, exc)

        self._read_head(soup, doc)
        root = soup.body or soup
        self._walk_children(root, doc)

        if doc.is_empty():
            from ..errors import EmptyDocumentError
            raise EmptyDocumentError(str(path))
        return doc

    # ------------------------------------------------------------------ 头部
    def _read_head(self, soup: BeautifulSoup, doc: Document) -> None:
        title_tag = soup.find("title")
        if title_tag and title_tag.get_text(strip=True):
            doc.title = title_tag.get_text(strip=True)
        html_tag = soup.find("html")
        if html_tag is not None and html_tag.get("lang"):
            doc.metadata["lang"] = str(html_tag.get("lang"))
        for meta in soup.find_all("meta"):
            name = (meta.get("name") or meta.get("property") or "").lower()
            content = meta.get("content")
            if not content:
                continue
            if name in ("author", "description", "keywords", "generator",
                        "subject", "dc.creator"):
                doc.metadata.setdefault(name, content)
        if not doc.title and doc.metadata.get("description"):
            doc.title = doc.metadata["description"][:80]

    # ------------------------------------------------------------------ 块级
    def _walk(self, tag: Tag, doc: Document) -> None:
        name = tag.name.lower()
        if name in ("script", "style", "head", "meta", "link", "noscript",
                    "template", "iframe", "object", "embed"):
            return
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            doc.blocks.append(Heading(level=int(name[1]), children=self._inlines(tag)))
            return
        if name == "p":
            inlines = self._inlines(tag)
            if inlines:
                doc.blocks.append(Paragraph(children=inlines,
                                            align=_paragraph_align(tag)))
            return
        if name in ("ul", "ol"):
            doc.blocks.append(self._read_list(tag, ordered=name == "ol"))
            return
        if name == "table":
            doc.blocks.append(self._read_table(tag))
            return
        if name == "pre":
            self._read_pre(tag, doc)
            return
        if name == "blockquote":
            inner: list[Block] = []
            self._walk_children(tag, _BlockSink(inner))
            inlines = self._inlines(tag)
            if inner or inlines:
                doc.blocks.append(QuoteBlock(children=inlines, blocks=inner))
            return
        if name == "hr":
            doc.blocks.append(HorizontalRule())
            return
        if name == "figure":
            img = tag.find("img")
            if img is not None:
                doc.blocks.append(self._image_block(img, tag))
                caption = tag.find("figcaption")
                if caption is not None and not doc.blocks[-1].caption:
                    doc.blocks[-1].caption = caption.get_text(" ", strip=True) or None
            else:
                self._walk_children(tag, doc)
            return
        if name == "dl":
            self._read_definition_list(tag, doc)
            return
        if name in ("div", "section", "article", "main", "header", "footer",
                    "aside", "form", "fieldset", "address", "nav"):
            self._walk_children(tag, doc)
            return
        # 其余标签：若内部只有行内内容则整体成段，否则递归
        self._walk_children(tag, doc)

    def _walk_children(self, tag: Tag, doc: Document) -> None:
        """块级容器：把连续的行内内容聚合为段落，块级子节点递归处理。"""
        buffer: list = []

        def flush() -> None:
            if not buffer:
                return
            inlines: list = []
            base_style = dict(bold=False, italic=False, underline=False,
                              strike=False, code=False, color=None,
                              superscript=False, subscript=False)
            for node in buffer:
                self._inline_node(node, base_style, inlines)
            buffer.clear()
            if inlines and inlines_plain_text(inlines).strip():
                doc.blocks.append(Paragraph(children=inlines))

        for child in tag.children:
            if isinstance(child, Tag):
                if child.name.lower() in _INLINE_TAGS:
                    buffer.append(child)
                else:
                    flush()
                    self._walk(child, doc)
            elif isinstance(child, NavigableString):
                buffer.append(child)
        flush()

    # ------------------------------------------------------------------ 列表
    def _read_list(self, tag: Tag, ordered: bool, level: int = 0) -> ListBlock:
        items: list[ListItem] = []
        start = 1
        if ordered:
            try:
                start = int(str(tag.get("start") or 1))
            except (TypeError, ValueError):
                start = 1
        for li in tag.find_all("li", recursive=False):
            children: list = []
            nested_lists: list[ListBlock] = []
            style = dict(bold=False, italic=False, underline=False, strike=False,
                         code=False, color=None, superscript=False,
                         subscript=False)
            for child in li.children:
                if isinstance(child, Tag) and child.name.lower() in ("ul", "ol"):
                    nested_lists.append(self._read_list(
                        child, ordered=child.name.lower() == "ol", level=level + 1))
                else:
                    self._inline_node(child, style, children)
            items.append(ListItem(children=children, level=level))
            # 子列表挂在父项内部，保持类型与嵌套顺序
            for nested in nested_lists:
                items[-1].blocks.append(nested)
        return ListBlock(ordered=ordered, items=items, start=start)

    # ------------------------------------------------------------------ 表格
    def _read_table(self, tag: Tag) -> TableBlock:
        rows: list[list[TableCell]] = []
        for tr in tag.find_all("tr"):
            cells: list[TableCell] = []
            for cell in tr.find_all(["td", "th"], recursive=False):
                colspan = _int_attr(cell, "colspan", 1)
                rowspan = _int_attr(cell, "rowspan", 1)
                cells.append(TableCell(children=self._inlines(cell),
                                       colspan=max(1, colspan),
                                       rowspan=max(1, rowspan)))
            if cells:
                rows.append(cells)
        has_thead = tag.find("thead") is not None
        first_row = tag.find("tr")
        first_is_th = bool(first_row is not None and first_row.find("th"))
        caption = tag.find("caption")
        return TableBlock(rows=rows, header=has_thead or first_is_th,
                          caption=caption.get_text(" ", strip=True) if caption else None)

    # ------------------------------------------------------------------ 代码
    def _read_pre(self, tag: Tag, doc: Document) -> None:
        code = tag.find("code")
        source = code if code is not None else tag
        text = source.get_text().replace("\r\n", "\n").replace("\r", "\n")
        if text.startswith("\n"):
            text = text[1:]
        lang = None
        for element in (code, tag):
            if element is None:
                continue
            classes = " ".join(element.get("class") or [])
            match = re.search(r"(?:language|lang)-([A-Za-z0-9_+#.-]+)", classes)
            if match:
                lang = match.group(1)
                break
        if text.strip():
            doc.blocks.append(CodeBlock(text=text.rstrip("\n"), lang=lang))

    # ------------------------------------------------------------------ 定义列表
    def _read_definition_list(self, tag: Tag, doc: Document) -> None:
        for child in tag.children:
            if not isinstance(child, Tag):
                continue
            if child.name == "dt":
                doc.blocks.append(Paragraph(children=[
                    Text(text=child.get_text(" ", strip=True), bold=True)]))
            elif child.name == "dd":
                inlines = self._inlines(child)
                if inlines:
                    doc.blocks.append(Paragraph(children=inlines))

    # ------------------------------------------------------------------ 行内
    def _inlines(self, tag: Tag) -> list:
        out: list = []
        style = dict(bold=False, italic=False, underline=False, strike=False,
                     code=False, color=None, superscript=False, subscript=False)
        for child in tag.children:
            self._inline_node(child, style, out)
        return _merge_adjacent_text(out)

    def _inline_node(self, node, style: dict, out: list) -> None:
        if isinstance(node, NavigableString):
            text = _WS_RE.sub(" ", str(node)).replace("\xa0", " ")
            if text:
                out.append(Text(text=text, **style))
            return
        if not isinstance(node, Tag):
            return
        name = node.name.lower()
        if name in ("script", "style", "template"):
            return
        child_style = style
        if name in ("strong", "b"):
            child_style = {**style, "bold": True}
        elif name in ("em", "i"):
            child_style = {**style, "italic": True}
        elif name == "u":
            child_style = {**style, "underline": True}
        elif name in ("s", "del", "strike"):
            child_style = {**style, "strike": True}
        elif name in ("code", "kbd", "samp"):
            child_style = {**style, "code": True}
        elif name == "sup":
            child_style = {**style, "superscript": True}
        elif name == "sub":
            child_style = {**style, "subscript": True}
        elif name == "font":
            child_style = dict(style)
            color = (node.get("color") or "").strip()
            if color:
                child_style["color"] = color
        elif node.get("style"):
            child_style = self._apply_inline_style(node.get("style", ""), style)

        if name == "br":
            out.append(LineBreak())
            return
        if name == "img":
            out.append(self._image_inline(node))
            return
        if name == "a":
            self._inline_anchor(node, child_style, out)
            return
        if name == "picture":
            img = node.find("img")
            if img is not None:
                out.append(self._image_inline(img))
            return
        for child in node.children:
            self._inline_node(child, child_style, out)

    def _inline_anchor(self, node: Tag, style: dict, out: list) -> None:
        href = (node.get("href") or "").strip()
        title = node.get("title")
        if not href or href.lower().startswith("javascript:"):
            for child in node.children:
                self._inline_node(child, style, out)
            return
        children: list = []
        for child in node.children:
            self._inline_node(child, style, children)
        if not children:
            children = [Text(text=node.get_text(" ", strip=True) or href)]
        out.append(Link(target=href, children=children, title=title))

    @staticmethod
    def _apply_inline_style(css: str, style: dict) -> dict:
        new = dict(style)
        for decl in css.split(";"):
            if ":" not in decl:
                continue
            prop, _, value = decl.partition(":")
            prop = prop.strip().lower()
            value = value.strip().lower()
            if prop == "font-weight":
                if value in ("bold", "bolder") or (value.isdigit() and int(value) >= 600):
                    new["bold"] = True
                elif value in ("normal", "lighter") or (value.isdigit() and int(value) < 600):
                    new["bold"] = False
            elif prop == "font-style":
                new["italic"] = value in ("italic", "oblique")
            elif prop == "text-decoration":
                if "underline" in value:
                    new["underline"] = True
                if "line-through" in value:
                    new["strike"] = True
            elif prop == "color" and value and value not in ("inherit", "initial"):
                new["color"] = value
        return new

    # ------------------------------------------------------------------ 图片
    def _image_inline(self, node: Tag) -> ImageInline:
        src = (node.get("src") or node.get("data-src") or "").strip()
        alt = node.get("alt") or ""
        data = self._load_image_bytes(src)
        mime = "image/png"
        if is_data_uri(src):
            try:
                data, mime = parse_data_uri(src)
            except Exception:  # noqa: BLE001
                data = None
        elif src:
            mime = _MIME_BY_EXT.get(Path(src.split("?")[0]).suffix.lower(), "image/png")
        return ImageInline(src=src, alt=alt, data=data, mime=mime,
                           width_px=_int_attr(node, "width", None),
                           height_px=_int_attr(node, "height", None))

    def _image_block(self, node: Tag, figure: Tag) -> ImageBlock:
        inline = self._image_inline(node)
        caption_tag = figure.find("figcaption")
        return ImageBlock(src=inline.src, alt=inline.alt, data=inline.data,
                          mime=inline.mime, width_px=inline.width_px,
                          height_px=inline.height_px,
                          caption=(caption_tag.get_text(" ", strip=True)
                                   if caption_tag else None))

    def _load_image_bytes(self, src: str) -> bytes | None:
        if not src or is_url(src) or is_data_uri(src):
            return None
        path = resolve_local_path(src, self.base_dir)
        if path is None:
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None

    # ------------------------------------------------------------------ 片段
    def parse_fragment(self, html: str, base_dir: Path,
                       doc: Document | None = None) -> list[Block]:
        """解析一段 HTML 片段为块列表（供 Markdown 中的内嵌 HTML 复用）。"""
        self.base_dir = Path(base_dir)
        soup = BeautifulSoup(html, "html.parser")
        sink = _BlockSink([])
        self._walk_children(soup, sink)
        return sink.blocks

    def parse_inline_fragment(self, html: str, base_dir: Path) -> list:
        """解析一段 HTML 片段为行内列表。"""
        self.base_dir = Path(base_dir)
        soup = BeautifulSoup(html, "html.parser")
        out: list = []
        style = dict(bold=False, italic=False, underline=False, strike=False,
                     code=False, color=None, superscript=False, subscript=False)
        for child in soup.children:
            self._inline_node(child, style, out)
        return _merge_adjacent_text(out)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

class _BlockSink:
    """轻量 Document 替身：收集块，供 ``_walk_children`` 写入嵌套容器。"""

    def __init__(self, blocks: list[Block]) -> None:
        self.blocks = blocks

    def warn(self, message: str) -> None:  # pragma: no cover - 兼容接口
        pass


def _int_attr(node: Tag, name: str, default: int | None) -> int | None:
    raw = node.get(name)
    if raw is None:
        return default
    value = str(raw).strip()
    if value.endswith("%"):
        return default
    match = re.match(r"(\d+)", value)
    return int(match.group(1)) if match else default


def _paragraph_align(tag: Tag) -> str | None:
    align = (tag.get("align") or "").strip().lower()
    if align in ("left", "center", "right", "justify"):
        return align
    style = tag.get("style") or ""
    match = re.search(r"text-align\s*:\s*(left|center|right|justify)", style, re.I)
    return match.group(1).lower() if match else None


def _looks_like_attachment(href: str, node: Tag) -> bool:
    if not href or is_url(href) or is_data_uri(href):
        return False
    ext = ext_of(href.split("?")[0].split("#")[0])
    if ext not in _ATTACHMENT_EXTS:
        return False
    return node.find(["img", "picture"]) is None


def _merge_adjacent_text(inlines: list) -> list:
    """合并样式相同的相邻文本片段，减少碎片化输出。"""
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
