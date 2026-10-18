"""pytest 配置：将 src/ 加入导入路径，使测试无需安装即可运行。"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
