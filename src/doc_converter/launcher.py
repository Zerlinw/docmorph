"""doc_converter.launcher —— 统一入口。

* 带参数：以命令行模式运行（打包为 windowed 程序时会附加到父控制台）；
* 不带参数：启动桌面 GUI。
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        _attach_console()
        from .cli import main as cli_main
        return cli_main(args)
    from .gui.app import run
    return run()


def _attach_console() -> None:
    """让 windowed 的 exe 在从终端调用时也能正常输出。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        if not kernel32.AttachConsole(-1):
            return
        for name in ("stdout", "stderr"):
            try:
                stream = open("CONOUT$", "w", encoding="utf-8",
                              errors="replace", buffering=1)
                setattr(sys, name, stream)
            except OSError:
                continue
        try:
            sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
        except OSError:
            pass
    except Exception:  # noqa: BLE001
        return


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
