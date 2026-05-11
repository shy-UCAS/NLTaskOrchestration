"""
gcjp/constraint_templates.py
Z3 约束模板注册器
将 TaskGraphBuilder 中注册的逻辑约束转化为 Z3 表达式
"""
from __future__ import annotations

from typing import Optional
from z3 import (
    Solver, ArithRef, BoolRef,
    Real, Int, Bool,
    And, Or, Not, Abs, If,
    sat, unsat, unknown,
)

from gcjp.mission_graph import BuiltGraph, Constraint, TaskNode


class Z3ConstraintBuilder:
    """
    将 BuiltGraph 中的约束列表转化为 Z3 求解器表达式。
    支持 assert_and_track 以实现 unsat core 归因。
    """

    def __init__(self, graph: BuiltGraph, use_tracking: bool = True):
        self.graph = graph
        self.use_tracking = use_tracking
        self.solver = Solver()

        # Z3 变量：每个任务的 start / end / duration
        self.start: dict[str, ArithRef] = {}
        self.end: dict[str, ArithRef] = {}
        self.duration: dict[str, ArithRef] = {}

        # 追踪布尔变量（用于 unsat core）
        self.track_vars: dict[str, BoolRef] = {}

        self._init_variables()

    # ─────────────────────────────────────────────────────────────────────────
    # 变量初始化
    # ─────────────────────────────────────────────────────────────────────────

    def _init_variables(self):
        """为每个任务节点创建 start / end / duration Z3 变量"""
        print("\n" + "-" * 60)
        print("[DEBUG][Z3] 初始化 Z3 变量")
        print("-" * 60)
        for tid in self.graph.task_ids:
            self.start[tid] = Real(f"start_{tid}")
            self.end[tid] = Real(f"end_{tid}")
            self.duration[tid] = Real(f"dur_{tid}")

            node = self.graph.nodes[tid]
            print(f"  变量: start_{tid}, end_{tid}, dur_{tid}  "
                  f"(dur_lb={node.duration_lb}, dur_ub={node.duration_ub})")

            # 基础约束：start >= 0, duration >= lb, end = start + duration
            self._assert(
                self.start[tid] >= 0,
                label=f"start_nonneg_{tid}"
            )
            self._assert(
                self.duration[tid] >= node.duration_lb,
                label=f"dur_lb_{tid}"
            )
            self._assert(
                self.end[tid] == self.start[tid] + self.duration[tid],
                label=f"end_def_{tid}"
            )
            if node.duration_ub is not None:
                self._assert(
                    self.duration[tid] <= node.duration_ub,
                    label=f"dur_ub_{tid}"
                )

    def _assert(self, expr: BoolRef, label: str):
        """添加约束，如果开启追踪则使用 assert_and_track"""
        if self.use_tracking:
            track_var = Bool(f"track_{label}")
            self.track_vars[label] = track_var
            self.solver.assert_and_track(expr, track_var)
        else:
            self.solver.add(expr)

    # ─────────────────────────────────────────────────────────────────────────
    # 约束类型处理器
    # ─────────────────────────────────────────────────────────────────────────

    def build_all(self):
        """将 graph.constraints 中的所有约束转化为 Z3 表达式"""
        print(f"\n[DEBUG][Z3] build_all — 共 {len(self.graph.constraints)} 条约束待转化")
        for i, c in enumerate(self.graph.constraints):
            print(f"  [{i+1:3d}] 分发约束: type={c.constraint_type:25s} "
                  f"label={c.source_label}")
            self._dispatch(c)
        print(f"[DEBUG][Z3] build_all 完成 — solver 中共 {len(self.track_vars)} 条追踪约束")
        return self

    def _dispatch(self, c: Constraint):
        """根据约束类型分发到对应处理器"""
        handlers = {
            "time_order":           self._handle_time_order,
            "duration":             self._handle_duration,
            "time_window":          self._handle_time_window,
            "sync":                 self._handle_sync,
            "resource":             self._handle_resource,
            "capability":           self._handle_capability,
            "physical_feasibility": self._handle_physical_feasibility,
        }
        handler = handlers.get(c.constraint_type)
        if handler:
            handler(c)
        else:
            raise ValueError(f"未知约束类型: {c.constraint_type}")

    def _handle_time_order(self, c: Constraint):
        """sequence 约束: end_before <= start_after"""
        before = c.params["before"]
        after = c.params["after"]
        if before not in self.end or after not in self.start:
            raise ValueError(f"time_order: 任务 '{before}' 或 '{after}' 未在图中")
        expr = self.end[before] <= self.start[after]
        print(f"        Z3 公式: {expr}")
        self._assert(expr, label=c.source_label)

    def _handle_duration(self, c: Constraint):
        """duration 约束（已在 _init_variables 中处理，此处为显式追加）"""
        tid = c.params.get("task_id")
        lb = c.params.get("lb")
        ub = c.params.get("ub")
        if tid and lb is not None:
            self._assert(self.duration[tid] >= lb, label=c.source_label + "_lb")
        if tid and ub is not None:
            self._assert(self.duration[tid] <= ub, label=c.source_label + "_ub")

    def _handle_time_window(self, c: Constraint):
        """time_window 约束: earliest <= start <= latest"""
        tid = c.params["task_id"]
        earliest = c.params.get("earliest")
        latest = c.params.get("latest")
        deadline = c.params.get("deadline")

        if earliest is not None:
            expr = self.start[tid] >= earliest
            print(f"        Z3 公式 (earliest): {expr}")
            self._assert(expr, label=f"{c.source_label}_earliest")
        if latest is not None:
            expr = self.start[tid] <= latest
            print(f"        Z3 公式 (latest): {expr}")
            self._assert(expr, label=f"{c.source_label}_latest")
        if deadline is not None:
            expr = self.end[tid] <= deadline
            print(f"        Z3 公式 (deadline): {expr}")
            self._assert(expr, label=f"{c.source_label}_deadline")

    def _handle_sync(self, c: Constraint):
        """sync 约束: |start_i - start_j| <= tolerance"""
        ti = c.params["task_i"]
        tj = c.params["task_j"]
        tol = c.params.get("tolerance", 1.0)
        diff = self.start[ti] - self.start[tj]
        expr = And(diff <= tol, diff >= -tol)
        print(f"        Z3 公式 (sync): {expr}")
        self._assert(expr, label=c.source_label)

    def _handle_resource(self, c: Constraint):
        """
        resource 约束: sum(cost_i for actor's tasks in segment) <= max_value
        注意：此处只验证本段内的资源消耗不超过预算
        """
        actor = c.params["actor"]
        resource_type = c.params["resource_type"]
        max_value = c.params["max_value"]
        cost_key = c.params.get("cost_key", "energy_cost")

        actor_tasks = self.graph.get_tasks_by_actor(actor)
        if not actor_tasks:
            print(f"        资源检查: actor={actor} 无任务，跳过")
            return

        total_cost = sum(
            getattr(t, cost_key) for t in actor_tasks
            if getattr(t, cost_key, 0) is not None
        )

        print(f"        资源检查: actor={actor}, {resource_type}: "
              f"total_cost={total_cost} vs max={max_value} → "
              f"{'超限!' if total_cost > max_value else '通过'}")

        # 资源约束是确定性的（不涉及时间变量），直接用 Python 检查
        if total_cost > max_value:
            # 仍注册为 Z3 追踪约束以便归因
            from z3 import BoolVal
            self._assert(
                BoolVal(False),  # 明确不可满足
                label=c.source_label,
            )
        # else: 通过，无需额外 Z3 断言

    def _handle_capability(self, c: Constraint):
        """
        capability 约束：由 Python 层面检查，结果以 BoolVal 写入 Z3
        （能力匹配是离散的，Z3 不需要处理连续变量）
        """
        from z3 import BoolVal
        task_id = c.params.get("task_id")
        required = set(c.params.get("required", []))
        actor_caps = set(c.params.get("actor_capabilities", []))
        satisfied = required.issubset(actor_caps)
        self._assert(BoolVal(satisfied), label=c.source_label)

    def _handle_physical_feasibility(self, c: Constraint):
        """physical_feasibility 约束: duration >= min_duration_units"""
        tid = c.params["task_id"]
        min_dur = c.params["min_duration_units"]
        expr = self.duration[tid] >= min_dur
        print(f"        Z3 公式 (phys): {expr}  "
              f"(distance={c.params.get('distance_km')}km, "
              f"speed={c.params.get('speed_kmh')}km/h)")
        self._assert(expr, label=c.source_label)

    # ─────────────────────────────────────────────────────────────────────────
    # 求解
    # ─────────────────────────────────────────────────────────────────────────

    def solve(self, timeout_ms: int = 10_000) -> dict:
        """
        运行 Z3 求解器。

        返回:
            {
              "result": "sat" | "unsat" | "unknown",
              "schedule": {task_id: {"start": float, "end": float, "duration": float}, ...},
              "unsat_core": [label, ...],   # 仅 result=="unsat" 时有值
              "error": str | None,
            }
        """
        self.solver.set("timeout", timeout_ms)

        print(f"\n[DEBUG][Z3] === 开始求解 (timeout={timeout_ms}ms) ===")
        print(f"  追踪变量数: {len(self.track_vars)}")
        print(f"  Solver 断言列表:")
        for i, a in enumerate(self.solver.assertions()):
            print(f"    [{i+1:3d}] {a}")

        result = self.solver.check()
        print(f"\n[DEBUG][Z3] 求解结果: {result}")

        if result == sat:
            model = self.solver.model()
            schedule = {}
            for tid in self.graph.task_ids:
                def _val(v):
                    try:
                        return float(model[v].as_decimal(6).rstrip("?"))
                    except Exception:
                        return None
                schedule[tid] = {
                    "start":    _val(self.start[tid]),
                    "end":      _val(self.end[tid]),
                    "duration": _val(self.duration[tid]),
                }

            print(f"\n[DEBUG][Z3] SAT — 可行调度方案:")
            for tid, sched in schedule.items():
                print(f"  {tid:30s} | start={sched['start']:8.2f} | "
                      f"end={sched['end']:8.2f} | dur={sched['duration']:8.2f}")

            return {"result": "sat", "schedule": schedule,
                    "unsat_core": [], "error": None}

        elif result == unsat:
            core_labels = []
            if self.use_tracking:
                core = self.solver.unsat_core()
                # 反查 Bool 变量 → label
                rev_map = {str(v): label for label, v in self.track_vars.items()}
                core_labels = [rev_map.get(str(b), str(b)) for b in core]

            print(f"\n[DEBUG][Z3] UNSAT — 不可满足的约束核心 ({len(core_labels)} 条):")
            for label in core_labels:
                print(f"  - {label}")

            return {"result": "unsat", "schedule": {},
                    "unsat_core": core_labels, "error": None}

        else:  # unknown（超时等）
            print(f"\n[DEBUG][Z3] UNKNOWN — 求解超时或未知错误")
            return {"result": "unknown", "schedule": {},
                    "unsat_core": [], "error": "Z3 求解超时或未知错误"}
