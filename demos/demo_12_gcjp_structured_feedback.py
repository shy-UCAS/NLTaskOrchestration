"""
demos/demo_12_gcjp_structured_feedback.py
python -m demos.demo_12_gcjp_structured_feedback

锁定结构化反馈契约：验证 LLM 修复闭环依赖的关键字段确实被填充。

覆盖：
    1. safety_checker.SafetyViolation: code / lineno / source_line / suggestion
       —— 含反向断言：建议文案中不能出现下线 / 不存在的 API（防止
       hardcode 与白名单漂移）。
    2. code_executor.GCJPExecutionResult.api_error: code / actual / expected /
       hint —— 验证 GCJPAPIError.to_dict() 在多种典型错误下落地。
    3. code_executor.GCJPExecutionResult.source_context: 含 `>` 行标记。
    4. INVALID_BUILT_TYPE 分支具备 gcjp_lineno + source_context（P2 修复点）。
    5. VerificationPipeline.verify_gcjp_code() 把上述字段透传到
       VerificationReport.layers[0].details。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gcjp.code_executor import (
    ERROR_EXECUTION_FAILED,
    ERROR_INVALID_BUILT_TYPE,
    execute_gcjp_code,
)
from verifier.pipeline import VerificationPipeline


# ─────────────────────────────────────────────────────────────────────────────
# 测试用例
# ─────────────────────────────────────────────────────────────────────────────

def case_disallowed_builder_method():
    """白名单外 API 调用：DISALLOWED_BUILDER_METHOD + 建议无虚构 API。"""
    code = """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg", assigned_actors=["fleet_1"])
g.add_constraint(constraint_type="time_window", params={}, source_label="x")
built = g.build()
"""
    r = execute_gcjp_code(code)
    assert not r.passed
    assert r.safety is not None

    sv = r.safety.structured_violations
    target = next((v for v in sv if v.code == "DISALLOWED_BUILDER_METHOD"), None)
    assert target is not None, "未找到 DISALLOWED_BUILDER_METHOD 结构化违规"

    assert target.lineno is not None and target.lineno > 0
    assert target.source_line and "add_constraint" in target.source_line
    assert target.suggestion

    # 反向断言：建议文案中不能出现已下线 / 不存在的 API
    assert "set_exit_node" not in target.suggestion, (
        f"建议文案泄漏虚构 API: {target.suggestion}"
    )
    # 真实白名单方法应出现（抽样校验 3 个，覆盖 add_/declare_/build 三大类）
    for method in ("add_task", "build", "declare_interface_fulfillment"):
        assert method in target.suggestion, (
            f"建议文案缺少真实白名单方法 {method}: {target.suggestion}"
        )


def case_illegal_metadata_key():
    """运行时 ValueError 升级为 GCJPAPIError：api_error 结构化。"""
    code = """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg", assigned_actors=["fleet_1"])
g.add_task(
    task_id="t1",
    actor="fleet_1",
    action="standby",
    target="area_A",
    duration_lb=1.0,
    required_capability=[],
    energy_cost=0.5,
    ammo_cost=0,
    metadata={"illegal_key": "bad"},
)
built = g.build()
"""
    r = execute_gcjp_code(code)
    assert r.error_type == ERROR_EXECUTION_FAILED
    assert r.api_error is not None
    assert r.api_error["code"] == "ILLEGAL_METADATA_KEY"
    assert r.api_error["actual"] == ["illegal_key"]
    assert r.api_error["hint"]

    # source_context 应带 `>` 行标记
    assert r.source_context and ">" in r.source_context, r.source_context


def case_actor_not_assigned():
    """ACTOR_NOT_ASSIGNED 应给出 actual / expected.assigned_actors / hint。"""
    code = """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg", assigned_actors=["fleet_1"])
g.add_task(task_id="t1", actor="fleet_3", action="standby", target="area_A",
           duration_lb=1.0, required_capability=[], energy_cost=0.5, ammo_cost=0)
built = g.build()
"""
    r = execute_gcjp_code(code)
    assert r.api_error is not None
    assert r.api_error["code"] == "ACTOR_NOT_ASSIGNED"
    assert r.api_error["actual"] == "fleet_3"
    assert r.api_error["expected"] == {"assigned_actors": ["fleet_1"]}
    assert r.api_error["hint"]


def case_invalid_built_type():
    """INVALID_BUILT_TYPE 在 P2 修复后应带 gcjp_lineno + source_context。"""
    code = """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg", assigned_actors=["fleet_1"])
built = "not_a_graph"
"""
    r = execute_gcjp_code(code)
    assert r.error_type == ERROR_INVALID_BUILT_TYPE
    assert r.gcjp_lineno is not None, "INVALID_BUILT_TYPE 缺 gcjp_lineno"
    assert r.source_context and "built" in r.source_context, (
        f"INVALID_BUILT_TYPE source_context 异常: {r.source_context!r}"
    )


def case_pipeline_passthrough():
    """VerificationPipeline 把 executor 端结构化字段完整透传到 report。"""
    code = """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg", assigned_actors=["fleet_1"])
g.add_task(task_id="t1", actor="fleet_3", action="standby", target="area_A",
           duration_lb=1.0, required_capability=[], energy_cost=0.5, ammo_cost=0)
built = g.build()
"""
    pipeline = VerificationPipeline()
    report = pipeline.verify_gcjp_code(code)
    d = report.to_dict()
    details = d["layers"][0]["details"]

    assert details["api_error"] is not None
    assert details["api_error"]["code"] == "ACTOR_NOT_ASSIGNED"
    assert details["traceback_text"]
    assert details["source_context"]
    assert details["gcjp_lineno"]
    assert isinstance(details["structured_violations"], list)


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────

CASES = [
    ("disallowed_builder_method_no_hallucination", case_disallowed_builder_method),
    ("illegal_metadata_key_structured", case_illegal_metadata_key),
    ("actor_not_assigned_structured", case_actor_not_assigned),
    ("invalid_built_type_has_source", case_invalid_built_type),
    ("pipeline_passthrough", case_pipeline_passthrough),
]


def main() -> bool:
    passed = 0
    for name, fn in CASES:
        try:
            fn()
            print(f"  [通过] {name}")
            passed += 1
        except AssertionError as exc:
            print(f"  [失败] {name}: {exc!r}")
    print(f"\nDemo 12 结果：{passed}/{len(CASES)} 结构化反馈契约检查通过")
    return passed == len(CASES)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
