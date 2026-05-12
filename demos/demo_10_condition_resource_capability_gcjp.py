"""
demos/demo_10_condition_resource_capability_gcjp.py
python -m demos.demo_10_condition_resource_capability_gcjp
Handwritten GCJP demo: condition-triggered resource conflict UNSAT case.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verifier.pipeline import VerificationPipeline


GCJP_CODE = """
from gcjp.mission_graph import TaskGraphBuilder

g = TaskGraphBuilder(
    segment_id="seg_demo_10_resource_conflict",
    assigned_actors=["fleet_1"],
)
g.declare_segment_meta(
    assumed_conditions=["mission_start"],
    interface_ids_to_fulfill=[],
)

g.add_task(
    task_id="t1_strike_target_a",
    actor="fleet_1",
    action="strike",
    target="target_A",
    duration_lb=1.5,
    required_capability=["strike_capable"],
    energy_cost=5.0,
    ammo_cost=1,
)
g.add_task(
    task_id="t2_strike_target_b",
    actor="fleet_1",
    action="strike",
    target="target_B",
    duration_lb=1.5,
    required_capability=["strike_capable"],
    energy_cost=5.0,
    ammo_cost=1,
)
g.add_task(
    task_id="t3_strike_target_c",
    actor="fleet_1",
    action="strike",
    target="target_C",
    duration_lb=1.5,
    required_capability=["strike_capable"],
    energy_cost=5.0,
    ammo_cost=1,
)
g.add_task(
    task_id="t4_strike_target_d",
    actor="fleet_1",
    action="strike",
    target="target_D",
    duration_lb=1.5,
    required_capability=["strike_capable"],
    energy_cost=5.0,
    ammo_cost=1,
)
g.add_task(
    task_id="t5_strike_target_e",
    actor="fleet_1",
    action="strike",
    target="target_E",
    duration_lb=1.5,
    required_capability=["strike_capable"],
    energy_cost=5.0,
    ammo_cost=1,
)

g.add_dependency("t1_strike_target_a", "t2_strike_target_b", relation="condition_trigger", condition="target_A_destroyed")
g.add_dependency("t2_strike_target_b", "t3_strike_target_c", relation="condition_trigger", condition="target_B_destroyed")
g.add_dependency("t3_strike_target_c", "t4_strike_target_d", relation="condition_trigger", condition="target_C_destroyed")
g.add_dependency("t4_strike_target_d", "t5_strike_target_e", relation="condition_trigger", condition="target_D_destroyed")

g.add_resource_constraint("fleet_1", "ammo", max_value=4)
g.add_resource_constraint("fleet_1", "energy_kwh", max_value=50.0)

built = g.build()
"""


def main():
    report = VerificationPipeline(z3_timeout_ms=15_000).verify_gcjp_code(GCJP_CODE)
    report.print_report()
    assert not report.overall_passed, "resource conflict GCJP demo should be UNSAT"
    assert any("resource_fleet_1_ammo" in label for label in report.unsat_core), (
        "resource conflict should be attributed to fleet_1 ammo"
    )
    print("PASS Demo 10 resource conflict UNSAT")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
