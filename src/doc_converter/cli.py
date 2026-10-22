"""doc_converter.cli —— 命令行入口。

用法示例::

    # 单文件转换
    python -m doc_converter report.docx -t md -o out/

    # 批量转换（支持 glob 与目录，目录会递归收集受支持的文件）
    python -m doc_converter "docs/*.docx" -t pdf -o out/
    python -m doc_converter docs/ -t html -o out/

    # 指定 PDF 引擎与页面尺寸
    python -m doc_converter report.md -t pdf --pdf-engine chromium --page-size A4

    # 显式指定源格式与资源模式
    python -m doc_converter input.bin -f html -t docx -o out/ --assets embed
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .converter import SUPPORTED_FORMATS, convert, normalize_format
from .errors import ConversionError
from .pdf_engine import ENGINE_CHOICES, PAGE_SIZES, available_engines
from .utils import ext_of

_SOURCE_EXTS = {"pdf", "docx", "doc", "html", "htm", "xhtml", "md", "markdown",
                "mdown", "mkd", "mkdn"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doc-converter",
        description="PDF / DOCX / HTML / Markdown 任意互转的文档转换助手",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python -m doc_converter report.docx -t md -o out/\n"
            "  python -m doc_converter \"docs/*.docx\" -t pdf -o out/\n"
            "  doc-converter page.html -t docx --pdf-engine chromium\n"
        ),
    )
    parser.add_argument("inputs", nargs="*", metavar="INPUT",
                        help="输入文件 / glob / 目录（目录会递归收集），可传多个")
    parser.add_argument("-t", "--to", "--target", dest="target",
                        default=None, choices=list(SUPPORTED_FORMATS),
                        help="目标格式（必填）")
    parser.add_argument("-f", "--from", "--source", dest="source",
                        default=None, choices=list(SUPPORTED_FORMATS),
                        help="源格式（缺省按扩展名推断）")
    parser.add_argument("-o", "--output", dest="output", default=None,
                        metavar="PATH",
                        help="输出路径：目录或具体文件（仅单文件时有效）")
    parser.add_argument("--assets", dest="assets", default=None,
                        choices=["file", "datauri", "embed"],
                        help="图片等资源的落地方式（缺省按目标格式自动选择）")
    parser.add_argument("--pdf-engine", dest="pdf_engine", default="auto",
                        choices=list(ENGINE_CHOICES),
                        help="PDF 排版引擎（默认 auto：优先本机 Edge/Chrome）")
    parser.add_argument("--page-size", dest="page_size", default="A4",
                        choices=list(PAGE_SIZES), help="PDF 页面尺寸（默认 A4）")
    parser.add_argument("-q", "--quiet", action="store_true", help="仅输出错误")
    parser.add_argument("--strict", action="store_true",
                        help="批量模式下遇到首个失败即中止")
    parser.add_argument("--list-engines", action="store_true",
                        help="列出可用的 PDF 引擎后退出")
    parser.add_argument("-V", "--version", action="version",
                        version=f"%(prog)s {__version__}")
    return parser


def _expand_inputs(patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        path = Path(pattern)
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and ext_of(child) in _SOURCE_EXTS:
                    files.append(child)
            continue
        import glob
        matched = sorted(glob.glob(pattern, recursive=True))
        if matched:
            files.extend(Path(m) for m in matched if Path(m).is_file())
        else:
            files.append(path)
    # 去重并保持顺序
    seen: set[Path] = set()
    unique: list[Path] = []
    for file in files:
        key = file.resolve() if file.exists() else file
        if key not in seen:
            seen.add(key)
            unique.append(file)
    return unique


def _configure_console() -> None:
    """让控制台输出兼容非 ASCII 字符（Windows 默认代码页可能无法编码）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass


def _unique_output(src: Path, args, used: set) -> str | None:
    """批量模式下避免不同源文件的同名输出互相覆盖。"""
    if args.output is None:
        base_dir, stem = src.parent, src.stem
    else:
        out = Path(args.output)
        if out.suffix and not out.is_dir():
            return args.output  # 显式指定了具体文件名，尊重用户选择
        base_dir, stem = out, src.stem
    candidate = (base_dir / f"{stem}.{args.target}").resolve()
    if candidate not in used:
        return args.output
    prefixed = base_dir / f"{ext_of(src)}-{stem}.{args.target}"
    print(f"[WARN] output name conflict: {candidate.name} exists, "
          f"renamed to {prefixed.name}", file=sys.stderr)
    return str(prefixed)


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_engines:
        from .pdf_engine import engine_diagnostics
        diagnostics = engine_diagnostics()
        engines = available_engines()
        print("可用 PDF 引擎: " + (", ".join(engines) if engines else "（无）"))
        for text in diagnostics.values():
            print(f"  - {text}")
        return 0

    if not args.target:
        parser.error("必须指定目标格式 -t/--to")
    if not args.inputs:
        parser.error("至少需要一个输入文件")

    try:
        normalize_format(args.target)
    except ConversionError as exc:
        parser.error(str(exc))

    inputs = _expand_inputs(args.inputs)
    if not inputs:
        print("错误: 没有匹配到任何输入文件", file=sys.stderr)
        return 2

    failures = 0
    used_outputs: set[Path] = set()
    for src in inputs:
        output = _unique_output(src, args, used_outputs)
        try:
            result = convert(
                src, target=args.target, source_format=args.source,
                output=output, asset_mode=args.assets,
                pdf_engine=args.pdf_engine, page_size=args.page_size)
        except ConversionError as exc:
            failures += 1
            print(f"[FAIL] {exc}", file=sys.stderr)
            if args.strict:
                return 1
            continue
        except KeyboardInterrupt:  # pragma: no cover
            print("\n已取消", file=sys.stderr)
            return 130
        used_outputs.add(result.output.resolve())

        if not args.quiet:
            extra = f"  engine={result.engine}" if result.engine else ""
            print(f"[OK] {result.summary()}{extra}")
            if result.assets:
                print(f"  assets: {len(result.assets)} -> "
                      f"{result.output.parent / (result.output.stem + '_assets')}")
            for warning in result.warnings:
                print(f"  [WARN] {warning}")

    if failures:
        print(f"\n完成：成功 {len(inputs) - failures} 个，失败 {failures} 个",
              file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"\n完成：共转换 {len(inputs)} 个文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
