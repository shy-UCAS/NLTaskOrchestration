"""
demos/demo_08_parallel_tasks_gcjp.py
python -m demos.demo_08_parallel_tasks_gcjp
Handwritten GCJP demo: parallel tasks SAT case.

Note: GCJP v1 treats `parallel` as a semantic graph edge. It does not
generate a Z3 time_order constraint.
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
    assert report.overall_passed, "parallel tasks GCJP demo should be SAT"
    print("PASS Demo 08 parallel tasks SAT")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
