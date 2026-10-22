"""doc_converter.utils —— 通用工具函数。"""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_filename(name: str, fallback: str = "file") -> str:
    """把任意字符串清理为安全的文件名（保留中日韩字符）。"""
    name = (name or "").strip().replace("\\", "/").split("/")[-1]
    name = _CTRL_RE.sub("", name)
    name = _SAFE_NAME_RE.sub("_", name).strip("._")
    if not name:
        return fallback
    # Windows 保留设备名
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL"} | {
            f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}:
        name = f"_{name}"
    return name[:120]


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


def is_file_uri(src: str) -> bool:
    return isinstance(src, str) and src.startswith("file:")


def file_uri_to_path(src: str) -> Path | None:
    try:
        parsed = urlparse(src)
    except ValueError:
        return None
    path = unquote(parsed.path or "")
    # Windows: /C:/xxx -> C:/xxx
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return Path(path) if path else None


def parse_data_uri(src: str) -> tuple[bytes, str]:
    """解析 data URI -> (bytes, mime)。"""
    header, _, payload = src.partition(",")
    meta = header[len("data:"):]
    is_base64 = meta.endswith(";base64")
    mime = meta[:-len(";base64")] if is_base64 else (meta.split(";")[0] or "text/plain")
    if is_base64:
        return base64.b64decode(payload, validate=False), mime or "application/octet-stream"
    from urllib.parse import unquote_to_bytes
    return unquote_to_bytes(payload), mime or "text/plain"


def make_data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime or 'application/octet-stream'};base64," + \
        base64.b64encode(data).decode("ascii")


def guess_ext(mime: str, default: str = "png") -> str:
    mapping = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
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
    key = (mime or "").lower().split(";")[0].strip()
    if key in mapping:
        return mapping[key]
    guessed = mimetypes.guess_extension(key) if key else None
    if guessed:
        return guessed.lstrip(".").replace("jpe", "jpg")
    return default


def ext_of(path: str | Path) -> str:
    return Path(path).suffix.lower().lstrip(".")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def human_size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(value) < 1024:
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{value:.1f}TB"


def inlines_plain_text(inlines) -> str:
    """提取行内元素的纯文本（用于标题去重、链接文字等判断）。"""
    from .model import ImageInline, LineBreak, Link, Text
    parts: list[str] = []
    for inline in inlines:
        if isinstance(inline, Text):
            parts.append(inline.text)
        elif isinstance(inline, Link):
            parts.append(inlines_plain_text(inline.children))
        elif isinstance(inline, LineBreak):
            parts.append("\n")
        elif isinstance(inline, ImageInline):
            parts.append(inline.alt or "")
    return "".join(parts)


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def needs_title_header(doc) -> bool:
    """判断是否需要在正文前额外输出文档标题。

    若文档首个块已经是与 ``doc.title`` 相同的一级标题，则无需重复输出。
    """
    from .model import Heading
    if not doc.title:
        return False
    if not doc.blocks:
        return True
    first = doc.blocks[0]
    if isinstance(first, Heading) and first.level <= 1:
        return normalize_ws(inlines_plain_text(first.children)) != normalize_ws(doc.title)
    return True


def resolve_local_path(src: str, base_dir: Path | None) -> Path | None:
    """把文档中的引用解析为本地路径（相对 ``base_dir``，也支持 file://）。"""
    if not src:
        return None
    if is_file_uri(src):
        return file_uri_to_path(src)
    if is_url(src) or is_data_uri(src):
        return None
    raw = unquote(src.split("?")[0].split("#")[0])
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate if candidate.exists() else None
    search_dirs = []
    if base_dir is not None:
        search_dirs.append(Path(base_dir))
    search_dirs.append(Path.cwd())
    for directory in search_dirs:
        path = (directory / candidate)
        if path.exists():
            return path
    return None


def split_text_lines(text: str) -> list[str]:
    return [line for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
