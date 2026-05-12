"""
demos/demo_01_simple_solo.py
演示样例 1：2集群纯Solo任务，线性依赖

场景：
  fleet_1 侦察 target_A 后飞往 target_B
  fleet_2 独立飞往 target_B 并执行打击
  两者在 target_B 无需同步（各自独立完成）

验证覆盖：
  ✓ 基本任务节点添加
  ✓ sequence 依赖边
  ✓ 资源约束（弹药/能量）
  ✓ 物理可行性约束
  ✓ 契约声明
  ✓ 四层验证管道 (Layer 2 + 3)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gcjp.mission_graph import BuiltGraph, TaskGraphBuilder
from verifier.pipeline import VerificationPipeline


# ─────────────────────────────────────────────────────────────────────────────
# 原始自然语言指令
# ─────────────────────────────────────────────────────────────────────────────

NL_INSTRUCTION = (
    "fleet_1 先侦察 target_A，完成后飞往 target_B 待命。"
    "fleet_2 直接飞往 target_B 并执行打击。"
)

# ─────────────────────────────────────────────────────────────────────────────
# 手工构建 fleet_1 的任务段
# ─────────────────────────────────────────────────────────────────────────────

def build_fleet1_segment() -> "BuiltGraph":
    g = TaskGraphBuilder(
        segment_id="seg_fleet1_solo",
        assigned_actors=["fleet_1"],
    )

    g.declare_segment_meta(
        assumed_conditions=["fleet_1 at initial position (0,0)"],
        interface_ids_to_fulfill=["interface_fleet1_done"],
    )

    # 任务1：侦察 target_A
    # fleet_1 是侦察打击混合型，min_duration=2.0，energy_cost=3.0
    g.add_task(
        task_id="t1_recon_targetA",
        actor="fleet_1",
        action="reconnaissance",
        target="target_A",
        duration_lb=2.0,
        required_capability=["recon_capable"],
        energy_cost=3.0,
        ammo_cost=0,
    )

    # 任务2：飞往 target_B
    # target_A(10,10) -> target_B(20,10)，距离约10km
    # fleet_1 巡航速度80km/h，飞行时间=10/80*60=7.5分钟≈7.5时间单位（分钟制）
    g.add_task(
        task_id="t2_fly_to_targetB",
        actor="fleet_1",
        action="fly_to",
        target="target_B",
        duration_lb=7.5,   # 飞行时间下界（物理约束推导）
        required_capability=[],
        energy_cost=2.0,   # 10km * 0.2kWh/km
        ammo_cost=0,
    )

    # 序列依赖：先侦察，再飞行
    g.add_dependency("t1_recon_targetA", "t2_fly_to_targetB", relation="sequence")

    # 物理可行性约束（显式注册，供 Z3 验证）
    g.add_physical_feasibility_constraint(
        task_id="t2_fly_to_targetB",
        from_position="target_A",
        to_position="target_B",
        distance_km=10.0,
        actor_speed_kmh=80.0,
        time_unit_minutes=1.0,
    )

    # 资源约束
    g.add_resource_constraint("fleet_1", "ammo", max_value=4)
    g.add_resource_constraint("fleet_1", "energy_kwh", max_value=50.0)

    # 出口资源状态声明
    g.declare_resource_state(
        actor="fleet_1",
        remaining_ammo=4,         # 未使用弹药
        remaining_energy=45.0,    # 50 - 3 - 2 = 45 kWh
        position="target_B",
    )

    # 契约履行声明
    g.declare_interface_fulfillment(
        interface_id="interface_fleet1_done",
        exit_node="t2_fly_to_targetB",
        resource_state={"fleet_1": {"ammo": 4, "energy_kwh": 45.0}},
        guaranteed_conditions=[
            "fleet_1 completed reconnaissance of target_A",
            "fleet_1 positioned at target_B",
        ],
    )

    return g.build()


# ─────────────────────────────────────────────────────────────────────────────
# 手工构建 fleet_2 的任务段
# ─────────────────────────────────────────────────────────────────────────────

def build_fleet2_segment() -> "BuiltGraph":
    g = TaskGraphBuilder(
        segment_id="seg_fleet2_solo",
        assigned_actors=["fleet_2"],
    )

    g.declare_segment_meta(
        assumed_conditions=["fleet_2 at initial position (2,0)"],
        interface_ids_to_fulfill=["interface_fleet2_done"],
    )

    # 任务1：飞往 target_B
    # fleet_2 初始位置(2,0)，target_B(20,10)，距离≈20.1km
    # fleet_2 巡航速度100km/h，飞行时间≈12.1分钟
    g.add_task(
        task_id="t1_fleet2_fly_targetB",
        actor="fleet_2",
        action="fly_to",
        target="target_B",
        duration_lb=12.1,
        required_capability=[],
        energy_cost=4.0,   # 20.1km * 0.2kWh/km
        ammo_cost=0,
    )

    # 任务2：打击 target_B
    g.add_task(
        task_id="t2_fleet2_strike_targetB",
        actor="fleet_2",
        action="strike",
        target="target_B",
        duration_lb=1.0,
        required_capability=["strike_capable"],
        energy_cost=5.0,
        ammo_cost=1,
    )

    # 序列依赖：先飞到，再打击
    g.add_dependency("t1_fleet2_fly_targetB", "t2_fleet2_strike_targetB",
                     relation="sequence")

    # 物理可行性
    g.add_physical_feasibility_constraint(
        task_id="t1_fleet2_fly_targetB",
        from_position="initial_fleet2",
        to_position="target_B",
        distance_km=20.1,
        actor_speed_kmh=100.0,
        time_unit_minutes=1.0,
    )

    # 资源约束
    g.add_resource_constraint("fleet_2", "ammo", max_value=6)
    g.add_resource_constraint("fleet_2", "energy_kwh", max_value=60.0)

    # 出口资源状态
    g.declare_resource_state(
        actor="fleet_2",
        remaining_ammo=5,          # 6 - 1 = 5
        remaining_energy=51.0,     # 60 - 4 - 5 = 51 kWh
        position="target_B",
    )

    g.declare_interface_fulfillment(
        interface_id="interface_fleet2_done",
        exit_node="t2_fleet2_strike_targetB",
        resource_state={"fleet_2": {"ammo": 5, "energy_kwh": 51.0}},
        guaranteed_conditions=[
            "fleet_2 completed strike on target_B",
            "fleet_2 positioned at target_B",
        ],
    )

    return g.build()


# ─────────────────────────────────────────────────────────────────────────────
# 主程序：运行验证
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Demo 01: 2集群 Solo 线性任务验证")
    print("=" * 60)
    print(f"NL指令: {NL_INSTRUCTION}\n")

    pipeline = VerificationPipeline(z3_timeout_ms=15_000)

    # 验证 fleet_1 段
    print(">>> 验证 fleet_1 段...")
    fleet1_graph = build_fleet1_segment()
    report1 = pipeline.verify_graph(fleet1_graph)
    report1.print_report()

    # 验证 fleet_2 段
    print(">>> 验证 fleet_2 段...")
    fleet2_graph = build_fleet2_segment()
    report2 = pipeline.verify_graph(fleet2_graph)
    report2.print_report()

    # 汇总
    all_passed = report1.overall_passed and report2.overall_passed
    print("=" * 60)
    print(f"Demo 01 总体结果: {'PASS 全部通过' if all_passed else 'FAIL 存在失败'}")
    if report1.schedule:
        print("\nfleet_1 时间调度:")
        for tid, sched in report1.schedule.items():
            if sched["start"] is not None:
                print(f"  {tid}: start={sched['start']:.2f}, "
                      f"end={sched['end']:.2f}, dur={sched['duration']:.2f}")
    if report2.schedule:
        print("\nfleet_2 时间调度:")
        for tid, sched in report2.schedule.items():
            if sched["start"] is not None:
                print(f"  {tid}: start={sched['start']:.2f}, "
                      f"end={sched['end']:.2f}, dur={sched['duration']:.2f}")
    print("=" * 60)

    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
