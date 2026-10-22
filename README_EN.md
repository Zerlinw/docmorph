# doc-converter · Document Converter

<div align="center">

**Convert PDF / DOCX / HTML / Markdown in any direction — desktop app + CLI + Python API**

[![Python](https://img.shields.io/badge/Python-3.10%20~%203.14-blue?logo=python&logoColor=white)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/GUI-PySide6%206.12-41cd52?logo=qt&logoColor=white)](https://doc.qt.io/qtforpython/)
[![Tests](https://img.shields.io/badge/tests-84%20passed-brightgreen)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Windows](https://img.shields.io/badge/Windows-10%20%2F%2011-0078d4?logo=windows&logoColor=white)]()

**Language**：[English](README_EN.md) | [简体中文](README.md)

</div>

---

## Overview

`doc-converter` is a fully offline document conversion tool supporting **all 12 conversion
combinations between PDF, DOCX, HTML and Markdown**. It ships as:

- a **Windows desktop app** (`DocConverter.exe`) with drag & drop and batch conversion;
- a **command-line tool** (`doc-converter.exe` / `python -m doc_converter`);
- a **Python API** (`convert()` / `convert_batch()`).

All readers parse their source into a single **intermediate representation (IR)**, and all
writers render that IR into the target format:

```
source ──► Reader ──► Document (IR) ──► Writer ──► target
        (docx/html/md/pdf)        (md/html/docx/pdf)
```

The IR covers headings, paragraphs, nested lists, tables (header / colspan / rowspan),
hyperlinks, inline and block images, code blocks, quotes, flowcharts, binary attachments
and document metadata.

![Desktop UI](docs/gui-light.png)

## Features

| Capability | Details |
| --- | --- |
| Any-to-any | 12 conversion combinations between 4 formats |
| Structure | Headings, paragraphs, ordered/unordered/nested lists, tables with header and merged cells, quotes, code blocks, horizontal rules |
| Inline styles | Bold, italic, underline, strikethrough, inline code, superscript/subscript, colour |
| Lists | Nested list hierarchy preserved for Markdown/HTML; DOCX `w:numPr` numbering (including localized style names) with per-list numbering restart |
| Images | DOCX binary extraction, PDF original stream/crop fallback, HTML/MD relative paths, data URIs and remote URLs; written as sidecar files, inlined as data URIs, or embedded natively in DOCX, with alt text |
| Flowcharts | DOCX grouped shapes → nodes; Mermaid → flow structure with source preserved; rendered per target (MD/HTML → Mermaid, DOCX → boxes + Mermaid appendix, PDF → text structure) |
| Attachments | DOCX OLE objects and PDF embedded files are extracted; PDF output also embeds the attachment inside the PDF itself |
| Metadata | DOCX/PDF title, author, subject, keywords; HTML `<meta>`/`<title>`; PDF `/Title`, `/Author` |
| PDF engines | Auto: local Edge/Chrome headless → WeasyPrint → xhtml2pdf, with automatic fallback and warnings |
| Batch | Multiple files, globs, recursive directories, collision-safe output names, per-file error isolation |
| Diagnostics | Every imperfect conversion emits a warning in `ConversionResult.warnings`; nothing is silently dropped |
| Desktop UX | Drag & drop, per-file progress and status, live log, light/dark themes, cancel, open output folder, remembered settings |

| Light | Dark |
| --- | --- |
| ![Light](docs/gui-light.png) | ![Dark](docs/gui-dark.png) |

## Run on Windows

Run `dist\DocConverter.exe` (single file, no Python installation required):

1. Double-click `DocConverter.exe`;
2. Drop files or folders into the window, or use “Add files / Add folder”;
3. Pick the target format, output folder and PDF options;
4. Click “Start” and watch the log for results and warnings.

> **PDF quality**: Windows 10/11 ships Microsoft Edge; the app uses its headless print
> engine for the best CJK/table/code layout. If Edge is unavailable it falls back to the
> bundled pure-Python engine automatically.

The same executable also accepts CLI arguments:

```bat
DocConverter.exe --help
DocConverter.exe report.docx -t md -o out\
DocConverter.exe "docs\*.pdf" -t docx -o out\ --pdf-engine chromium
```

## Command line

```bash
doc-converter report.docx -t md -o out/              # single file
doc-converter "docs/*.docx" -t pdf -o out/           # glob
doc-converter docs/ -t html -o out/                  # recursive directory
doc-converter report.md -t pdf --pdf-engine chromium --page-size A4
doc-converter input.bin -f html -t docx -o out/ --assets embed
```

```
usage: doc-converter [-h] -t {pdf,docx,html,md} [-f {pdf,docx,html,md}]
                     [-o PATH] [--assets {file,datauri,embed}]
                     [--pdf-engine {auto,chromium,weasyprint,xhtml2pdf}]
                     [--page-size {A4,Letter,A3,A5}] [-q] [--strict]
                     [--list-engines] [-V] [INPUT ...]
```

| Option | Description |
| --- | --- |
| `INPUT` | Files / globs / directories (recursively collected) |
| `-t, --to` | Target format: `pdf`, `docx`, `html`, `md` |
| `-f, --from` | Source format (inferred from extension by default) |
| `-o, --output` | Output directory or explicit file path |
| `--assets` | Asset mode: `file`, `datauri`, `embed` |
| `--pdf-engine` | `auto` (default), `chromium`, `weasyprint`, `xhtml2pdf` |
| `--page-size` | `A4` (default), `Letter`, `A3`, `A5` |
| `--list-engines` | Show available PDF engines |
| `-q, --quiet` / `--strict` | Errors only / stop at first failure |

## Python API

```python
from doc_converter import convert, convert_batch

result = convert("report.docx", target="md", output="out/")
print(result.output, result.warnings, result.assets)

result = convert("report.md", target="pdf", output="out/",
                 pdf_engine="chromium", page_size="A4")
print(result.engine)

results, failures = convert_batch(["a.docx", "b.html"], target="md", output="out/")
```

## Conversion matrix

| Source \ Target | Markdown | HTML | DOCX | PDF |
| --- | :-: | :-: | :-: | :-: |
| **Markdown** | — | ✅ | ✅ | ✅ |
| **HTML** | ✅ | — | ✅ | ✅ |
| **DOCX** | ✅ | ✅ | — | ✅ |
| **PDF** | ⚠️ | ⚠️ | ⚠️ | — |

✅ full support · ⚠️ heuristic reconstruction (PDF is layout-driven; headings are inferred
from font sizes, tables from borders/column alignment, links from annotations — all with
warnings).

### Known limitations

- PDF: multi-column layouts, formulas and scanned PDFs cannot be reconstructed as text;
  scanned pages produce images.
- DOCX: SmartArt, charts, equations, comments and headers/footers are outside the IR and
  are skipped or degraded; flowchart connector relationships fall back to sequential edges.
- Markdown has no cell-merging syntax, so `colspan/rowspan` is expanded into empty cells.
- Encrypted / password-protected files must be unlocked first.

## PDF engines

1. **Chromium / Microsoft Edge** (default on Windows 10/11) — best CSS, tables, CJK and
   code block rendering, zero extra dependencies;
2. **WeasyPrint** — requires GTK/Pango (common on Linux/macOS);
3. **xhtml2pdf** — pure-Python fallback with embedded system CJK fonts.

```bash
doc-converter --list-engines
```

## Development

```bash
pip install -e ".[gui]"
python -m doc_converter          # CLI
python -m doc_converter.gui      # desktop
```

Python **3.10+** (tested on 3.14).

## Tests

```bash
pip install pytest
pytest -q          # 84 passed
```

Covers the 12-way matrix, readers (DOCX localized styles/`w:numPr`/content controls/revisions/
text boxes/merged tables, HTML nested lists/spans, Markdown nesting/Mermaid/front matter,
PDF headings/paragraphs/lists/tables/header-footer removal), writers (Markdown escaping,
HTML structure, DOCX numbering restarts/merges/alt text/hyperlinks, PDF engines/page size/
attachments), CLI behaviour, and the Qt GUI in offscreen mode.

## Build the Windows executables

```bash
python -m pip install pyinstaller PySide6
python packaging/build.py          # both GUI and CLI
```

| Artifact | Description |
| --- | --- |
| `dist/DocConverter.exe` | Desktop app (single file, windowed, CLI args supported) |
| `dist/doc-converter.exe` | CLI-only build (smaller) |

Specs: `packaging/DocConverter.spec`, `packaging/doc-converter-cli.spec`.

## License

[MIT](LICENSE)
