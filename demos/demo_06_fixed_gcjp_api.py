"""
demos/demo_06_fixed_gcjp_api.py
python -m demos.demo_06_fixed_gcjp_api

验证 GCJP v1 受限 API 接口契约的完整性。

场景：
  通过 4 组代码样本验证受限 API 在各种情况下的行为：
    1. 合法 GCJP 代码（sequence + time_window + resource）→ 完整验证通过
    2. 直接调用 add_constraint()（绕过结构化接口）→ 安全检查拒绝
    3. 导入 Z3 构建器 → 安全检查拒绝
    4. 缺少 built = g.build() → 验证失败

预期结果：
  合法的 GCJP 代码全部通过，非法的全部被拦截。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gcjp.safety_checker import check_gcjp_code
from verifier.pipeline import VerificationPipeline


VALID_GCJP_CODE = """
from gcjp.mission_graph import TaskGraphBuilder

g = TaskGraphBuilder(segment_id="seg_fixed_api", assigned_actors=["fleet_1"])
g.declare_segment_meta(
    assumed_conditions=["mission_start"],
    interface_ids_to_fulfill=[],
)
g.add_task(
    task_id="t1_recon_area_a",
    actor="fleet_1",
    action="reconnaissance",
    target="area_A",
    duration_lb=2.0,
    required_capability=["recon_capable"],
    energy_cost=3.0,
    ammo_cost=0,
)
g.add_task(
    task_id="t2_standby_area_a",
    actor="fleet_1",
    action="standby",
    target="area_A",
    duration_lb=1.0,
    required_capability=[],
    energy_cost=0.5,
    ammo_cost=0,
)
g.add_dependency("t1_recon_area_a", "t2_standby_area_a", relation="sequence")
g.add_time_window_constraint(
    task_id="t2_standby_area_a",
    deadline=10.0,
    source_label="deadline_t2_standby_area_a",
)
g.add_resource_constraint("fleet_1", "energy_kwh", max_value=20.0)
built = g.build()
"""


INVALID_DIRECT_CONSTRAINT_CODE = """
from gcjp.mission_graph import TaskGraphBuilder

g = TaskGraphBuilder(segment_id="seg_bad", assigned_actors=["fleet_1"])
g.add_constraint(
    constraint_type="time_window",
    params={"task_id": "t1", "deadline": 10.0},
    source_label="bad_direct_constraint",
)
"""


INVALID_Z3_IMPORT_CODE = """
from gcjp.constraint_templates import Z3ConstraintBuilder
"""


INVALID_NO_BUILT_CODE = """
from gcjp.mission_graph import TaskGraphBuilder

g = TaskGraphBuilder(segment_id="seg_no_built", assigned_actors=["fleet_1"])
g.declare_segment_meta(
    assumed_conditions=["mission_start"],
    interface_ids_to_fulfill=[],
)
g.add_task(
    task_id="t1_standby_area_a",
    actor="fleet_1",
    action="standby",
    target="area_A",
    duration_lb=1.0,
    required_capability=[],
    energy_cost=0.5,
    ammo_cost=0,
)
"""


def main():
    pipeline = VerificationPipeline(z3_timeout_ms=15_000)

    report = pipeline.verify_gcjp_code(VALID_GCJP_CODE)
    report.print_report()
    assert report.overall_passed, "合法 GCJP 代码应通过完整验证"

    bad_constraint = check_gcjp_code(INVALID_DIRECT_CONSTRAINT_CODE)
    assert not bad_constraint.passed, "直接调用 add_constraint() 应被拒绝"

    bad_import = check_gcjp_code(INVALID_Z3_IMPORT_CODE)
    assert not bad_import.passed, "导入 Z3 构建器应被拒绝"

    no_built_report = pipeline.verify_gcjp_code(INVALID_NO_BUILT_CODE)
    assert not no_built_report.overall_passed, "缺少 built 应导致验证失败"

    print("Demo 06 通过：GCJP v1 受限 API 接口验证")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
