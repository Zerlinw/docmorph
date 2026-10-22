"""doc-converter —— PDF / DOCX / HTML / Markdown 任意互转的文档转换助手。

快速上手::

    from doc_converter import convert

    result = convert("report.docx", target="md", output="out/")
    print(result.output, result.warnings)

命令行::

    python -m doc_converter report.docx -t md -o out/

桌面版（打包后的 Windows 程序）: ``DocConverter.exe``。
"""

from .converter import (ConversionResult, SUPPORTED_FORMATS, convert,
                        convert_batch)
from .errors import (ConversionError, DependencyError, EmptyDocumentError,
                     FileNotFoundError_, ReadError, UnsupportedFormatError,
                     WriteError)
from .model import (Attachment, Block, CodeBlock, Document, FlowchartBlock,
                    Heading, HorizontalRule, ImageBlock, ImageInline, Inline,
                    LineBreak, Link, ListBlock, ListItem, Paragraph, QuoteBlock,
                    TableBlock, TableCell, Text)
from .pdf_engine import available_engines

__version__ = "2.0.0"

__all__ = [
    "convert", "convert_batch", "ConversionResult", "SUPPORTED_FORMATS",
    "Document", "Block", "Heading", "Paragraph", "ListBlock", "ListItem",
    "TableBlock", "TableCell", "ImageBlock", "ImageInline", "CodeBlock",
    "QuoteBlock", "FlowchartBlock", "Attachment", "HorizontalRule",
    "Text", "Link", "LineBreak", "Inline",
    "ConversionError", "UnsupportedFormatError", "ReadError", "WriteError",
    "EmptyDocumentError", "FileNotFoundError_", "DependencyError",
    "available_engines", "__version__",
]
