"""
gcjp/task_plan_loader.py

将标准化任务计划 JSON 转换为 TaskGraphBuilder / BuiltGraph。

用途：
1. 衔接 demos/demo_01_simple_task_plan.json；
2. 将 tasks / relations 翻译为 TaskGraphBuilder API 调用；
3. 为后续 NL → 标准化任务计划 JSON → GCJP 验证闭环提供确定性转换层。

注意：
- 本文件不调用 LLM；
- 第一版使用动作默认参数 ACTION_DEFAULTS；
- 后续可改为从 configs/action_templates.yaml 和 configs/capability_model.yaml 读取。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from gcjp.mission_graph import TaskGraphBuilder, BuiltGraph


# =============================================================================
# 第一版动作默认参数
# 后续可替换为从 configs/action_templates.yaml 中读取
# =============================================================================

ACTION_DEFAULTS: dict[str, dict[str, Any]] = {
    "reconnaissance": {
        "duration_lb": 5.0,
        "required_capability": ["recon_capable"],
        "energy_cost": 10.0,
        "ammo_cost": 0,
    },
    "track": {
        "duration_lb": 5.0,
        "required_capability": ["recon_capable", "tracking_capable"],
        "energy_cost": 8.0,
        "ammo_cost": 0,
    },
    "standby": {
        "duration_lb": 1.0,
        "required_capability": [],
        "energy_cost": 1.0,
        "ammo_cost": 0,
    },
    "intercept": {
        "duration_lb": 3.0,
        "required_capability": ["intercept_capable"],
        "energy_cost": 20.0,
        "ammo_cost": 1,
    },
    "strike": {
        "duration_lb": 2.0,
        "required_capability": ["strike_capable"],
        "energy_cost": 15.0,
        "ammo_cost": 1,
    },
    "fly_to": {
        "duration_lb": 5.0,
        "required_capability": [],
        "energy_cost": 5.0,
        "ammo_cost": 0,
    },
    "jam": {
        "duration_lb": 3.0,
        "required_capability": ["jamming_capable"],
        "energy_cost": 10.0,
        "ammo_cost": 0,
    },
    "rendezvous": {
        "duration_lb": 2.0,
        "required_capability": [],
        "energy_cost": 3.0,
        "ammo_cost": 0,
    },
    "breakthrough": {
        "duration_lb": 4.0,
        "required_capability": ["breakthrough_capable"],
        "energy_cost": 18.0,
        "ammo_cost": 0,
    },
}


# 第一版默认资源上限，保证 demo 可以跑通
# 后续应由 configs/capability_model.yaml 提供
DEFAULT_RESOURCE_BUDGETS: dict[str, dict[str, float]] = {
    "default": {
        "ammo": 999,
        "energy_kwh": 999.0,
    }
}


# =============================================================================
# 文件加载与可选 schema 校验
# =============================================================================

def load_task_plan(
    task_plan_path: str | Path,
    schema_path: Optional[str | Path] = None,
) -> dict[str, Any]:
    """
    加载标准化任务计划 JSON。

    如果提供 schema_path，则使用 jsonschema 进行格式校验。
    """
    task_plan_path = Path(task_plan_path)

    with task_plan_path.open("r", encoding="utf-8") as f:
        plan = json.load(f)

    if schema_path is not None:
        _validate_with_schema(plan, schema_path)

    return plan


def _validate_with_schema(plan: dict[str, Any], schema_path: str | Path) -> None:
    """
    使用 jsonschema 校验任务计划格式。
    """
    try:
        from jsonschema import validate
    except ImportError as exc:
        raise RuntimeError(
            "未安装 jsonschema，请运行：python -m pip install jsonschema"
        ) from exc

    schema_path = Path(schema_path)
    with schema_path.open("r", encoding="utf-8") as f:
        schema = json.load(f)

    validate(instance=plan, schema=schema)


# =============================================================================
# 可选：从 action_templates.yaml 读取动作参数
# =============================================================================

def load_action_defaults_from_yaml(action_templates_path: str | Path) -> dict[str, dict[str, Any]]:
    """
    从 configs/action_templates.yaml 读取动作默认参数。

    兼容以下两种形式：

    actions:
      reconnaissance:
        min_duration: 5.0
        resource_cost:
          energy_kwh: 10.0
          ammo: 0
        required_capabilities: [recon_capable]

    或：

    actions:
      reconnaissance:
        min_duration: 5.0
        resource_cost:
          energy: 10.0
          ammo: 0
        requires: [recon_capable]
    """
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "未安装 pyyaml，请运行：python -m pip install pyyaml"
        ) from exc

    action_templates_path = Path(action_templates_path)
    with action_templates_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    actions = raw.get("actions", raw)
    defaults: dict[str, dict[str, Any]] = {}

    for action_name, cfg in actions.items():
        resource_cost = cfg.get("resource_cost", {}) or {}

        defaults[action_name] = {
            "duration_lb": float(
                cfg.get("min_duration", cfg.get("duration_lb", 1.0))
            ),
            "required_capability": list(
                cfg.get("required_capabilities", cfg.get("requires", []))
            ),
            "energy_cost": float(
                resource_cost.get(
                    "energy_kwh",
                    resource_cost.get("energy", cfg.get("energy_cost", 0.0)),
                )
            ),
            "ammo_cost": int(
                resource_cost.get("ammo", cfg.get("ammo_cost", 0))
            ),
        }

    return defaults


# =============================================================================
# 核心函数：标准化任务计划 JSON → BuiltGraph
# =============================================================================

def build_graph_from_task_plan(
    plan: dict[str, Any],
    *,
    segment_id: Optional[str] = None,
    action_defaults: Optional[dict[str, dict[str, Any]]] = None,
    add_resource_constraints: bool = True,
    resource_budgets: Optional[dict[str, dict[str, float]]] = None,
) -> BuiltGraph:
    """
    将标准化任务计划 dict 转换为 BuiltGraph。

    参数：
        plan:
            已通过 task_plan_schema.json 校验的标准化任务计划。
        segment_id:
            生成的任务段 ID。若为空，则使用 plan_id。
        action_defaults:
            动作默认参数。若为空，则使用 ACTION_DEFAULTS。
        add_resource_constraints:
            是否为每个 actor 自动添加资源约束。
        resource_budgets:
            资源上限。第一版可不传，默认给较大上限。

    返回：
        BuiltGraph，可直接传入 VerificationPipeline.verify_graph()。
    """
    action_defaults = action_defaults or ACTION_DEFAULTS
    resource_budgets = resource_budgets or DEFAULT_RESOURCE_BUDGETS

    plan_id = plan["plan_id"]
    segment_id = segment_id or f"seg_{plan_id}"

    participants = plan.get("participants", [])
    assigned_actors = [p["actor_id"] for p in participants]

    # 如果 participants 为空，则从 tasks 中回退推断 actor
    if not assigned_actors:
        assigned_actors = sorted({t["actor"] for t in plan.get("tasks", [])})

    if not assigned_actors:
        raise ValueError("任务计划中没有 participants，也无法从 tasks 推断 actor")

    builder = TaskGraphBuilder(
        segment_id=segment_id,
        assigned_actors=assigned_actors,
    )

    # 第一版只声明最小段信息
    builder.declare_segment_meta(
        assumed_conditions=["mission_start"],
        contract_ids_to_fulfill=[],
    )

    # -------------------------------------------------------------------------
    # 1. 添加任务节点
    # -------------------------------------------------------------------------
    for task in plan.get("tasks", []):
        task_id = task["task_id"]
        actor = task["actor"]
        action = task["action"]
        target = task["target"]

        defaults = action_defaults.get(action)
        if defaults is None:
            raise ValueError(
                f"未知动作类型 '{action}'，请在 ACTION_DEFAULTS 或 action_templates.yaml 中定义"
            )

        time_window = task.get("time_window") or {}

        builder.add_task(
            task_id=task_id,
            actor=actor,
            action=action,
            target=target,
            duration_lb=float(defaults["duration_lb"]),
            required_capability=list(defaults.get("required_capability", [])),
            energy_cost=float(defaults.get("energy_cost", 0.0)),
            ammo_cost=int(defaults.get("ammo_cost", 0)),
            time_window_earliest=time_window.get("earliest"),
            time_window_latest=time_window.get("latest"),
            is_coalition=bool(task.get("is_coalition", False)),
            coalition_members=task.get("coalition_members") or [],
            condition=task.get("condition"),
            expected_output=task.get("expected_output"),
            source="task_plan_json",
        )

        # 如果 schema 中给出了 deadline，需要额外注册 time_window 约束
        deadline = time_window.get("deadline")
        if deadline is not None:
            builder.add_constraint(
                constraint_type="time_window",
                params={
                    "task_id": task_id,
                    "deadline": float(deadline),
                },
                source_label=f"time_window_{task_id}_deadline",
            )

    # -------------------------------------------------------------------------
    # 2. 添加任务依赖边
    # -------------------------------------------------------------------------
    for rel in plan.get("relations", []):
        relation_type = normalize_relation_type(rel["type"])

        builder.add_dependency(
            source=rel["source"],
            target=rel["target"],
            relation=relation_type,
            sync_tolerance=rel.get("sync_tolerance"),
            condition=rel.get("condition"),
        )

    # -------------------------------------------------------------------------
    # 3. 添加全局时间约束
    # -------------------------------------------------------------------------
    global_constraints = plan.get("global_constraints") or {}
    total_time_budget = global_constraints.get("total_time_budget")

    if total_time_budget is not None:
        # 对所有无出边节点添加 deadline，近似表示总任务完成时间上限
        sink_nodes = _get_sink_tasks(plan)
        for tid in sink_nodes:
            builder.add_constraint(
                constraint_type="time_window",
                params={
                    "task_id": tid,
                    "deadline": float(total_time_budget),
                },
                source_label=f"global_deadline_{total_time_budget}_{tid}",
            )

    # -------------------------------------------------------------------------
    # 4. 添加资源约束
    # -------------------------------------------------------------------------
    if add_resource_constraints:
        for actor in assigned_actors:
            budget = resource_budgets.get(actor, resource_budgets["default"])

            builder.add_resource_constraint(
                actor=actor,
                resource_type="ammo",
                max_value=float(budget.get("ammo", 999)),
            )
            builder.add_resource_constraint(
                actor=actor,
                resource_type="energy_kwh",
                max_value=float(budget.get("energy_kwh", 999.0)),
            )

    return builder.build()


def build_graph_from_task_plan_file(
    task_plan_path: str | Path,
    *,
    schema_path: Optional[str | Path] = None,
    action_templates_path: Optional[str | Path] = None,
    segment_id: Optional[str] = None,
) -> BuiltGraph:
    """
    从任务计划 JSON 文件直接构建 BuiltGraph。

    适合 demo 中直接调用。
    """
    plan = load_task_plan(task_plan_path, schema_path=schema_path)

    action_defaults = ACTION_DEFAULTS
    if action_templates_path is not None:
        action_defaults = load_action_defaults_from_yaml(action_templates_path)

    return build_graph_from_task_plan(
        plan,
        segment_id=segment_id,
        action_defaults=action_defaults,
    )


# =============================================================================
# 工具函数
# =============================================================================

def normalize_relation_type(relation_type: str) -> str:
    """
    标准化 relation type。

    schema 推荐使用：
        condition_trigger

    旧版本可能使用：
        conditional
    """
    mapping = {
        "conditional": "condition_trigger",
    }
    return mapping.get(relation_type, relation_type)


def _get_sink_tasks(plan: dict[str, Any]) -> list[str]:
    """
    获取任务图中的汇点任务：没有出边的任务。
    用于给 total_time_budget 添加 deadline。
    """
    task_ids = {t["task_id"] for t in plan.get("tasks", [])}
    sources = {r["source"] for r in plan.get("relations", [])}
    return sorted(task_ids - sources)