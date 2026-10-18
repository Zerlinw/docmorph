"""doc_converter.writers.pdf_writer —— PDF 写入器。

策略：先把 IR 渲染为带样式的 HTML（复用 HtmlWriter），再排版为 PDF。

排版引擎按可用性自动选择：
  1. `WeasyPrint`（质量最佳，但依赖 GTK/Pango 原生运行时）；
  2. `xhtml2pdf`（纯 Python，无原生依赖，作为通用降级方案）。

注意：PDF 中无法执行 Mermaid 脚本，流程图降级为"节点 + 连线"的文本结构。
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path

from ..assets import MODE_DATAURI, AssetStore
from ..errors import DependencyError, WriteError
from ..model import Document
from .base import BaseWriter, register_writer


@register_writer
class PdfWriter(BaseWriter):
    extension = "pdf"

    def write(self, doc: Document, out_path: Path, assets: AssetStore) -> Path:
        # PDF 需要自包含资源：将图片/附件转为 data URI，避免 base_url 歧义
        embedded = AssetStore(out_path.parent, out_path.stem, mode=MODE_DATAURI)
        html = self._render_html(doc, embedded)

        if _weasyprint_available():
            self._write_weasyprint(html, out_path)
        elif _xhtml2pdf_available():
            self._write_xhtml2pdf(html, out_path)
        else:
            raise DependencyError(
                "PDF 输出需要 weasyprint（推荐，需 GTK 运行时）或 xhtml2pdf，"
                "请执行: pip install weasyprint xhtml2pdf")
        return out_path

    # ------------------------------------------------------------------ 引擎
    @staticmethod
    def _write_weasyprint(html: str, out_path: Path) -> None:
        try:
            from weasyprint import HTML
            HTML(string=html, base_url=str(out_path.parent)).write_pdf(str(out_path))
        except Exception as exc:  # noqa: BLE001
            raise WriteError(str(out_path),
                             f"weasyprint: {type(exc).__name__}: {exc}")

    @staticmethod
    def _write_xhtml2pdf(html: str, out_path: Path) -> None:
        try:
            from xhtml2pdf import pisa
            _register_cjk_font()
            # xhtml2pdf 需要字节输入才能正确按 UTF-8 解码
            source = _inject_cjk_css(html).encode("utf-8")
            with _QuietLogging(), open(out_path, "wb") as fh:
                status = pisa.CreatePDF(source, dest=fh, encoding="utf-8")
            if status.err:
                raise WriteError(str(out_path),
                                 f"xhtml2pdf 报告 {status.err} 个错误（输出可能不完整）")
        except WriteError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise WriteError(str(out_path),
                             f"xhtml2pdf: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ HTML
    @staticmethod
    def _render_html(doc: Document, assets: AssetStore) -> str:
        from .html_writer import HtmlWriter
        writer = HtmlWriter()
        writer.render_mermaid = False  # PDF 不支持脚本渲染，使用文本结构
        return writer.render_to_string(doc, assets)


# ---------------------------------------------------------------------------
# 引擎可用性探测（带缓存，避免重复导入与日志噪音）
# ---------------------------------------------------------------------------

_ENGINE_CACHE: dict[str, bool] = {}


class _QuietLogging:
    """临时静默第三方库在 import 期间写入的日志。"""

    def __enter__(self):
        self._previous = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        return self

    def __exit__(self, *exc_info):
        logging.disable(self._previous)
        return False


def _weasyprint_available() -> bool:
    if "weasyprint" in _ENGINE_CACHE:
        return _ENGINE_CACHE["weasyprint"]
    try:
        with warnings.catch_warnings(), _QuietLogging():
            warnings.simplefilter("ignore")
            from weasyprint import HTML  # noqa: F401
        ok = True
    except Exception:  # noqa: BLE001 —— 原生库缺失时 ImportError/OSError 均可能
        ok = False
    _ENGINE_CACHE["weasyprint"] = ok
    return ok


def _xhtml2pdf_available() -> bool:
    if "xhtml2pdf" in _ENGINE_CACHE:
        return _ENGINE_CACHE["xhtml2pdf"]
    try:
        from xhtml2pdf import pisa  # noqa: F401
        ok = True
    except Exception:  # noqa: BLE001
        ok = False
    _ENGINE_CACHE["xhtml2pdf"] = ok
    return ok


# ---------------------------------------------------------------------------
# xhtml2pdf 的中文支持
# ---------------------------------------------------------------------------

_CJK_FONT_CSS = (
    "<style>"
    "* { font-family: 'STSong-Light', sans-serif !important; }"
    "head, title, style, script, meta, link { display: none !important; }"
    "</style>"
)


def _register_cjk_font() -> None:
    """注册 reportlab 内置中日韩字体（STSong-Light），避免中文变方框。"""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    except Exception:  # noqa: BLE001
        pass


def _inject_cjk_css(html: str) -> str:
    if "STSong-Light" in html:
        return html
    marker = "</head>"
    if marker in html:
        return html.replace(marker, _CJK_FONT_CSS + marker, 1)
    return _CJK_FONT_CSS + html
