"""
离线确定性自测(不耗 API):验证 exp_01i 的核心路径——
给定只含作战语义的 task_plan,build_graph_from_task_plan 从 YAML 确定性填参,
verify_graph 通过(sat)。
"""
import unittest
from pathlib import Path

from gcjp.task_plan_loader import (
    build_graph_from_task_plan,
    load_action_defaults_from_yaml,
    load_capability_model_from_yaml,
)
from verifier.pipeline import VerificationPipeline

ROOT = Path(__file__).resolve().parents[1]


class TestTaskPlanDeterministicBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.action_defaults = load_action_defaults_from_yaml(
            ROOT / "configs" / "action_templates.yaml"
        )
        cls.capability_model = load_capability_model_from_yaml(
            ROOT / "configs" / "capability_model.yaml"
        )
        cls.plan = {
            "plan_id": "seg_test_1i",
            "participants": [{"actor_id": "fleet_1", "type": "fleet"}],
            "tasks": [
                {"task_id": "t1", "actor": "fleet_1",
                 "action": "reconnaissance", "target": "area_A"},
                {"task_id": "t2", "actor": "fleet_1",
                 "action": "strike", "target": "target_A",
                 "time_window": {"deadline": 10.0}},
            ],
            "relations": [{"source": "t1", "target": "t2", "type": "sequence"}],
        }
        cls.graph = build_graph_from_task_plan(
            cls.plan,
            segment_id=cls.plan["plan_id"],
            action_defaults=cls.action_defaults,
            capability_model=cls.capability_model,
        )

    def test_node_params_come_from_yaml(self) -> None:
        recon = self.graph.nodes["t1"]
        strike = self.graph.nodes["t2"]
        ad = self.action_defaults

        self.assertEqual(recon.duration_lb, ad["reconnaissance"]["duration_lb"])
        self.assertEqual(recon.energy_cost, ad["reconnaissance"]["energy_cost"])
        self.assertEqual(recon.ammo_cost, ad["reconnaissance"]["ammo_cost"])
        self.assertEqual(
            list(recon.required_capability),
            list(ad["reconnaissance"]["required_capability"]),
        )
        self.assertEqual(strike.duration_lb, ad["strike"]["duration_lb"])
        self.assertEqual(strike.ammo_cost, ad["strike"]["ammo_cost"])

    def test_resource_limits_come_from_capability_model(self) -> None:
        resource = [c for c in self.graph.constraints if c.constraint_type == "resource"]
        by_type = {c.params["resource_type"]: c.params["max_value"] for c in resource}
        cm = self.capability_model["fleet_1"]
        self.assertEqual(by_type.get("ammo"), float(cm["max_ammo"]))
        self.assertEqual(by_type.get("energy_kwh"), float(cm["max_energy_kwh"]))

    def test_verify_graph_sat(self) -> None:
        report = VerificationPipeline(z3_timeout_ms=15_000).verify_graph(self.graph)
        self.assertTrue(report.overall_passed, msg=f"unsat_core={report.unsat_core}")

    def test_unknown_action_raises(self) -> None:
        bad_plan = dict(self.plan)
        bad_plan["tasks"] = [
            {"task_id": "t1", "actor": "fleet_1",
             "action": "no_such_action", "target": "area_A"},
        ]
        with self.assertRaises(Exception):
            build_graph_from_task_plan(
                bad_plan,
                segment_id="seg_bad",
                action_defaults=self.action_defaults,
                capability_model=self.capability_model,
            )


if __name__ == "__main__":
    unittest.main()
