"""doc_converter.writers.base —— 写入器抽象基类与注册表。"""

from __future__ import annotations

from pathlib import Path
from typing import Type

from ..assets import MODE_DATAURI, MODE_EMBED, MODE_FILE, AssetStore
from ..model import Document


class BaseWriter:
    """写入器接口：把统一 ``Document`` 渲染为一种目标格式。"""

    #: 目标扩展名（不含点）
    extension: str = ""
    #: 资源落地模式
    asset_mode: str = MODE_FILE

    def write(self, doc: Document, out_path: Path,
              assets: AssetStore) -> Path:
        raise NotImplementedError


_REGISTRY: dict[str, Type[BaseWriter]] = {}


def register_writer(writer_cls: Type[BaseWriter]) -> Type[BaseWriter]:
    _REGISTRY[writer_cls.extension.lower()] = writer_cls
    return writer_cls


def get_writer(fmt: str) -> BaseWriter:
    from .docx_writer import DocxWriter
    from .html_writer import HtmlWriter
    from .markdown_writer import MarkdownWriter
    from .pdf_writer import PdfWriter

    for cls in (MarkdownWriter, HtmlWriter, DocxWriter, PdfWriter):
        register_writer(cls)

    fmt = (fmt or "").lower().lstrip(".")
    aliases = {
        "doc": "docx", "word": "docx",
        "htm": "html", "xhtml": "html",
        "markdown": "md", "mdown": "md",
    }
    key = aliases.get(fmt, fmt)
    cls = _REGISTRY.get(key)
    if cls is None:
        from ..errors import UnsupportedFormatError
        raise UnsupportedFormatError(fmt, sorted(set(_REGISTRY)))
    return cls()
