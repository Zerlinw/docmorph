"""doc_converter.assets —— 图片 / 附件等二进制资源的统一管理。

资源以三种模式落地，由目标写入器决定：
  - ``file``    保存为独立文件，返回相对引用（Markdown / HTML / PDF 用）
  - ``datauri`` 内联为 data URI（HTML / PDF 自包含场景）
  - ``embed``   不落盘，由写入器以二进制方式嵌入（DOCX 用，保留 bytes）

附件是例外：任何目标格式都无法"内嵌渲染"任意二进制，因此附件始终落盘到
``<输出名>_assets/``，并以相对路径引用。
"""

from __future__ import annotations

import io
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .utils import (ensure_dir, ext_of, guess_ext, is_data_uri, is_url,
                    make_data_uri, parse_data_uri, resolve_local_path,
                    sanitize_filename, short_hash)

MODE_FILE = "file"
MODE_DATAURI = "datauri"
MODE_EMBED = "embed"

DEFAULT_MAX_REMOTE_BYTES = 32 * 1024 * 1024
DEFAULT_TIMEOUT = 10.0


@dataclass
class Asset:
    """一个已落盘的资源。"""

    ref: str                    # 供文本格式引用的字符串（相对路径 / data URI / 原名）
    data: bytes                 # 二进制内容（embed 模式下由写入器直接使用）
    mime: str
    path: Path | None = None    # file 模式下的实际文件路径
    remote: bool = False        # 是否来自远程 URL
    error: str | None = None    # 解析失败原因（写入器据此输出占位说明）


class AssetStore:
    """把文档中的二进制资源统一写入 ``<输出目录>/<文件名>_assets/``。"""

    def __init__(self, out_dir: Path, stem: str, mode: str = MODE_FILE,
                 source_dir: Path | None = None,
                 fetch_remote: bool = False,
                 max_remote_bytes: int = DEFAULT_MAX_REMOTE_BYTES,
                 timeout: float = DEFAULT_TIMEOUT,
                 warn: Callable[[str], None] | None = None) -> None:
        self.out_dir = Path(out_dir)
        self.stem = stem
        self.mode = mode
        self.source_dir = Path(source_dir) if source_dir else None
        self.fetch_remote = fetch_remote
        self.max_remote_bytes = max_remote_bytes
        self.timeout = timeout
        self._warn = warn
        self.assets_dir = self.out_dir / f"{stem}_assets"
        self._seen: dict[str, Asset] = {}
        self._counter = 0
        self.files: list[Path] = []

    # -- 图片 -------------------------------------------------------------
    def add_image(self, data: bytes, mime: str, hint: str = "image") -> Asset:
        key = f"img:{short_hash(data)}"
        cached = self._seen.get(key)
        if cached:
            return cached
        self._counter += 1
        ext = guess_ext(mime)
        if ext == "bin":                     # 未知 MIME 时按字节签名猜测
            ext = _sniff_ext(data)
        name = (f"{sanitize_filename(hint, 'image')}-{self._counter}"
                f"-{short_hash(data, 6)}.{ext}")
        return self._store(key, data, mime, name)

    def resolve_image(self, src: str, data: bytes | None, mime: str,
                      hint: str = "image") -> Asset:
        """按引用解析图片：URL 可按需下载，其余从 data URI / 本地路径读取。"""
        if data is None and is_data_uri(src):
            try:
                data, mime = parse_data_uri(src)
            except Exception as exc:  # noqa: BLE001
                return self._missing(src, mime, f"data URI 解析失败: {exc}")
        if data is None and is_url(src):
            if not self.fetch_remote:
                return Asset(ref=src, data=b"", mime=mime, remote=True)
            fetched, error = self._download(src)
            if fetched is None:
                return self._missing(src, mime, error)
            data = fetched
            mime = _guess_mime_from_url(src, data)
        if data is None and src:
            path = resolve_local_path(src, self.source_dir)
            if path is not None:
                try:
                    data = path.read_bytes()
                except OSError as exc:
                    return self._missing(src, mime, f"读取失败: {exc}")
                mime = _guess_mime_from_url(str(path), data) or mime
        if data is None:
            return self._missing(src, mime, "无法解析图片资源")
        return self.add_image(data, mime or "image/png", hint)

    # -- 附件 -------------------------------------------------------------
    def add_attachment(self, name: str, data: bytes,
                       mime: str = "application/octet-stream") -> Asset:
        """附件始终落盘并返回相对引用（保证目标文档中的链接可用）。"""
        safe = sanitize_filename(name, "attachment")
        if not ext_of(safe):
            safe = f"{safe}.{guess_ext(mime, 'bin')}"
        name_on_disk = f"{short_hash(data, 6)}-{safe}"
        key = f"att:{name_on_disk}"
        cached = self._seen.get(key)
        if cached:
            return cached
        ensure_dir(self.assets_dir)
        path = self.assets_dir / name_on_disk
        if not path.exists():
            path.write_bytes(data)
        self.files.append(path)
        asset = Asset(ref=f"{self.stem}_assets/{name_on_disk}", data=data,
                      mime=mime, path=path)
        self._seen[key] = asset
        return asset

    # -- 内部 --------------------------------------------------------------
    def _store(self, key: str, data: bytes, mime: str, name: str) -> Asset:
        if self.mode == MODE_DATAURI:
            asset = Asset(ref=make_data_uri(data, mime), data=data, mime=mime)
        elif self.mode == MODE_EMBED:
            asset = Asset(ref=name, data=data, mime=mime)
        else:
            ensure_dir(self.assets_dir)
            path = self.assets_dir / name
            if not path.exists() or path.stat().st_size != len(data):
                path.write_bytes(data)
            self.files.append(path)
            asset = Asset(ref=f"{self.stem}_assets/{name}", data=data, mime=mime,
                          path=path)
        self._seen[key] = asset
        return asset

    def _download(self, url: str) -> tuple[bytes | None, str | None]:
        request = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (compatible; doc-converter)"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                length = resp.headers.get("Content-Length")
                if length and int(length) > self.max_remote_bytes:
                    return None, f"远程图片超过大小上限（{length} 字节）"
                data = resp.read(self.max_remote_bytes + 1)
        except (urllib.error.URLError, socket.timeout, ValueError, OSError) as exc:
            return None, f"下载失败: {exc}"
        if len(data) > self.max_remote_bytes:
            return None, "远程图片超过大小上限"
        if not data:
            return None, "远程图片内容为空"
        return data, None

    def _missing(self, src: str, mime: str, reason: str) -> Asset:
        if self._warn:
            self._warn(f"图片无法获取（{reason}）：{src[:160]}")
        return Asset(ref=src or "", data=b"", mime=mime, error=reason)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _sniff_ext(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:2] == b"BM":
        return "bmp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    return "png"


def _guess_mime_from_url(url: str, data: bytes) -> str:
    ext = Path(url.split("?")[0].split("#")[0]).suffix.lower().lstrip(".")
    mapping = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
        "tif": "image/tiff", "tiff": "image/tiff", "svg": "image/svg+xml",
        "ico": "image/x-icon", "avif": "image/avif",
    }
    if ext in mapping:
        return mapping[ext]
    return {
        b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg",
        b"GIF8": "image/gif", b"RIFF": "image/webp",
    }.get(data[:3] if data[:3] != b"RIF" else data[:4], "image/png")


def image_size_px(data: bytes) -> tuple[int, int] | None:
    """读取图片像素尺寸（不依赖 Pillow 的高级解码路径）。"""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:  # noqa: BLE001
        return None


def is_supported_by_docx(mime: str) -> bool:
    """python-docx 可嵌入的图片格式（SVG/EMF 等需要特殊处理）。"""
    return (mime or "").lower() in {
        "image/png", "image/jpeg", "image/gif", "image/bmp", "image/tiff",
        "image/x-emf", "image/x-wmf",
    }
