"""doc_converter.readers.html_reader —— HTML 读取器。

覆盖：标题层级、段落、列表（含嵌套）、表格、超链接、图片（本地相对路径 /
data URI / 远程 URL）、代码块、引用、水平线，以及指向非页面文件的附件链接。
"""

from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup, Tag

from ..model import (Attachment, CodeBlock, Document, FlowchartBlock, Heading,
                     HorizontalRule, ImageBlock, ImageInline, LineBreak, Link,
                     ListBlock, ListItem, Paragraph, QuoteBlock, TableBlock,
                     TableCell, Text)
from ..utils import guess_ext, is_data_uri, is_url, parse_data_uri
from .base import BaseReader, register_reader

_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table",
               "pre", "blockquote", "hr", "div", "section", "article",
               "figure", "main", "header", "footer", "aside", "dl"}

_ATTACHMENT_EXTS = {
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip", "rar", "7z",
    "tar", "gz", "csv", "json", "xml", "txt", "png", "jpg", "jpeg", "gif",
    "svg", "mp3", "mp4", "apk", "exe",
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

        title_tag = soup.find("title")
        if title_tag and title_tag.get_text(strip=True):
            doc.title = title_tag.get_text(strip=True)

        root = soup.body or soup
        for child in root.children:
            if isinstance(child, Tag):
                self._walk(child, doc)
        if doc.is_empty():
            from ..errors import EmptyDocumentError
            raise EmptyDocumentError(str(path))
        return doc

    # ------------------------------------------------------------------ 块级
    def _walk(self, tag: Tag, doc: Document) -> None:
        name = tag.name.lower()
        if name in ("script", "style", "head", "meta", "link", "noscript"):
            return
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            doc.blocks.append(Heading(level=int(name[1]),
                                      children=self._inlines(tag)))
        elif name == "p":
            doc.blocks.append(Paragraph(children=self._inlines(tag)))
        elif name in ("ul", "ol"):
            doc.blocks.append(self._read_list(tag, ordered=name == "ol"))
        elif name == "table":
            doc.blocks.append(self._read_table(tag))
        elif name == "pre":
            code = tag.find("code")
            lang = None
            if code is not None:
                cls = " ".join(code.get("class") or [])
                if "language-" in cls:
                    lang = cls.split("language-")[-1].split()[0]
            doc.blocks.append(CodeBlock(text=tag.get_text(), lang=lang))
        elif name == "blockquote":
            doc.blocks.append(QuoteBlock(children=self._inlines(tag)))
        elif name == "hr":
            doc.blocks.append(HorizontalRule())
        elif name == "figure":
            img = tag.find("img")
            if img is not None:
                doc.blocks.append(self._image_block(img, tag))
            else:
                self._walk_children(tag, doc)
        elif name in ("div", "section", "article", "main", "header", "footer",
                      "aside"):
            self._walk_children(tag, doc)
        else:
            # 未知块级容器：递归，保证内容不丢失
            self._walk_children(tag, doc)

    def _walk_children(self, tag: Tag, doc: Document) -> None:
        for child in tag.children:
            if isinstance(child, Tag):
                self._walk(child, doc)

    # ------------------------------------------------------------------ 列表
    def _read_list(self, tag: Tag, ordered: bool, level: int = 0) -> ListBlock:
        items: list[ListItem] = []
        start = 1
        if ordered:
            try:
                start = int(tag.get("start") or 1)
            except (TypeError, ValueError):
                start = 1
        for li in tag.find_all("li", recursive=False):
            children: list = []
            for child in li.children:
                if isinstance(child, Tag):
                    if child.name in ("ul", "ol"):
                        nested = self._read_list(child, ordered=child.name == "ol",
                                                 level=level + 1)
                        for n_item in nested.items:
                            items.append(ListItem(children=n_item.children,
                                                  level=n_item.level + 1))
                    else:
                        children.extend(self._inlines(child))
                else:
                    text = str(child)
                    if text.strip():
                        children.append(Text(text=text.strip()))
            items.append(ListItem(children=children, level=level))
        return ListBlock(ordered=ordered, items=items, start=start)

    # ------------------------------------------------------------------ 表格
    def _read_table(self, tag: Tag) -> TableBlock:
        rows: list[list[TableCell]] = []
        for tr in tag.find_all("tr"):
            cells: list[TableCell] = []
            for cell in tr.find_all(["td", "th"], recursive=False):
                cells.append(TableCell(children=self._inlines(cell)))
            if cells:
                rows.append(cells)
        has_thead = tag.find("thead") is not None
        first_is_th = bool(tag.find("tr") and tag.find("tr").find("th"))
        return TableBlock(rows=rows, header=has_thead or first_is_th)

    # ------------------------------------------------------------------ 行内
    def _inlines(self, tag: Tag) -> list:
        # 递归处理子节点，以正确支持样式嵌套与附件链接
        out: list = []
        self._inline_node(tag, dict(bold=False, italic=False, underline=False,
                                    strike=False, code=False, color=None), out)
        return out

    def _inline_node(self, node, style: dict, out: list) -> None:
        if isinstance(node, str):
            text = node
            if text:
                out.append(Text(text=text, **style))
            return
        name = node.name.lower()
        if name in ("script", "style"):
            return
        if name == "br":
            out.append(LineBreak())
            return
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
        elif name == "span" and node.get("style"):
            child_style = self._apply_inline_style(node.get("style", ""), style)
        else:
            child_style = style

        if name == "img":
            out.append(self._image_inline(node))
            return
        if name == "a":
            href = (node.get("href") or "").strip()
            text = node.get_text()
            attachment = self._maybe_attachment(node, href, text)
            if attachment is not None:
                out.append(attachment)
                return
            children: list = []
            self._inline_node_children(node, child_style, children)
            if href:
                out.append(Link(target=href, children=children,
                                title=node.get("title")))
            else:
                out.extend(children)
            return

        self._inline_node_children(node, child_style, out)

    def _inline_node_children(self, node, style: dict, out: list) -> None:
        for child in node.children:
            self._inline_node(child, style, out)

    @staticmethod
    def _apply_inline_style(css: str, style: dict) -> dict:
        import re
        new = dict(style)
        for decl in css.split(";"):
            if ":" not in decl:
                continue
            prop, _, value = decl.partition(":")
            prop = prop.strip().lower()
            value = value.strip().lower()
            if prop == "font-weight" and value in ("bold", "bolder") or \
                    (prop == "font-weight" and value.isdigit() and int(value) >= 600):
                new["bold"] = True
            elif prop == "font-style" and value == "italic":
                new["italic"] = True
            elif prop == "text-decoration" and "underline" in value:
                new["underline"] = True
            elif prop == "text-decoration" and "line-through" in value:
                new["strike"] = True
            elif prop == "color":
                new["color"] = value
        return new

    # ------------------------------------------------------------------ 图片
    def _image_inline(self, node: Tag) -> ImageInline:
        src = (node.get("src") or "").strip()
        alt = node.get("alt") or ""
        data = self._load_image_bytes(src)
        mime = "image/png"
        if data is None and is_data_uri(src):
            data, mime = parse_data_uri(src)
        elif data is not None and src.lower().endswith((".jpg", ".jpeg")):
            mime = "image/jpeg"
        elif data is not None and src.lower().endswith(".gif"):
            mime = "image/gif"
        elif data is not None and src.lower().endswith(".svg"):
            mime = "image/svg+xml"
        return ImageInline(src=src, alt=alt, data=data, mime=mime)

    def _image_block(self, node: Tag, figure: Tag) -> ImageBlock:
        inline = self._image_inline(node)
        caption_tag = figure.find("figcaption")
        return ImageBlock(src=inline.src, alt=inline.alt, data=inline.data,
                          mime=inline.mime,
                          caption=caption_tag.get_text(strip=True) if caption_tag else None)

    def _load_image_bytes(self, src: str) -> bytes | None:
        if not src or is_url(src) or is_data_uri(src):
            return None
        path = (self.base_dir / src).resolve()
        try:
            if path.exists() and path.is_file():
                return path.read_bytes()
        except OSError:
            return None
        return None

    # ------------------------------------------------------------------ 附件
    @staticmethod
    def _maybe_attachment(node: Tag, href: str, text: str) -> Link | None:
        """指向可下载文件的链接建模为附件引用（保留普通链接语义）。"""
        if not href or is_url(href) or is_data_uri(href):
            return None
        ext = Path(href.split("?")[0]).suffix.lower().lstrip(".")
        if ext not in _ATTACHMENT_EXTS:
            return None
        if node.find(["img", "picture"]) is not None:
            return None
        return Link(target=href, children=[Text(text=text or href)])
