"""命令行接口测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc_converter import __version__
from doc_converter.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_list_engines(capsys):
    code = main(["--list-engines"])
    assert code == 0
    assert "可用 PDF 引擎" in capsys.readouterr().out


def test_single_file(rich_md_file, tmp_path, capsys):
    code = main([str(rich_md_file), "-t", "html", "-o", str(tmp_path)])
    assert code == 0
    output = capsys.readouterr().out
    assert "[OK]" in output
    assert (tmp_path / "rich.html").exists()


def test_quiet_mode(rich_md_file, tmp_path, capsys):
    code = main([str(rich_md_file), "-t", "html", "-o", str(tmp_path), "-q"])
    assert code == 0
    assert capsys.readouterr().out == ""


def test_missing_file(tmp_path, capsys):
    code = main([str(tmp_path / "missing.md"), "-t", "html", "-o", str(tmp_path)])
    assert code == 1
    assert "[FAIL]" in capsys.readouterr().err


def test_no_inputs(capsys):
    with pytest.raises(SystemExit):
        main(["-t", "html"])


def test_batch_conflict(rich_md_file, rich_html_file, tmp_path, capsys):
    code = main([str(rich_md_file), str(rich_html_file), "-t", "docx",
                 "-o", str(tmp_path)])
    assert code == 0
    outputs = sorted(path.name for path in tmp_path.glob("*.docx"))
    assert len(outputs) == 2, outputs
    assert "conflict" in capsys.readouterr().err


def test_glob_expansion(rich_md_file, tmp_path, capsys):
    pattern = str(rich_md_file.parent / "*.md")
    code = main([pattern, "-t", "html", "-o", str(tmp_path)])
    assert code == 0
    assert (tmp_path / "rich.html").exists()


def test_directory_input_recursive(rich_md_file, tmp_path, capsys):
    directory = tmp_path / "docs"
    directory.mkdir()
    (directory / "a.md").write_text("# A\n\n正文", encoding="utf-8")
    (directory / "sub").mkdir()
    (directory / "sub" / "b.html").write_text("<h1>B</h1>", encoding="utf-8")
    output = tmp_path / "from_dir"
    code = main([str(directory), "-t", "docx", "-o", str(output)])
    assert code == 0
    names = {path.name for path in output.glob("*.docx")}
    assert names == {"a.docx", "b.docx"}


def test_strict_stops_on_first_failure(rich_md_file, tmp_path, capsys):
    missing = tmp_path / "missing.md"
    code = main([str(missing), str(rich_md_file), "-t", "html",
                 "-o", str(tmp_path), "--strict"])
    assert code == 1
    assert "[FAIL]" in capsys.readouterr().err


def test_pdf_engine_option(rich_md_file, tmp_path, capsys):
    from doc_converter.pdf_engine import available_engines

    if not available_engines():
        pytest.skip("没有可用的 PDF 引擎")
    code = main([str(rich_md_file), "-t", "pdf", "-o", str(tmp_path),
                 "--pdf-engine", available_engines()[0]])
    assert code == 0
    assert (tmp_path / "rich.pdf").exists()
