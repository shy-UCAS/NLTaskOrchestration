"""
demos/demo_07_gcjp_code_executor_failures.py
python -m demos.demo_07_gcjp_code_executor_failures

GCJP 代码执行器失败诊断全覆盖测试。

场景：
  构造 14 种失败 GCJP 代码样本，覆盖 6 种 error_type 及所有禁止语法模式，
  验证 execute_gcjp_code() 对每种失败场景都能给出精确的诊断结果。

覆盖的失败类型：
  ✓ SAFETY_CHECK_FAILED：直接调用 add_constraint、非法 import os、
                          非法 import Z3 模板、语法错误、while 循环、
                          for 循环、try 块、lambda、调用不存在的 API
  ✓ COMPILE_FAILED：（由语法错误覆盖）
  ✓ EXECUTION_FAILED：metadata 字段非法、重复 task_id、依赖缺失节点、空图 build
  ✓ MISSING_BUILT：代码中缺少 built = g.build()
  ✓ INVALID_BUILT_TYPE：built 变量类型不是 BuiltGraph

预期结果：
  所有 14 个 case 的 error_type 与预期一致，pipeline 透传结果一致。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gcjp.code_executor import (
    ERROR_EXECUTION_FAILED,
    ERROR_INVALID_BUILT_TYPE,
    ERROR_MISSING_BUILT,
    ERROR_SAFETY_CHECK_FAILED,
    execute_gcjp_code,
)
from verifier.pipeline import VerificationPipeline


CASES = [
    {
        "name": "direct_add_constraint",
        "code": """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_bad", assigned_actors=["fleet_1"])
g.add_constraint(
    constraint_type="time_window",
    params={"task_id": "t1", "deadline": 10.0},
    source_label="bad_direct_constraint",
)
built = g.build()
""",
        "expected_error_type": ERROR_SAFETY_CHECK_FAILED,
    },
    {
        "name": "illegal_import_os",
        "code": """
import os
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_bad", assigned_actors=["fleet_1"])
built = g.build()
""",
        "expected_error_type": ERROR_SAFETY_CHECK_FAILED,
    },
    {
        "name": "illegal_z3_import",
        "code": """
from gcjp.constraint_templates import Z3ConstraintBuilder
""",
        "expected_error_type": ERROR_SAFETY_CHECK_FAILED,
    },
    {
        "name": "missing_built",
        "code": """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_no_built", assigned_actors=["fleet_1"])
g.add_task(
    task_id="t1",
    actor="fleet_1",
    action="standby",
    target="area_A",
    duration_lb=1.0,
    required_capability=[],
    energy_cost=0.5,
    ammo_cost=0,
)
""",
        "expected_error_type": ERROR_MISSING_BUILT,
    },
    {
        "name": "syntax_error",
        "code": """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_syntax", assigned_actors=["fleet_1"])
g.add_task(
""",
        "expected_error_type": ERROR_SAFETY_CHECK_FAILED,
    },
    {
        "name": "invalid_built_type",
        "code": """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_bad_type", assigned_actors=["fleet_1"])
built = "not_a_graph"
""",
        "expected_error_type": ERROR_INVALID_BUILT_TYPE,
    },
    {
        "name": "metadata_illegal_key",
        "code": """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_bad_metadata", assigned_actors=["fleet_1"])
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
""",
        "expected_error_type": ERROR_EXECUTION_FAILED,
    },
    {
        "name": "while_loop",
        "code": """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_loop", assigned_actors=["fleet_1"])
while True:
    pass
built = g.build()
""",
        "expected_error_type": ERROR_SAFETY_CHECK_FAILED,
    },
    {
        "name": "for_loop",
        "code": """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_for", assigned_actors=["fleet_1"])
for i in range(2):
    pass
built = g.build()
""",
        "expected_error_type": ERROR_SAFETY_CHECK_FAILED,
    },
    {
        "name": "try_block",
        "code": """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_try", assigned_actors=["fleet_1"])
try:
    built = g.build()
except Exception:
    built = "bad"
""",
        "expected_error_type": ERROR_SAFETY_CHECK_FAILED,
    },
    {
        "name": "lambda_expr",
        "code": """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_lambda", assigned_actors=["fleet_1"])
f = lambda x: x
built = g.build()
""",
        "expected_error_type": ERROR_SAFETY_CHECK_FAILED,
    },
    {
        "name": "nonexistent_api",
        "code": """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_bad_api", assigned_actors=["fleet_1"])
g.add_nonexistent_api()
built = g.build()
""",
        "expected_error_type": ERROR_SAFETY_CHECK_FAILED,
    },
    {
        "name": "duplicate_task_id",
        "code": """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_duplicate_task", assigned_actors=["fleet_1"])
g.add_task(
    task_id="t1",
    actor="fleet_1",
    action="standby",
    target="area_A",
    duration_lb=1.0,
    required_capability=[],
    energy_cost=0.5,
    ammo_cost=0,
)
g.add_task(
    task_id="t1",
    actor="fleet_1",
    action="standby",
    target="area_B",
    duration_lb=1.0,
    required_capability=[],
    energy_cost=0.5,
    ammo_cost=0,
)
built = g.build()
""",
        "expected_error_type": ERROR_EXECUTION_FAILED,
    },
    {
        "name": "missing_dependency_node",
        "code": """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_missing_dep", assigned_actors=["fleet_1"])
g.add_task(
    task_id="t1",
    actor="fleet_1",
    action="standby",
    target="area_A",
    duration_lb=1.0,
    required_capability=[],
    energy_cost=0.5,
    ammo_cost=0,
)
g.add_dependency("t1", "missing_task", relation="sequence")
built = g.build()
""",
        "expected_error_type": ERROR_EXECUTION_FAILED,
    },
    {
        "name": "empty_build",
        "code": """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_empty", assigned_actors=["fleet_1"])
built = g.build()
""",
        "expected_error_type": ERROR_EXECUTION_FAILED,
    },
]


def main():
    pipeline = VerificationPipeline(z3_timeout_ms=15_000)
    passed = 0

    for case in CASES:
        result = execute_gcjp_code(case["code"])
        report = pipeline.verify_gcjp_code(case["code"])
        report_error_type = report.layers[0].details.get("error_type")

        print(
            f"[{case['name']}] 预期={case['expected_error_type']}, "
            f"executor={result.error_type}, pipeline={report_error_type}"
        )

        assert not result.passed, f"{case['name']} 执行器应判定失败"
        assert result.error_type == case["expected_error_type"], result.error_msg
        assert not report.overall_passed, f"{case['name']} 管道应判定失败"
        assert report_error_type == case["expected_error_type"], report.layers[0].error_msg
        passed += 1

    print(f"Demo 07 通过：{passed}/{len(CASES)} GCJP 执行器失败诊断用例全部符合预期")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
