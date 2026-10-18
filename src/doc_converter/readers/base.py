"""doc_converter.readers.base —— 读取器抽象基类与注册表。"""

from __future__ import annotations

from pathlib import Path
from typing import Type

from ..model import Document
from ..errors import ReadError


class BaseReader:
    """读取器接口：把一种源格式解析为统一的 ``Document``。"""

    #: 支持的扩展名（不含点）
    extensions: tuple[str, ...] = ()

    def read(self, path: Path, doc: Document) -> Document:
        raise NotImplementedError

    # -- 工具 --------------------------------------------------------------
    def _fail(self, path: Path, exc: Exception) -> ReadError:
        return ReadError(str(path), f"{type(exc).__name__}: {exc}")


_REGISTRY: dict[str, Type[BaseReader]] = {}


def register_reader(reader_cls: Type[BaseReader]) -> Type[BaseReader]:
    for ext in reader_cls.extensions:
        _REGISTRY[ext.lower()] = reader_cls
    return reader_cls


def get_reader(fmt: str) -> BaseReader:
    """按格式名（如 ``docx``）实例化读取器。"""
    from .docx_reader import DocxReader
    from .html_reader import HtmlReader
    from .markdown_reader import MarkdownReader
    from .pdf_reader import PdfReader

    for cls in (DocxReader, HtmlReader, MarkdownReader, PdfReader):
        register_reader(cls)

    fmt = (fmt or "").lower().lstrip(".")
    aliases = {
        "doc": "docx", "word": "docx",
        "htm": "html", "xhtml": "html",
        "md": "markdown", "markdown": "markdown", "mdown": "markdown",
        "pdf": "pdf",
    }
    key = aliases.get(fmt, fmt)
    if key == "markdown":
        key = "md"
    cls = _REGISTRY.get(key)
    if cls is None:
        from ..errors import UnsupportedFormatError
        raise UnsupportedFormatError(fmt, sorted(set(_REGISTRY)))
    return cls()
