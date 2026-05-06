"""
demos/demo_05_unsat_example.py
演示样例 5：含不可行约束的反例 —— UNSAT 路径验证

场景：
  fleet_3（电子战型，速度70km/h）被要求：
    1. 先飞到 hq_mark6（距离30km，最少需要25.7分钟）
    2. 执行干扰（最短3分钟）
    3. 再飞到 hq_mark7（再飞10km，需8.6分钟）
  总时间下界 = 25.7 + 3 + 8.6 = 37.3分钟
  但任务时间窗要求在30分钟内完成 ← 物理不可行

预期结果：
  Layer 2 通过（图结构合法）
  Layer 3 UNSAT，归因指向物理可行性约束与时间窗约束的冲突
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gcjp.mission_graph import TaskGraphBuilder
from verifier.pipeline import VerificationPipeline


def build_infeasible_segment():
    g = TaskGraphBuilder(
        segment_id="seg_fleet3_infeasible",
        assigned_actors=["fleet_3"],
    )

    g.declare_segment_meta(
        assumed_conditions=["fleet_3 at initial position"],
        contract_ids_to_fulfill=[],
    )

    # 飞往 hq_mark6（30km，70km/h → 最短需25.7分钟）
    g.add_task(
        task_id="t1_fly_to_mark6",
        actor="fleet_3",
        action="fly_to",
        target="hq_mark6",
        duration_lb=25.7,
        required_capability=[],
        energy_cost=6.0,
        ammo_cost=0,
    )

    # 电子干扰（最短3分钟）
    g.add_task(
        task_id="t2_jam_mark6",
        actor="fleet_3",
        action="jam",
        target="hq_mark6",
        duration_lb=3.0,
        required_capability=["jamming_capable"],
        energy_cost=10.0,
        ammo_cost=0,
    )

    # 飞往 hq_mark7（再10km，需8.6分钟）
    g.add_task(
        task_id="t3_fly_to_mark7",
        actor="fleet_3",
        action="fly_to",
        target="hq_mark7",
        duration_lb=8.6,
        required_capability=[],
        energy_cost=2.0,
        ammo_cost=0,
    )

    # ← 冲突点：要求整个任务链在30分钟内完成（end of t3 <= 30）
    # 但物理最短路径：25.7 + 3.0 + 8.6 = 37.3 分钟 > 30 → UNSAT
    g.add_constraint(
        constraint_type="time_window",
        params={"task_id": "t3_fly_to_mark7", "deadline": 30.0},
        source_label="hard_deadline_30min_t3",
    )

    g.add_dependency("t1_fly_to_mark6", "t2_jam_mark6", relation="sequence")
    g.add_dependency("t2_jam_mark6", "t3_fly_to_mark7", relation="sequence")

    g.add_physical_feasibility_constraint(
        "t1_fly_to_mark6", "initial", "hq_mark6", 30.0, 70.0, 1.0
    )
    g.add_physical_feasibility_constraint(
        "t3_fly_to_mark7", "hq_mark6", "hq_mark7", 10.0, 70.0, 1.0
    )

    g.add_resource_constraint("fleet_3", "ammo", max_value=1)
    g.add_resource_constraint("fleet_3", "energy_kwh", max_value=80.0)

    return g.build()


def main():
    print("=" * 60)
    print("Demo 05: UNSAT 不可行约束反例验证")
    print("=" * 60)
    print("场景：fleet_3 被要求在30分钟内完成37.3分钟的物理路径")
    print("预期：Z3 报告 UNSAT 并归因到时间窗 vs 物理可行性冲突\n")

    pipeline = VerificationPipeline(z3_timeout_ms=15_000)
    graph = build_infeasible_segment()
    report = pipeline.verify_graph(graph)
    report.print_report()

    # 验证 UNSAT 被正确检测
    assert not report.overall_passed, "应该检测到 UNSAT！"
    assert len(report.unsat_core) > 0, "应该有 unsat core！"
    print("✅ UNSAT 路径验证成功：约束冲突被正确检测并归因")
    return True


if __name__ == "__main__":
    main()
