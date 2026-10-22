"""PyInstaller 打包入口（GUI + CLI 二合一）。"""

from __future__ import annotations

import sys

from doc_converter.launcher import main

if __name__ == "__main__":
    sys.exit(main())
