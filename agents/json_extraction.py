"""
从 LLM 回复中提取 JSON 对象的工具函数。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


_JSON_FENCE_RE = re.compile(
    r"```json\s*(.*?)```",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class JsonExtractionResult:
    ok: bool
    data: dict[str, Any] | None = None
    method: str | None = None
    error: str | None = None


def extract_json_object(raw_response: str) -> JsonExtractionResult:
    """
    从模型回复中提取 JSON 对象。

    优先级：
    1. 第一个围栏式 ```json``` 代码块。
    2. 回复中第一个裸 JSON 对象（以 { 开头、} 结尾）。
    3. 失败返回。
    """
    raw_response = raw_response or ""

    for match in _JSON_FENCE_RE.finditer(raw_response):
        candidate = match.group(1).strip()
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return JsonExtractionResult(ok=True, data=data, method="fenced")
        except json.JSONDecodeError:
            continue

    result = _try_bare_json(raw_response)
    if result is not None:
        return JsonExtractionResult(ok=True, data=result, method="bare")

    return JsonExtractionResult(
        ok=False,
        error="未找到有效的 JSON 对象。",
    )


def _try_bare_json(text: str) -> dict[str, Any] | None:
    """扫描文本中第一个裸 JSON 对象，用括号计数定位匹配的 }。"""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                if in_string:
                    escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        data = json.loads(candidate)
                        if isinstance(data, dict):
                            return data
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None
