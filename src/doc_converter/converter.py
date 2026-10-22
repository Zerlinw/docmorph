"""doc_converter.converter —— 转换编排：读 -> IR -> 写。

互转矩阵天然支持 4 x 4 - 4 = 12 种有效组合（同格式转换会被跳过）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .assets import MODE_DATAURI, MODE_EMBED, MODE_FILE, AssetStore
from .errors import (ConversionError, EmptyDocumentError, FileNotFoundError_,
                     UnsupportedFormatError)
from .model import Document
from .pdf_engine import PAGE_SIZES
from .readers.base import get_reader
from .utils import ext_of
from .writers.base import get_writer

SUPPORTED_FORMATS = ("pdf", "docx", "html", "md")

_EXT_ALIASES = {
    "htm": "html", "xhtml": "html",
    "markdown": "md", "mdown": "md", "mkd": "md", "mkdn": "md",
    "doc": "docx", "word": "docx",
}

# 目标格式 -> 资源落地模式
_ASSET_MODES = {
    "md": MODE_FILE,
    "html": MODE_FILE,
    "docx": MODE_EMBED,
    "pdf": MODE_DATAURI,
}


@dataclass
class ConversionResult:
    """单次转换的结果与诊断信息。"""

    source: Path
    output: Path
    source_format: str
    target_format: str
    warnings: list[str] = field(default_factory=list)
    assets: list[Path] = field(default_factory=list)
    engine: str | None = None          # PDF 实际使用的排版引擎

    @property
    def ok(self) -> bool:
        return self.output.exists()

    def summary(self) -> str:
        return (f"{self.source.name} ({self.source_format} -> {self.target_format})"
                f" => {self.output}")


def normalize_format(fmt: str) -> str:
    fmt = (fmt or "").strip().lower().lstrip(".")
    fmt = _EXT_ALIASES.get(fmt, fmt)
    if fmt not in SUPPORTED_FORMATS:
        raise UnsupportedFormatError(fmt, list(SUPPORTED_FORMATS))
    return fmt


def infer_format(path: Path) -> str:
    return normalize_format(ext_of(path))


def convert(input_path: str | Path,
            target: str,
            source_format: str | None = None,
            output: str | Path | None = None,
            asset_mode: str | None = None,
            pdf_engine: str = "auto",
            page_size: str = "A4") -> ConversionResult:
    """转换单个文件。

    参数:
        input_path:    输入文件路径。
        target:        目标格式（``pdf`` / ``docx`` / ``html`` / ``md``）。
        source_format: 源格式，缺省按扩展名推断。
        output:        输出路径。可以是目录（自动命名）或具体文件路径；
                       缺省时输出到输入文件所在目录。
        asset_mode:    资源模式（``file`` / ``datauri`` / ``embed``），
                       缺省按目标格式自动选择。
        pdf_engine:    PDF 排版引擎（``auto`` / ``chromium`` / ``weasyprint`` /
                       ``xhtml2pdf``）。
        page_size:     PDF 页面尺寸（``A4`` / ``Letter`` / ``A3`` / ``A5``）。
    """
    src = Path(input_path)
    if not src.exists():
        raise FileNotFoundError_(str(src))
    if src.is_dir():
        raise ConversionError(f"输入路径是目录而不是文件: {src}", code="read_error",
                              source=str(src))

    src_fmt = normalize_format(source_format) if source_format else infer_format(src)
    dst_fmt = normalize_format(target)

    if src_fmt == dst_fmt:
        raise ConversionError(
            f"源格式与目标格式相同（{src_fmt}），无需转换",
            code="same_format", source=str(src))

    out_path = _resolve_output(src, output, dst_fmt)
    reader = get_reader(src_fmt)
    writer = get_writer(dst_fmt)

    doc = Document(source_format=src_fmt, source_path=str(src))
    reader.read(src, doc)

    if doc.is_empty():
        raise EmptyDocumentError(str(src))

    mode = asset_mode or _ASSET_MODES[dst_fmt]
    if mode not in (MODE_FILE, MODE_DATAURI, MODE_EMBED):
        raise ConversionError(f"未知的资源模式: {mode!r}",
                              code="invalid_option", source=str(src))
    if mode == MODE_EMBED and dst_fmt != "docx":
        # embed 语义只对 DOCX 成立，其它格式回退为独立文件，避免图片引用失效
        doc.warn("embed 资源模式仅适用于 DOCX，已自动回退为独立文件模式")
        mode = MODE_FILE
    assets = AssetStore(
        out_path.parent, out_path.stem, mode=mode,
        source_dir=src.parent,
        # DOCX 需要真实二进制、PDF 需要 data URI，远程图片必须下载
        fetch_remote=dst_fmt in ("docx", "pdf") or mode in (MODE_EMBED, MODE_DATAURI),
        warn=doc.warn,
    )

    engine_used = None
    if hasattr(writer, "engine"):
        writer.engine = pdf_engine
    if hasattr(writer, "page_size"):
        writer.page_size = page_size if page_size in PAGE_SIZES else "A4"

    writer.write(doc, out_path, assets)
    if dst_fmt == "pdf":
        engine_used = _used_engine(writer)

    return ConversionResult(
        source=src,
        output=out_path,
        source_format=src_fmt,
        target_format=dst_fmt,
        warnings=list(doc.warnings),
        assets=list(assets.files),
        engine=engine_used,
    )


def convert_batch(inputs: list[str | Path],
                  target: str,
                  source_format: str | None = None,
                  output: str | Path | None = None,
                  asset_mode: str | None = None,
                  on_error: str = "continue",
                  pdf_engine: str = "auto",
                  page_size: str = "A4"
                  ) -> tuple[list[ConversionResult],
                             list[tuple[Path, ConversionError]]]:
    """批量转换。

    返回 ``(成功结果列表, 失败列表)``。``on_error='continue'`` 时单个文件
    失败不影响其余文件；``'raise'`` 时首个失败即抛出。
    """
    results: list[ConversionResult] = []
    failures: list[tuple[Path, ConversionError]] = []
    for item in inputs:
        src = Path(item)
        try:
            results.append(convert(src, target, source_format, output, asset_mode,
                                   pdf_engine=pdf_engine, page_size=page_size))
        except ConversionError as exc:
            failures.append((src, exc))
            if on_error == "raise":
                raise
    return results, failures


def _used_engine(writer) -> str | None:
    """PdfWriter 在 write 后记录实际引擎（由 pdf_engine 返回）。"""
    return getattr(writer, "used_engine", None)


def _resolve_output(src: Path, output: str | Path | None, dst_fmt: str) -> Path:
    """解析输出路径：目录（自动命名）或具体文件。"""
    if output is None:
        out_dir = src.parent
        stem = src.stem
    else:
        out = Path(output)
        treat_as_dir = (not out.suffix) or out.is_dir() or str(output).endswith(
            ("/", "\\"))
        if treat_as_dir:
            out_dir = out
            stem = src.stem
        else:
            out_dir = out.parent if str(out.parent) else Path(".")
            stem = out.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{stem}.{dst_fmt}"
