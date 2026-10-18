"""doc_converter.errors —— 统一的异常与降级警告体系。"""

from __future__ import annotations


class ConversionError(Exception):
    """转换过程中的可预期错误基类。

    code 取值:
        unsupported_format  不支持的格式
        file_not_found      输入文件不存在
        read_error          读取源文档失败
        write_error         写入目标文档失败
        empty_document      文档为空或未提取到任何内容
        dependency_error    缺少可选依赖
    """

    def __init__(self, message: str, code: str = "conversion_error",
                 source: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.source = source

    def __str__(self) -> str:  # pragma: no cover - 展示辅助
        prefix = f"[{self.code}]"
        if self.source:
            prefix += f" {self.source}:"
        return f"{prefix} {super().__str__()}"


class UnsupportedFormatError(ConversionError):
    def __init__(self, fmt: str, supported: list[str] | None = None) -> None:
        msg = f"不支持的格式: {fmt!r}"
        if supported:
            msg += f"，支持的格式: {', '.join(supported)}"
        super().__init__(msg, code="unsupported_format")


class FileNotFoundError_(ConversionError):
    def __init__(self, path: str) -> None:
        super().__init__(f"文件不存在: {path}", code="file_not_found", source=path)


class ReadError(ConversionError):
    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"读取失败: {reason}", code="read_error", source=path)


class WriteError(ConversionError):
    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"写入失败: {reason}", code="write_error", source=path)


class EmptyDocumentError(ConversionError):
    def __init__(self, path: str) -> None:
        super().__init__("文档为空或未提取到任何内容", code="empty_document", source=path)


class DependencyError(ConversionError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"缺少依赖: {reason}", code="dependency_error")
