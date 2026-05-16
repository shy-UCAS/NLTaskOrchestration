"""
demos/demo_09_sync_barrier_gcjp.py
python -m demos.demo_09_sync_barrier_gcjp

SAT 正例：同步 barrier 协同汇聚验证。

场景：
  fleet_1 和 fleet_2 各自飞往集结点（rendezvous_point），要求两方同时出发（sync），
  到达后通过 barrier 汇聚，联合执行 t3 会合任务（is_coalition=True）。

说明：
  GCJP v1 中 sync 约束的是任务开始时间：abs(start_i - start_j) <= tolerance，
  不是到达时间同步。barrier 约束要求所有前驱任务完成后后继才能开始。

验证覆盖：
  ✓ sync 约束（开始时间差在容忍范围内）
  ✓ barrier 约束（多前驱汇聚为单后继）
  ✓ is_coalition 联合任务声明
  ✓ 多 actor 资源约束独立验证
  ✓ GCJP 代码字符串路径（L1 → L2 → L3 → L4）

预期结果：
  总体 PASS，sync + barrier 组合可满足。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verifier.pipeline import VerificationPipeline


GCJP_CODE = """
from gcjp.mission_graph import TaskGraphBuilder

g = TaskGraphBuilder(
    segment_id="seg_demo_09_sync_barrier",
    assigned_actors=["fleet_1", "fleet_2"],
)
g.declare_segment_meta(
    assumed_conditions=["mission_start"],
    interface_ids_to_fulfill=[],
)

g.add_task(
    task_id="t1_fleet1_arrive_rendezvous",
    actor="fleet_1",
    action="fly_to",
    target="rendezvous_point",
    duration_lb=6.0,
    required_capability=[],
    energy_cost=3.0,
    ammo_cost=0,
)
g.add_task(
    task_id="t2_fleet2_arrive_rendezvous",
    actor="fleet_2",
    action="fly_to",
    target="rendezvous_point",
    duration_lb=6.5,
    required_capability=[],
    energy_cost=4.0,
    ammo_cost=0,
)
g.add_task(
    task_id="t3_joint_recon_ready",
    actor="fleet_1",
    action="rendezvous",
    target="rendezvous_point",
    duration_lb=0.5,
    required_capability=[],
    energy_cost=1.0,
    ammo_cost=0,
    is_coalition=True,
    coalition_members=["fleet_1", "fleet_2"],
)

g.add_dependency(
    source="t1_fleet1_arrive_rendezvous",
    target="t2_fleet2_arrive_rendezvous",
    relation="sync",
    sync_tolerance=1.0,
)
g.add_dependency(
    source="t1_fleet1_arrive_rendezvous",
    target="t3_joint_recon_ready",
    relation="barrier",
)
g.add_dependency(
    source="t2_fleet2_arrive_rendezvous",
    target="t3_joint_recon_ready",
    relation="barrier",
)

g.add_resource_constraint("fleet_1", "energy_kwh", max_value=50.0)
g.add_resource_constraint("fleet_2", "energy_kwh", max_value=60.0)

built = g.build()
"""


def main():
    report = VerificationPipeline(z3_timeout_ms=15_000).verify_gcjp_code(GCJP_CODE)
    report.print_report()
    assert report.overall_passed, "sync/barrier GCJP demo 预期 SAT"
    print("Demo 09 通过：sync/barrier SAT 验证")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
