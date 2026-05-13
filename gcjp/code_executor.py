"""
gcjp/code_executor.py

Execute checked GCJP code and extract `built: BuiltGraph`.
This module is the runtime bridge from GCJP code string to BuiltGraph.
"""
from __future__ import annotations

import builtins
import re
import traceback
from dataclasses import dataclass
from typing import Any

from gcjp.errors import GCJPAPIError
from gcjp.mission_graph import BuiltGraph, TaskGraphBuilder
from gcjp.safety_checker import SafetyCheckResult, check_gcjp_code


class GCJPExecutionError(RuntimeError):
    """Raised when GCJP code fails safety check or execution."""


ERROR_SUCCESS = "SUCCESS"
ERROR_SAFETY_CHECK_FAILED = "SAFETY_CHECK_FAILED"
ERROR_COMPILE_FAILED = "COMPILE_FAILED"
ERROR_EXECUTION_FAILED = "EXECUTION_FAILED"
ERROR_MISSING_BUILT = "MISSING_BUILT"
ERROR_INVALID_BUILT_TYPE = "INVALID_BUILT_TYPE"


@dataclass
class GCJPExecutionResult:
    passed: bool
    graph: BuiltGraph | None = None
    safety: SafetyCheckResult | None = None
    error_type: str | None = None
    error_msg: str | None = None
    locals_snapshot: dict[str, Any] | None = None
    traceback_text: str | None = None
    gcjp_lineno: int | None = None
    source_context: str | None = None
    api_error: dict | None = None


_GCJP_TB_LINE_RE = re.compile(r'File "<gcjp_code>", line (\d+)')


def _extract_gcjp_source_context(
    code: str, tb_text: str, radius: int = 2
) -> tuple[int | None, str | None]:
    """
    从 traceback 文本中解析 <gcjp_code> 出错行号，并返回该行附近 ±radius 行的源码片段。
    返回 (lineno, context_str)；任一解析失败时对应位置为 None。
    """
    matches = _GCJP_TB_LINE_RE.findall(tb_text)
    if not matches:
        return None, None
    try:
        lineno = int(matches[-1])  # 取最深一层 frame
    except ValueError:
        return None, None

    code_lines = code.splitlines()
    if not (1 <= lineno <= len(code_lines)):
        return lineno, None

    start = max(1, lineno - radius)
    end = min(len(code_lines), lineno + radius)
    width = len(str(end))
    parts: list[str] = []
    for ln in range(start, end + 1):
        marker = ">" if ln == lineno else " "
        parts.append(f"{marker} {ln:>{width}} | {code_lines[ln - 1]}")
    return lineno, "\n".join(parts)


def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
    """
    Allow only:
        from gcjp.mission_graph import TaskGraphBuilder
    """
    if name != "gcjp.mission_graph":
        raise ImportError(f"Import '{name}' is not allowed in GCJP code")
    return builtins.__import__(name, globals, locals, fromlist, level)


SAFE_BUILTINS = {
    "__import__": _restricted_import,
    "print": print,
    "len": len,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "round": round,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "isinstance": isinstance,
    "hasattr": hasattr,
}


def execute_gcjp_code(code: str) -> GCJPExecutionResult:
    """
    Execute a GCJP code string and extract variable `built`.

    Required convention:
        built = g.build()
    """
    safety = check_gcjp_code(code)
    if not safety.passed:
        return GCJPExecutionResult(
            passed=False,
            safety=safety,
            error_type=ERROR_SAFETY_CHECK_FAILED,
            error_msg="Safety check failed:\n" + "\n".join(safety.violations),
        )

    exec_globals: dict[str, Any] = {
        "__builtins__": SAFE_BUILTINS,
        "TaskGraphBuilder": TaskGraphBuilder,
    }
    exec_locals: dict[str, Any] = {}

    try:
        compiled = compile(code, filename="<gcjp_code>", mode="exec")
    except Exception as exc:
        tb = traceback.format_exc()
        lineno, context = _extract_gcjp_source_context(code, tb)
        # SyntaxError 在 compile 阶段不会进 <gcjp_code> traceback frame，
        # 但异常对象自身带 lineno，单独兜底一次。
        if lineno is None and isinstance(exc, SyntaxError) and exc.lineno is not None:
            lineno, context = _extract_gcjp_source_context(
                code, f'File "<gcjp_code>", line {exc.lineno}'
            )
        return GCJPExecutionResult(
            passed=False,
            safety=safety,
            error_type=ERROR_COMPILE_FAILED,
            error_msg=f"GCJP compile failed: {type(exc).__name__}: {exc}",
            locals_snapshot=exec_locals,
            traceback_text=tb,
            gcjp_lineno=lineno,
            source_context=context,
        )

    try:
        exec(compiled, exec_globals, exec_locals)
    except Exception as exc:
        tb = traceback.format_exc()
        lineno, context = _extract_gcjp_source_context(code, tb)
        api_error = exc.to_dict() if isinstance(exc, GCJPAPIError) else None
        return GCJPExecutionResult(
            passed=False,
            safety=safety,
            error_type=ERROR_EXECUTION_FAILED,
            error_msg=f"GCJP execution failed: {type(exc).__name__}: {exc}",
            locals_snapshot=exec_locals,
            traceback_text=tb,
            gcjp_lineno=lineno,
            source_context=context,
            api_error=api_error,
        )

    built = exec_locals.get("built", exec_globals.get("built"))

    if built is None:
        return GCJPExecutionResult(
            passed=False,
            safety=safety,
            error_type=ERROR_MISSING_BUILT,
            error_msg="GCJP code must define `built = g.build()`.",
            locals_snapshot=exec_locals,
        )

    if not isinstance(built, BuiltGraph):
        return GCJPExecutionResult(
            passed=False,
            safety=safety,
            error_type=ERROR_INVALID_BUILT_TYPE,
            error_msg=f"`built` must be BuiltGraph, got {type(built).__name__}.",
            locals_snapshot=exec_locals,
        )

    return GCJPExecutionResult(
        passed=True,
        graph=built,
        safety=safety,
        error_type=ERROR_SUCCESS,
        locals_snapshot=exec_locals,
    )
