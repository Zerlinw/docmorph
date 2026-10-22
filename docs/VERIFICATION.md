# 验证报告（Windows 打包版）

本文件记录 2.0.0 版本的功能自测、打包与运行验证结果，便于复现。

## 验证环境

| 项目 | 值 |
| --- | --- |
| 操作系统 | Windows 11 家庭版 10.0.22621 |
| Python | 3.14.5 |
| Microsoft Edge | 147.0.3912.98（Chromium 打印引擎） |
| 构建环境 | `.venv-build`（uv 创建，仅含运行/打包依赖） |

主要依赖版本：

| 包 | 版本 |
| --- | --- |
| python-docx | 1.2.0 |
| beautifulsoup4 | 4.15.0 |
| lxml | 6.1.3 |
| markdown-it-py | 4.2.0 |
| linkify-it-py | 2.2.0 |
| pdfplumber | 0.11.10 |
| pypdf | 6.21.2 |
| Pillow | 13.0.0 |
| xhtml2pdf | 0.2.27 |
| reportlab | 5.0.1 |
| PySide6 | 6.12.0 |
| PyInstaller | 6.22.3 |
| pytest | 9.1.1 |

## 自动化测试

```bash
.venv-build\Scripts\python.exe -m pytest -q
# 84 passed in 15~20s
```

测试覆盖：

- 12 种互转组合的产物存在性、结构/内容断言；
- DOCX：本地化样式名、`w:numPr` 编号、内容控件、修订插入、文本框、合并表格、超链接、图片、OLE 附件；
- HTML：嵌套列表、`colspan/rowspan`、题注、裸文本、定义列表、代码块、元数据；
- Markdown：嵌套列表、Mermaid、front matter、内联 HTML、表格转义；
- PDF：标题/段落/列表/表格重建、页眉页脚剔除、元数据；
- 写入器：Markdown 转义与编号、HTML 语义结构/打印样式、DOCX 编号重启/合并单元格/图片替代文字、PDF 引擎与附件嵌入；
- CLI：版本、引擎列表、单文件、批量冲突、glob、目录递归、strict；
- GUI（offscreen）：窗口、增删文件、目录过滤、PDF 选项联动、主题、完整转换流程、失败状态。

## 示例矩阵（源码环境）

```bash
python examples/make_samples.py
python examples/convert_all.py
# matrix done: 12/12 succeeded
```

## 打包产物

| 文件 | 大小 | SHA-256 |
| --- | --- | --- |
| `dist/DocConverter.exe` | 82.2 MB | `27812D39126DD7ABFB234A1EB76D8BBCC2D7038940ECE6C6404C7636871C1401` |
| `dist/doc-converter.exe` | 45.7 MB | `FF12D7F3BDC6E393CDDD97FF2A00B2E1A43A37C1F6B568791245F568A1A4248E` |

版本资源：`FileVersion=2.0.0`、`ProductName=DocConverter`、`FileDescription=文档转换助手 DocConverter`。

## 打包版功能验证

```bat
dist\doc-converter.exe --version          → doc-converter 2.0.0
dist\doc-converter.exe --list-engines     → chromium / xhtml2pdf（WeasyPrint 正确报告缺少 GTK）
dist\doc-converter.exe samples\sample.md -t pdf -o out
dist\doc-converter.exe samples\sample.docx -t md -o out
dist\DocConverter.exe --version           → doc-converter 2.0.0（GUI 版附加控制台）
dist\DocConverter.exe                     → 打开桌面窗口“文档转换助手 DocConverter 2.0.0”
```

冻结版 12 种互转全部通过：

```
frozen matrix: 12 ok, 0 fail
```

PDF 兜底引擎（xhtml2pdf，STSong-Light CID 字体）验证：

```bat
dist\doc-converter.exe samples\sample.md -t pdf -o out --pdf-engine xhtml2pdf
```

- 输出 3 页 A4；
- `pdfplumber` 提取文本包含“文档转换助手示例文档”，中文可复制、可检索；
- 不再出现 `nnnn` 占位字符或方框。

## 视觉检查

- `docs/gui-light.png`、`docs/gui-dark.png`：桌面版浅色/深色主题；
- `docs/gui-exe.png`：打包后的 `DocConverter.exe` 实际窗口；
- `samples/sample.pdf`：Chromium 排版结果，中文、表格、代码块、图片均正常。

## 已知限制

- PDF 为版式格式，扫描件/复杂分栏/公式无法结构化还原；
- DOCX 的 SmartArt、图表、公式、批注、页眉页脚不在 IR 范围内；
- Markdown 不支持单元格合并；
- 加密文档需先解除保护；
- Windows 上 WeasyPrint 通常缺少 GTK/Pango，会自动降级（不影响使用）。
