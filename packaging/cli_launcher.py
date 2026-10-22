"""PyInstaller 打包入口（纯命令行版）。"""

from __future__ import annotations

import sys

from doc_converter.cli import main

if __name__ == "__main__":
    sys.exit(main())
