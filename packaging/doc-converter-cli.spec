# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 配置：doc-converter.exe（命令行版，单文件）。

不包含 PySide6，体积更小，适合脚本 / 批处理调用。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve()
if not (ROOT / "cli_launcher.py").exists():
    ROOT = ROOT / "packaging"
PROJECT = ROOT.parent
SRC = PROJECT / "src"

datas = []
for package in ("docx", "xhtml2pdf", "reportlab", "pdfminer", "markdown_it",
                "pypdf", "pyphen", "html5lib", "svglib", "arabic_reshaper"):
    try:
        datas += collect_data_files(package)
    except Exception:  # noqa: BLE001
        pass

hiddenimports = ["linkify_it", "mdurl", "docx", "pdfplumber", "pdfminer",
                 "pypdf", "xhtml2pdf", "reportlab", "PIL", "bs4", "lxml",
                 "lxml.etree", "html5lib", "html5lib.treebuilders",
                 "html5lib.treebuilders.etree", "pyphen", "svglib",
                 "svglib.svglib", "arabic_reshaper", "bidi", "bidi.algorithm",
                 "reportlab.platypus.xpreformatted",
                 "reportlab.pdfbase.ttfonts",
                 "reportlab.pdfbase.cidfonts",
                 "reportlab.pdfbase._cidfontdata",
                 "reportlab.pdfbase.pdfmetrics",
                 "reportlab.pdfgen.canvas",
                 "reportlab.lib.styles",
                 "reportlab.lib.pagesizes",
                 "reportlab.lib.colors"]
for package in ("markdown_it", "pdfplumber", "pypdf", "xhtml2pdf", "reportlab"):
    try:
        hiddenimports += collect_submodules(package)
    except Exception:  # noqa: BLE001
        pass

excludes = [
    "tkinter", "matplotlib", "numpy", "pandas", "scipy", "torch", "torchvision",
    "cv2", "IPython", "jupyter", "pytest", "PyInstaller",
    "PySide6", "doc_converter.gui", "weasyprint",
]

a = Analysis(
    [str(ROOT / "cli_launcher.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="doc-converter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "app.ico"),
    version=str(ROOT / "assets" / "version_info.txt"),
)
