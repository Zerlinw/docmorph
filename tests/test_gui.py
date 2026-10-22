"""桌面 GUI 的无头冒烟测试。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")


def _wait_worker(window, app, timeout: float = 180.0) -> None:
    started = time.monotonic()
    while window._worker is not None and time.monotonic() - started < timeout:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    assert window._worker is None, "转换线程超时"


def test_window_builds(main_window):
    assert main_window.windowTitle().startswith("文档转换助手")
    assert main_window.target_combo.count() == 4
    assert main_window.start_button.isEnabled() is False
    assert main_window.progress is not None


def test_add_and_remove_files(main_window, rich_md_file):
    main_window._add_paths([rich_md_file])
    assert len(main_window.files) == 1
    assert main_window.table.rowCount() == 1
    assert main_window.table.item(0, 0).text() == "rich.md"
    assert main_window.table.item(0, 3).text() == "等待"
    main_window.table.selectRow(0)
    main_window._remove_selected()
    assert main_window.files == []


def test_add_directory_filters_supported(main_window, rich_md_file, tmp_path):
    directory = tmp_path / "bundle"
    directory.mkdir()
    (directory / "a.md").write_text("# A", encoding="utf-8")
    (directory / "b.txt").write_text("ignore", encoding="utf-8")
    (directory / "c.html").write_text("<h1>C</h1>", encoding="utf-8")
    main_window._add_paths([directory])
    names = {path.name for path in main_window.files}
    assert names == {"a.md", "c.html"}


def test_duplicate_files_ignored(main_window, rich_md_file):
    main_window._add_paths([rich_md_file, rich_md_file])
    assert len(main_window.files) == 1


def test_pdf_options_toggle(main_window):
    index = main_window.target_combo.findData("md")
    main_window.target_combo.setCurrentIndex(index)
    assert main_window.engine_combo.isEnabled() is False
    index = main_window.target_combo.findData("pdf")
    main_window.target_combo.setCurrentIndex(index)
    assert main_window.engine_combo.isEnabled() is True


def test_theme_toggle(main_window):
    original = main_window._dark
    main_window._toggle_theme()
    assert main_window._dark is not original
    main_window._toggle_theme()
    assert main_window._dark is original


def test_conversion_flow(main_window, qt_app, rich_md_file, tmp_path):
    output = tmp_path / "gui-out"
    main_window.output_edit.setText(str(output))
    main_window._add_paths([rich_md_file])
    main_window.target_combo.setCurrentIndex(
        main_window.target_combo.findData("html"))
    main_window.open_after.setChecked(False)
    main_window._start()
    _wait_worker(main_window, qt_app)
    assert main_window.table.item(0, 3).text() == "完成"
    assert (output / "rich.html").exists()
    assert "成功 1 个" in main_window.status_label.text()
    assert "[完成]" in main_window.log_view.toPlainText()
    assert main_window.open_button.isEnabled() is True


def test_failure_marks_row(main_window, qt_app, tmp_path):
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"not a real docx")
    main_window.output_edit.setText(str(tmp_path / "out"))
    main_window._add_paths([broken])
    main_window.target_combo.setCurrentIndex(
        main_window.target_combo.findData("html"))
    main_window.open_after.setChecked(False)
    main_window._start()
    _wait_worker(main_window, qt_app)
    assert main_window.table.item(0, 3).text() == "失败"
    assert "失败 1 个" in main_window.status_label.text()
