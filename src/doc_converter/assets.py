"""doc_converter.assets —— 图片 / 附件等二进制资源的统一管理。

资源以三种模式落地，由目标写入器决定：
  - ``file``    保存为独立文件，返回相对引用（Markdown / HTML / PDF 用）
  - ``datauri`` 内联为 data URI（HTML 单文件分发场景）
  - ``embed``   不落盘，由写入器以二进制方式嵌入（DOCX 用，保留 bytes）
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .utils import (ensure_dir, ext_of, guess_ext, is_data_uri, is_url,
                    make_data_uri, parse_data_uri, sanitize_filename, short_hash)

MODE_FILE = "file"
MODE_DATAURI = "datauri"
MODE_EMBED = "embed"


@dataclass
class Asset:
    """一个已落盘的资源。"""

    ref: str                    # 供文本格式引用的字符串（相对路径 / data URI / 原名）
    data: bytes                 # 二进制内容（embed 模式下由写入器直接使用）
    mime: str
    path: Path | None = None    # file 模式下的实际文件路径


class AssetStore:
    """把文档中的二进制资源统一写入 ``<输出目录>/<文件名>_assets/``。"""

    def __init__(self, out_dir: Path, stem: str, mode: str = MODE_FILE) -> None:
        self.out_dir = Path(out_dir)
        self.stem = stem
        self.mode = mode
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
        name = f"{sanitize_filename(hint)}-{self._counter}-{short_hash(data, 6)}.{guess_ext(mime)}"
        asset = self._store(key, data, mime, name)
        return asset

    def resolve_image(self, src: str, data: bytes | None, mime: str,
                      hint: str = "image") -> Asset:
        """按引用解析图片：URL 原样保留，其余统一落盘。"""
        if is_url(src):
            return Asset(ref=src, data=b"", mime=mime)
        if data is None and is_data_uri(src):
            data, mime = parse_data_uri(src)
        if data is None and src and not is_data_uri(src):
            p = Path(src)
            if p.exists():
                data = p.read_bytes()
        if data is None:
            raise ValueError(f"无法解析图片资源: {src!r}")
        return self.add_image(data, mime, hint)

    # -- 附件 -------------------------------------------------------------
    def add_attachment(self, name: str, data: bytes,
                       mime: str = "application/octet-stream") -> Asset:
        """附件始终落盘（任何格式都无法"内嵌渲染"任意二进制）。"""
        safe = sanitize_filename(name, "attachment")
        ext = ext_of(safe)
        if not ext:
            safe = f"{safe}.{guess_ext(mime, 'bin')}"
        name_on_disk = f"{short_hash(data, 6)}-{safe}"
        asset = self._store(f"att:{name_on_disk}", data, mime, name_on_disk)
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
            path.write_bytes(data)
            self.files.append(path)
            asset = Asset(ref=f"{self.stem}_assets/{name}", data=data, mime=mime,
                          path=path)
        self._seen[key] = asset
        return asset
