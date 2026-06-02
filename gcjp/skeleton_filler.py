"""
gcjp/skeleton_filler.py

Phase 1J 骨架代码确定性填参器。

LLM 输出**完整 GCJP 代码结构**,但系统参数处用裸名占位符(sentinel);
本模块用标准库 ast 解析骨架,把 sentinel 按所在调用的 action/actor/task 上下文
从 action_templates.yaml / capability_model.yaml 确定性替换为字面量,再 unparse
回可执行代码字符串。指挥官时间语义(deadline 等)由 LLM 写真实数值,本模块不动。

占位符契约(裸名,不要加引号/不要套列表):
  add_task:
    duration_lb=FILL_DURATION, energy_cost=FILL_ENERGY, ammo_cost=FILL_AMMO,
    required_capability=FILL_CAPABILITY
  add_resource_constraint:
    max_value=FILL_MAX_AMMO   (resource_type="ammo")
    max_value=FILL_MAX_ENERGY (resource_type="energy_kwh")
  add_capability_constraint:
    required=FILL_CAPABILITY, actor_capabilities=FILL_ACTOR_CAPS
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

SENTINELS = frozenset(
    {
        "FILL_DURATION",
        "FILL_ENERGY",
        "FILL_AMMO",
        "FILL_CAPABILITY",
        "FILL_MAX_AMMO",
        "FILL_MAX_ENERGY",
        "FILL_ACTOR_CAPS",
    }
)

_FILL_METHODS = frozenset(
    {"add_task", "add_resource_constraint", "add_capability_constraint"}
)


class SkeletonFillError(Exception):
    """骨架填参失败(未知 action/actor、缺上下文、解析失败等)。"""


@dataclass
class SkeletonFillResult:
    ok: bool
    code: str = ""
    error: str | None = None
    num_filled: int = 0


@dataclass
class _TaskCtx:
    actor: str | None
    action: str | None


def fill_skeleton_code(
    code: str,
    *,
    action_defaults: dict[str, dict[str, Any]],
    capability_model: dict[str, dict[str, Any]],
) -> SkeletonFillResult:
    """把含 sentinel 的骨架代码填成可执行 GCJP 代码。"""
    try:
        module = ast.parse(code)
    except SyntaxError as exc:
        return SkeletonFillResult(ok=False, error=f"SyntaxError: {exc}")

    calls = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _FILL_METHODS
    ]

    # 第一遍:从 add_task 收集 task_id -> (actor, action)
    task_map: dict[str, _TaskCtx] = {}
    for call in calls:
        if call.func.attr != "add_task":
            continue
        task_id = _arg_literal(call, "task_id", pos=0)
        if isinstance(task_id, str):
            task_map[task_id] = _TaskCtx(
                actor=_coerce_str(_arg_literal(call, "actor", pos=1)),
                action=_coerce_str(_arg_literal(call, "action", pos=2)),
            )

    # 第二遍:替换 sentinel
    filled = 0
    try:
        for call in calls:
            filled += _fill_call(
                call, task_map, action_defaults, capability_model
            )
    except SkeletonFillError as exc:
        return SkeletonFillResult(ok=False, error=str(exc))

    remaining = _remaining_sentinels(module)
    if remaining:
        return SkeletonFillResult(
            ok=False,
            error=f"残留未填充的占位符: {sorted(remaining)}",
        )

    ast.fix_missing_locations(module)
    try:
        new_code = ast.unparse(module)
    except Exception as exc:  # noqa: BLE001 - unparse 理论上不应失败
        return SkeletonFillResult(ok=False, error=f"unparse 失败: {exc}")

    return SkeletonFillResult(ok=True, code=new_code, num_filled=filled)


def _fill_call(
    call: ast.Call,
    task_map: dict[str, _TaskCtx],
    action_defaults: dict[str, dict[str, Any]],
    capability_model: dict[str, dict[str, Any]],
) -> int:
    method = call.func.attr  # type: ignore[union-attr]
    ctx = _call_context(call, method, task_map)

    count = 0
    # 位置参数
    for i, node in enumerate(call.args):
        sentinel = _sentinel_of(node)
        if sentinel:
            call.args[i] = _resolve(
                sentinel, method, ctx, action_defaults, capability_model
            )
            count += 1
    # 关键字参数
    for kw in call.keywords:
        sentinel = _sentinel_of(kw.value)
        if sentinel:
            kw.value = _resolve(
                sentinel, method, ctx, action_defaults, capability_model
            )
            count += 1
    return count


def _call_context(
    call: ast.Call,
    method: str,
    task_map: dict[str, _TaskCtx],
) -> _TaskCtx:
    if method == "add_task":
        return _TaskCtx(
            actor=_coerce_str(_arg_literal(call, "actor", pos=1)),
            action=_coerce_str(_arg_literal(call, "action", pos=2)),
        )
    if method == "add_resource_constraint":
        return _TaskCtx(
            actor=_coerce_str(_arg_literal(call, "actor", pos=0)),
            action=None,
        )
    if method == "add_capability_constraint":
        task_id = _coerce_str(_arg_literal(call, "task_id", pos=0))
        return task_map.get(task_id or "", _TaskCtx(actor=None, action=None))
    return _TaskCtx(actor=None, action=None)


def _resolve(
    sentinel: str,
    method: str,
    ctx: _TaskCtx,
    action_defaults: dict[str, dict[str, Any]],
    capability_model: dict[str, dict[str, Any]],
) -> ast.expr:
    if sentinel in {"FILL_DURATION", "FILL_ENERGY", "FILL_AMMO", "FILL_CAPABILITY"} and (
        method in {"add_task", "add_capability_constraint"}
    ):
        defaults = _require(action_defaults, ctx.action, "action", sentinel)
        key = {
            "FILL_DURATION": "duration_lb",
            "FILL_ENERGY": "energy_cost",
            "FILL_AMMO": "ammo_cost",
            "FILL_CAPABILITY": "required_capability",
        }[sentinel]
        return _literal(defaults.get(key) if key != "ammo_cost" else defaults.get(key, 0))

    if sentinel in {"FILL_MAX_AMMO", "FILL_MAX_ENERGY"}:
        info = _require(capability_model, ctx.actor, "actor", sentinel)
        key = "max_ammo" if sentinel == "FILL_MAX_AMMO" else "max_energy_kwh"
        return _literal(info.get(key))

    if sentinel == "FILL_ACTOR_CAPS":
        info = _require(capability_model, ctx.actor, "actor", sentinel)
        return _literal(list(info.get("capabilities", [])))

    raise SkeletonFillError(f"占位符 {sentinel} 出现在不支持的调用 {method}")


def _require(
    table: dict[str, dict[str, Any]],
    key: str | None,
    kind: str,
    sentinel: str,
) -> dict[str, Any]:
    if not key:
        raise SkeletonFillError(f"无法为 {sentinel} 确定 {kind}(调用缺少字面量 {kind})")
    value = table.get(key)
    if value is None:
        raise SkeletonFillError(f"{kind} '{key}' 不在配置表中(为 {sentinel} 取值失败)")
    return value


def _sentinel_of(node: ast.expr) -> str | None:
    """node 是裸 sentinel Name,或仅含单个 sentinel 的 List(容错)。返回 sentinel 名。"""
    if isinstance(node, ast.Name) and node.id in SENTINELS:
        return node.id
    if (
        isinstance(node, ast.List)
        and len(node.elts) == 1
        and isinstance(node.elts[0], ast.Name)
        and node.elts[0].id in SENTINELS
    ):
        return node.elts[0].id
    return None


def _arg_literal(call: ast.Call, name: str, *, pos: int | None = None) -> Any:
    """按关键字名或位置取字面量值;非字面量返回 None。"""
    for kw in call.keywords:
        if kw.arg == name:
            return _const_value(kw.value)
    if pos is not None and pos < len(call.args):
        return _const_value(call.args[pos])
    return None


def _const_value(node: ast.expr) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    return None


def _coerce_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _literal(value: Any) -> ast.expr:
    """把 Python 值(数值/列表/字符串)转成 AST 表达式节点。"""
    expr = ast.parse(repr(value), mode="eval").body
    return expr


def _remaining_sentinels(module: ast.AST) -> set[str]:
    return {
        node.id
        for node in ast.walk(module)
        if isinstance(node, ast.Name) and node.id in SENTINELS
    }
