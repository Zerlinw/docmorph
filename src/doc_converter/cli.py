"""doc_converter.cli —— 命令行入口。

用法示例::

    # 单文件转换
    python -m doc_converter report.docx -t md -o out/

    # 批量转换（支持 glob，Shell 不展开时由程序展开）
    python -m doc_converter "docs/*.docx" -t pdf -o out/

    # 显式指定源格式与资源模式
    python -m doc_converter input.bin -f html -t docx -o out/ --assets embed
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

from .converter import SUPPORTED_FORMATS, convert, convert_batch, normalize_format
from .errors import ConversionError
from .utils import ext_of


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doc-converter",
        description="PDF / DOCX / HTML / Markdown 任意互转的文档转换助手",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python -m doc_converter report.docx -t md -o out/\n"
            "  python -m doc_converter \"docs/*.docx\" -t pdf -o out/\n"
            "  doc-converter page.html -t docx\n"
        ),
    )
    parser.add_argument("inputs", nargs="+", metavar="INPUT",
                        help="输入文件，支持 glob 模式，可一次传多个")
    parser.add_argument("-t", "--to", "--target", dest="target",
                        required=True, choices=list(SUPPORTED_FORMATS),
                        help="目标格式")
    parser.add_argument("-f", "--from", "--source", dest="source",
                        default=None, choices=list(SUPPORTED_FORMATS),
                        help="源格式（缺省按扩展名推断）")
    parser.add_argument("-o", "--output", dest="output", default=None,
                        metavar="PATH",
                        help="输出路径：目录或具体文件（仅单文件时有效）")
    parser.add_argument("--assets", dest="assets", default=None,
                        choices=["file", "datauri", "embed"],
                        help="图片等资源的落地方式（缺省按目标格式自动选择）")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="仅输出错误")
    parser.add_argument("--strict", action="store_true",
                        help="批量模式下遇到首个失败即中止")
    return parser


def _expand_inputs(patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        matched = sorted(glob.glob(pattern, recursive=True))
        if matched:
            files.extend(Path(m) for m in matched if Path(m).is_file())
        else:
            files.append(Path(pattern))
    # 去重并保持顺序
    seen: set[Path] = set()
    unique: list[Path] = []
    for f in files:
        key = f.resolve() if f.exists() else f
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def _configure_console() -> None:
    """让控制台输出兼容非 ASCII 字符（Windows 默认代码页可能无法编码）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass


def _unique_output(src: Path, args, used: set) -> str | None:
    """批量模式下避免不同源文件的同名输出互相覆盖。

    仅在输出为目录（或未指定）且候选路径已被占用时，为文件名添加
    源格式前缀，例如 ``sample.docx`` -> ``md-sample.docx``。
    """
    if args.output is None:
        base_dir, stem = src.parent, src.stem
    else:
        out = Path(args.output)
        if out.suffix:
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
            result = convert(src, target=args.target, source_format=args.source,
                             output=output, asset_mode=args.assets)
        except ConversionError as exc:
            failures += 1
            print(f"[FAIL] {exc}", file=sys.stderr)
            if args.strict:
                return 1
            continue
        used_outputs.add(result.output.resolve())

        if not args.quiet:
            print(f"[OK] {result.summary()}")
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
