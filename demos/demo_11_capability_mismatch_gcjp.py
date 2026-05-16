"""
demos/demo_11_capability_mismatch_gcjp.py
python -m demos.demo_11_capability_mismatch_gcjp
手写 GCJP demo：能力不匹配 UNSAT 反例。
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
    assert not report.overall_passed, "能力不匹配 GCJP demo 预期 UNSAT"
    assert any("capability_t1_fleet2_jam_area_a" in label for label in report.unsat_core), (
        "能力不匹配应归因于 fleet_2 缺少 jamming_capable"
    )
    print("Demo 11 通过：能力不匹配 UNSAT 验证")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
