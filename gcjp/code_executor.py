"""
gcjp/code_executor.py

Execute checked GCJP code and extract `built: BuiltGraph`.
This module is the runtime bridge from GCJP code string to BuiltGraph.
"""
from __future__ import annotations

import builtins
from dataclasses import dataclass
from typing import Any

from gcjp.mission_graph import BuiltGraph, TaskGraphBuilder
from gcjp.safety_checker import SafetyCheckResult, check_gcjp_code


class GCJPExecutionError(RuntimeError):
    """Raised when GCJP code fails safety check or execution."""


@dataclass
class GCJPExecutionResult:
    passed: bool
    graph: BuiltGraph | None = None
    safety: SafetyCheckResult | None = None
    error_msg: str | None = None
    locals_snapshot: dict[str, Any] | None = None


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
            error_msg="Safety check failed:\n" + "\n".join(safety.violations),
        )

    exec_globals: dict[str, Any] = {
        "__builtins__": SAFE_BUILTINS,
        "TaskGraphBuilder": TaskGraphBuilder,
    }
    exec_locals: dict[str, Any] = {}

    try:
        compiled = compile(code, filename="<gcjp_code>", mode="exec")
        exec(compiled, exec_globals, exec_locals)
    except Exception as exc:
        return GCJPExecutionResult(
            passed=False,
            safety=safety,
            error_msg=f"GCJP execution failed: {type(exc).__name__}: {exc}",
            locals_snapshot=exec_locals,
        )

    built = exec_locals.get("built", exec_globals.get("built"))

    if built is None:
        return GCJPExecutionResult(
            passed=False,
            safety=safety,
            error_msg="GCJP code must define `built = g.build()`.",
            locals_snapshot=exec_locals,
        )

    if not isinstance(built, BuiltGraph):
        return GCJPExecutionResult(
            passed=False,
            safety=safety,
            error_msg=f"`built` must be BuiltGraph, got {type(built).__name__}.",
            locals_snapshot=exec_locals,
        )

    return GCJPExecutionResult(
        passed=True,
        graph=built,
        safety=safety,
        locals_snapshot=exec_locals,
    )
