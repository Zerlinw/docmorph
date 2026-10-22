"""doc_converter.writers.pdf_writer —— PDF 写入器。

策略：先把 IR 渲染为带样式的 HTML（复用 :class:`HtmlWriter`），再交给
:mod:`doc_converter.pdf_engine` 排版为 PDF。

资源以 data URI 内联，保证版式引擎无需解析相对路径；附件仍落盘到
``<输出名>_assets/``，同时通过 pypdf 嵌入 PDF 文件本身，做到"自包含 + 可提取"。
"""

from __future__ import annotations

from pathlib import Path

from ..assets import MODE_DATAURI, AssetStore
from ..errors import DependencyError, WriteError
from ..model import Attachment, Document, iter_blocks
from ..pdf_engine import PAGE_SIZES, render_pdf
from .base import BaseWriter, register_writer
from .html_writer import HtmlWriter, print_stylesheet


@register_writer
class PdfWriter(BaseWriter):
    extension = "pdf"

    def __init__(self, engine: str = "auto", page_size: str = "A4") -> None:
        self.engine = engine
        self.page_size = page_size if page_size in PAGE_SIZES else "A4"
        self.used_engine: str | None = None

    def write(self, doc: Document, out_path: Path, assets: AssetStore) -> Path:
        # PDF 需要自包含资源：图片转为 data URI，远程图片按需下载
        embedded = AssetStore(
            out_path.parent, out_path.stem, mode=MODE_DATAURI,
            source_dir=assets.source_dir, fetch_remote=True, warn=doc.warn)
        html_writer = HtmlWriter()
        html_writer.render_mermaid = False     # PDF 不执行脚本，使用文本结构
        html = html_writer.render_to_string(doc, embedded)
        html = inject_print_stylesheet(html, self.page_size)

        try:
            self.used_engine = render_pdf(html, out_path, engine=self.engine,
                                          page_size=self.page_size, warn=doc.warn)
        except DependencyError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise WriteError(str(out_path), str(exc)) from exc

        self._finalize(out_path, doc)
        return out_path

    # ------------------------------------------------------------------ 后处理
    def _finalize(self, out_path: Path, doc: Document) -> None:
        attachments = [block for block in iter_blocks(doc.blocks)
                       if isinstance(block, Attachment) and block.data]
        metadata = _pdf_metadata(doc)
        if not attachments and not metadata:
            return
        try:
            from pypdf import PdfReader, PdfWriter
        except ImportError:  # pragma: no cover
            return
        temp = out_path.parent / (out_path.name + ".tmp")
        try:
            reader = PdfReader(str(out_path))
            writer = PdfWriter()
            writer.append(reader)
            if metadata:
                writer.add_metadata(metadata)
            for attachment in attachments:
                try:
                    writer.add_attachment(attachment.name, attachment.data)
                except Exception as exc:  # noqa: BLE001
                    doc.warn(f"PDF: 嵌入附件 {attachment.name} 失败: {exc}")
            with open(temp, "wb") as handle:
                writer.write(handle)
            temp.replace(out_path)
        except Exception as exc:  # noqa: BLE001
            doc.warn(f"PDF: 写入元数据/附件失败（不影响正文）: {exc}")
            temp.unlink(missing_ok=True)


def inject_print_stylesheet(html: str, page_size: str = "A4") -> str:
    style = f"<style>{print_stylesheet(page_size)}</style>"
    if "</head>" in html:
        return html.replace("</head>", style + "</head>", 1)
    return style + html


def _pdf_metadata(doc: Document) -> dict[str, str]:
    meta = doc.metadata or {}
    if not doc.title and not meta:
        return {}
    result: dict[str, str] = {"/Creator": "doc-converter"}
    if doc.title:
        result["/Title"] = doc.title
    for key, pdf_key in (("author", "/Author"), ("subject", "/Subject"),
                         ("keywords", "/Keywords")):
        value = meta.get(key)
        if value:
            result[pdf_key] = value
    lang = meta.get("lang")
    if lang:
        result["/Lang"] = lang
    return result
