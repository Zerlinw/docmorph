# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 配置：DocConverter.exe（桌面 GUI，单文件）。

构建::

    python packaging/build.py            # 生成图标 + 构建 GUI 与 CLI
    python -m PyInstaller --noconfirm --clean packaging/DocConverter.spec
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve()
if not (ROOT / "launcher.py").exists():
    ROOT = ROOT / "packaging"                   # SPECPATH 可能是项目根目录
PROJECT = ROOT.parent                            # 仓库根目录
SRC = PROJECT / "src"

datas = []
for package in ("docx", "xhtml2pdf", "reportlab", "pdfminer", "markdown_it",
                "pypdf", "pyphen", "html5lib", "svglib", "arabic_reshaper"):
    try:
        datas += collect_data_files(package)
    except Exception:  # noqa: BLE001
        pass

hiddenimports = [
    "linkify_it",
    "mdurl",
    "docx",
    "docx.oxml",
    "pdfplumber",
    "pdfminer",
    "pypdf",
    "xhtml2pdf",
    "reportlab",
    "PIL",
    "PIL.Image",
    "bs4",
    "lxml",
    "lxml.etree",
    "html5lib",
    "html5lib.treebuilders",
    "html5lib.treebuilders.etree",
    "pyphen",
    "svglib",
    "svglib.svglib",
    "arabic_reshaper",
    "bidi",
    "bidi.algorithm",
    "reportlab.platypus.xpreformatted",
    "reportlab.pdfbase.ttfonts",
    "reportlab.pdfbase.cidfonts",
    "reportlab.pdfbase._cidfontdata",
    "reportlab.pdfbase.pdfmetrics",
    "reportlab.pdfgen.canvas",
    "reportlab.lib.styles",
    "reportlab.lib.pagesizes",
    "reportlab.lib.colors",
]
for package in ("markdown_it", "pdfplumber", "pypdf", "xhtml2pdf", "reportlab"):
    try:
        hiddenimports += collect_submodules(package)
    except Exception:  # noqa: BLE001
        pass

excludes = [
    "tkinter", "matplotlib", "numpy", "pandas", "scipy", "torch", "torchvision",
    "cv2", "IPython", "jupyter", "pytest", "PyInstaller",
    "weasyprint",
    # PySide6 中未使用的重型模块
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtQuick3D",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtNetworkAuth", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSpatialAudio",
    "PySide6.QtStateMachine", "PySide6.QtTextToSpeech", "PySide6.QtUiTools",
    "PySide6.QtSql", "PySide6.QtHelp", "PySide6.QtDesigner",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtXml",
    "PySide6.QtDBus", "PySide6.QtConcurrent", "PySide6.QtPrintSupport",
    "PySide6.QtSvgWidgets", "PySide6.QtWebView",
]

a = Analysis(
    [str(ROOT / "launcher.py")],
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
    name="DocConverter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "app.ico"),
    version=str(ROOT / "assets" / "version_info.txt"),
)
