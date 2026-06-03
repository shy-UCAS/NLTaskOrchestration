"""
Offline tests for exp_01k API-fill config resolution.
"""
import unittest
from pathlib import Path

from gcjp.code_executor import execute_gcjp_code
from gcjp.safety_checker import check_gcjp_apifill_contract
from gcjp.task_plan_loader import (
    load_action_defaults_from_yaml,
    load_capability_model_from_yaml,
)
from experiments.exp_01k_nl_to_gcjp_apifill_deterministic import (
    check_config_param_conformance,
)
from verifier.pipeline import VerificationPipeline

ROOT = Path(__file__).resolve().parents[1]

APIFILL_CODE = """from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_test_1k", assigned_actors=["fleet_1"])
g.declare_segment_meta(assumed_conditions=[])
g.add_task("t1", actor="fleet_1", action="reconnaissance", target="area_A")
g.add_task("t2", actor="fleet_1", action="strike", target="target_A")
g.add_dependency("t1", "t2", relation="sequence")
g.add_time_window_constraint("t2", deadline=10.0)
built = g.build()
"""


class TestTaskGraphBuilderConfigResolution(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.action_defaults = load_action_defaults_from_yaml(
            ROOT / "configs" / "action_templates.yaml"
        )
        cls.capability_model = load_capability_model_from_yaml(
            ROOT / "configs" / "capability_model.yaml"
        )

    def test_apifill_code_executes_with_injected_config(self) -> None:
        contract = check_gcjp_apifill_contract(APIFILL_CODE)
        self.assertTrue(contract.passed, msg=contract.violations)

        result = execute_gcjp_code(
            APIFILL_CODE,
            action_defaults=self.action_defaults,
            capability_model=self.capability_model,
        )
        self.assertTrue(result.passed, msg=result.error_msg)
        graph = result.graph
        self.assertIsNotNone(graph)

        recon = graph.nodes["t1"]
        strike = graph.nodes["t2"]
        ad = self.action_defaults
        self.assertEqual(recon.duration_lb, ad["reconnaissance"]["duration_lb"])
        self.assertEqual(recon.energy_cost, ad["reconnaissance"]["energy_cost"])
        self.assertEqual(strike.ammo_cost, ad["strike"]["ammo_cost"])
        self.assertEqual(
            list(recon.required_capability),
            list(ad["reconnaissance"]["required_capability"]),
        )

        resource = [c for c in graph.constraints if c.constraint_type == "resource"]
        by_type = {c.params["resource_type"]: c.params["max_value"] for c in resource}
        cm = self.capability_model["fleet_1"]
        self.assertEqual(by_type.get("ammo"), float(cm["max_ammo"]))
        self.assertEqual(by_type.get("energy_kwh"), float(cm["max_energy_kwh"]))

        capability = [
            c for c in graph.constraints if c.constraint_type == "capability"
        ]
        self.assertEqual({c.params["task_id"] for c in capability}, {"t1", "t2"})

    def test_apifill_code_without_config_fails_before_builtgraph(self) -> None:
        result = execute_gcjp_code(APIFILL_CODE)
        self.assertFalse(result.passed)
        self.assertIsNone(result.graph)
        self.assertIn("system parameter missing", result.error_msg or "")
        self.assertIn("MISSING_TASK_PARAMETER", str(result.api_error))

    def test_verify_gcjp_code_receives_injected_config(self) -> None:
        report = VerificationPipeline(z3_timeout_ms=15_000).verify_gcjp_code(
            APIFILL_CODE,
            action_defaults=self.action_defaults,
            capability_model=self.capability_model,
        )
        self.assertTrue(report.overall_passed, msg=f"unsat_core={report.unsat_core}")

    def test_system_keyword_param_is_rejected_by_contract(self) -> None:
        code = APIFILL_CODE.replace(
            'target="area_A")',
            'target="area_A", duration_lb=5.0)',
            1,
        )
        result = check_gcjp_apifill_contract(code)
        self.assertFalse(result.passed)
        self.assertTrue(
            any("duration_lb" in violation for violation in result.violations),
            msg=result.violations,
        )

    def test_system_positional_param_is_rejected_by_contract(self) -> None:
        code = APIFILL_CODE.replace(
            'g.add_task("t1", actor="fleet_1", action="reconnaissance", target="area_A")',
            'g.add_task("t1", "fleet_1", "reconnaissance", "area_A", 5.0)',
        )
        result = check_gcjp_apifill_contract(code)
        self.assertFalse(result.passed)
        self.assertTrue(
            any("位置参数" in violation for violation in result.violations),
            msg=result.violations,
        )

    def test_resource_and_capability_calls_are_rejected_by_contract(self) -> None:
        code = APIFILL_CODE.replace(
            "built = g.build()",
            'g.add_resource_constraint("fleet_1", "ammo", max_value=4)\n'
            'g.add_capability_constraint("t1", required=["recon_capable"], '
            'actor_capabilities=["recon_capable"])\n'
            "built = g.build()",
        )
        result = check_gcjp_apifill_contract(code)
        self.assertFalse(result.passed)
        self.assertTrue(
            any("add_resource_constraint" in violation for violation in result.violations),
            msg=result.violations,
        )
        self.assertTrue(
            any("add_capability_constraint" in violation for violation in result.violations),
            msg=result.violations,
        )

    def test_unknown_action_fails_during_config_resolution(self) -> None:
        code = APIFILL_CODE.replace("reconnaissance", "no_such_action", 1)
        result = execute_gcjp_code(
            code,
            action_defaults=self.action_defaults,
            capability_model=self.capability_model,
        )
        self.assertFalse(result.passed)
        self.assertIn("no_such_action", result.error_msg or "")

    def test_config_param_conformance_detects_mismatch(self) -> None:
        result = execute_gcjp_code(
            APIFILL_CODE,
            action_defaults=self.action_defaults,
            capability_model=self.capability_model,
        )
        self.assertTrue(result.passed, msg=result.error_msg)
        graph = result.graph
        self.assertTrue(
            check_config_param_conformance(
                graph,
                action_defaults=self.action_defaults,
            )["ok"]
        )

        graph.nodes["t2"].ammo_cost = 999
        check = check_config_param_conformance(
            graph,
            action_defaults=self.action_defaults,
        )
        self.assertFalse(check["ok"])
        self.assertEqual(check["mismatches"][0]["field"], "ammo_cost")


if __name__ == "__main__":
    unittest.main()
