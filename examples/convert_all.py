"""端到端验证：对 samples/ 下的四种源格式执行全部 12 种互转组合。

运行::

    python examples/convert_all.py

产物写入 out/<源格式>/<目标格式>/，并打印每个组合的告警信息。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from doc_converter import SUPPORTED_FORMATS, convert  # noqa: E402
from doc_converter.errors import ConversionError  # noqa: E402

SAMPLES = ROOT / "samples"
OUT = ROOT / "out"


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main() -> int:
    _configure_console()
    OUT.mkdir(parents=True, exist_ok=True)
    failures = 0
    total = 0
    for src_fmt in SUPPORTED_FORMATS:
        src = SAMPLES / f"sample.{src_fmt}"
        if not src.exists():
            print(f"[SKIP] missing sample {src}")
            continue
        for dst_fmt in SUPPORTED_FORMATS:
            if src_fmt == dst_fmt:
                continue
            total += 1
            out_dir = OUT / src_fmt / dst_fmt
            try:
                result = convert(src, target=dst_fmt, output=out_dir)
            except ConversionError as exc:
                failures += 1
                print(f"[FAIL] {src_fmt} -> {dst_fmt}: {exc}")
                continue
            asset_count = len(list(out_dir.glob("*_assets/*"))) if out_dir.exists() else 0
            print(f"[OK] {src_fmt:5s} -> {dst_fmt:5s}  {result.output.name}"
                  f"  assets={asset_count}  warnings={len(result.warnings)}")
            for warning in result.warnings:
                print(f"       - {warning}")
    print(f"\nmatrix done: {total - failures}/{total} succeeded")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
