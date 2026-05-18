"""
从 LLM 回复中提取可执行 GCJP 代码的工具函数。
"""
from __future__ import annotations

import re
from dataclasses import dataclass


GCJP_IMPORT_LINE = "from gcjp.mission_graph import TaskGraphBuilder"
_PYTHON_FENCE_RE = re.compile(
    r"```(?:python|py)?\s*(.*?)```",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class CodeExtractionResult:
    ok: bool
    code: str = ""
    method: str | None = None
    error: str | None = None


def extract_gcjp_code(raw_response: str) -> CodeExtractionResult:
    """
    从模型回复中提取 GCJP Python 代码。

    优先级：
    1. 第一个含 GCJP import 的围栏式 Python 代码块。
    2. 从 GCJP import 行开始到回复末尾的纯文本。
    """
    raw_response = raw_response or ""

    for match in _PYTHON_FENCE_RE.finditer(raw_response):
        candidate = match.group(1).strip()
        if GCJP_IMPORT_LINE in candidate:
            return CodeExtractionResult(ok=True, code=candidate, method="fenced")

    idx = raw_response.find(GCJP_IMPORT_LINE)
    if idx >= 0:
        candidate = raw_response[idx:].strip()
        candidate = candidate.replace("```", "").strip()
        return CodeExtractionResult(ok=True, code=candidate, method="import_anchor")

    return CodeExtractionResult(
        ok=False,
        error="未找到 GCJP 代码块或 TaskGraphBuilder 导入语句。",
    )

