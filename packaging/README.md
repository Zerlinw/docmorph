# Windows 打包说明

## 环境

- Windows 10 / 11 x64
- Python 3.10 ~ 3.14
- `PySide6`、`PyInstaller`、项目运行依赖

```bash
python -m pip install -r requirements.txt
python -m pip install PySide6 pyinstaller
```

## 一键构建

```bash
python packaging/build.py          # GUI + CLI
python packaging/build.py --gui    # 仅 DocConverter.exe
python packaging/build.py --cli    # 仅 doc-converter.exe
```

构建脚本会先用 Pillow 生成 `assets/app.ico` 与 `assets/version_info.txt`，然后调用
PyInstaller 生成单文件 exe。

## 产物

| 文件 | 入口 | 说明 |
| --- | --- | --- |
| `dist/DocConverter.exe` | `launcher.py` | 桌面版，`console=False`；无参数启动 GUI，带参数时附加到父控制台执行 CLI |
| `dist/doc-converter.exe` | `cli_launcher.py` | 纯命令行版，不包含 PySide6，体积更小 |

## 打包要点

- `pathex` 指向 `src/`，因此无需先 `pip install -e .`；
- 收集 `docx` 的默认模板、`xhtml2pdf`/`reportlab` 的资源与字体、`pdfminer` 的 CMaps；
- 显式排除 PySide6 中未使用的 Qt 模块与 `weasyprint`（Windows 上通常缺少 GTK/Pango）；
- `console=False` 的 GUI 版在从终端调用时，由 `doc_converter.launcher._attach_console()`
  附加到父控制台，因此 `DocConverter.exe --help` 依旧可用；
- 单文件模式启动时会解压到 `%TEMP%\_MEIxxxx`，首次启动约 2–5 秒。

## 校验

```bash
dist\doc-converter.exe --version
dist\doc-converter.exe --list-engines
dist\doc-converter.exe samples\sample.docx -t md -o out\
dist\DocConverter.exe
```

> 如果系统没有 Microsoft Edge / Chrome，PDF 输出会自动使用内置的 xhtml2pdf 引擎；
> 可通过 `--pdf-engine xhtml2pdf` 强制验证兜底路径。
