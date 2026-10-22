"""doc_converter.pdf_engine —— PDF 排版引擎的探测与调度。

按质量与可用性排序自动选择：

1. ``chromium``：调用本机 Microsoft Edge / Google Chrome 的无头模式打印
   （Windows 10/11 自带 Edge，零额外依赖，CSS/表格/中文/代码块效果最好）；
2. ``weasyprint``：纯 Python 生态的排版引擎，需 GTK/Pango 原生运行时；
3. ``xhtml2pdf``：纯 Python 兜底方案，自动嵌入系统中文字体。

任一引擎失败时自动降级到下一个，并返回实际使用的引擎名与诊断信息。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

from .errors import DependencyError, WriteError

ENGINE_CHOICES = ("auto", "chromium", "weasyprint", "xhtml2pdf")
PAGE_SIZES = ("A4", "Letter", "A3", "A5")

_REGISTRY_KEYS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
)

_WINDOWS_RELATIVE_BROWSERS = (
    r"Microsoft\Edge\Application\msedge.exe",
    r"Google\Chrome\Application\chrome.exe",
    r"Chromium\Application\chrome.exe",
)


# ---------------------------------------------------------------------------
# Chromium / Edge
# ---------------------------------------------------------------------------

_browser_cache: dict[str, str | None] = {}


def find_browser() -> str | None:
    """定位可用于无头打印的 Chromium 内核浏览器。"""
    if "path" in _browser_cache:
        return _browser_cache["path"]

    candidates: list[str] = []
    override = os.environ.get("DOC_CONVERTER_BROWSER")
    if override:
        candidates.append(override)
    if sys.platform == "win32":
        candidates.extend(_windows_browser_candidates())
    else:
        for name in ("microsoft-edge", "google-chrome", "chromium",
                     "chromium-browser", "google-chrome-stable"):
            found = shutil.which(name)
            if found:
                candidates.append(found)
        candidates.extend([
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ])
    for name in ("msedge", "chrome", "chromium", "google-chrome"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    result = None
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            result = str(Path(candidate))
            break
    _browser_cache["path"] = result
    return result


def _windows_browser_candidates() -> list[str]:
    paths: list[str] = []
    try:
        import winreg
        for key_path in _REGISTRY_KEYS:
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(root, key_path) as key:
                        value, _ = winreg.QueryValueEx(key, None)
                        if value:
                            paths.append(value)
                except OSError:
                    continue
    except Exception:  # noqa: BLE001
        pass

    bases = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
             os.environ.get("LOCALAPPDATA")]
    for base in bases:
        if not base:
            continue
        for relative in _WINDOWS_RELATIVE_BROWSERS:
            paths.append(str(Path(base) / relative))
    return paths


def chromium_available() -> bool:
    return find_browser() is not None


def _render_chromium(html: str, out_path: Path, page_size: str,
                     timeout: float = 180.0) -> None:
    browser = find_browser()
    if not browser:
        raise DependencyError("未找到 Microsoft Edge 或 Google Chrome，无法使用 Chromium 打印引擎")
    # 打印目标必须是绝对路径：Chromium 以子进程工作目录为基准解析相对路径
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="docconv_pdf_"))
    try:
        html_file = workdir / "document.html"
        html_file.write_text(html, encoding="utf-8")
        profile_dir = workdir / "profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        args = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--mute-audio",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-pdf-header-footer",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=5000",
            "--allow-file-access-from-files",
            f"--user-data-dir={profile_dir}",
            f"--print-to-pdf={out_path}",
            html_file.as_uri(),
        ]
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        started = time.monotonic()
        try:
            proc = subprocess.run(
                args, capture_output=True, timeout=timeout,
                creationflags=creationflags,
                cwd=str(workdir),
            )
        except subprocess.TimeoutExpired as exc:
            raise WriteError(str(out_path), f"Chromium 打印超时（>{timeout:.0f}s）") from exc
        if not out_path.exists() or out_path.stat().st_size < 200:
            detail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace")
            raise WriteError(
                str(out_path),
                f"Chromium 打印失败（exit={proc.returncode}, "
                f"{time.monotonic() - started:.1f}s）：{detail.strip()[:300]}")
        if not _looks_like_pdf(out_path):
            raise WriteError(str(out_path), "Chromium 输出的文件不是有效 PDF")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# WeasyPrint
# ---------------------------------------------------------------------------

_engine_cache: dict[str, bool] = {}
_engine_errors: dict[str, str] = {}


def weasyprint_available() -> bool:
    if "weasyprint" in _engine_cache:
        return _engine_cache["weasyprint"]
    ok = False
    if sys.platform == "win32" and not _pango_available():
        _engine_cache["weasyprint"] = False
        _engine_errors["weasyprint"] = "未找到 GTK/Pango 运行库（libpango / libgobject）"
        return False
    try:
        import contextlib
        import io
        import os
        import warnings
        # WeasyPrint 在导入失败时会直接写 stderr（C 层），这里连同文件描述符一起静默
        saved_stderr = os.dup(2)
        devnull = open(os.devnull, "w")
        try:
            os.dup2(devnull.fileno(), 2)
            with warnings.catch_warnings(), contextlib.redirect_stderr(io.StringIO()):
                warnings.simplefilter("ignore")
                import weasyprint  # noqa: F401
        finally:
            os.dup2(saved_stderr, 2)
            os.close(saved_stderr)
            devnull.close()
        ok = True
    except Exception as exc:  # noqa: BLE001 —— 缺少 Pango/GTK 时可能抛 OSError
        ok = False
        _engine_errors["weasyprint"] = f"{type(exc).__name__}: {exc}"
    _engine_cache["weasyprint"] = ok
    return ok


def _pango_available() -> bool:
    """Windows 上先检查原生库，避免无意义的 import 失败与噪声输出。"""
    try:
        import ctypes
        for library in ("libgobject-2.0-0.dll", "libpango-1.0-0.dll"):
            ctypes.WinDLL(library)
        return True
    except Exception:  # noqa: BLE001
        return False


def _render_weasyprint(html: str, out_path: Path, page_size: str) -> None:
    if not weasyprint_available():
        raise DependencyError("未安装 WeasyPrint 或其原生依赖（GTK/Pango）")
    try:
        import logging
        import warnings
        from weasyprint import HTML
        logging.getLogger("weasyprint").setLevel(logging.ERROR)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            HTML(string=html, base_url=str(out_path.parent)).write_pdf(str(out_path))
    except Exception as exc:  # noqa: BLE001
        raise WriteError(str(out_path), f"weasyprint: {type(exc).__name__}: {exc}") from exc
    if not _looks_like_pdf(out_path):
        raise WriteError(str(out_path), "WeasyPrint 输出的文件不是有效 PDF")


# ---------------------------------------------------------------------------
# xhtml2pdf
# ---------------------------------------------------------------------------

def xhtml2pdf_available() -> bool:
    if "xhtml2pdf" in _engine_cache:
        return _engine_cache["xhtml2pdf"]
    try:
        from xhtml2pdf import pisa  # noqa: F401
        ok = True
    except Exception as exc:  # noqa: BLE001
        ok = False
        _engine_errors["xhtml2pdf"] = f"{type(exc).__name__}: {exc}"
    _engine_cache["xhtml2pdf"] = ok
    return ok


def engine_diagnostics() -> dict[str, str]:
    """返回每个引擎的可用状态描述，便于排查打包/环境问题。"""
    labels = {"chromium": "Chromium / Edge", "weasyprint": "WeasyPrint",
              "xhtml2pdf": "xhtml2pdf"}
    result: dict[str, str] = {}
    for name, label in labels.items():
        ok = engine_available(name)
        reason = _engine_errors.get(name, "")
        result[name] = f"{label}: {'可用' if ok else '不可用'}" + (
            f"（{reason}）" if reason and not ok else "")
    return result


_font_registered: str | None = None
_font_checked = False


def _register_cjk_font() -> str | None:
    """注册 reportlab 内置中日韩字体（STSong-Light）。

    xhtml2pdf 0.2.x 对自定义 ``@font-face`` / TTFont 的支持不稳定（会回退到
    Helvetica 导致中文变方框），因此兜底引擎统一使用 reportlab 自带、可嵌入
    且带 ToUnicode 映射的 CID 字体，保证中文可显示、可复制、可检索。
    """
    global _font_registered, _font_checked
    if _font_checked:
        return _font_registered
    _font_checked = True
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        _font_registered = "STSong-Light"
    except Exception:  # noqa: BLE001
        _font_registered = None
    return _font_registered


def _render_xhtml2pdf(html: str, out_path: Path, page_size: str) -> None:
    if not xhtml2pdf_available():
        raise DependencyError("未安装 xhtml2pdf")
    try:
        import logging
        from xhtml2pdf import pisa
    except Exception as exc:  # noqa: BLE001
        raise DependencyError(f"xhtml2pdf 不可用: {exc}") from exc

    font = _register_cjk_font()
    css_parts = []
    if font:
        # 兜底引擎无法可靠做中西文混排，统一使用可嵌入的中日韩字体，保证内容正确
        css_parts.append(f"* {{ font-family: '{font}', sans-serif !important; }}")
    css_parts.append("head, title, style, script, meta, link, noscript { display:none !important; }")
    css_parts.append("body { margin: 0 !important; padding: 0 !important; "
                     "max-width: none !important; font-size: 10.5pt; "
                     "line-height: 1.6; }")
    css_parts.append("table { width: 100%; border-collapse: collapse; }")
    css_parts.append("@page { size: %s; margin: 1.8cm 1.6cm; }" % page_size)
    css = "<style>" + "".join(css_parts) + "</style>"
    if "</head>" in html:
        source = html.replace("</head>", css + "</head>", 1).encode("utf-8")
    else:
        source = (css + html).encode("utf-8")

    temp = out_path.parent / (out_path.name + ".tmp")
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with open(temp, "wb") as handle:
            status = pisa.CreatePDF(source, dest=handle, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        temp.unlink(missing_ok=True)
        raise WriteError(str(out_path), f"xhtml2pdf: {type(exc).__name__}: {exc}") from exc
    finally:
        logging.disable(previous)
    if status.err:
        temp.unlink(missing_ok=True)
        raise WriteError(str(out_path), f"xhtml2pdf 报告 {status.err} 个错误")
    if not _looks_like_pdf(temp):
        temp.unlink(missing_ok=True)
        raise WriteError(str(out_path), "xhtml2pdf 输出的文件不是有效 PDF")
    os.replace(temp, out_path)


# ---------------------------------------------------------------------------
# 调度
# ---------------------------------------------------------------------------

_RENDERERS: dict[str, Callable[[str, Path, str], None]] = {
    "chromium": _render_chromium,
    "weasyprint": _render_weasyprint,
    "xhtml2pdf": _render_xhtml2pdf,
}


def engine_available(name: str) -> bool:
    if name == "chromium":
        return chromium_available()
    if name == "weasyprint":
        return weasyprint_available()
    if name == "xhtml2pdf":
        return xhtml2pdf_available()
    return False


def available_engines() -> list[str]:
    return [name for name in ("chromium", "weasyprint", "xhtml2pdf")
            if engine_available(name)]


def render_pdf(html: str, out_path: Path, engine: str = "auto",
               page_size: str = "A4",
               warn: Callable[[str], None] | None = None) -> str:
    """把 HTML 渲染为 PDF，返回实际使用的引擎名。"""
    out_path = Path(out_path)
    page_size = page_size if page_size in PAGE_SIZES else "A4"
    if engine == "auto":
        order = ["chromium", "weasyprint", "xhtml2pdf"]
    elif engine in _RENDERERS:
        order = [engine]
    else:
        raise DependencyError(
            f"未知的 PDF 引擎: {engine!r}，可选: {', '.join(ENGINE_CHOICES)}")

    errors: list[str] = []
    for name in order:
        if not engine_available(name):
            errors.append(f"{name}: 不可用")
            continue
        try:
            _RENDERERS[name](html, out_path, page_size)
            if name != order[0] and warn is not None:
                first_error = errors[0] if errors else "首选引擎不可用"
                warn(f"PDF 引擎降级为 {name}（{first_error}）")
            return name
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
            out_path.unlink(missing_ok=True)
            continue
    raise DependencyError("没有可用的 PDF 排版引擎：" + "；".join(errors))


def _looks_like_pdf(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False
