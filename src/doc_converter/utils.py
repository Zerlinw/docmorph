"""doc_converter.utils —— 通用工具函数。"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse

_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z._\-]+")


def sanitize_filename(name: str, fallback: str = "file") -> str:
    """把任意字符串清理为安全的文件名。"""
    name = (name or "").strip().replace("\\", "/").split("/")[-1]
    name = _SAFE_NAME_RE.sub("_", name).strip("._")
    return name or fallback


def short_hash(data: bytes, length: int = 8) -> str:
    return hashlib.sha256(data).hexdigest()[:length]


def is_url(src: str) -> bool:
    try:
        parsed = urlparse(src)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https", "ftp") and bool(parsed.netloc)


def is_data_uri(src: str) -> bool:
    return isinstance(src, str) and src.startswith("data:")


def parse_data_uri(src: str) -> tuple[bytes, str]:
    """解析 data URI -> (bytes, mime)。"""
    header, _, payload = src.partition(",")
    meta = header[len("data:"):]
    is_base64 = meta.endswith(";base64")
    mime = meta[:-len(";base64")] if is_base64 else (meta.split(";")[0] or "text/plain")
    if is_base64:
        return base64.b64decode(payload), mime
    from urllib.parse import unquote_to_bytes
    return unquote_to_bytes(payload), mime


def make_data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def guess_ext(mime: str, default: str = "png") -> str:
    mapping = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/gif": "gif",
        "image/webp": "webp",
        "image/bmp": "bmp",
        "image/tiff": "tiff",
        "image/svg+xml": "svg",
        "application/pdf": "pdf",
        "application/zip": "zip",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/msword": "doc",
        "text/html": "html",
        "text/plain": "txt",
        "text/markdown": "md",
    }
    return mapping.get((mime or "").lower(), default)


def ext_of(path: str | Path) -> str:
    return Path(path).suffix.lower().lstrip(".")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def human_size(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024:
            return f"{num:.1f}{unit}" if unit != "B" else f"{num}B"
        num /= 1024
    return f"{num:.1f}TB"


def inlines_plain_text(inlines) -> str:
    """提取行内元素的纯文本（用于标题去重等判断）。"""
    from .model import ImageInline, LineBreak, Link, Text
    parts: list[str] = []
    for inline in inlines:
        if isinstance(inline, Text):
            parts.append(inline.text)
        elif isinstance(inline, Link):
            parts.append(inlines_plain_text(inline.children))
        elif isinstance(inline, LineBreak):
            parts.append("\n")
    return "".join(parts)


def needs_title_header(doc) -> bool:
    """判断是否需要在正文前额外输出文档标题。

    若文档首个块已经是与 ``doc.title`` 相同的一级标题，则无需重复输出。
    """
    from .model import Heading
    if not doc.title or not doc.blocks:
        return bool(doc.title)
    first = doc.blocks[0]
    if isinstance(first, Heading) and first.level <= 1:
        return inlines_plain_text(first.children).strip() != doc.title.strip()
    return True
