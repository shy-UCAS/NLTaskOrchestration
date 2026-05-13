"""
gcjp/errors.py
GCJP 受限 API 的结构化错误类型。

设计目标：
    将 mission_graph 等模块原本的裸 ValueError / RuntimeError 升级为携带
    `code / api / actual / expected / hint / details` 的结构化错误，便于
    下游验证管道直接以 JSON 形式反馈给 LLM 完成"生成 → 验证 → 修复"闭环。

兼容性：
    `GCJPAPIError` 继承 `ValueError`，所有现有 `except ValueError` 仍能捕获；
    `str(exc)` 仍返回原始中文 message，error_msg 链路保持不变。
"""
from __future__ import annotations

from typing import Any


class GCJPAPIError(ValueError):
    """GCJP API 调用层的结构化错误。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        api: str | None = None,
        actual: Any = None,
        expected: Any = None,
        hint: str | None = None,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.api = api
        self.actual = actual
        self.expected = expected
        self.hint = hint
        self.details = details or {}

    def to_dict(self) -> dict:
        """序列化为可直接送入 LLM 反馈 prompt 的字典。"""
        return {
            "code": self.code,
            "message": self.message,
            "api": self.api,
            "actual": self.actual,
            "expected": self.expected,
            "hint": self.hint,
            "details": self.details,
        }
