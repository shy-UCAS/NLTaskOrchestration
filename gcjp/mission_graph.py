"""
gcjp/mission_graph.py
GCJP (Graph-Code Joint Planning) 受限 API
LLM 生成的代码只能调用此文件中定义的白名单方法
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import networkx as nx

from gcjp.api_spec import (
    ALLOWED_BUILDER_METHODS,
    RELATION_ALIASES,
    VALID_CONSTRAINT_TYPES,
    VALID_RELATION_TYPES,
    VALID_RESOURCE_TYPES,
    VALID_TASK_METADATA_KEYS,
)
from gcjp.errors import GCJPAPIError


class MissingTaskParameterError(GCJPAPIError):
    """Raised before TaskNode construction when a system parameter is unresolved."""

    def __init__(
        self,
        field: str,
        *,
        task_id: str,
        action: str,
        has_runtime_config: bool,
    ):
        super().__init__(
            code="MISSING_TASK_PARAMETER",
            message=(
                f"system parameter missing: {field} for task '{task_id}' "
                f"(action='{action}'); no injected action_defaults/capability_model "
                "or explicit GCJP parameter was provided"
            ),
            api="add_task",
            actual={field: None, "action": action, "has_runtime_config": has_runtime_config},
            expected=(
                "duration_lb / energy_cost / ammo_cost / required_capability must be "
                "explicitly provided or resolved from injected runtime config"
            ),
            hint=(
                "For exp_01k, call execute_gcjp_code(..., action_defaults=..., "
                "capability_model=...) so API-fill code can bind system parameters."
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# 数据类定义
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TaskNode:
    """DAG 中的任务节点"""
    task_id: str
    actor: str
    action: str
    target: str
    duration_lb: float              # 持续时间下界（时间单位）
    duration_ub: Optional[float]    # 持续时间上界（None=无上界）
    required_capability: list[str]  # 执行此任务所需能力
    energy_cost: float              # 能量消耗
    ammo_cost: int                  # 弹药消耗
    time_window_earliest: Optional[float] = None
    time_window_latest: Optional[float] = None
    is_coalition: bool = False
    coalition_members: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class DependencyEdge:
    """DAG 中的依赖边"""
    source: str                     # 源任务 task_id
    target: str                     # 目标任务 task_id
    relation: str                   # sequence / sync / fork / join / conditional
    sync_tolerance: Optional[float] = None
    condition: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class Constraint:
    """挂载在图上的额外约束"""
    constraint_id: str
    constraint_type: str            # time_order / duration / time_window / sync /
                                    # group_sync / resource / capability /
                                    # physical_feasibility
    params: dict
    source_label: str               # 来源标注（用于 unsat core 归因）
    applies_to: list[str]           # 涉及的 task_id 列表


@dataclass
class InterfaceFulfillment:
    """段出口处的接口履行声明"""
    interface_id: str
    exit_node: str
    resource_state: dict
    guaranteed_conditions: list[str]


# Backward-compatible type alias for old callers.
ContractFulfillment = InterfaceFulfillment


@dataclass
class SegmentMeta:
    """GCJP 代码头部的段元信息"""
    segment_id: str
    assigned_actors: list[str]
    assumed_conditions: list[str]   # 本段假设上游已满足的条件
    interface_ids_to_fulfill: list[str]

    @property
    def contract_ids_to_fulfill(self) -> list[str]:
        """Backward-compatible alias for older code."""
        return self.interface_ids_to_fulfill


# ─────────────────────────────────────────────────────────────────────────────
# TaskGraphBuilder —— 受限 API 主类
# ─────────────────────────────────────────────────────────────────────────────

class TaskGraphBuilder:
    """
    GCJP 受限 API 主类。
    LLM 生成的规划代码通过调用此类的方法来构建任务图，
    不允许直接操作底层 NetworkX 对象。

    典型用法（LLM生成的代码片段）：
    ─────────────────────────────────
    from gcjp.mission_graph import TaskGraphBuilder

    g = TaskGraphBuilder(segment_id="seg_fleet1_solo", assigned_actors=["fleet_1"])

    # 声明段元信息
    g.declare_segment_meta(
        assumed_conditions=["fleet_1 at initial position"],
        interface_ids_to_fulfill=["interface_fleet1_to_coalition"]
    )

    # 添加任务节点
    g.add_task("t1_recon_mark9", actor="fleet_1", action="reconnaissance",
               target="hq_mark9", duration_lb=2.0, required_capability=["recon_capable"],
               energy_cost=3.0, ammo_cost=0)

    g.add_task("t2_fly_to_mark8", actor="fleet_1", action="fly_to",
               target="hq_mark8", duration_lb=1.5, required_capability=[],
               energy_cost=2.0, ammo_cost=0)

    # 添加依赖边
    g.add_dependency("t1_recon_mark9", "t2_fly_to_mark8", relation="sequence")

    # 添加资源约束
    g.add_resource_constraint("fleet_1", "ammo", max_value=4)
    g.add_resource_constraint("fleet_1", "energy_kwh", max_value=50.0)

    # 声明出口资源状态（供整合器和下游段使用）
    g.declare_resource_state("fleet_1", remaining_ammo=4, remaining_energy=45.0,
                              position="hq_mark8")

    g.declare_interface_fulfillment(
        interface_id="interface_fleet1_to_coalition",
        exit_node="t2_fly_to_mark8",
        resource_state={"fleet_1": {"ammo": 4, "energy_kwh": 45.0}},
        guaranteed_conditions=["fleet_1 completed recon of hq_mark9",
                                "fleet_1 at hq_mark8"]
    )
    ─────────────────────────────────
    """

    # 白名单：允许 LLM 调用的方法名集合（由 safety_checker.py 验证）
    ALLOWED_METHODS = ALLOWED_BUILDER_METHODS

    def __init__(self, segment_id: str, assigned_actors: list[str]):
        self.segment_id = segment_id
        self.assigned_actors = assigned_actors

        self._graph = nx.DiGraph()
        self._nodes: dict[str, TaskNode] = {}
        self._edges: list[DependencyEdge] = []
        self._constraints: list[Constraint] = []
        self._interface_fulfillments: list[InterfaceFulfillment] = []
        self._resource_states: dict[str, dict] = {}
        self._segment_meta: Optional[SegmentMeta] = None
        self._constraint_counter = 0
        self._constraint_source_labels: set[str] = set()
        self._constraint_semantic_index: dict[tuple[Any, ...], str] = {}
        self._dependency_semantic_keys: set[tuple[Any, ...]] = set()

    # ─────────────────────────────────────────────────────────────────────────
    # 段元信息声明
    # ─────────────────────────────────────────────────────────────────────────

    def declare_segment_meta(
        self,
        assumed_conditions: list[str],
        interface_ids_to_fulfill: Optional[list[str]] = None,
        contract_ids_to_fulfill: Optional[list[str]] = None,
    ) -> None:
        """声明本段的元信息（必须在 add_task 之前调用）"""
        if interface_ids_to_fulfill is None:
            interface_ids_to_fulfill = contract_ids_to_fulfill or []
        self._segment_meta = SegmentMeta(
            segment_id=self.segment_id,
            assigned_actors=self.assigned_actors,
            assumed_conditions=assumed_conditions,
            interface_ids_to_fulfill=interface_ids_to_fulfill,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 任务节点管理
    # ─────────────────────────────────────────────────────────────────────────

    def add_task(
        self,
        task_id: str,
        actor: str,
        action: str,
        target: str,
        duration_lb: Optional[float] = None,
        required_capability: Optional[list[str]] = None,
        energy_cost: Optional[float] = None,
        ammo_cost: Optional[int] = None,
        duration_ub: Optional[float] = None,
        time_window_earliest: Optional[float] = None,
        time_window_latest: Optional[float] = None,
        is_coalition: bool = False,
        coalition_members: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """
        向任务图中添加一个原子任务节点。

        参数:
            task_id:               唯一任务ID，建议格式: t{n}_{action}_{target}
            actor:                 执行主体ID（必须在 assigned_actors 中）
            action:                动作类型（必须在 action_templates.yaml 白名单中）
            target:                任务目标点/区域ID
            duration_lb:           持续时间下界（时间单位，必须 > 0）
            required_capability:   所需能力列表（如 ["recon_capable"]）
            energy_cost:           能量消耗（kWh）
            ammo_cost:             弹药消耗（整数，默认0）
            duration_ub:           持续时间上界（None=无上界）
            time_window_earliest:  最早开始时间
            time_window_latest:    最晚开始时间（deadline前必须开始）
            is_coalition:          是否为联合执行任务
            coalition_members:     联合执行的所有主体ID列表
        """
        if task_id in self._nodes:
            raise GCJPAPIError(
                code="DUPLICATE_TASK_ID",
                message=f"task_id '{task_id}' 已存在，请使用唯一ID",
                api="add_task",
                actual=task_id,
                expected={"existing_task_ids": list(self._nodes.keys())},
                hint="task_id 必须唯一；改用不同标识，或检查是否漏调 add_dependency 而重复 add_task。",
            )

        runtime_config = None
        if (
            duration_lb is None
            or required_capability is None
            or energy_cost is None
            or ammo_cost is None
        ):
            from gcjp.runtime_context import get_runtime_config

            runtime_config = get_runtime_config()
            action_defaults = runtime_config.action_defaults if runtime_config else None
            if action_defaults is not None:
                from gcjp.task_plan_loader import resolve_task_params

                resolved = resolve_task_params(action, action_defaults)
                if duration_lb is None:
                    duration_lb = resolved["duration_lb"]
                if required_capability is None:
                    required_capability = resolved["required_capability"]
                if energy_cost is None:
                    energy_cost = resolved["energy_cost"]
                if ammo_cost is None:
                    ammo_cost = resolved["ammo_cost"]
            elif (
                duration_lb is not None
                and required_capability is not None
                and energy_cost is not None
                and ammo_cost is None
            ):
                # Backward compatibility with the old add_task(..., ammo_cost=0) default.
                ammo_cost = 0

        has_runtime_config = runtime_config is not None
        missing_fields = {
            "duration_lb": duration_lb,
            "required_capability": required_capability,
            "energy_cost": energy_cost,
            "ammo_cost": ammo_cost,
        }
        for field_name, field_value in missing_fields.items():
            if field_value is None:
                raise MissingTaskParameterError(
                    field_name,
                    task_id=task_id,
                    action=action,
                    has_runtime_config=has_runtime_config,
                )

        try:
            duration_lb = float(duration_lb)
        except (TypeError, ValueError) as exc:
            raise GCJPAPIError(
                code="INVALID_DURATION_LB",
                message=f"duration_lb 必须是可转换为 float 的正数，当前值: {duration_lb}",
                api="add_task",
                actual=duration_lb,
                expected="positive float",
                hint="duration_lb 表示任务最短持续时间，必须为正浮点数，如 1.0。",
            ) from exc
        if duration_lb <= 0:
            raise GCJPAPIError(
                code="INVALID_DURATION_LB",
                message=f"duration_lb 必须大于0，当前值: {duration_lb}",
                api="add_task",
                actual=duration_lb,
                expected="duration_lb > 0",
                hint="duration_lb 表示任务最短持续时间，必须为正浮点数，如 1.0。",
            )
        if isinstance(required_capability, str) or not isinstance(
            required_capability, (list, tuple, set)
        ):
            raise GCJPAPIError(
                code="INVALID_REQUIRED_CAPABILITY",
                message=(
                    "required_capability 必须是 list/tuple/set，"
                    f"当前类型: {type(required_capability).__name__}"
                ),
                api="add_task",
                actual=required_capability,
                expected="list[str] or tuple[str] or set[str]",
                hint="required_capability 示例: ['recon_capable']；无能力要求时使用 []。",
            )
        required_capability = list(required_capability)
        try:
            energy_cost = float(energy_cost)
        except (TypeError, ValueError) as exc:
            raise GCJPAPIError(
                code="INVALID_ENERGY_COST",
                message=f"energy_cost 必须是可转换为 float 的数值，当前值: {energy_cost}",
                api="add_task",
                actual=energy_cost,
                expected="float",
                hint="energy_cost 表示任务能量消耗，应来自配置或显式 GCJP 参数。",
            ) from exc
        try:
            ammo_cost = int(ammo_cost)
        except (TypeError, ValueError) as exc:
            raise GCJPAPIError(
                code="INVALID_AMMO_COST",
                message=f"ammo_cost 必须是可转换为 int 的数值，当前值: {ammo_cost}",
                api="add_task",
                actual=ammo_cost,
                expected="int",
                hint="ammo_cost 表示任务弹药消耗，应来自配置或显式 GCJP 参数。",
            ) from exc
        if actor not in self.assigned_actors and not is_coalition:
            raise GCJPAPIError(
                code="ACTOR_NOT_ASSIGNED",
                message=f"actor '{actor}' 不在本段的 assigned_actors {self.assigned_actors} 中",
                api="add_task",
                actual=actor,
                expected={"assigned_actors": list(self.assigned_actors)},
                hint=(
                    "将 actor 改为 assigned_actors 中已有的值；"
                    "若确需新增执行主体，请在 TaskGraphBuilder(...) 的 assigned_actors 中包含它。"
                ),
            )
        metadata = metadata or {}
        unknown_metadata = set(metadata) - VALID_TASK_METADATA_KEYS
        if unknown_metadata:
            raise GCJPAPIError(
                code="ILLEGAL_METADATA_KEY",
                message=f"metadata 中存在非法字段: {sorted(unknown_metadata)}",
                api="add_task",
                actual=sorted(unknown_metadata),
                expected={"allowed_metadata_keys": sorted(VALID_TASK_METADATA_KEYS)},
                hint="metadata 仅允许 condition / expected_output / source / priority 等字段；删除或重命名非法键。",
            )

        node = TaskNode(
            task_id=task_id,
            actor=actor,
            action=action,
            target=target,
            duration_lb=duration_lb,
            duration_ub=duration_ub,
            required_capability=required_capability,
            energy_cost=energy_cost,
            ammo_cost=ammo_cost,
            time_window_earliest=time_window_earliest,
            time_window_latest=time_window_latest,
            is_coalition=is_coalition,
            coalition_members=coalition_members or [],
            metadata=metadata,
        )
        self._nodes[task_id] = node
        self._graph.add_node(task_id, **node.__dict__)

        # 自动注册时间窗约束
        if time_window_earliest is not None or time_window_latest is not None:
            self.add_time_window_constraint(
                task_id=task_id,
                earliest=time_window_earliest,
                latest=time_window_latest,
                source_label=f"time_window_auto_{task_id}",
            )

    # ─────────────────────────────────────────────────────────────────────────
    # 依赖边管理
    # ─────────────────────────────────────────────────────────────────────────

    def add_dependency(
        self,
        source: str,
        target: str,
        relation: str,
        sync_tolerance: Optional[float] = None,
        condition: Optional[str] = None,
    ) -> None:
        """
        在两个任务节点之间添加依赖边。

        参数:
            source:          源任务 task_id
            target:          目标任务 task_id
            relation:        关系类型 —— 必须是以下之一:
                             'sequence'    顺序执行（target在source完成后开始）
                             'sync'        同步会合（source和target需在同步窗口内）
                             'fork'        分叉（source完成后target与其他任务并行）
                             'join'        汇聚（所有前驱完成后target才能开始）
                             'conditional' 条件触发（需指定condition）
            sync_tolerance:  同步容忍时间窗（仅 relation='sync' 时有效）
            condition:       触发条件（仅 relation='conditional' 时有效）
        """
        relation = RELATION_ALIASES.get(relation, relation)
        if relation not in VALID_RELATION_TYPES:
            valid = sorted(VALID_RELATION_TYPES | set(RELATION_ALIASES))
            raise GCJPAPIError(
                code="INVALID_RELATION",
                message=f"relation 必须是 {valid} 之一，当前: '{relation}'",
                api="add_dependency",
                actual=relation,
                expected={"valid_relations": valid},
                hint=(
                    "relation 必须是 sequence / sync / fork / join / conditional 之一；"
                    "表达\"先后顺序\"用 sequence，\"同时发生\"用 sync。"
                ),
            )
        if source not in self._nodes:
            raise GCJPAPIError(
                code="MISSING_SOURCE_TASK",
                message=f"源任务 '{source}' 未在图中，请先调用 add_task()",
                api="add_dependency",
                actual=source,
                expected={"available_task_ids": list(self._nodes.keys())},
                hint="先用 add_task() 注册该 task_id，或将 source 改为已注册的任务名。",
            )
        if target not in self._nodes:
            raise GCJPAPIError(
                code="MISSING_TARGET_TASK",
                message=f"目标任务 '{target}' 未在图中，请先调用 add_task()",
                api="add_dependency",
                actual=target,
                expected={"available_task_ids": list(self._nodes.keys())},
                hint="先用 add_task() 注册该 task_id，或将 target 改为已注册的任务名。",
            )
        if relation == "sync" and sync_tolerance is None:
            sync_tolerance = 1.0  # 使用默认同步容忍
        edge_key = (source, target, relation)
        if edge_key in self._dependency_semantic_keys:
            return
        self._dependency_semantic_keys.add(edge_key)

        edge = DependencyEdge(
            source=source, target=target, relation=relation,
            sync_tolerance=sync_tolerance, condition=condition,
        )
        self._edges.append(edge)
        self._graph.add_edge(source, target, relation=relation,
                             sync_tolerance=sync_tolerance, condition=condition)

        # 自动注册对应约束
        if relation in {"sequence", "condition_trigger", "handoff", "barrier", "join"}:
            self.add_time_order_constraint(
                before=source,
                after=target,
                source_label=f"{relation}_{source}__{target}",
            )
        elif relation == "sync":
            self.add_sync_constraint(
                task_i=source,
                task_j=target,
                tolerance=sync_tolerance,
                source_label=f"sync_{source}__{target}",
            )

    # ─────────────────────────────────────────────────────────────────────────
    # 约束管理
    # ─────────────────────────────────────────────────────────────────────────

    def _constraint_semantic_key(
        self,
        constraint_type: str,
        params: dict,
    ) -> tuple[Any, ...] | None:
        if constraint_type == "resource":
            return (
                "resource",
                params.get("actor"),
                params.get("resource_type"),
            )
        if constraint_type == "capability":
            return ("capability", params.get("task_id"))
        if constraint_type == "time_window":
            return (
                "time_window",
                params.get("task_id"),
                params.get("earliest"),
                params.get("latest"),
                params.get("deadline"),
            )
        if constraint_type == "time_order":
            return ("time_order", params.get("before"), params.get("after"))
        if constraint_type == "sync":
            return (
                "sync",
                params.get("task_i"),
                params.get("task_j"),
                params.get("tolerance"),
            )
        if constraint_type == "group_sync":
            return (
                "group_sync",
                tuple(params.get("task_ids") or []),
                params.get("tolerance"),
                params.get("mode"),
            )
        if constraint_type == "physical_feasibility":
            return (
                "physical_feasibility",
                params.get("task_id"),
                params.get("from_position"),
                params.get("to_position"),
                params.get("distance_km"),
                params.get("speed_kmh"),
            )
        return None

    def _add_constraint(
        self,
        constraint_type: str,
        params: dict,
        source_label: str,
    ) -> str:
        """
        内部约束注册方法。
        LLM 生成代码不允许直接调用，应使用 add_xxx_constraint() 结构化接口。
        """
        if constraint_type not in VALID_CONSTRAINT_TYPES:
            raise GCJPAPIError(
                code="INVALID_CONSTRAINT_TYPE",
                message=f"constraint_type 必须是 {VALID_CONSTRAINT_TYPES} 之一",
                api="_add_constraint",
                actual=constraint_type,
                expected={"valid_constraint_types": sorted(VALID_CONSTRAINT_TYPES)},
                hint=(
                    "请改用结构化 API（add_time_window_constraint / add_time_order_constraint / "
                    "add_resource_constraint / ...）而非直接调用 add_constraint。"
                ),
            )
        semantic_key = self._constraint_semantic_key(constraint_type, params)
        if semantic_key is not None and semantic_key in self._constraint_semantic_index:
            return self._constraint_semantic_index[semantic_key]
        if not source_label:
            raise GCJPAPIError(
                code="EMPTY_SOURCE_LABEL",
                message="source_label 不能为空",
                api="_add_constraint",
                actual=source_label,
                expected="非空字符串",
                hint="为约束提供唯一的 source_label 字符串，便于 unsat core 归因。",
            )
        if source_label in self._constraint_source_labels:
            raise GCJPAPIError(
                code="DUPLICATE_SOURCE_LABEL",
                message=f"source_label '{source_label}' 已存在，请保持约束来源唯一",
                api="_add_constraint",
                actual=source_label,
                expected="unique within graph",
                hint="约束的 source_label 必须全局唯一；为不同约束分配不同标签，或检查是否重复添加。",
            )
        self._constraint_source_labels.add(source_label)

        self._constraint_counter += 1
        cid = f"c{self._constraint_counter:04d}_{constraint_type}_{source_label}"

        # 推断 applies_to
        applies_to = []
        for key in ("task_id", "before", "after", "task_i", "task_j", "actor"):
            if key in params:
                val = params[key]
                if isinstance(val, str) and val in self._nodes:
                    applies_to.append(val)
        if "task_ids" in params:
            for tid in params["task_ids"]:
                if (isinstance(tid, str)
                        and tid in self._nodes
                        and tid not in applies_to):
                    applies_to.append(tid)

        constraint = Constraint(
            constraint_id=cid,
            constraint_type=constraint_type,
            params=params,
            source_label=source_label,
            applies_to=applies_to,
        )
        self._constraints.append(constraint)
        if semantic_key is not None:
            self._constraint_semantic_index[semantic_key] = cid
        return cid

    def add_constraint(
        self,
        constraint_type: str,
        params: dict,
        source_label: str,
    ) -> str:
        """
        兼容旧代码用法。新的 LLM 生成代码不允许调用此方法。
        """
        return self._add_constraint(
            constraint_type=constraint_type,
            params=params,
            source_label=source_label,
        )

    def add_time_order_constraint(
        self,
        before: str,
        after: str,
        source_label: Optional[str] = None,
    ) -> str:
        if before not in self._nodes:
            raise GCJPAPIError(
                code="TIME_ORDER_BEFORE_NOT_FOUND",
                message=f"before task '{before}' 未在图中",
                api="add_time_order_constraint",
                actual=before,
                expected={"available_task_ids": list(self._nodes.keys())},
                hint="先用 add_task() 注册该任务，或将 before 改为已注册的 task_id。",
            )
        if after not in self._nodes:
            raise GCJPAPIError(
                code="TIME_ORDER_AFTER_NOT_FOUND",
                message=f"after task '{after}' 未在图中",
                api="add_time_order_constraint",
                actual=after,
                expected={"available_task_ids": list(self._nodes.keys())},
                hint="先用 add_task() 注册该任务，或将 after 改为已注册的 task_id。",
            )
        return self._add_constraint(
            constraint_type="time_order",
            params={"before": before, "after": after},
            source_label=source_label or f"time_order_{before}__{after}",
        )

    def add_time_window_constraint(
        self,
        task_id: str,
        earliest: Optional[float] = None,
        latest: Optional[float] = None,
        deadline: Optional[float] = None,
        source_label: Optional[str] = None,
    ) -> str:
        if task_id not in self._nodes:
            raise GCJPAPIError(
                code="TIME_WINDOW_TASK_NOT_FOUND",
                message=f"task_id '{task_id}' 未在图中",
                api="add_time_window_constraint",
                actual=task_id,
                expected={"available_task_ids": list(self._nodes.keys())},
                hint="先用 add_task() 注册该任务，或将 task_id 改为已注册的任务名。",
            )
        if earliest is None and latest is None and deadline is None:
            raise GCJPAPIError(
                code="EMPTY_TIME_WINDOW",
                message="time_window 至少需要 earliest/latest/deadline 中的一个",
                api="add_time_window_constraint",
                actual={"earliest": earliest, "latest": latest, "deadline": deadline},
                expected="至少一个浮点值",
                hint="为时间窗约束至少指定 earliest、latest、deadline 中的一个浮点数。",
            )
        return self._add_constraint(
            constraint_type="time_window",
            params={
                "task_id": task_id,
                "earliest": earliest,
                "latest": latest,
                "deadline": deadline,
            },
            source_label=source_label or f"time_window_{task_id}",
        )

    def add_sync_constraint(
        self,
        task_i: str,
        task_j: str,
        tolerance: float = 1.0,
        source_label: Optional[str] = None,
    ) -> str:
        if task_i not in self._nodes:
            raise GCJPAPIError(
                code="SYNC_TASK_I_NOT_FOUND",
                message=f"task_i '{task_i}' 未在图中",
                api="add_sync_constraint",
                actual=task_i,
                expected={"available_task_ids": list(self._nodes.keys())},
                hint="先用 add_task() 注册该任务，或将 task_i 改为已注册的 task_id。",
            )
        if task_j not in self._nodes:
            raise GCJPAPIError(
                code="SYNC_TASK_J_NOT_FOUND",
                message=f"task_j '{task_j}' 未在图中",
                api="add_sync_constraint",
                actual=task_j,
                expected={"available_task_ids": list(self._nodes.keys())},
                hint="先用 add_task() 注册该任务，或将 task_j 改为已注册的 task_id。",
            )
        if tolerance < 0:
            raise GCJPAPIError(
                code="INVALID_SYNC_TOLERANCE",
                message="sync tolerance 不能为负数",
                api="add_sync_constraint",
                actual=tolerance,
                expected="tolerance >= 0",
                hint="tolerance 表示同步时间窗（分钟），必须为非负浮点数，如 5.0。",
            )
        return self._add_constraint(
            constraint_type="sync",
            params={"task_i": task_i, "task_j": task_j, "tolerance": tolerance},
            source_label=source_label or f"sync_{task_i}__{task_j}",
        )

    def add_group_sync_constraint(
        self,
        task_ids: list[str],
        tolerance: float = 1.0,
        mode: str = "start",
        source_label: Optional[str] = None,
    ) -> str:
        """
        添加多任务同步约束。

        采用两两同步语义：组内任意两个任务的 start/end 时间差
        不超过 tolerance。mode 可选 start / end / both。
        """
        if not isinstance(task_ids, list):
            raise GCJPAPIError(
                code="INVALID_GROUP_SYNC_TASK_IDS",
                message="group sync 的 task_ids 必须是任务ID列表",
                api="add_group_sync_constraint",
                actual=task_ids,
                expected="list[str]",
                hint="请传入形如 ['T1', 'T2', 'T3'] 的任务ID列表。",
            )
        if len(task_ids) < 2:
            raise GCJPAPIError(
                code="GROUP_SYNC_TOO_FEW_TASKS",
                message="group sync 至少需要两个任务",
                api="add_group_sync_constraint",
                actual=task_ids,
                expected="len(task_ids) >= 2",
                hint="如果只有两个任务，可使用 add_sync_constraint；多任务同步请提供至少两个 task_id。",
            )

        invalid_items = [tid for tid in task_ids if not isinstance(tid, str)]
        if invalid_items:
            raise GCJPAPIError(
                code="INVALID_GROUP_SYNC_TASK_ID",
                message=f"group sync 中存在非法 task_id: {invalid_items}",
                api="add_group_sync_constraint",
                actual=invalid_items,
                expected="task_ids 中的每一项都是字符串",
                hint="请传入已注册任务的 task_id 字符串列表。",
            )

        seen = set()
        duplicates = []
        for tid in task_ids:
            if tid in seen and tid not in duplicates:
                duplicates.append(tid)
            seen.add(tid)
        if duplicates:
            raise GCJPAPIError(
                code="GROUP_SYNC_DUPLICATE_TASKS",
                message=f"group sync 中存在重复任务: {duplicates}",
                api="add_group_sync_constraint",
                actual=task_ids,
                expected="task_ids 中每个任务最多出现一次",
                hint="请移除重复的 task_id，保证每个参与同步的任务只出现一次。",
            )

        missing = [tid for tid in task_ids if tid not in self._nodes]
        if missing:
            raise GCJPAPIError(
                code="GROUP_SYNC_TASK_NOT_FOUND",
                message=f"group sync 中存在未注册任务: {missing}",
                api="add_group_sync_constraint",
                actual=missing,
                expected={"available_task_ids": list(self._nodes.keys())},
                hint="请先用 add_task() 注册所有参与同步的任务。",
            )
        if tolerance < 0:
            raise GCJPAPIError(
                code="INVALID_GROUP_SYNC_TOLERANCE",
                message="group sync tolerance 不能为负数",
                api="add_group_sync_constraint",
                actual=tolerance,
                expected="tolerance >= 0",
                hint="tolerance 表示多任务同步时间窗，必须为非负浮点数，如 5.0。",
            )
        if mode not in {"start", "end", "both"}:
            raise GCJPAPIError(
                code="INVALID_GROUP_SYNC_MODE",
                message=f"group sync mode 不合法: {mode}",
                api="add_group_sync_constraint",
                actual=mode,
                expected=["start", "end", "both"],
                hint="mode 可选 start / end / both。",
            )

        return self._add_constraint(
            constraint_type="group_sync",
            params={
                "task_ids": task_ids,
                "tolerance": tolerance,
                "mode": mode,
            },
            source_label=source_label or f"group_sync_{mode}_{'_'.join(task_ids)}",
        )

    def add_capability_constraint(
        self,
        task_id: str,
        required: list[str],
        actor_capabilities: list[str],
        source_label: Optional[str] = None,
    ) -> str:
        if task_id not in self._nodes:
            raise GCJPAPIError(
                code="CAPABILITY_TASK_NOT_FOUND",
                message=f"task_id '{task_id}' 未在图中",
                api="add_capability_constraint",
                actual=task_id,
                expected={"available_task_ids": list(self._nodes.keys())},
                hint="先用 add_task() 注册该任务，或将 task_id 改为已注册的任务名。",
            )
        return self._add_constraint(
            constraint_type="capability",
            params={
                "task_id": task_id,
                "required": required,
                "actor_capabilities": actor_capabilities,
            },
            source_label=source_label or f"capability_{task_id}",
        )

    def add_resource_constraint(
        self,
        actor: str,
        resource_type: str,
        max_value: float,
        source_label: Optional[str] = None,
    ) -> str:
        """
        为指定执行主体添加资源上限约束。

        参数:
            actor:         执行主体ID
            resource_type: 资源类型 —— 'ammo' | 'energy_kwh'
            max_value:     资源上限值
        """
        if resource_type not in VALID_RESOURCE_TYPES:
            raise GCJPAPIError(
                code="INVALID_RESOURCE_TYPE",
                message=f"resource_type 必须是 {VALID_RESOURCE_TYPES} 之一",
                api="add_resource_constraint",
                actual=resource_type,
                expected={"valid_resource_types": sorted(VALID_RESOURCE_TYPES)},
                hint="resource_type 只能是 'ammo' 或 'energy_kwh'。",
            )

        # 计算该 actor 在本段所有任务中的总消耗
        total_cost_key = "ammo_cost" if resource_type == "ammo" else "energy_cost"

        return self._add_constraint(
            constraint_type="resource",
            params={
                "actor": actor,
                "resource_type": resource_type,
                "max_value": max_value,
                "cost_key": total_cost_key,
            },
            source_label=source_label or f"resource_{actor}_{resource_type}",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 物理可行性约束（快捷方法）
    # ─────────────────────────────────────────────────────────────────────────

    def add_physical_feasibility_constraint(
        self,
        task_id: str,
        from_position: str,
        to_position: str,
        distance_km: float,
        actor_speed_kmh: float,
        time_unit_minutes: float = 1.0,
        source_label: Optional[str] = None,
    ) -> str:
        """
        添加物理可行性约束：任务持续时间 >= 飞行距离 / 速度。

        参数:
            task_id:            任务节点ID
            from_position:      出发点ID
            to_position:        目标点ID
            distance_km:        飞行距离（km）
            actor_speed_kmh:    执行主体巡航速度（km/h）
            time_unit_minutes:  时间单位换算（默认1时间单位=1分钟）
        """
        if task_id not in self._nodes:
            raise GCJPAPIError(
                code="PHYSICAL_FEASIBILITY_TASK_NOT_FOUND",
                message=f"task_id '{task_id}' 未在图中",
                api="add_physical_feasibility_constraint",
                actual=task_id,
                expected={"available_task_ids": list(self._nodes.keys())},
                hint="先用 add_task() 注册该任务，或将 task_id 改为已注册的任务名。",
            )
        if distance_km < 0:
            raise GCJPAPIError(
                code="INVALID_DISTANCE",
                message="distance_km 不能为负数",
                api="add_physical_feasibility_constraint",
                actual=distance_km,
                expected="distance_km >= 0",
                hint="distance_km 表示两点间飞行距离（公里），必须为非负浮点数。",
            )
        if actor_speed_kmh <= 0:
            raise GCJPAPIError(
                code="INVALID_ACTOR_SPEED",
                message="actor_speed_kmh 必须大于0",
                api="add_physical_feasibility_constraint",
                actual=actor_speed_kmh,
                expected="actor_speed_kmh > 0",
                hint="actor_speed_kmh 表示执行主体巡航速度（km/h），必须为正浮点数，如 60.0。",
            )
        if time_unit_minutes <= 0:
            raise GCJPAPIError(
                code="INVALID_TIME_UNIT",
                message="time_unit_minutes 必须大于0",
                api="add_physical_feasibility_constraint",
                actual=time_unit_minutes,
                expected="time_unit_minutes > 0",
                hint="time_unit_minutes 表示 1 时间单位 = 多少分钟，必须为正浮点数；默认 1.0。",
            )

        min_flight_minutes = (distance_km / actor_speed_kmh) * 60
        min_duration_units = min_flight_minutes / time_unit_minutes

        return self._add_constraint(
            constraint_type="physical_feasibility",
            params={
                "task_id": task_id,
                "from_position": from_position,
                "to_position": to_position,
                "distance_km": distance_km,
                "speed_kmh": actor_speed_kmh,
                "min_duration_units": min_duration_units,
            },
            source_label=source_label or f"phys_feasibility_{task_id}",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 出口资源状态与契约履行
    # ─────────────────────────────────────────────────────────────────────────

    def declare_resource_state(
        self,
        actor: str,
        remaining_ammo: int,
        remaining_energy: float,
        position: str,
        remaining_range_km: Optional[float] = None,
    ) -> None:
        """
        声明本段结束时指定主体的剩余资源状态。
        整合器使用此信息推导下游段的初始资源状态。

        参数:
            actor:               执行主体ID
            remaining_ammo:      剩余弹药量
            remaining_energy:    剩余能量（kWh）
            position:            本段结束时的位置（目标点ID）
            remaining_range_km:  剩余航程（可选）
        """
        self._resource_states[actor] = {
            "remaining_ammo": remaining_ammo,
            "remaining_energy_kwh": remaining_energy,
            "position": position,
            "remaining_range_km": remaining_range_km,
        }

    def declare_interface_fulfillment(
        self,
        interface_id: str,
        exit_node: str,
        resource_state: dict,
        guaranteed_conditions: list[str],
    ) -> None:
        """
        声明本段履行了指定接口契约。

        参数:
            interface_id:           契约接口ID（与 decomposition_schema 中的 contract_id 对应）
            exit_node:              履行契约的出口任务节点 task_id
            resource_state:         出口时的资源状态快照（dict of actor -> dict）
            guaranteed_conditions:  本段保证提供给下游的条件列表（自然语言）
        """
        if exit_node not in self._nodes:
            raise GCJPAPIError(
                code="MISSING_EXIT_NODE",
                message=f"出口节点 '{exit_node}' 未在图中",
                api="declare_interface_fulfillment",
                actual=exit_node,
                expected={"available_task_ids": list(self._nodes.keys())},
                hint="将 exit_node 设为本段实际出口任务的 task_id；该任务必须先通过 add_task() 注册。",
            )

        fulfillment = InterfaceFulfillment(
            interface_id=interface_id,
            exit_node=exit_node,
            resource_state=resource_state,
            guaranteed_conditions=guaranteed_conditions,
        )
        self._interface_fulfillments.append(fulfillment)

    def declare_contract_fulfillment(
        self,
        interface_id: str,
        exit_node: str,
        resource_state: dict,
        guaranteed_conditions: list[str],
    ) -> None:
        """
        兼容旧名称。新的 LLM 生成代码不允许调用此方法。
        """
        return self.declare_interface_fulfillment(
            interface_id=interface_id,
            exit_node=exit_node,
            resource_state=resource_state,
            guaranteed_conditions=guaranteed_conditions,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 构建与导出
    # ─────────────────────────────────────────────────────────────────────────

    def _derive_runtime_config_constraints(self) -> None:
        from gcjp.runtime_context import get_runtime_config

        runtime_config = get_runtime_config()
        if (
            runtime_config is None
            or runtime_config.action_defaults is None
            or runtime_config.capability_model is None
        ):
            return

        from gcjp.task_plan_loader import (
            derive_actor_resource_limits,
            derive_capability_constraint_params,
        )

        for actor in self.assigned_actors:
            limits = derive_actor_resource_limits(actor, runtime_config.capability_model)
            self.add_resource_constraint(
                actor=actor,
                resource_type="ammo",
                max_value=limits["max_ammo"],
                source_label=f"auto_resource_{actor}_ammo",
            )
            self.add_resource_constraint(
                actor=actor,
                resource_type="energy_kwh",
                max_value=limits["max_energy_kwh"],
                source_label=f"auto_resource_{actor}_energy_kwh",
            )

        for task_id, node in self._nodes.items():
            params = derive_capability_constraint_params(
                task_id=task_id,
                actor=node.actor,
                action=node.action,
                action_defaults=runtime_config.action_defaults,
                capability_model=runtime_config.capability_model,
            )
            self.add_capability_constraint(
                task_id=params["task_id"],
                required=params["required"],
                actor_capabilities=params["actor_capabilities"],
                source_label=f"auto_capability_{task_id}_{node.actor}",
            )

    def build(self) -> "BuiltGraph":
        """
        完成图构建，返回 BuiltGraph 对象供验证管道使用。
        调用前需至少已添加一个任务节点。
        """
        if not self._nodes:
            raise GCJPAPIError(
                code="EMPTY_GRAPH",
                message="图中没有任务节点，无法 build()",
                api="build",
                actual="no tasks added",
                expected=">= 1 task node",
                hint="调用 build() 前至少 add_task() 一次；GCJP 段不能为空。",
            )

        self._derive_runtime_config_constraints()

        return BuiltGraph(
            segment_id=self.segment_id,
            assigned_actors=self.assigned_actors,
            graph=self._graph,
            nodes=self._nodes,
            edges=self._edges,
            constraints=self._constraints,
            interface_fulfillments=self._interface_fulfillments,
            resource_states=self._resource_states,
            segment_meta=self._segment_meta,
        )

    def __repr__(self) -> str:
        return (f"TaskGraphBuilder(segment_id={self.segment_id!r}, "
                f"nodes={len(self._nodes)}, edges={len(self._edges)}, "
                f"constraints={len(self._constraints)})")


# ─────────────────────────────────────────────────────────────────────────────
# BuiltGraph —— build() 返回的只读结果对象
# ─────────────────────────────────────────────────────────────────────────────

class BuiltGraph:
    """TaskGraphBuilder.build() 的返回值，供验证管道只读访问"""

    def __init__(
        self,
        segment_id: str,
        assigned_actors: list[str],
        graph: nx.DiGraph,
        nodes: dict[str, TaskNode],
        edges: list[DependencyEdge],
        constraints: list[Constraint],
        interface_fulfillments: list[InterfaceFulfillment],
        resource_states: dict[str, dict],
        segment_meta: Optional[SegmentMeta],
    ):
        self.segment_id = segment_id
        self.assigned_actors = assigned_actors
        self.graph = graph
        self.nodes = nodes
        self.edges = edges
        self.constraints = constraints
        self.interface_fulfillments = interface_fulfillments
        self.contract_fulfillments = interface_fulfillments
        self.resource_states = resource_states
        self.segment_meta = segment_meta

    @property
    def task_ids(self) -> list[str]:
        return list(self.nodes.keys())

    @property
    def actor_set(self) -> set[str]:
        return {n.actor for n in self.nodes.values()}

    def get_tasks_by_actor(self, actor: str) -> list[TaskNode]:
        return [n for n in self.nodes.values() if n.actor == actor]

    def total_ammo_cost(self, actor: str) -> int:
        return sum(n.ammo_cost for n in self.get_tasks_by_actor(actor))

    def total_energy_cost(self, actor: str) -> float:
        return sum(n.energy_cost for n in self.get_tasks_by_actor(actor))

    def __repr__(self) -> str:
        return (f"BuiltGraph(segment_id={self.segment_id!r}, "
                f"nodes={len(self.nodes)}, constraints={len(self.constraints)})")

    # ── 序列化 / 反序列化 ──────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """序列化为 JSON 兼容 dict。"""
        return {
            "version": "1.0",
            "segment_id": self.segment_id,
            "assigned_actors": self.assigned_actors,
            "nodes": {tid: asdict(node) for tid, node in self.nodes.items()},
            "edges": [asdict(e) for e in self.edges],
            "constraints": [asdict(c) for c in self.constraints],
            "interface_fulfillments": [
                asdict(f) for f in self.interface_fulfillments
            ],
            "resource_states": self.resource_states,
            "segment_meta": (
                asdict(self.segment_meta) if self.segment_meta else None
            ),
            "nx_graph": nx.node_link_data(self.graph),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BuiltGraph":
        """从 to_dict() 产出的 dict 重建 BuiltGraph。"""
        nodes = {tid: TaskNode(**d) for tid, d in data["nodes"].items()}
        edges = [DependencyEdge(**e) for e in data["edges"]]
        constraints = [Constraint(**c) for c in data["constraints"]]
        interface_fulfillments = [
            InterfaceFulfillment(**f) for f in data["interface_fulfillments"]
        ]
        segment_meta = (
            SegmentMeta(**data["segment_meta"])
            if data.get("segment_meta") else None
        )
        graph = nx.node_link_graph(data["nx_graph"])

        return cls(
            segment_id=data["segment_id"],
            assigned_actors=data["assigned_actors"],
            graph=graph,
            nodes=nodes,
            edges=edges,
            constraints=constraints,
            interface_fulfillments=interface_fulfillments,
            resource_states=data.get("resource_states", {}),
            segment_meta=segment_meta,
        )

    def save(self, path: str | Path) -> Path:
        """序列化并写入 JSON 文件。"""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return out

    @classmethod
    def load(cls, path: str | Path) -> "BuiltGraph":
        """从 save() 产出的 JSON 文件加载 BuiltGraph。"""
        text = Path(path).read_text(encoding="utf-8")
        return cls.from_dict(json.loads(text))
