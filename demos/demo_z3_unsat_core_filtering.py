"""
demos/demo_z3_unsat_core_filtering.py
python -m demos.demo_z3_unsat_core_filtering

Regression demo for Phase 0 UNSAT core filtering.
It verifies that reports expose raw / semantic / framework cores and that the
legacy report.unsat_core field now points to the semantic core.
"""

from pathlib import Path

from gcjp.mission_graph import TaskGraphBuilder
from gcjp.task_plan_loader import build_graph_from_task_plan_file
from verifier.pipeline import VerificationPipeline

from demos.demo_05_unsat_example import build_infeasible_segment
from demos.demo_10_condition_resource_conflict_gcjp import GCJP_CODE as RESOURCE_GCJP
from demos.demo_11_capability_mismatch_gcjp import GCJP_CODE as CAPABILITY_GCJP


FRAMEWORK_PREFIXES = ("start_nonneg_", "dur_lb_", "dur_ub_", "end_def_")


def build_group_sync_unsat_segment():
    g = TaskGraphBuilder(
        segment_id="seg_demo_group_sync_unsat",
        assigned_actors=["fleet_1", "fleet_2", "fleet_3"],
    )
    g.declare_segment_meta(
        assumed_conditions=["mission_start"],
        interface_ids_to_fulfill=[],
    )

    g.add_task(
        task_id="t1_fleet1_ready",
        actor="fleet_1",
        action="rendezvous",
        target="point_a",
        duration_lb=1.0,
        required_capability=[],
        energy_cost=1.0,
        ammo_cost=0,
        time_window_earliest=0.0,
        time_window_latest=0.0,
    )
    g.add_task(
        task_id="t2_fleet2_ready",
        actor="fleet_2",
        action="rendezvous",
        target="point_a",
        duration_lb=1.0,
        required_capability=[],
        energy_cost=1.0,
        ammo_cost=0,
        time_window_earliest=10.0,
        time_window_latest=10.0,
    )
    g.add_task(
        task_id="t3_fleet3_ready",
        actor="fleet_3",
        action="rendezvous",
        target="point_a",
        duration_lb=1.0,
        required_capability=[],
        energy_cost=1.0,
        ammo_cost=0,
        time_window_earliest=0.0,
        time_window_latest=0.0,
    )

    g.add_group_sync_constraint(
        task_ids=["t1_fleet1_ready", "t2_fleet2_ready", "t3_fleet3_ready"],
        tolerance=0.0,
        mode="start",
        source_label="group_sync_ready_start",
    )

    return g.build()


def _assert_core_contract(name: str, report, expected_label_part: str | None = None):
    data = report.to_dict()
    for key in (
        "unsat_core",
        "unsat_core_raw",
        "unsat_core_semantic",
        "unsat_core_framework",
        "attribution",
    ):
        assert key in data, f"{name}: missing {key}"

    assert not report.overall_passed, f"{name}: expected UNSAT"
    assert report.unsat_core == report.unsat_core_semantic, (
        f"{name}: legacy unsat_core should equal semantic core"
    )
    assert sorted(report.unsat_core_raw) == sorted(
        report.unsat_core_semantic + report.unsat_core_framework
    ), f"{name}: raw core should be semantic + framework"
    assert all(
        not label.startswith(FRAMEWORK_PREFIXES)
        for label in report.unsat_core_semantic
    ), f"{name}: semantic core still contains framework labels"
    assert all(
        label.startswith(FRAMEWORK_PREFIXES)
        for label in report.unsat_core_framework
    ), f"{name}: framework core contains semantic labels"
    assert report.attribution, f"{name}: expected readable attribution"

    if expected_label_part is not None:
        assert any(expected_label_part in label for label in report.unsat_core_semantic), (
            f"{name}: semantic core missing expected label part {expected_label_part!r}"
        )

    print(f"[OK] {name}")
    print(f"  RAW       : {report.unsat_core_raw}")
    print(f"  SEMANTIC  : {report.unsat_core_semantic}")
    print(f"  FRAMEWORK : {report.unsat_core_framework}")
    print(f"  ATTR      : {report.attribution}")


def main() -> bool:
    root = Path(__file__).resolve().parent.parent
    pipeline = VerificationPipeline(z3_timeout_ms=15_000)

    graph = build_graph_from_task_plan_file(
        task_plan_path=root / "demos" / "demo_02_resource_unsat_task_plan.json",
        schema_path=root / "schemas" / "task_plan_schema.json",
        action_templates_path=root / "configs" / "action_templates.yaml",
        capability_model_path=root / "configs" / "capability_model.yaml",
        segment_id="seg_demo_02_resource_unsat",
    )
    _assert_core_contract(
        "json_resource_unsat",
        pipeline.verify_graph(graph),
        expected_label_part="resource_fleet_1_ammo",
    )

    _assert_core_contract(
        "physical_deadline_unsat",
        pipeline.verify_graph(build_infeasible_segment()),
        expected_label_part="phys_feasibility",
    )

    _assert_core_contract(
        "gcjp_resource_unsat",
        pipeline.verify_gcjp_code(RESOURCE_GCJP),
        expected_label_part="resource_fleet_1_ammo",
    )

    _assert_core_contract(
        "gcjp_capability_unsat",
        pipeline.verify_gcjp_code(CAPABILITY_GCJP),
        expected_label_part="capability_t1_fleet2_jam_area_a",
    )

    _assert_core_contract(
        "group_sync_unsat",
        pipeline.verify_graph(build_group_sync_unsat_segment()),
        expected_label_part="group_sync_pair_",
    )

    print("Phase 0 UNSAT core filtering demo passed.")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
