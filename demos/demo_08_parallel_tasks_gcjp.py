"""
demos/demo_08_parallel_tasks_gcjp.py
python -m demos.demo_08_parallel_tasks_gcjp

SAT 正例：并行任务无冲突验证。

场景：
  fleet_1 侦察 area_A，fleet_2 待机 area_B。两者之间只声明 `parallel` 语义边，
  不引入任何时间顺序约束，资源各自独立，预期无冲突。

说明：
  GCJP v1 中 `parallel` 为纯语义边，不会生成 Z3 time_order 约束，
  仅作为 NetworkX 图边参与结构分析。

验证覆盖：
  ✓ parallel 关系边不会引入虚假时间冲突
  ✓ 不同 actor 的资源约束独立验证

预期结果：
  总体 PASS，两集群并行任务均可满足各自资源上限。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verifier.pipeline import VerificationPipeline


GCJP_CODE = """
from gcjp.mission_graph import TaskGraphBuilder

g = TaskGraphBuilder(
    segment_id="seg_demo_08_parallel_tasks",
    assigned_actors=["fleet_1", "fleet_2"],
)
g.declare_segment_meta(
    assumed_conditions=["mission_start"],
    interface_ids_to_fulfill=[],
)

g.add_task(
    task_id="t1_fleet1_recon_area_a",
    actor="fleet_1",
    action="reconnaissance",
    target="area_A",
    duration_lb=2.0,
    required_capability=["recon_capable"],
    energy_cost=3.0,
    ammo_cost=0,
)
g.add_task(
    task_id="t2_fleet2_standby_area_b",
    actor="fleet_2",
    action="standby",
    target="area_B",
    duration_lb=1.0,
    required_capability=[],
    energy_cost=0.5,
    ammo_cost=0,
)

g.add_dependency(
    source="t1_fleet1_recon_area_a",
    target="t2_fleet2_standby_area_b",
    relation="parallel",
)
g.add_resource_constraint("fleet_1", "energy_kwh", max_value=50.0)
g.add_resource_constraint("fleet_2", "energy_kwh", max_value=60.0)

built = g.build()
"""


def main():
    report = VerificationPipeline(z3_timeout_ms=15_000).verify_gcjp_code(GCJP_CODE)
    report.print_report()
    assert report.overall_passed, "并行任务 GCJP demo 预期 SAT"
    print("Demo 08 通过：并行任务 SAT 验证")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
