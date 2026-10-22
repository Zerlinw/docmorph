"""pytest 配置与共享 fixture。"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# GUI 测试在无桌面环境下使用 offscreen 后端
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

SAMPLES = ROOT / "samples"


def pytest_configure(config):
    config.addinivalue_line("markers", "pdf: 需要可用的 PDF 排版引擎")
    config.addinivalue_line("markers", "gui: 需要 PySide6")


# ---------------------------------------------------------------------------
# 基础资源
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def png_bytes() -> bytes:
    from PIL import Image

    image = Image.new("RGB", (120, 60), (37, 99, 235))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture(scope="session")
def workdir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("doc_converter_tests")


# ---------------------------------------------------------------------------
# 富文本 Markdown / HTML
# ---------------------------------------------------------------------------

RICH_MD = """---
title: 富文本示例
author: 测试作者
---

# 富文本示例

第一段包含 **加粗**、*斜体*、~~删除线~~、`行内代码` 与 [链接](https://example.com "示例")。

## 列表

1. 第一步
2. 第二步
   - 子项 A
   - 子项 B
3. 第三步

- 无序一
- 无序二

## 表格

| 名称 | 数量 | 备注 |
| --- | ---: | :--- |
| 苹果 | 3 | 红 |
| 香蕉 | 5 | 黄 |

## 流程图

```mermaid
graph TD
    A["开始"] --> B["处理"]
    B --> C["结束"]
```

## 代码

```python
def hello(name: str) -> str:
    return f"你好，{name}"
```

> 引用第一段
>
> 引用第二段

---

![示意图](diagram.png)
"""

RICH_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>富文本示例</title>
<meta name="author" content="测试作者">
<style>body{font-family:"Microsoft YaHei",sans-serif}</style>
</head>
<body>
<h1>富文本示例</h1>
<p>第一段包含<strong>加粗</strong>、<em>斜体</em>、<del>删除线</del>、
<code>行内代码</code>与<a href="https://example.com" title="示例">链接</a>。</p>
<h2>列表</h2>
<ol>
  <li>第一步</li>
  <li>第二步
    <ul><li>子项 A</li><li>子项 B</li></ul>
  </li>
  <li>第三步</li>
</ol>
<h2>表格</h2>
<table>
  <caption>数据表</caption>
  <thead><tr><th>名称</th><th colspan="2">指标</th></tr></thead>
  <tbody>
    <tr><td>苹果</td><td>3</td><td>红</td></tr>
    <tr><td>香蕉</td><td>5</td><td>黄</td></tr>
  </tbody>
</table>
<h2>代码</h2>
<pre><code class="language-python">def hello(name):\n    return "你好，" + name\n</code></pre>
<blockquote><p>引用第一段</p><p>引用第二段</p></blockquote>
<hr>
<figure><img src="diagram.png" alt="示意图" width="320"><figcaption>示意图题注</figcaption></figure>
</body>
</html>
"""


@pytest.fixture(scope="session")
def rich_md_file(workdir, png_bytes) -> Path:
    (workdir / "diagram.png").write_bytes(png_bytes)
    path = workdir / "rich.md"
    path.write_text(RICH_MD, encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def rich_html_file(workdir, png_bytes) -> Path:
    (workdir / "diagram.png").write_bytes(png_bytes)
    path = workdir / "rich.html"
    path.write_text(RICH_HTML, encoding="utf-8")
    (workdir / "attachment.bin").write_bytes(b"binary-attachment")
    return path


@pytest.fixture(scope="session")
def rich_docx_file(workdir, png_bytes) -> Path:
    from helpers import build_word_like_docx

    (workdir / "diagram.png").write_bytes(png_bytes)
    path = workdir / "rich.docx"
    build_word_like_docx(path, workdir / "diagram.png")
    return path


@pytest.fixture(scope="session")
def rich_pdf_file(workdir, rich_html_file) -> Path:
    """用真实排版引擎生成 PDF（优先 Chromium，保证字体与表格可提取）。"""
    from doc_converter import convert
    from doc_converter.errors import ConversionError
    from doc_converter.pdf_engine import available_engines

    if not available_engines():
        pytest.skip("没有可用的 PDF 排版引擎")
    path = workdir / "rich.pdf"
    try:
        convert(rich_html_file, target="pdf", output=path)
    except ConversionError as exc:  # pragma: no cover
        pytest.skip(f"PDF 生成失败: {exc}")
    return path


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def qt_app():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def main_window(qt_app):
    from doc_converter.gui.app import MainWindow

    window = MainWindow()
    window._dark = False
    window._apply_theme()
    window.resize(1040, 780)
    window.show()
    qt_app.processEvents()
    yield window
    window.close()
    window.deleteLater()
    qt_app.processEvents()
