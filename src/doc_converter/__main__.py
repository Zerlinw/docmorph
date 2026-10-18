"""``python -m doc_converter`` 入口。"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
