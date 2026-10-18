"""doc_converter.model —— 与具体格式无关的文档中间表示（IR）。

设计思路：所有读取器（DOCX / HTML / Markdown / PDF）都先把源文档解析为
统一的 ``Document``（块级结构 + 行内样式 + 二进制资源），所有写入器再从
``Document`` 渲染为各自的目标格式。互转 = 读 -> IR -> 写，天然支持
N x N 种格式组合，新增一种格式只需实现一对读写器。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# 行内元素
# ---------------------------------------------------------------------------

@dataclass
class Inline:
    """行内元素基类。"""


@dataclass
class Text(Inline):
    """带样式的文本片段。"""

    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    code: bool = False
    color: Optional[str] = None


@dataclass
class Link(Inline):
    """超链接。"""

    target: str
    children: list[Inline] = field(default_factory=list)
    title: Optional[str] = None


@dataclass
class ImageInline(Inline):
    """行内图片（与段落文字混排）。"""

    src: str = ""                                  # 原始引用（本地路径/URL/data URI）
    alt: str = ""
    data: Optional[bytes] = None                   # 已解码的二进制内容
    mime: str = "image/png"
    width_emu: Optional[int] = None                # DOCX 中的原始尺寸（EMU）
    height_emu: Optional[int] = None


@dataclass
class LineBreak(Inline):
    """段内软换行。"""


# ---------------------------------------------------------------------------
# 块级元素
# ---------------------------------------------------------------------------

@dataclass
class Block:
    """块级元素基类。"""


@dataclass
class Heading(Block):
    level: int                                    # 1..6
    children: list[Inline] = field(default_factory=list)


@dataclass
class Paragraph(Block):
    children: list[Inline] = field(default_factory=list)


@dataclass
class ListItem:
    children: list[Inline] = field(default_factory=list)
    level: int = 0


@dataclass
class ListBlock(Block):
    ordered: bool = False
    items: list[ListItem] = field(default_factory=list)
    start: int = 1


@dataclass
class TableCell:
    children: list[Inline] = field(default_factory=list)


@dataclass
class TableBlock(Block):
    rows: list[list[TableCell]] = field(default_factory=list)
    header: bool = True                           # 首行是否为表头
    caption: Optional[str] = None


@dataclass
class ImageBlock(Block):
    """块级图片（独立成段，可带题注）。"""

    src: str = ""
    alt: str = ""
    caption: Optional[str] = None
    data: Optional[bytes] = None
    mime: str = "image/png"
    width_emu: Optional[int] = None
    height_emu: Optional[int] = None


@dataclass
class CodeBlock(Block):
    text: str = ""
    lang: Optional[str] = None


@dataclass
class QuoteBlock(Block):
    children: list[Inline] = field(default_factory=list)


@dataclass
class FlowchartBlock(Block):
    """流程图 / 结构图。

    不同源格式对"流程"的表达能力不同：DOCX 中可能是组合形状（SmartArt /
    文本框 + 连接线），Markdown 中可能是 Mermaid 代码块。统一抽象为
    ``nodes`` + ``edges``；无法恢复连接关系时按顺序连边（降级）。
    """

    nodes: list[str] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    title: Optional[str] = None


@dataclass
class Attachment(Block):
    """嵌入的文件附件（DOCX OLE 对象、PDF EmbeddedFile 等）。"""

    name: str
    data: Optional[bytes] = None
    mime: str = "application/octet-stream"
    caption: Optional[str] = None


@dataclass
class HorizontalRule(Block):
    pass


@dataclass
class Document:
    blocks: list[Block] = field(default_factory=list)
    title: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    # 元信息：源格式、源文件路径，便于写入器做格式特定优化
    source_format: Optional[str] = None
    source_path: Optional[str] = None

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def is_empty(self) -> bool:
        return not any(_block_has_content(b) for b in self.blocks)


def _block_has_content(block: Block) -> bool:
    if isinstance(block, Heading):
        return bool(block.children)
    if isinstance(block, Paragraph):
        return bool(block.children)
    if isinstance(block, QuoteBlock):
        return bool(block.children)
    if isinstance(block, ListBlock):
        return bool(block.items)
    if isinstance(block, TableBlock):
        return bool(block.rows)
    if isinstance(block, ImageBlock):
        return bool(block.data or block.src)
    if isinstance(block, CodeBlock):
        return bool(block.text.strip())
    if isinstance(block, FlowchartBlock):
        return bool(block.nodes)
    if isinstance(block, Attachment):
        return bool(block.data)
    return True  # HorizontalRule 等
