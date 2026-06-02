"""
离线确定性自测(不耗 API):验证 exp_01j 的核心路径——
含 sentinel 的骨架代码经 fill_skeleton_code 用 YAML 确定性填参后,
无残留占位符、可受限执行、节点参数等于 YAML、验证通过(sat)。
"""
import unittest
from pathlib import Path

from gcjp.code_executor import execute_gcjp_code
from gcjp.skeleton_filler import SENTINELS, fill_skeleton_code
from gcjp.task_plan_loader import (
    load_action_defaults_from_yaml,
    load_capability_model_from_yaml,
)
from verifier.pipeline import VerificationPipeline

ROOT = Path(__file__).resolve().parents[1]

SKELETON = """from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_test_1j", assigned_actors=["fleet_1"])
g.declare_segment_meta(assumed_conditions=[])
g.add_task("t1", actor="fleet_1", action="reconnaissance", target="area_A", duration_lb=FILL_DURATION, required_capability=FILL_CAPABILITY, energy_cost=FILL_ENERGY, ammo_cost=FILL_AMMO)
g.add_task("t2", actor="fleet_1", action="strike", target="target_A", duration_lb=FILL_DURATION, required_capability=FILL_CAPABILITY, energy_cost=FILL_ENERGY, ammo_cost=FILL_AMMO)
g.add_dependency("t1", "t2", relation="sequence")
g.add_time_window_constraint("t2", deadline=10.0)
g.add_resource_constraint("fleet_1", "ammo", max_value=FILL_MAX_AMMO)
g.add_resource_constraint("fleet_1", "energy_kwh", max_value=FILL_MAX_ENERGY)
g.add_capability_constraint("t1", required=FILL_CAPABILITY, actor_capabilities=FILL_ACTOR_CAPS)
g.add_capability_constraint("t2", required=FILL_CAPABILITY, actor_capabilities=FILL_ACTOR_CAPS)
built = g.build()
"""


class TestSkeletonFiller(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.action_defaults = load_action_defaults_from_yaml(
            ROOT / "configs" / "action_templates.yaml"
        )
        cls.capability_model = load_capability_model_from_yaml(
            ROOT / "configs" / "capability_model.yaml"
        )
        cls.result = fill_skeleton_code(
            SKELETON,
            action_defaults=cls.action_defaults,
            capability_model=cls.capability_model,
        )

    def test_fill_ok_no_remaining_sentinels(self) -> None:
        self.assertTrue(self.result.ok, msg=self.result.error)
        for sentinel in SENTINELS:
            self.assertNotIn(sentinel, self.result.code)
        self.assertGreater(self.result.num_filled, 0)

    def test_filled_code_executes_with_yaml_params(self) -> None:
        exec_result = execute_gcjp_code(self.result.code)
        self.assertTrue(exec_result.passed, msg=exec_result.error_msg)
        graph = exec_result.graph
        ad = self.action_defaults
        self.assertEqual(graph.nodes["t1"].duration_lb, ad["reconnaissance"]["duration_lb"])
        self.assertEqual(graph.nodes["t1"].energy_cost, ad["reconnaissance"]["energy_cost"])
        self.assertEqual(graph.nodes["t2"].ammo_cost, ad["strike"]["ammo_cost"])

    def test_filled_graph_verifies_sat(self) -> None:
        report = VerificationPipeline(z3_timeout_ms=15_000).verify_gcjp_code(self.result.code)
        self.assertTrue(report.overall_passed, msg=f"unsat_core={report.unsat_core}")

    def test_unknown_action_fails_gracefully(self) -> None:
        bad = SKELETON.replace("action=\"reconnaissance\"", "action=\"no_such_action\"")
        result = fill_skeleton_code(
            bad,
            action_defaults=self.action_defaults,
            capability_model=self.capability_model,
        )
        self.assertFalse(result.ok)
        self.assertIn("no_such_action", result.error or "")


if __name__ == "__main__":
    unittest.main()
