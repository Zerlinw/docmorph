"""生成 Windows 可执行程序：图标 + PyInstaller 构建 + 基础校验。

用法::

    python packaging/build.py            # 构建 GUI 与 CLI 两个 exe
    python packaging/build.py --gui      # 只构建 GUI
    python packaging/build.py --cli      # 只构建 CLI
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
DIST = PROJECT / "dist"
ASSETS = ROOT / "assets"
VERSION = "2.0.0"

VERSION_INFO = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(2, 0, 0, 0),
    prodvers=(2, 0, 0, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404b0',
        [
          StringStruct('CompanyName', 'doc-converter'),
          StringStruct('FileDescription', '文档转换助手 DocConverter'),
          StringStruct('FileVersion', '2.0.0'),
          StringStruct('InternalName', 'DocConverter'),
          StringStruct('LegalCopyright', 'MIT License'),
          StringStruct('OriginalFilename', 'DocConverter.exe'),
          StringStruct('ProductName', 'DocConverter'),
          StringStruct('ProductVersion', '2.0.0'),
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


def build_icon() -> None:
    from PIL import Image, ImageDraw, ImageFont

    ASSETS.mkdir(parents=True, exist_ok=True)
    size = 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # 圆角蓝色底
    draw.rounded_rectangle([8, 8, size - 8, size - 8], radius=56,
                           fill=(37, 99, 235, 255))
    # 内层浅色卡片，提升小尺寸下的辨识度
    draw.rounded_rectangle([54, 46, size - 54, size - 46], radius=22,
                           fill=(255, 255, 255, 235))
    # 两条"转换"箭头
    draw.line([(86, 106), (170, 106)], fill=(37, 99, 235, 255), width=14)
    draw.polygon([(170, 88), (198, 106), (170, 124)], fill=(37, 99, 235, 255))
    draw.line([(170, 150), (86, 150)], fill=(37, 99, 235, 255), width=14)
    draw.polygon([(86, 132), (58, 150), (86, 168)], fill=(37, 99, 235, 255))
    image.save(ASSETS / "app.png")
    image.save(ASSETS / "app.ico",
               sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                      (128, 128), (256, 256)])
    (ASSETS / "version_info.txt").write_text(VERSION_INFO, encoding="utf-8")
    print(f"[ok] icon -> {ASSETS / 'app.ico'}")


def build(spec: str) -> None:
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
               str(ROOT / spec)]
    print("[run]", " ".join(command))
    subprocess.run(command, cwd=str(PROJECT), check=True)


def main() -> int:
    args = set(sys.argv[1:])
    only_gui = "--gui" in args
    only_cli = "--cli" in args
    build_icon()
    if not only_cli:
        build("DocConverter.spec")
    if not only_gui:
        build("doc-converter-cli.spec")
    print("\n构建产物：")
    for name in ("DocConverter.exe", "doc-converter.exe"):
        path = DIST / name
        if path.exists():
            print(f"  {path}  ({path.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
