"""
demos/demo_11_capability_mismatch_gcjp.py
python -m demos.demo_11_capability_mismatch_gcjp
Handwritten GCJP demo: capability mismatch UNSAT case.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verifier.pipeline import VerificationPipeline


GCJP_CODE = """
from gcjp.mission_graph import TaskGraphBuilder

g = TaskGraphBuilder(
    segment_id="seg_demo_11_capability_mismatch",
    assigned_actors=["fleet_2"],
)
g.declare_segment_meta(
    assumed_conditions=["mission_start"],
    interface_ids_to_fulfill=[],
)

g.add_task(
    task_id="t1_fleet2_jam_area_a",
    actor="fleet_2",
    action="jam",
    target="area_A",
    duration_lb=3.0,
    required_capability=["jamming_capable"],
    energy_cost=10.0,
    ammo_cost=0,
)

g.add_capability_constraint(
    task_id="t1_fleet2_jam_area_a",
    required=["jamming_capable"],
    actor_capabilities=["strike_capable"],
    source_label="capability_t1_fleet2_jam_area_a",
)
g.add_resource_constraint("fleet_2", "energy_kwh", max_value=60.0)

built = g.build()
"""


def main():
    report = VerificationPipeline(z3_timeout_ms=15_000).verify_gcjp_code(GCJP_CODE)
    report.print_report()
    assert not report.overall_passed, "capability mismatch GCJP demo should be UNSAT"
    assert any("capability_t1_fleet2_jam_area_a" in label for label in report.unsat_core), (
        "capability mismatch should be attributed to fleet_2 missing jamming_capable"
    )
    print("PASS Demo 11 capability mismatch UNSAT")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
