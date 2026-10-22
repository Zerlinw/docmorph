"""doc_converter.gui.app —— 桌面主窗口（PySide6）。

交互流程：拖拽 / 选择文件 -> 选择目标格式与选项 -> 批量转换 -> 查看日志与结果。
转换在独立 QThread 中执行，主界面保持响应，可随时取消。
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QIcon, QKeySequence,
                           QPainter, QPixmap, QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                               QComboBox, QFileDialog, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QScrollArea, QSizePolicy,
                               QStackedLayout, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .. import __version__
from ..converter import SUPPORTED_FORMATS, convert
from ..errors import ConversionError
from ..pdf_engine import available_engines
from ..utils import ext_of, human_size
from .theme import palette, stylesheet

APP_NAME = "DocConverter"
ORG_NAME = "doc-converter"

SOURCE_EXTS = {"pdf", "docx", "html", "htm", "xhtml", "md", "markdown",
               "mdown", "mkd", "mkdn"}
FORMAT_LABELS = {
    "pdf": "PDF 文档",
    "docx": "Word（DOCX）",
    "html": "网页（HTML）",
    "md": "Markdown",
}
ENGINE_LABELS = {
    "auto": "自动（推荐）",
    "chromium": "Chromium / Edge（排版最佳）",
    "weasyprint": "WeasyPrint",
    "xhtml2pdf": "xhtml2pdf（兜底）",
}
ASSET_MODES = {
    "auto": None,
    "file": "file",
    "datauri": "datauri",
}


# ---------------------------------------------------------------------------
# 转换线程
# ---------------------------------------------------------------------------

@dataclass
class _Job:
    paths: list[Path]
    target: str
    output_dir: Path
    engine: str
    page_size: str
    asset_mode: str | None


class ConversionWorker(QThread):
    fileStarted = Signal(int)
    fileDone = Signal(int, object)
    fileFailed = Signal(int, str)
    logMessage = Signal(str)
    allDone = Signal(int, int, bool)

    def __init__(self, job: _Job, parent=None) -> None:
        super().__init__(parent)
        self.job = job
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:  # noqa: D401
        used: set[Path] = set()
        success = 0
        failures = 0
        for index, path in enumerate(self.job.paths):
            if self._cancelled:
                self.allDone.emit(success, failures, True)
                return
            self.fileStarted.emit(index)
            output = self._unique_output(path, used)
            try:
                result = convert(
                    path, target=self.job.target, output=output,
                    asset_mode=self.job.asset_mode,
                    pdf_engine=self.job.engine, page_size=self.job.page_size)
            except ConversionError as exc:
                failures += 1
                self.fileFailed.emit(index, str(exc))
                continue
            except Exception as exc:  # noqa: BLE001
                failures += 1
                detail = traceback.format_exc(limit=2)
                self.fileFailed.emit(index, f"未预期错误: {exc}")
                self.logMessage.emit(detail)
                continue
            used.add(result.output.resolve())
            success += 1
            self.fileDone.emit(index, result)
        self.allDone.emit(success, failures, self._cancelled)

    def _unique_output(self, path: Path, used: set[Path]) -> Path:
        candidate = (self.job.output_dir / f"{path.stem}.{self.job.target}").resolve()
        if candidate not in used:
            return candidate
        prefixed = self.job.output_dir / (
            f"{ext_of(path)}-{path.stem}.{self.job.target}")
        return prefixed.resolve()


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings(ORG_NAME, APP_NAME)
        self.files: list[Path] = []
        self._worker: ConversionWorker | None = None
        self._dark = self.settings.value("theme", "light") == "dark"
        self._last_output: Path | None = None
        self._drag_active = False

        self.setWindowTitle(f"文档转换助手 DocConverter {__version__}")
        self.setMinimumSize(QSize(900, 640))
        self.resize(QSize(1040, 780))
        self.setWindowIcon(_app_icon())

        self._build_ui()
        self._bind_shortcuts()
        self._restore_settings()
        self._apply_theme()
        self._update_pdf_options()
        self._refresh_placeholder()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        # ---- 顶栏 ----
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("文档转换助手")
        title.setObjectName("title")
        subtitle = QLabel("PDF / Word / HTML / Markdown 任意互转 · 本地离线 · 批量处理")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch(1)

        self.theme_button = QPushButton("🌙")
        self.theme_button.setObjectName("ghost")
        self.theme_button.setToolTip("切换深色/浅色主题")
        self.theme_button.setFixedWidth(40)
        self.theme_button.clicked.connect(self._toggle_theme)
        self.about_button = QPushButton("关于")
        self.about_button.setObjectName("ghost")
        self.about_button.clicked.connect(self._show_about)
        header.addWidget(self.theme_button)
        header.addWidget(self.about_button)
        root.addLayout(header)

        # ---- 主体：左侧文件列表 + 右侧选项 ----
        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self._build_file_card(), 1)
        body.addWidget(self._build_options_card())
        root.addLayout(body, 1)

        # ---- 日志 ----
        log_card = QFrame()
        log_card.setObjectName("card")
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(14, 10, 14, 12)
        log_layout.setSpacing(6)
        log_header = QHBoxLayout()
        log_title = QLabel("转换日志")
        log_title.setObjectName("section")
        clear_log = QPushButton("清空")
        clear_log.setObjectName("ghost")
        clear_log.clicked.connect(lambda: self.log_view.clear())
        log_header.addWidget(log_title)
        log_header.addStretch(1)
        log_header.addWidget(clear_log)
        log_layout.addLayout(log_header)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("转换过程中的提示与告警会显示在这里…")
        self.log_view.setFixedHeight(120)
        log_layout.addWidget(self.log_view)
        root.addWidget(log_card)

        status_row = QHBoxLayout()
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("status")
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        version = QLabel(f"v{__version__}")
        version.setObjectName("hint")
        status_row.addWidget(version)
        root.addLayout(status_row)

    def _build_file_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("待转换文件")
        title.setObjectName("section")
        self.count_label = QLabel("0 个文件")
        self.count_label.setObjectName("hint")
        header.addWidget(title)
        header.addWidget(self.count_label)
        header.addStretch(1)
        layout.addLayout(header)

        self.stack_host = QWidget()
        self.stack = QStackedLayout(self.stack_host)
        self.stack.setContentsMargins(0, 0, 0, 0)

        # 空状态：拖拽提示
        self.drop_area = QFrame()
        self.drop_area.setObjectName("dropArea")
        self.drop_area.setProperty("dragActive", False)
        drop_layout = QVBoxLayout(self.drop_area)
        drop_layout.setContentsMargins(20, 30, 20, 30)
        icon = QLabel("📄")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size: 44px; background: transparent;")
        hint = QLabel("把文件或文件夹拖到这里")
        hint.setObjectName("bigHint")
        hint.setAlignment(Qt.AlignCenter)
        sub = QLabel("支持 PDF / DOCX / HTML / Markdown，可批量添加")
        sub.setObjectName("hint")
        sub.setAlignment(Qt.AlignCenter)
        pick = QPushButton("选择文件…")
        pick.setFixedWidth(130)
        pick.clicked.connect(self._choose_files)
        drop_layout.addStretch(1)
        drop_layout.addWidget(icon)
        drop_layout.addWidget(hint)
        drop_layout.addWidget(sub)
        pick_row = QHBoxLayout()
        pick_row.addStretch(1)
        pick_row.addWidget(pick)
        pick_row.addStretch(1)
        drop_layout.addLayout(pick_row)
        drop_layout.addStretch(1)
        self.drop_area.mousePressEvent = lambda event: self._choose_files()

        # 文件表
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["文件名", "格式", "大小", "状态"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            head.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(lambda _index: self._open_selected_output())

        self.stack.addWidget(self.drop_area)
        self.stack.addWidget(self.table)
        layout.addWidget(self.stack_host, 1)

        buttons = QHBoxLayout()
        add_files = QPushButton("添加文件")
        add_files.clicked.connect(self._choose_files)
        add_dir = QPushButton("添加文件夹")
        add_dir.clicked.connect(self._choose_folder)
        self.remove_button = QPushButton("移除所选")
        self.remove_button.clicked.connect(self._remove_selected)
        self.clear_button = QPushButton("清空")
        self.clear_button.clicked.connect(self._clear_files)
        for button in (add_files, add_dir, self.remove_button, self.clear_button):
            button.setCursor(Qt.PointingHandCursor)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return card

    def _build_options_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(352)
        outer = QVBoxLayout(card)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(8)

        title = QLabel("转换设置")
        title.setObjectName("section")
        outer.addWidget(title)

        # 可滚动区域：窗口较矮时字段可滚动，底部操作按钮始终可见
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.viewport().setAutoFillBackground(False)
        body = QWidget()
        body.setObjectName("scrollBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.target_combo = QComboBox()
        for fmt in SUPPORTED_FORMATS:
            self.target_combo.addItem(FORMAT_LABELS[fmt], fmt)
        self.target_combo.setCurrentIndex(
            max(0, SUPPORTED_FORMATS.index("pdf")))
        self.target_combo.currentIndexChanged.connect(self._update_pdf_options)
        layout.addLayout(_form_row("目标格式", self.target_combo))

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("默认：源文件目录下的 converted")
        browse = QPushButton("浏览…")
        browse.setFixedWidth(58)
        browse.clicked.connect(self._choose_output)
        layout.addLayout(_form_row("输出目录", self.output_edit, browse,
                                   stretch_last=False))

        self.engine_combo = QComboBox()
        engines = set(available_engines())
        for key, label in ENGINE_LABELS.items():
            self.engine_combo.addItem(label, key)
            if key not in ("auto",) and key not in engines:
                index = self.engine_combo.count() - 1
                self.engine_combo.model().item(index).setEnabled(False)
        layout.addLayout(_form_row("PDF 引擎", self.engine_combo))

        self.page_combo = QComboBox()
        for size in ("A4", "Letter", "A3", "A5"):
            self.page_combo.addItem(size, size)
        layout.addLayout(_form_row("页面尺寸", self.page_combo))

        self.assets_combo = QComboBox()
        for key, label in (("auto", "自动（推荐）"),
                           ("file", "保存为独立文件"),
                           ("datauri", "内联到文档（data URI）")):
            self.assets_combo.addItem(label, key)
        layout.addLayout(_form_row("图片资源", self.assets_combo))

        self.open_after = QCheckBox("转换完成后打开输出目录")
        self.open_after.setChecked(True)
        layout.addStretch(1)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        outer.addWidget(self.open_after)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("等待转换")
        outer.addWidget(self.progress)

        self.start_button = QPushButton("开始转换")
        self.start_button.setObjectName("primary")
        self.start_button.setCursor(Qt.PointingHandCursor)
        self.start_button.clicked.connect(self._start)
        outer.addWidget(self.start_button)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel)
        outer.addWidget(self.cancel_button)

        self.open_button = QPushButton("打开输出目录")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_output)
        outer.addWidget(self.open_button)
        return card

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+O"), self, self._choose_files)
        QShortcut(QKeySequence("Ctrl+Shift+O"), self, self._choose_folder)
        QShortcut(QKeySequence("Ctrl+Return"), self, self._start)
        QShortcut(QKeySequence("Ctrl+Enter"), self, self._start)
        QShortcut(QKeySequence("Delete"), self.table, self._remove_selected)

    # ------------------------------------------------------------------ 文件
    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要转换的文件", str(self._default_dir()),
            "文档 (*.pdf *.docx *.html *.htm *.xhtml *.md *.markdown);;所有文件 (*.*)")
        if paths:
            self._add_paths([Path(p) for p in paths])

    def _choose_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "选择包含文档的文件夹", str(self._default_dir()))
        if directory:
            self._add_paths([Path(directory)])

    def _choose_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "选择输出目录", str(self._current_output()))
        if directory:
            self.output_edit.setText(directory)
            self.output_edit.setCursorPosition(0)

    def _default_dir(self) -> Path:
        if self.files:
            return self.files[0].parent
        return Path.home()

    def _add_paths(self, paths: list[Path]) -> None:
        added = 0
        skipped = 0
        existing = {p.resolve() for p in self.files if p.exists()}
        for path in paths:
            path = Path(path)
            candidates: list[Path] = []
            if path.is_dir():
                candidates = sorted(
                    child for child in path.rglob("*")
                    if child.is_file() and ext_of(child) in SOURCE_EXTS)
            elif path.is_file():
                candidates = [path]
            if not candidates:
                skipped += 1
                continue
            for candidate in candidates:
                resolved = candidate.resolve()
                if resolved in existing:
                    continue
                existing.add(resolved)
                self.files.append(candidate)
                added += 1
        self._refresh_table()
        self._refresh_placeholder()
        if added:
            self._log(f"已添加 {added} 个文件")
            if not self.output_edit.text().strip():
                self.output_edit.setText(str(self._default_output()))
                self.output_edit.setCursorPosition(0)
        if skipped:
            self._log(f"已忽略 {skipped} 个不支持的文件或空文件夹")

    def _remove_selected(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()},
                      reverse=True)
        if not rows:
            return
        for row in rows:
            if 0 <= row < len(self.files):
                self.files.pop(row)
        self._refresh_table()
        self._refresh_placeholder()

    def _clear_files(self) -> None:
        if self._worker is not None:
            return
        self.files.clear()
        self._refresh_table()
        self._refresh_placeholder()

    def _refresh_placeholder(self) -> None:
        self.stack.setCurrentIndex(1 if self.files else 0)
        self.count_label.setText(f"{len(self.files)} 个文件")
        self.start_button.setEnabled(bool(self.files) and self._worker is None)
        self.remove_button.setEnabled(bool(self.files))
        self.clear_button.setEnabled(bool(self.files))

    def _refresh_table(self) -> None:
        self.table.setRowCount(len(self.files))
        for row, path in enumerate(self.files):
            self._set_row(row, path, "等待", None)
        if self.files:
            self.stack.setCurrentIndex(1)
        self.count_label.setText(f"{len(self.files)} 个文件")

    def _set_row(self, row: int, path: Path, status: str,
                 color: str | None) -> None:
        colors = palette(self._dark)
        name = QTableWidgetItem(path.name)
        name.setToolTip(str(path))
        fmt = QTableWidgetItem(ext_of(path).upper() or "?")
        try:
            size = human_size(path.stat().st_size)
        except OSError:
            size = "-"
        size_item = QTableWidgetItem(size)
        status_item = QTableWidgetItem(status)
        if color:
            status_item.setForeground(QColor(color))
        for column, item in enumerate((name, fmt, size_item, status_item)):
            item.setTextAlignment(Qt.AlignVCenter | (
                Qt.AlignLeft if column == 0 else Qt.AlignCenter))
            self.table.setItem(row, column, item)
        self.table.setRowHeight(row, 34)

    # ------------------------------------------------------------------ 转换
    def _start(self) -> None:
        if self._worker is not None:
            return
        if not self.files:
            QMessageBox.information(self, "没有待转换文件", "请先添加要转换的文件。")
            return
        target = self.target_combo.currentData() or "pdf"
        output_dir = self._current_output()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "输出目录不可用", f"无法创建输出目录：\n{exc}")
            return
        engine = self.engine_combo.currentData() or "auto"
        page_size = self.page_combo.currentData() or "A4"
        asset_mode = ASSET_MODES.get(self.assets_combo.currentData() or "auto")

        job = _Job(paths=list(self.files), target=target,
                   output_dir=output_dir, engine=engine,
                   page_size=page_size, asset_mode=asset_mode)
        self._log("")
        self._log(f"开始转换 {len(job.paths)} 个文件 -> {FORMAT_LABELS[target]}"
                  f"（输出：{output_dir}）")
        self._worker = ConversionWorker(job, self)
        self._worker.fileStarted.connect(self._on_started)
        self._worker.fileDone.connect(self._on_done)
        self._worker.fileFailed.connect(self._on_failed)
        self._worker.logMessage.connect(self._log)
        self._worker.allDone.connect(self._on_all_done)

        self._set_busy(True)
        self.progress.setRange(0, len(job.paths))
        self.progress.setValue(0)
        self.progress.setFormat(f"0 / {len(job.paths)}")
        self._worker.start()

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("正在取消…")
            self._log("已请求取消，当前文件完成后停止。")

    def _set_busy(self, busy: bool) -> None:
        for widget in (self.target_combo, self.output_edit, self.engine_combo,
                       self.page_combo, self.assets_combo, self.open_after,
                       self.remove_button, self.clear_button):
            widget.setEnabled(not busy)
        self.start_button.setEnabled(not busy and bool(self.files))
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(busy)

    def _on_started(self, index: int) -> None:
        if index < len(self.files):
            self._set_row(index, self.files[index], "转换中…",
                          palette(self._dark)["accent"])
            self.status_label.setText(f"正在转换：{self.files[index].name}")
        self.progress.setValue(index)
        self.progress.setFormat(f"{index} / {len(self.files)}")

    def _on_done(self, index: int, result) -> None:
        if index < len(self.files):
            self._set_row(index, self.files[index], "完成",
                          palette(self._dark)["ok"])
        self.progress.setValue(index + 1)
        self.progress.setFormat(f"{index + 1} / {len(self.files)}")
        self._last_output = Path(result.output)
        engine = f" · {result.engine}" if getattr(result, "engine", None) else ""
        self._log(f"[完成] {result.source.name} -> {result.output}{engine}")
        for warning in result.warnings:
            self._log(f"       提示：{warning}")

    def _on_failed(self, index: int, message: str) -> None:
        if index < len(self.files):
            self._set_row(index, self.files[index], "失败",
                          palette(self._dark)["error"])
        self.progress.setValue(index + 1)
        self.progress.setFormat(f"{index + 1} / {len(self.files)}")
        name = self.files[index].name if index < len(self.files) else "?"
        self._log(f"[失败] {name}：{message}")

    def _on_all_done(self, success: int, failures: int, cancelled: bool) -> None:
        self._worker = None
        self._set_busy(False)
        self._update_pdf_options()
        self._refresh_placeholder()
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        if cancelled:
            self.progress.setFormat("已取消")
            self.status_label.setText(
                f"已取消：成功 {success} 个，失败 {failures} 个")
        else:
            self.progress.setFormat("完成")
            self.status_label.setText(f"转换完成：成功 {success} 个，失败 {failures} 个")
        self._log("")
        self._log(f"全部完成：成功 {success} 个，失败 {failures} 个"
                  + ("（已取消）" if cancelled else ""))
        self.open_button.setEnabled(success > 0)
        if success and self.open_after.isChecked():
            self._open_output()
        if failures and not cancelled:
            self.status_label.setText(
                f"转换完成：成功 {success} 个，失败 {failures} 个（详见日志）")

    # ------------------------------------------------------------------ 打开
    def _current_output(self) -> Path:
        text = self.output_edit.text().strip()
        if text:
            return Path(text).expanduser()
        return self._default_output()

    def _default_output(self) -> Path:
        if self.files:
            return self.files[0].parent / "converted"
        return Path.home() / "Documents" / "doc-converter"

    def _open_output(self) -> None:
        directory = self._current_output()
        if not directory.exists() and self._last_output is not None:
            directory = self._last_output.parent
        if directory.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        else:
            self.status_label.setText("输出目录尚不存在")

    def _open_selected_output(self) -> None:
        rows = {index.row() for index in self.table.selectedIndexes()}
        if not rows:
            return
        name = self.files[min(rows)].stem
        target = self.target_combo.currentData()
        candidate = self._current_output() / f"{name}.{target}"
        if candidate.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(candidate)))

    # ------------------------------------------------------------------ 其他
    def _update_pdf_options(self) -> None:
        enabled = (self.target_combo.currentData() or "pdf") == "pdf"
        for widget in (self.engine_combo, self.page_combo):
            widget.setEnabled(enabled and self._worker is None)

    def _apply_theme(self) -> None:
        self.setStyleSheet(stylesheet(self._dark))
        palette_colors = palette(self._dark)
        self.theme_button.setText("☀️" if self._dark else "🌙")
        # 样式表会改变控件的 sizeHint，必须让布局重新计算，否则按钮会互相覆盖
        central = self.centralWidget()
        if central is not None and central.layout() is not None:
            _invalidate_layout_tree(central.layout())
            central.layout().activate()
        for row, path in enumerate(self.files):
            item = self.table.item(row, 3)
            status = item.text() if item is not None else "等待"
            color = None
            if status == "完成":
                color = palette_colors["ok"]
            elif status == "失败":
                color = palette_colors["error"]
            elif status.startswith("转换"):
                color = palette_colors["accent"]
            elif status == "等待":
                color = palette_colors["muted"]
            self._set_row(row, path, status, color)

    def _toggle_theme(self) -> None:
        self._dark = not self._dark
        self._apply_theme()
        self.settings.setValue("theme", "dark" if self._dark else "light")

    def _show_about(self) -> None:
        engines = "、".join(available_engines()) or "无"
        QMessageBox.about(
            self, "关于 文档转换助手",
            f"<h3>文档转换助手 DocConverter {__version__}</h3>"
            "<p>PDF / DOCX / HTML / Markdown 任意互转，完全本地处理，"
            "不上传任何文件。</p>"
            f"<p>可用 PDF 引擎：{engines}</p>"
            "<p>PDF 输出优先调用本机 Microsoft Edge / Chrome 的无头打印，"
            "排版质量最佳；未安装时自动降级。</p>"
            "<p>许可证：MIT</p>")

    def _log(self, message: str) -> None:
        if message == "":
            self.log_view.appendPlainText("")
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {message}")
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ------------------------------------------------------------------ 拖拽
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.drop_area.setProperty("dragActive", True)
            self._restyle(self.drop_area)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.drop_area.setProperty("dragActive", False)
        self._restyle(self.drop_area)

    def dropEvent(self, event) -> None:  # noqa: N802
        self.drop_area.setProperty("dragActive", False)
        self._restyle(self.drop_area)
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()
                 if url.isLocalFile()]
        if paths:
            self._add_paths(paths)
        event.acceptProposedAction()

    @staticmethod
    def _restyle(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    # ------------------------------------------------------------------ 设置
    def _restore_settings(self) -> None:
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        target = self.settings.value("target", "pdf")
        index = self.target_combo.findData(target)
        if index >= 0:
            self.target_combo.setCurrentIndex(index)
        engine = self.settings.value("engine", "auto")
        index = self.engine_combo.findData(engine)
        if index >= 0:
            item = self.engine_combo.model().item(index)
            if item is not None and item.isEnabled():
                self.engine_combo.setCurrentIndex(index)
        page = self.settings.value("page", "A4")
        index = self.page_combo.findData(page)
        if index >= 0:
            self.page_combo.setCurrentIndex(index)
        assets = self.settings.value("assets", "auto")
        index = self.assets_combo.findData(assets)
        if index >= 0:
            self.assets_combo.setCurrentIndex(index)
        self.output_edit.setText(self.settings.value("output", ""))
        self.output_edit.setCursorPosition(0)
        self.open_after.setChecked(
            self.settings.value("open_after", "true") in (True, "true", "1"))

    def _save_settings(self) -> None:
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("target", self.target_combo.currentData())
        self.settings.setValue("engine", self.engine_combo.currentData())
        self.settings.setValue("page", self.page_combo.currentData())
        self.settings.setValue("assets", self.assets_combo.currentData())
        self.settings.setValue("output", self.output_edit.text().strip())
        self.settings.setValue("open_after",
                               "true" if self.open_after.isChecked() else "false")

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None:
            answer = QMessageBox.question(
                self, "正在转换",
                "还有文件正在转换，确定要退出吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self._worker.cancel()
            self._worker.wait(3000)
        self._save_settings()
        event.accept()


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    return label


def _form_row(label_text: str, *widgets: QWidget,
              stretch_last: bool = True) -> QHBoxLayout:
    """一行「标签 + 控件」的表单布局，节省纵向空间。"""
    row = QHBoxLayout()
    row.setSpacing(8)
    label = _field_label(label_text)
    label.setFixedWidth(62)
    row.addWidget(label)
    for index, widget in enumerate(widgets):
        last = index == len(widgets) - 1
        row.addWidget(widget, 1 if (last and stretch_last) else 0)
        if not last:
            row.setStretch(row.count() - 1, 1)
    return row


def _invalidate_layout_tree(layout) -> None:
    """递归失效所有子布局的缓存，确保样式表改动后尺寸重新计算。"""
    if layout is None:
        return
    layout.invalidate()
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item is None:
            continue
        if item.layout() is not None:
            _invalidate_layout_tree(item.layout())
        widget = item.widget()
        if widget is not None and widget.layout() is not None:
            _invalidate_layout_tree(widget.layout())


def _app_icon() -> QIcon:
    """用 QPainter 画出应用图标，避免依赖外部资源文件。"""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#2563eb"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 14, 14)
    painter.setPen(QColor("#ffffff"))
    font = QFont("Segoe UI", 26, QFont.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "文")
    painter.end()
    return QIcon(pixmap)


def run() -> int:
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(ORG_NAME)
    QApplication.setApplicationVersion(__version__)
    app = QApplication(sys.argv)
    app.setWindowIcon(_app_icon())
    window = MainWindow()
    window.show()
    return app.exec()


def main() -> int:
    return run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
