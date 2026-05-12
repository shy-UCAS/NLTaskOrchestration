"""
demos/demo_09_sync_barrier_gcjp.py
python -m demos.demo_09_sync_barrier_gcjp
Handwritten GCJP demo: sync and barrier SAT case.

Note: GCJP v1 sync constrains task start times:
    abs(start_i - start_j) <= tolerance
It is a start-sync approximation, not end/arrival synchronization.
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
    assert report.overall_passed, "sync/barrier GCJP demo should be SAT"
    print("PASS Demo 09 sync barrier SAT")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
