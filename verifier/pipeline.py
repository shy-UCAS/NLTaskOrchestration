"""
verifier/pipeline.py
四层递进验证管道
Layer 1: 代码执行验证（语法 + 沙箱运行）
Layer 2: 图结构验证（DAG 合法性 + 连通性 + 关键路径）
Layer 3: Z3 约束验证（时序 + 资源 + 物理可行性）
Layer 4: 语义反向校验（预留接口）
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import networkx as nx

from gcjp.debug_logger import debug
from gcjp.code_executor import execute_gcjp_code
from gcjp.mission_graph import BuiltGraph
from gcjp.constraint_templates import Z3ConstraintBuilder
from gcjp.safety_checker import check_gcjp_code


# ─────────────────────────────────────────────────────────────────────────────
# 验证报告数据类
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LayerResult:
    layer: int
    name: str
    passed: bool
    details: dict = field(default_factory=dict)
    error_msg: Optional[str] = None
    elapsed_ms: float = 0.0

    def summary_line(self) -> str:
        icon = "[PASS]" if self.passed else "[FAIL]"
        return f"{icon} Layer {self.layer} [{self.name}]: {'Pass' if self.passed else 'Fail'}"


@dataclass
class VerificationReport:
    segment_id: str
    overall_passed: bool
    layers: list[LayerResult] = field(default_factory=list)
    schedule: dict = field(default_factory=dict)      # Z3 求出的时间调度
    unsat_core: list[str] = field(default_factory=list)
    unsat_core_raw: list[str] = field(default_factory=list)
    unsat_core_semantic: list[str] = field(default_factory=list)
    unsat_core_framework: list[str] = field(default_factory=list)
    attribution: list[str] = field(default_factory=list)  # unsat core 归因
    total_elapsed_ms: float = 0.0

    def print_report(self):
        print(f"\n{'='*60}")
        print(f"验证报告 — 段: {self.segment_id}")
        print(f"{'='*60}")
        print(f"总体结果: {'PASS' if self.overall_passed else 'FAIL'}")
        print(f"总耗时: {self.total_elapsed_ms:.1f} ms\n")
        for lr in self.layers:
            print(lr.summary_line())
            if not lr.passed and lr.error_msg:
                print(f"   ->{lr.error_msg}")
            if lr.details:
                for k, v in lr.details.items():
                    print(f"   {k}: {v}")
        if self.unsat_core:
            print(f"\nUNSAT Core 语义约束标签:")
            for label in self.unsat_core:
                print(f"  - {label}")
        if self.unsat_core_framework:
            print(f"\nUNSAT Core 框架约束标签（调试用）:")
            for label in self.unsat_core_framework:
                print(f"  - {label}")
        if self.attribution:
            print(f"\n归因分析:")
            for a in self.attribution:
                print(f"  → {a}")
        print(f"{'='*60}\n")

    def to_dict(self) -> dict:
        return {
            "segment_id": self.segment_id,
            "overall_passed": self.overall_passed,
            "layers": [
                {
                    "layer": lr.layer, "name": lr.name, "passed": lr.passed,
                    "details": lr.details, "error_msg": lr.error_msg,
                    "elapsed_ms": lr.elapsed_ms,
                }
                for lr in self.layers
            ],
            "schedule": self.schedule,
            "unsat_core": self.unsat_core,
            "unsat_core_raw": self.unsat_core_raw,
            "unsat_core_semantic": self.unsat_core_semantic,
            "unsat_core_framework": self.unsat_core_framework,
            "attribution": self.attribution,
            "total_elapsed_ms": self.total_elapsed_ms,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 各层验证器
# ─────────────────────────────────────────────────────────────────────────────

class Layer1CodeVerifier:
    """
    第一层：代码执行验证
    - 安全检查（API 白名单）
    - subprocess 沙箱执行
    - 语法错误 / 运行时错误捕获
    """

    TIMEOUT_SECONDS = 10

    def verify(self, code: str) -> LayerResult:
        t0 = time.time()

        # 安全检查
        safety = check_gcjp_code(code)
        if not safety.passed:
            return LayerResult(
                layer=1, name="代码执行验证", passed=False,
                error_msg="API 白名单校验失败:\n" + "\n".join(safety.violations),
                details={"violations": safety.violations},
                elapsed_ms=(time.time() - t0) * 1000,
            )

        # 在子进程中执行（沙箱）
        # 注入 sys.path 保证能 import gcjp
        sandbox_code = textwrap.dedent(f"""\
            import sys, os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            {code}
            # 验证 build() 是否被调用
            # 由调用者通过 BuiltGraph 对象验证
        """)

        with tempfile.NamedTemporaryFile(suffix=".py", mode="w",
                                         delete=False, encoding="utf-8") as f:
            f.write(sandbox_code)
            tmpfile = f.name

        try:
            result = subprocess.run(
                [sys.executable, tmpfile],
                capture_output=True, text=True,
                timeout=self.TIMEOUT_SECONDS,
            )
            elapsed = (time.time() - t0) * 1000
            if result.returncode != 0:
                return LayerResult(
                    layer=1, name="代码执行验证", passed=False,
                    error_msg=f"运行时错误:\n{result.stderr}",
                    details={"returncode": result.returncode, "stderr": result.stderr},
                    elapsed_ms=elapsed,
                )
            return LayerResult(
                layer=1, name="代码执行验证", passed=True,
                details={"warnings": safety.warnings},
                elapsed_ms=elapsed,
            )
        except subprocess.TimeoutExpired:
            return LayerResult(
                layer=1, name="代码执行验证", passed=False,
                error_msg=f"执行超时（>{self.TIMEOUT_SECONDS}s）",
                elapsed_ms=(time.time() - t0) * 1000,
            )
        finally:
            import os
            try:
                os.unlink(tmpfile)
            except Exception:
                pass


class Layer2GraphVerifier:
    """
    第二层：图结构验证
    - DAG 合法性（无环）
    - 连通性（无孤立节点）
    - 节点覆盖（所有 actor 的任务都在图中）
    - 关键路径计算
    """

    def verify(self, graph: BuiltGraph) -> LayerResult:
        t0 = time.time()
        g = graph.graph
        issues = []

        debug.log_banner("[DEBUG][Layer2] 图结构验证 — 开始", char="=")
        debug.log(f"  节点列表 ({len(g.nodes)}):")
        for n in g.nodes:
            node = graph.nodes[n]
            in_d = g.in_degree(n)
            out_d = g.out_degree(n)
            preds = list(g.predecessors(n))
            succs = list(g.successors(n))
            debug.log(f"    {n:30s} | in_degree={in_d} | out_degree={out_d} | "
                      f"preds={preds} | succs={succs}")
        debug.log(f"\n  边列表 ({len(g.edges)}):")
        for u, v, data in g.edges(data=True):
            debug.log(f"    {u:30s} -- {v:30s} | {data}")

        # 1. DAG 合法性
        is_dag = nx.is_directed_acyclic_graph(g)
        debug.log(f"\n  DAG 合法性: {'是' if is_dag else '否 (存在环路!)'}")
        if not is_dag:
            cycles = list(nx.simple_cycles(g))
            issues.append(f"图中存在环路: {cycles}")

        # 2. 孤立节点检测（无入边且无出边的节点，除非图只有1个节点）
        # sync / group_sync 是多任务关系约束，不写入二元图边，但应避免被误判为孤立。
        if len(g.nodes) > 1:
            sync_constraint_linked = {
                tid
                for c in graph.constraints
                if c.constraint_type in {"sync", "group_sync"}
                for tid in c.applies_to
            }
            isolated = [n for n in g.nodes
                        if (g.in_degree(n) == 0
                            and g.out_degree(n) == 0
                            and n not in sync_constraint_linked)]
            if isolated:
                issues.append(f"存在孤立节点（无依赖边或同步约束）: {isolated}")

        # 3. 节点覆盖：检查所有任务是否都在图中
        missing = set(graph.task_ids) - set(g.nodes)
        if missing:
            issues.append(f"任务节点未加入图中: {missing}")

        # 4. 关键路径（以节点 duration_lb 为权重的最长路径）
        critical_path = []
        critical_path_len = 0.0
        if nx.is_directed_acyclic_graph(g) and len(g.nodes) > 0:
            try:
                for u, v in g.edges():
                    g.edges[u, v]["_node_weight"] = graph.nodes[u].duration_lb
                cp = nx.dag_longest_path(g, weight="_node_weight")
                cp_edge_len = nx.dag_longest_path_length(g, weight="_node_weight")
                if cp:
                    cp_edge_len += graph.nodes[cp[-1]].duration_lb
                critical_path = cp
                critical_path_len = cp_edge_len
            except Exception as e:
                issues.append(f"关键路径计算失败: {e}")

        debug.log(f"\n  关键路径: {' -> '.join(critical_path) if critical_path else '(无)'}")
        debug.log(f"  关键路径长度 (duration_lb 之和): {critical_path_len:.1f}")

        # 拓扑排序
        if nx.is_directed_acyclic_graph(g):
            topo_order = list(nx.topological_sort(g))
            debug.log(f"  拓扑排序: {topo_order}")

        elapsed = (time.time() - t0) * 1000
        passed = len(issues) == 0
        debug.log(f"\n[DEBUG][Layer2] 图结构验证 — {'通过' if passed else '失败'} ({elapsed:.1f}ms)")
        if issues:
            for iss in issues:
                debug.log(f"  问题: {iss}")

        return LayerResult(
            layer=2, name="图结构验证", passed=passed,
            error_msg=("\n".join(issues) if issues else None),
            details={
                "node_count": len(g.nodes),
                "edge_count": len(g.edges),
                "is_dag": nx.is_directed_acyclic_graph(g),
                "critical_path": critical_path,
                "critical_path_length": critical_path_len,
                "issues": issues,
            },
            elapsed_ms=elapsed,
        )


class Layer3Z3Verifier:
    """
    第三层：Z3 约束验证
    - 构建 Z3 约束并求解
    - SAT: 提取调度方案
    - UNSAT: 提取 unsat core 并归因
    """

    def __init__(self, timeout_ms: int = 10_000):
        self.timeout_ms = timeout_ms

    def verify(self, graph: BuiltGraph) -> tuple[LayerResult, dict, list[str]]:
        """
        返回: (LayerResult, schedule_dict, unsat_core_labels)
        """
        debug.log_banner("[DEBUG][Layer3] Z3 约束验证 — 开始", char="=")
        debug.log(f"  图中约束数: {len(graph.constraints)}")
        debug.log(f"  timeout: {self.timeout_ms}ms")

        t0 = time.time()
        try:
            builder = Z3ConstraintBuilder(graph, use_tracking=True)
            builder.build_all()
            result = builder.solve(timeout_ms=self.timeout_ms)
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            lr = LayerResult(
                layer=3, name="Z3 约束验证", passed=False,
                error_msg=f"Z3 构建/求解异常: {e}",
                elapsed_ms=elapsed,
            )
            return lr, {}, []

        elapsed = (time.time() - t0) * 1000
        res_str = result["result"]

        if res_str == "sat":
            lr = LayerResult(
                layer=3, name="Z3 约束验证", passed=True,
                details={
                    "z3_result": "sat",
                    "tasks_scheduled": len(result["schedule"]),
                },
                elapsed_ms=elapsed,
            )
            return lr, result["schedule"], []

        elif res_str == "unsat":
            core_semantic = result.get("unsat_core_semantic",
                                       result.get("unsat_core", []))
            core_raw = result.get("unsat_core_raw", core_semantic)
            core_framework = result.get("unsat_core_framework", [])
            attribution = result.get("attribution")
            if attribution is None:
                attribution = _attribute_unsat_core(core_semantic, graph)
            lr = LayerResult(
                layer=3, name="Z3 约束验证", passed=False,
                error_msg=(
                    "约束不可满足（UNSAT），"
                    f"{len(core_semantic)} 条语义冲突约束"
                ),
                details={
                    "z3_result": "unsat",
                    "unsat_core": core_semantic,
                    "unsat_core_raw": core_raw,
                    "unsat_core_semantic": core_semantic,
                    "unsat_core_framework": core_framework,
                    "attribution": attribution,
                },
                elapsed_ms=elapsed,
            )
            return lr, {}, core_semantic

        else:  # unknown
            lr = LayerResult(
                layer=3, name="Z3 约束验证", passed=False,
                error_msg=result.get("error", "Z3 未知错误"),
                details={"z3_result": "unknown"},
                elapsed_ms=elapsed,
            )
            return lr, {}, []


class Layer4SemanticVerifier:
    """第四层：语义反向校验（预留接口，暂不实现）"""

    def verify(self, graph: BuiltGraph, schedule: dict) -> LayerResult:
        return LayerResult(
            layer=4, name="语义反向校验", passed=True,
            details={"status": "预留接口，暂未实现"},
            elapsed_ms=0.0,
        )


# ─────────────────────────────────────────────────────────────────────────────
# unsat core 归因
# ─────────────────────────────────────────────────────────────────────────────

def _attribute_unsat_core(core_labels: list[str], graph: BuiltGraph) -> list[str]:
    """将 unsat core 中的约束标签翻译为人类可读的归因说明"""
    attribution = []
    for label in core_labels:
        if label.startswith("group_sync_pair_"):
            matched = False
            for c in graph.constraints:
                if c.constraint_type != "group_sync":
                    continue
                prefix = f"group_sync_pair_{c.source_label}_"
                if not label.startswith(prefix):
                    continue
                rest = label[len(prefix):]
                for mode in ("start", "end"):
                    mode_prefix = f"{mode}_"
                    if rest.startswith(mode_prefix):
                        pair = rest[len(mode_prefix):].split("__")
                        if len(pair) == 2:
                            when = "开始时间" if mode == "start" else "结束时间"
                            attribution.append(
                                f"组同步冲突: 任务组 {c.params.get('task_ids')} 中 "
                                f"'{pair[0]}' 与 '{pair[1]}' 的{when}无法满足 "
                                f"tolerance={c.params.get('tolerance')}"
                            )
                            matched = True
                        break
                break
            if not matched:
                attribution.append(f"组同步冲突: {label}")
        elif label.startswith("seq_"):
            parts = label[4:].split("__")
            if len(parts) == 2:
                attribution.append(
                    f"顺序冲突: 任务 '{parts[0]}' 必须在 '{parts[1]}' 之前完成"
                )
        elif label.startswith("sync_"):
            parts = label[5:].split("__")
            if len(parts) == 2:
                attribution.append(
                    f"同步冲突: 任务 '{parts[0]}' 与 '{parts[1]}' 无法在同步时间窗内同时开始"
                )
        elif "resource" in label:
            attribution.append(f"资源超限: {label}")
        elif "phys_feasibility" in label:
            parts = label.split("_")
            tid = "_".join(parts[2:]) if len(parts) > 2 else label
            attribution.append(
                f"物理不可行: 任务 '{tid}' 的时间预算不足以完成飞行距离"
            )
        elif "dur_lb" in label:
            tid = label.replace("dur_lb_", "")
            attribution.append(
                f"时间预算不足: 任务 '{tid}' 的分配时间小于最短执行时间"
            )
        elif "time_window" in label:
            attribution.append(f"时间窗冲突: {label}")
        else:
            attribution.append(f"约束冲突: {label}")
    return attribution


# ─────────────────────────────────────────────────────────────────────────────
# 统一入口
# ─────────────────────────────────────────────────────────────────────────────

class VerificationPipeline:
    """
    四层验证管道统一入口。

    用法:
        pipeline = VerificationPipeline()
        report = pipeline.verify_graph(built_graph)
        report.print_report()

        # 如果只有 GCJP 代码字符串（LLM 生成的情况）：
        report = pipeline.verify_gcjp_code(code_str)
    """

    def __init__(self, z3_timeout_ms: int = 10_000):
        self.l1 = Layer1CodeVerifier()
        self.l2 = Layer2GraphVerifier()
        self.l3 = Layer3Z3Verifier(timeout_ms=z3_timeout_ms)
        self.l4 = Layer4SemanticVerifier()

    def verify_graph(self, graph: BuiltGraph) -> VerificationReport:
        """
        对已构建的 BuiltGraph 对象运行 Layer 2-4 验证。
        （Layer 1 代码验证需要原始代码字符串，此方法跳过）
        """
        debug.log_banner("[DEBUG] VerificationPipeline.verify_graph — 开始验证", char="#")
        debug.log(f"  segment_id: {graph.segment_id}")
        debug.log(f"  节点数: {len(graph.nodes)}, 边数: {len(graph.edges)}, "
                  f"约束数: {len(graph.constraints)}")

        t0 = time.time()
        layers: list[LayerResult] = []

        # Layer 2
        debug.log(f"\n{'>'*20} 进入 Layer 2: 图结构验证 {'<'*20}")
        l2_result = self.l2.verify(graph)
        layers.append(l2_result)
        if not l2_result.passed:
            debug.log(f"[DEBUG] Layer 2 失败，终止后续验证")
            return VerificationReport(
                segment_id=graph.segment_id,
                overall_passed=False,
                layers=layers,
                total_elapsed_ms=(time.time() - t0) * 1000,
            )

        # Layer 3
        debug.log(f"\n{'>'*20} 进入 Layer 3: Z3 约束验证 {'<'*20}")
        l3_result, schedule, unsat_core = self.l3.verify(graph)
        layers.append(l3_result)
        details = l3_result.details
        attribution = details.get("attribution", [])
        if not l3_result.passed:
            debug.log(f"[DEBUG] Layer 3 失败，终止后续验证")
            return VerificationReport(
                segment_id=graph.segment_id,
                overall_passed=False,
                layers=layers,
                schedule={},
                unsat_core=details.get("unsat_core_semantic", unsat_core),
                unsat_core_raw=details.get("unsat_core_raw", unsat_core),
                unsat_core_semantic=details.get(
                    "unsat_core_semantic", unsat_core
                ),
                unsat_core_framework=details.get("unsat_core_framework", []),
                attribution=attribution,
                total_elapsed_ms=(time.time() - t0) * 1000,
            )

        # Layer 4
        debug.log(f"\n{'>'*20} 进入 Layer 4: 语义反向校验 {'<'*20}")
        l4_result = self.l4.verify(graph, schedule)
        layers.append(l4_result)

        debug.log(f"\n[DEBUG] VerificationPipeline — 全部验证完成")
        layer_status = [
            f"L{lr.layer}:{'Pass' if lr.passed else 'Fail'}"
            for lr in layers
        ]
        debug.log(f"  各层结果: {layer_status}")

        return VerificationReport(
            segment_id=graph.segment_id,
            overall_passed=all(lr.passed for lr in layers),
            layers=layers,
            schedule=schedule,
            unsat_core=[],
            unsat_core_raw=[],
            unsat_core_semantic=[],
            unsat_core_framework=[],
            attribution=[],
            total_elapsed_ms=(time.time() - t0) * 1000,
        )

    def verify_gcjp_code(
        self,
        code: str,
        *,
        action_defaults: dict[str, dict[str, Any]] | None = None,
        capability_model: dict[str, dict[str, Any]] | None = None,
    ) -> VerificationReport:
        """
        对 GCJP 代码字符串运行完整验证流程：
        L1 安全检查与受限执行 -> 提取 BuiltGraph -> L2/L3/L4 验证。
        """
        t0 = time.time()
        exec_result = execute_gcjp_code(
            code,
            action_defaults=action_defaults,
            capability_model=capability_model,
        )
        elapsed = (time.time() - t0) * 1000

        structured_violations = (
            [asdict(v) for v in exec_result.safety.structured_violations]
            if exec_result.safety else []
        )
        l1_result = LayerResult(
            layer=1,
            name="GCJP代码执行验证",
            passed=exec_result.passed,
            details={
                "error_type": exec_result.error_type,
                "warnings": exec_result.safety.warnings if exec_result.safety else [],
                "violations": exec_result.safety.violations if exec_result.safety else [],
                "structured_violations": structured_violations,
                "gcjp_lineno": exec_result.gcjp_lineno,
                "source_context": exec_result.source_context,
                "traceback_text": exec_result.traceback_text,
                "api_error": exec_result.api_error,
            },
            error_msg=exec_result.error_msg,
            elapsed_ms=elapsed,
        )

        if not exec_result.passed or exec_result.graph is None:
            return VerificationReport(
                segment_id="unknown",
                overall_passed=False,
                layers=[l1_result],
                total_elapsed_ms=(time.time() - t0) * 1000,
            )

        rest = self.verify_graph(exec_result.graph)
        rest.layers = [l1_result] + rest.layers
        rest.total_elapsed_ms = (time.time() - t0) * 1000
        rest.overall_passed = l1_result.passed and rest.overall_passed
        return rest

    def verify_code(self, code: str, graph: BuiltGraph) -> VerificationReport:
        """
        兼容旧接口：对代码做 L1 检查，对外部传入 graph 做 L2-L4。

        注意：此路径走 Layer1CodeVerifier（subprocess 沙箱），仅产出
        旧式字符串错误，**不会填充 structured_violations / api_error /
        source_context / traceback_text 等结构化反馈字段**。如需 LLM
        修复闭环所依赖的结构化诊断，请改用 verify_gcjp_code(code)。
        """
        t0 = time.time()
        layers: list[LayerResult] = []

        # Layer 1
        l1_result = self.l1.verify(code)
        layers.append(l1_result)
        if not l1_result.passed:
            return VerificationReport(
                segment_id=graph.segment_id,
                overall_passed=False,
                layers=layers,
                total_elapsed_ms=(time.time() - t0) * 1000,
            )

        # Layer 2-4
        rest = self.verify_graph(graph)
        rest.layers = layers + rest.layers
        rest.total_elapsed_ms = (time.time() - t0) * 1000
        return rest
