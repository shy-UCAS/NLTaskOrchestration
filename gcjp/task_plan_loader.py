"""
gcjp/task_plan_loader.py

将标准化任务计划 JSON 转换为 TaskGraphBuilder / BuiltGraph。

用途：
1. 衔接 demos/demo_01_simple_task_plan.json；
2. 将 tasks / relations 翻译为 TaskGraphBuilder API 调用；
3. 为后续 NL → 标准化任务计划 JSON → GCJP 验证闭环提供确定性转换层。

注意：
- 本文件不调用 LLM；
- 支持从 action_templates.yaml 读取动作模板；
- 支持从 capability_model.yaml 读取主体能力与资源上限；
- 支持从 environment_config.yaml / environment_facilities.yaml 做环境引用校验；
- 当前环境模型仅做引用校验，不做复杂轨迹可行性分析。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from gcjp.debug_logger import debug
from gcjp.mission_graph import TaskGraphBuilder, BuiltGraph
from gcjp.environment_model import (
    load_environment_config,
    validate_task_plan_environment,
)


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

    支持 min_duration / energy_kwh 为 null 的情况。
    对于 fly_to 这类需要动态计算的动作，先给 fallback 默认值；
    后续再接入 environment_config.yaml 后按距离和速度动态计算。
    """
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "未安装 pyyaml，请运行：python -m pip install pyyaml"
        ) from exc

    def first_not_none(*values, default=None):
        for value in values:
            if value is not None:
                return value
        return default

    action_templates_path = Path(action_templates_path)
    debug.log(f"\n[DEBUG] load_action_defaults_from_yaml — 读取文件: {action_templates_path}")
    with action_templates_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    actions = raw.get("actions", raw)
    debug.log(f"  动作模板数量: {len(actions)}")
    defaults: dict[str, dict[str, Any]] = {}

    for action_name, cfg in actions.items():
        cfg = cfg or {}
        resource_cost = cfg.get("resource_cost", {}) or {}

        # 读取原始 YAML 字段值（用于 debug 展示）
        raw_min_dur = cfg.get("min_duration")
        raw_dur_lb = cfg.get("duration_lb")
        raw_energy_kwh = resource_cost.get("energy_kwh")
        raw_energy = resource_cost.get("energy") or cfg.get("energy_cost")
        raw_ammo = resource_cost.get("ammo") or cfg.get("ammo_cost")
        raw_req_caps = cfg.get("required_capabilities") or cfg.get("requires") or []

        duration_value = first_not_none(raw_min_dur, raw_dur_lb, default=1.0)
        energy_value = first_not_none(raw_energy_kwh, raw_energy, default=0.0)
        ammo_value = first_not_none(raw_ammo, default=0)
        required_caps = first_not_none(raw_req_caps, default=[])

        defaults[action_name] = {
            "duration_lb": float(duration_value),
            "required_capability": list(required_caps),
            "energy_cost": float(energy_value),
            "ammo_cost": int(ammo_value),
        }

        # 展示解析结果，标注字段来源
        src_dur = "min_duration" if raw_min_dur is not None else ("duration_lb" if raw_dur_lb is not None else "default")
        src_energy = "resource_cost.energy_kwh" if raw_energy_kwh is not None else ("resource_cost.energy" if resource_cost.get("energy") is not None else ("energy_cost" if cfg.get("energy_cost") is not None else "default"))
        src_ammo = "resource_cost.ammo" if resource_cost.get("ammo") is not None else ("ammo_cost" if cfg.get("ammo_cost") is not None else "default")
        src_caps = "required_capabilities" if (cfg.get("required_capabilities")) else ("requires" if cfg.get("requires") else "default")
        debug.log(f"  {action_name:20s} | dur={float(duration_value):.1f}[{src_dur}] "
                  f"energy={float(energy_value):.1f}[{src_energy}] ammo={int(ammo_value)}[{src_ammo}] "
                  f"caps={list(required_caps)}[{src_caps}]")

    return defaults


# =============================================================================
# 可选：从 capability_model.yaml 读取集群能力模型
# =============================================================================

def load_capability_model_from_yaml(
    capability_model_path: str | Path,
) -> dict[str, dict[str, Any]]:
    """
    从 configs/capability_model.yaml 读取各集群的能力与资源上限。

    返回格式：
        {
            "fleet_1": {
                "capabilities": ["recon_capable", "strike_capable"],
                "max_ammo": 4,
                "max_energy_kwh": 50.0,
                "cruise_speed_kmh": 80,
            },
            ...
        }
    """
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "未安装 pyyaml，请运行：python -m pip install pyyaml"
        ) from exc

    capability_model_path = Path(capability_model_path)
    debug.log(f"\n[DEBUG] load_capability_model_from_yaml — 读取文件: {capability_model_path}")
    with capability_model_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    fleets_raw = raw.get("fleets", {})
    debug.log(f"  集群数量: {len(fleets_raw)}")
    result: dict[str, dict[str, Any]] = {}

    for fleet_id, fleet_cfg in fleets_raw.items():
        debug.log(f"\n  [{fleet_id}]")
        fc = fleet_cfg.get("fleet_constraints", {}) or {}

        # 扫描所有 *_capable 字段，展示每个的取值
        all_capable_fields = {k: v for k, v in fc.items()
                              if isinstance(k, str) and k.endswith("_capable")}
        debug.log(f"    能力字段原始值: {all_capable_fields}")

        capabilities = [
            key for key, val in fc.items()
            if isinstance(key, str) and key.endswith("_capable") and val is True
        ]
        debug.log(f"    提取到的能力 (val is True): {capabilities}")

        max_ammo_val = int(fc.get("max_ammo", 999))
        max_energy_val = float(fc.get("max_energy_kwh", 999.0))
        cruise_speed_val = float(fc.get("cruise_speed_kmh", 0))
        debug.log(f"    max_ammo={max_ammo_val}, max_energy_kwh={max_energy_val}, "
                  f"cruise_speed_kmh={cruise_speed_val}")

        result[fleet_id] = {
            "capabilities": sorted(capabilities),
            "max_ammo": max_ammo_val,
            "max_energy_kwh": max_energy_val,
            "cruise_speed_kmh": cruise_speed_val,
        }

    return result


def _get_actor_from_capability_model(
    capability_model: dict[str, dict[str, Any]],
    actor: str,
) -> dict[str, Any]:
    """从 capability_model 获取 actor 信息，未找到则显式报错。"""
    if actor not in capability_model:
        raise ValueError(
            f"执行主体 '{actor}' 未在 capability_model 中定义，"
            f"可用主体: {list(capability_model.keys())}"
        )
    return capability_model[actor]


def resolve_task_params(
    action: str,
    action_defaults: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Resolve deterministic task system parameters for an action."""
    defaults = action_defaults.get(action)
    if defaults is None:
        raise ValueError(
            f"未知动作类型 '{action}'，请在 ACTION_DEFAULTS 或 action_templates.yaml 中定义"
        )
    return {
        "duration_lb": float(defaults["duration_lb"]),
        "required_capability": list(defaults.get("required_capability", [])),
        "energy_cost": float(defaults.get("energy_cost", 0.0)),
        "ammo_cost": int(defaults.get("ammo_cost", 0)),
    }


def derive_actor_resource_limits(
    actor: str,
    capability_model: dict[str, dict[str, Any]],
) -> dict[str, float]:
    """Resolve deterministic resource limits for an actor."""
    actor_info = _get_actor_from_capability_model(capability_model, actor)
    return {
        "max_ammo": float(actor_info["max_ammo"]),
        "max_energy_kwh": float(actor_info["max_energy_kwh"]),
    }


def derive_capability_constraint_params(
    task_id: str,
    actor: str,
    action: str,
    action_defaults: dict[str, dict[str, Any]],
    capability_model: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Resolve deterministic capability-constraint params for a task."""
    task_params = resolve_task_params(action, action_defaults)
    actor_info = _get_actor_from_capability_model(capability_model, actor)
    return {
        "task_id": task_id,
        "required": list(task_params.get("required_capability", [])),
        "actor_capabilities": list(actor_info.get("capabilities", [])),
    }


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
    capability_model: Optional[dict[str, dict[str, Any]]] = None,
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
        capability_model:
            集群能力模型。若提供，则使用真实资源上限和能力匹配约束。

    返回：
        BuiltGraph，可直接传入 VerificationPipeline.verify_graph()。
    """
    action_defaults = action_defaults or ACTION_DEFAULTS
    resource_budgets = resource_budgets or DEFAULT_RESOURCE_BUDGETS

    plan_id = plan.get("plan_id", "unknown")
    segment_id = segment_id or f"seg_{plan_id}"

    participants = plan.get("participants", [])
    assigned_actors = [p["actor_id"] for p in participants]

    # 如果 participants 为空，则从 tasks 中回退推断 actor
    if not assigned_actors:
        assigned_actors = sorted({t["actor"] for t in plan.get("tasks", [])})

    if not assigned_actors:
        raise ValueError("任务计划中没有 participants，也无法从 tasks 推断 actor")

    debug.log_banner("[DEBUG] build_graph_from_task_plan — 开始构建图")
    debug.log(f"  segment_id     : {segment_id}")
    debug.log(f"  assigned_actors: {assigned_actors}")

    builder = TaskGraphBuilder(
        segment_id=segment_id,
        assigned_actors=assigned_actors,
    )

    # 第一版只声明最小段信息
    builder.declare_segment_meta(
        assumed_conditions=["mission_start"],
        interface_ids_to_fulfill=[],
    )

    # -------------------------------------------------------------------------
    # 1. 添加任务节点
    # -------------------------------------------------------------------------
    debug.log(f"\n[DEBUG] === 阶段 1: 添加任务节点 ===")
    for task in plan.get("tasks", []):
        task_id = task["task_id"]
        actor = task["actor"]
        action = task["action"]
        target = task["target"]

        defaults = resolve_task_params(action, action_defaults)

        time_window = task.get("time_window") or {}

        debug.log(f"  [+] 任务: {task_id:30s} | actor={actor:12s} | action={action:15s} | "
                  f"target={target:15s} | dur_lb={defaults['duration_lb']:.1f} | "
                  f"energy={defaults.get('energy_cost', 0):.1f} | ammo={defaults.get('ammo_cost', 0)}")

        builder.add_task(
            task_id=task_id,
            actor=actor,
            action=action,
            target=target,
            duration_lb=defaults["duration_lb"],
            required_capability=defaults["required_capability"],
            energy_cost=defaults["energy_cost"],
            ammo_cost=defaults["ammo_cost"],
            time_window_earliest=time_window.get("earliest"),
            time_window_latest=time_window.get("latest"),
            is_coalition=bool(task.get("is_coalition", False)),
            coalition_members=task.get("coalition_members") or [],
            metadata={
                "condition": task.get("condition"),
                "expected_output": task.get("expected_output"),
                "source": "task_plan_json",
            },
        )

        # 如果 schema 中给出了 deadline，需要额外注册 time_window 约束
        deadline = time_window.get("deadline")
        if deadline is not None:
            builder.add_time_window_constraint(
                task_id=task_id,
                deadline=float(deadline),
                source_label=f"time_window_{task_id}_deadline",
            )

    # -------------------------------------------------------------------------
    # 2. 添加任务依赖边
    # -------------------------------------------------------------------------
    debug.log(f"\n[DEBUG] === 阶段 2: 添加依赖边 ===")
    for rel in plan.get("relations", []):
        relation_type = normalize_relation_type(rel.get("type") or rel.get("relation", "sequence"))

        debug.log(f"  [→] 边: {rel['source']:30s} → {rel['target']:30s} | "
                  f"type={relation_type:20s}"
                  + (f" | sync_tol={rel.get('sync_tolerance')}" if rel.get("sync_tolerance") else "")
                  + (f" | condition={rel.get('condition')}" if rel.get("condition") else ""))

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
    debug.log(f"\n[DEBUG] === 阶段 3: 全局时间约束 ===")
    global_constraints = plan.get("global_constraints") or {}
    total_time_budget = global_constraints.get("total_time_budget")

    if total_time_budget is not None:
        # 对所有无出边节点添加 deadline，近似表示总任务完成时间上限
        sink_nodes = _get_sink_tasks(plan)
        debug.log(f"  total_time_budget = {total_time_budget}")
        debug.log(f"  汇点任务 (sink nodes): {sink_nodes}")
        for tid in sink_nodes:
            debug.log(f"  [+] 为汇点 {tid} 添加 deadline={total_time_budget}")
            builder.add_time_window_constraint(
                task_id=tid,
                deadline=float(total_time_budget),
                source_label=f"global_deadline_{total_time_budget}_{tid}",
            )
    else:
        debug.log("  无全局时间预算约束")

    # -------------------------------------------------------------------------
    # 4. 添加资源约束
    # -------------------------------------------------------------------------
    debug.log(f"\n[DEBUG] === 阶段 4: 资源约束 ===")
    if add_resource_constraints:
        for actor in assigned_actors:
            if capability_model is not None:
                limits = derive_actor_resource_limits(actor, capability_model)
                ammo_limit = limits["max_ammo"]
                energy_limit = limits["max_energy_kwh"]
                debug.log(f"  [+] {actor} (capability_model): ammo <= {ammo_limit}, "
                          f"energy_kwh <= {energy_limit}")
            else:
                budget = resource_budgets.get(actor, resource_budgets["default"])
                ammo_limit = float(budget.get("ammo", 999))
                energy_limit = float(budget.get("energy_kwh", 999.0))
                debug.log(f"  [+] {actor} (fallback): ammo <= {ammo_limit}, "
                          f"energy_kwh <= {energy_limit}")

            builder.add_resource_constraint(
                actor=actor,
                resource_type="ammo",
                max_value=ammo_limit,
            )
            builder.add_resource_constraint(
                actor=actor,
                resource_type="energy_kwh",
                max_value=energy_limit,
            )
    else:
        debug.log("  跳过资源约束添加")

    # -------------------------------------------------------------------------
    # 5. 添加能力约束
    # -------------------------------------------------------------------------
    debug.log(f"\n[DEBUG] === 阶段 5: 能力约束 ===")
    if capability_model is not None:
        for task in plan.get("tasks", []):
            task_id = task["task_id"]
            actor = task["actor"]
            action = task["action"]
            params = derive_capability_constraint_params(
                task_id=task_id,
                actor=actor,
                action=action,
                action_defaults=action_defaults,
                capability_model=capability_model,
            )
            required = params["required"]
            if not required:
                debug.log(f"  [·] {task_id}: action={action} 无能力要求，跳过")
                continue
            actor_caps = params["actor_capabilities"]
            satisfied = set(required).issubset(set(actor_caps))
            mark = "OK" if satisfied else "FAIL"
            debug.log(f"  [{mark}] {task_id}: actor={actor} "
                      f"needs {required}, has {actor_caps} -> "
                      f"{'matched' if satisfied else 'MISMATCH!'}")
            builder.add_capability_constraint(
                task_id=params["task_id"],
                required=required,
                actor_capabilities=actor_caps,
                source_label=f"capability_{task_id}_{actor}",
            )
    else:
        debug.log("  未提供 capability_model，跳过能力约束")

    graph = builder.build()

    # 打印最终构建的图摘要
    debug.log(f"\n[DEBUG] === 构建完成: BuiltGraph 摘要 ===")
    debug.log(f"  segment_id  : {graph.segment_id}")
    debug.log(f"  节点数       : {len(graph.nodes)}")
    debug.log(f"  边数         : {len(graph.edges)}")
    debug.log(f"  约束数       : {len(graph.constraints)}")
    debug.log(f"  执行主体     : {graph.actor_set}")
    debug.log(f"  任务ID列表   : {graph.task_ids}")
    debug.log(f"\n  [节点详情]")
    for tid, node in graph.nodes.items():
        debug.log(f"    {tid}: actor={node.actor}, action={node.action}, target={node.target}, "
                  f"dur_lb={node.duration_lb}, dur_ub={node.duration_ub}, "
                  f"energy={node.energy_cost}, ammo={node.ammo_cost}")
    debug.log(f"\n  [边详情]")
    for edge in graph.edges:
        debug.log(f"    {edge.source} → {edge.target} [{edge.relation}]"
                  + (f" sync_tol={edge.sync_tolerance}" if edge.sync_tolerance else "")
                  + (f" cond={edge.condition}" if edge.condition else ""))
    debug.log(f"\n  [约束详情]")
    for c in graph.constraints:
        debug.log(f"    {c.constraint_id}")
        debug.log(f"      type={c.constraint_type}, label={c.source_label}")
        debug.log(f"      params={c.params}")
        debug.log(f"      applies_to={c.applies_to}")

    return graph


def build_graph_from_task_plan_file(
    task_plan_path: str | Path,
    *,
    schema_path: Optional[str | Path] = None,
    action_templates_path: Optional[str | Path] = None,
    capability_model_path: Optional[str | Path] = None,
    environment_config_path: Optional[str | Path] = None,
    segment_id: Optional[str] = None,
) -> BuiltGraph:
    """
    从任务计划 JSON 文件直接构建 BuiltGraph。

    适合 demo 中直接调用。
    """
    plan = load_task_plan(task_plan_path, schema_path=schema_path)

    debug.log_banner("[DEBUG] build_graph_from_task_plan_file — 加载的任务计划", char="=")
    debug.log(f"  plan_id       : {plan.get('plan_id')}")
    debug.log(f"  description   : {plan.get('description', 'N/A')}")
    debug.log(f"  任务数         : {len(plan.get('tasks', []))}")
    debug.log(f"  关系数         : {len(plan.get('relations', []))}")
    debug.log(f"  参与者         : {[p.get('actor_id') for p in plan.get('participants', [])]}")
    debug.log(f"  全局约束       : {plan.get('global_constraints', {})}")

    action_defaults = ACTION_DEFAULTS
    if action_templates_path is not None:
        action_defaults = load_action_defaults_from_yaml(action_templates_path)

    debug.log(f"\n[DEBUG] 使用的动作默认参数来源: "
              f"{'action_templates.yaml' if action_templates_path else '内置 ACTION_DEFAULTS'}")
    for act_name, act_cfg in action_defaults.items():
        debug.log(f"  {act_name:20s} → duration_lb={act_cfg['duration_lb']}, "
                  f"energy={act_cfg['energy_cost']}, ammo={act_cfg['ammo_cost']}, "
                  f"caps={act_cfg.get('required_capability', [])}")

    capability_model = None
    if capability_model_path is not None:
        capability_model = load_capability_model_from_yaml(capability_model_path)
        debug.log(f"\n[DEBUG] 使用的能力模型来源: capability_model.yaml")
        for fleet_id, info in capability_model.items():
            debug.log(f"  {fleet_id:20s} → caps={info['capabilities']}, "
                      f"max_ammo={info['max_ammo']}, max_energy={info['max_energy_kwh']}")
    else:
        debug.log(f"\n[DEBUG] 未提供 capability_model，使用默认资源上限")

    # 环境配置引用校验
    if environment_config_path is not None:
        debug.log(f"\n[DEBUG] 使用的环境配置来源: environment_facilities.yaml")
        env_config = load_environment_config(environment_config_path)
        env_result = validate_task_plan_environment(plan, env_config)
        if env_result.errors:
            raise ValueError(
                "环境引用校验失败:\n" + "\n".join(env_result.errors)
            )
        for w in env_result.warnings:
            debug.log(f"  [环境校验 WARNING] {w}")
        debug.log(f"  [环境校验] 引用校验通过")
    else:
        debug.log(f"\n[DEBUG] 未提供 environment_config_path，跳过环境引用校验")

    return build_graph_from_task_plan(
        plan,
        segment_id=segment_id,
        action_defaults=action_defaults,
        capability_model=capability_model,
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
