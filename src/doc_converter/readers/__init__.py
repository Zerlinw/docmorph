"""doc_converter.readers —— 各源格式读取器。"""

from .base import BaseReader, get_reader, register_reader
from .docx_reader import DocxReader
from .html_reader import HtmlReader
from .markdown_reader import MarkdownReader
from .pdf_reader import PdfReader

__all__ = ["BaseReader", "get_reader", "register_reader",
           "DocxReader", "HtmlReader", "MarkdownReader", "PdfReader"]
