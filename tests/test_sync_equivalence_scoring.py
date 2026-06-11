"""Regression + alignment tests for sync/group_sync semantic-equivalence scoring.

Covers the core fix package (A+C+D+E):

* A — `experiments.phase1_common` scores synchronization by *semantics*, not by
  which API was called: `add_dependency(relation="sync")` (edge+constraint),
  `add_sync_constraint` (sync constraint), and `add_group_sync_constraint`
  (group_sync constraint) are all mutually accepted for a sync expectation.
* C — the generation prompt canonicalizes every synchronization to a single
  `add_group_sync_constraint` and no longer offers `relation="sync"` /
  `add_sync_constraint`.
* D — the exp_01l repair loop only fires when the verification report carries an
  actionable signal (completeness-only failures are skipped).
* Regression — the two real cases that failed the qwen3-max run
  (`trial_binary_sync_ac0a598d` edge_complete, `trial_aggregate_disperse_d6a87b1d`
  constraint_complete) now reach first_pass with their *original* model output.
"""
import unittest
from pathlib import Path

from gcjp.mission_graph import TaskGraphBuilder
from experiments.phase1_common import (
    _constraint_complete,
    _edge_complete,
    _sync_realized,
    evaluate_graph_against_expected,
)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "standard_nl_to_gcjp_prompt.md"


def _two_rendezvous() -> TaskGraphBuilder:
    g = TaskGraphBuilder(segment_id="seg_sync_equiv", assigned_actors=["fleet_1", "fleet_2"])
    g.add_task("t1", actor="fleet_1", action="rendezvous", target="p",
               duration_lb=0.5, required_capability=[], energy_cost=1.0, ammo_cost=0)
    g.add_task("t2", actor="fleet_2", action="rendezvous", target="p",
               duration_lb=0.5, required_capability=[], energy_cost=1.0, ammo_cost=0)
    return g


class TestSyncEquivalenceScoring(unittest.TestCase):
    """The three sync encodings are interchangeable for scoring purposes."""

    def test_group_sync_satisfies_expected_sync(self):
        g = _two_rendezvous()
        g.add_group_sync_constraint(["t1", "t2"], tolerance=0.5)
        graph = g.build()
        self.assertTrue(_sync_realized(graph))
        self.assertTrue(_edge_complete(graph, {"edge_relations": ["sync"]}))
        self.assertTrue(_constraint_complete(graph, {"constraint_types": ["sync"]}))

    def test_sync_constraint_satisfies_expected_group_sync(self):
        g = _two_rendezvous()
        g.add_sync_constraint("t1", "t2", tolerance=0.5)
        graph = g.build()
        self.assertTrue(_constraint_complete(graph, {"constraint_types": ["group_sync"]}))
        self.assertTrue(_edge_complete(graph, {"edge_relations": ["sync"]}))

    def test_sync_edge_satisfies_expected_group_sync(self):
        g = _two_rendezvous()
        g.add_dependency("t1", "t2", relation="sync", sync_tolerance=0.5)
        graph = g.build()
        self.assertTrue(_constraint_complete(graph, {"constraint_types": ["group_sync"]}))
        self.assertTrue(_edge_complete(graph, {"edge_relations": ["sync"]}))

    def test_no_sync_does_not_satisfy_expected_sync(self):
        graph = _two_rendezvous().build()
        self.assertFalse(_sync_realized(graph))
        self.assertFalse(_edge_complete(graph, {"edge_relations": ["sync"]}))
        self.assertFalse(_constraint_complete(graph, {"constraint_types": ["sync"]}))

    def test_non_sync_constraint_types_remain_strict(self):
        # The equivalence must NOT make every constraint type lenient.
        g = _two_rendezvous()
        g.add_group_sync_constraint(["t1", "t2"], tolerance=0.5)
        graph = g.build()
        self.assertFalse(_constraint_complete(graph, {"constraint_types": ["capability"]}))

    def test_non_sync_edge_relations_remain_strict(self):
        g = _two_rendezvous()
        g.add_group_sync_constraint(["t1", "t2"], tolerance=0.5)
        graph = g.build()
        self.assertFalse(_edge_complete(graph, {"edge_relations": ["sequence"]}))


# --- Exact previously-failing model outputs from the qwen3-max run ---------- #

BINARY_SYNC_CODE = """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_trial_binary_sync_ac0a598d", assigned_actors=["fleet_10", "fleet_5"])
g.declare_segment_meta(assumed_conditions=[])
g.add_task("t1", actor="fleet_5", action="rendezvous", target="point_27", duration_lb=0.5, required_capability=[], energy_cost=1.0, ammo_cost=0)
g.add_task("t2", actor="fleet_10", action="rendezvous", target="point_27", duration_lb=0.5, required_capability=[], energy_cost=1.0, ammo_cost=0)
g.add_sync_constraint("t1", "t2", tolerance=0.5)
g.add_resource_constraint("fleet_5", "ammo", max_value=0)
g.add_resource_constraint("fleet_5", "energy_kwh", max_value=55.0)
g.add_resource_constraint("fleet_10", "ammo", max_value=5)
g.add_resource_constraint("fleet_10", "energy_kwh", max_value=52.0)
built = g.build()
"""
BINARY_SYNC_EXPECTED = {"constraint_types": ["sync"], "edge_relations": ["sync"], "node_count": 2}

AGGREGATE_CODE = """
from gcjp.mission_graph import TaskGraphBuilder
g = TaskGraphBuilder(segment_id="seg_trial_aggregate_disperse_d6a87b1d", assigned_actors=["fleet_11", "fleet_12", "fleet_8"])
g.declare_segment_meta(assumed_conditions=[])
g.add_task("t0", actor="fleet_11", action="reconnaissance", target="area_36", duration_lb=2.0, required_capability=["recon_capable"], energy_cost=3.0, ammo_cost=0)
g.add_task("t1_strike", actor="fleet_12", action="strike", target="target_21", duration_lb=1.5, required_capability=["strike_capable"], energy_cost=5.0, ammo_cost=1)
g.add_task("t1_rdv", actor="fleet_12", action="rendezvous", target="point_56", duration_lb=0.5, required_capability=[], energy_cost=1.0, ammo_cost=0)
g.add_task("t2_strike", actor="fleet_8", action="breakthrough", target="target_40", duration_lb=2.0, required_capability=["strike_capable"], energy_cost=8.0, ammo_cost=1)
g.add_task("t2_rdv", actor="fleet_8", action="rendezvous", target="point_56", duration_lb=0.5, required_capability=[], energy_cost=1.0, ammo_cost=0)
g.add_dependency("t0", "t1_strike", relation="sequence")
g.add_dependency("t1_strike", "t1_rdv", relation="sequence")
g.add_dependency("t0", "t2_strike", relation="sequence")
g.add_dependency("t2_strike", "t2_rdv", relation="sequence")
g.add_sync_constraint("t1_rdv", "t2_rdv", tolerance=1.0)
g.add_resource_constraint("fleet_11", "ammo", max_value=1)
g.add_resource_constraint("fleet_11", "energy_kwh", max_value=90.0)
g.add_resource_constraint("fleet_12", "ammo", max_value=7)
g.add_resource_constraint("fleet_12", "energy_kwh", max_value=95.0)
g.add_resource_constraint("fleet_8", "ammo", max_value=6)
g.add_resource_constraint("fleet_8", "energy_kwh", max_value=70.0)
g.add_capability_constraint("t0", required=["recon_capable"], actor_capabilities=["jamming_capable", "recon_capable"])
g.add_capability_constraint("t1_strike", required=["strike_capable"], actor_capabilities=["jamming_capable", "recon_capable", "strike_capable"])
g.add_capability_constraint("t2_strike", required=["strike_capable"], actor_capabilities=["recon_capable", "strike_capable"])
built = g.build()
"""
AGGREGATE_EXPECTED = {
    "constraint_types": ["time_order", "group_sync", "capability"],
    "edge_relations": ["sequence"],
    "node_count": 5,
}


class TestFailingCaseRegression(unittest.TestCase):
    """The two real failures now reach first_pass with their original output."""

    @staticmethod
    def _evaluate(code: str, expected_patterns: dict) -> dict:
        from gcjp.code_executor import execute_gcjp_code
        from verifier.pipeline import VerificationPipeline

        exec_result = execute_gcjp_code(code)
        graph = exec_result.graph if exec_result and exec_result.graph else None
        report = VerificationPipeline(z3_timeout_ms=15_000).verify_gcjp_code(code)
        case = {"expected_patterns": expected_patterns, "expected_result": "sat"}
        return evaluate_graph_against_expected(case, graph, report)

    def test_binary_sync_original_output_now_first_pass(self):
        ev = self._evaluate(BINARY_SYNC_CODE, BINARY_SYNC_EXPECTED)
        self.assertTrue(ev["edge_complete"], "standalone sync constraint should satisfy expected sync edge")
        self.assertTrue(ev["constraint_complete"])
        self.assertTrue(ev["first_pass"])

    def test_aggregate_disperse_original_output_now_first_pass(self):
        ev = self._evaluate(AGGREGATE_CODE, AGGREGATE_EXPECTED)
        self.assertTrue(ev["constraint_complete"], "pairwise sync should satisfy expected group_sync")
        self.assertTrue(ev["edge_complete"])
        self.assertTrue(ev["first_pass"])


class TestRepairActionableGuard(unittest.TestCase):
    """Completeness-only failures (all 4 layers green) must not enter repair."""

    @staticmethod
    def _guard():
        from experiments.exp_01l_standard_nl_to_gcjp_with_repair import _repair_actionable
        return _repair_actionable

    class _Report:
        def __init__(self, overall_passed: bool):
            self.overall_passed = overall_passed

    def test_completeness_only_failure_not_actionable(self):
        guard = self._guard()
        ev = {"first_pass": False, "l3_expected_result": True}
        self.assertFalse(guard(ev, self._Report(True)))

    def test_verification_failure_is_actionable(self):
        guard = self._guard()
        ev = {"first_pass": False, "l3_expected_result": True}
        self.assertTrue(guard(ev, self._Report(False)))

    def test_l3_mismatch_is_actionable(self):
        guard = self._guard()
        ev = {"first_pass": False, "l3_expected_result": False}
        self.assertTrue(guard(ev, self._Report(True)))

    def test_already_first_pass_not_actionable(self):
        guard = self._guard()
        ev = {"first_pass": True, "l3_expected_result": True}
        self.assertFalse(guard(ev, self._Report(True)))


class TestPromptSyncCanonical(unittest.TestCase):
    """The generation prompt prefers group_sync and steers off sync edges."""

    def setUp(self):
        self.text = _PROMPT_PATH.read_text(encoding="utf-8")

    def test_group_sync_is_the_preferred_api(self):
        self.assertIn("add_group_sync_constraint", self.text)
        self.assertIn("Prefer a single `add_group_sync_constraint`", self.text)

    def test_sync_edge_is_discouraged(self):
        self.assertIn('Do NOT use `relation="sync"`', self.text)

    def test_sync_removed_from_relation_value_list(self):
        # The old relation enumeration offered `sync` as a dependency relation.
        self.assertNotIn("`sequence`, `parallel`, `sync`,", self.text)

    def test_pairwise_sync_still_documented(self):
        # add_sync_constraint stays in ALLOWED_BUILDER_METHODS (executable), so the
        # prompt must keep its signature to satisfy prompt-API alignment.
        self.assertIn("g.add_sync_constraint(", self.text)


if __name__ == "__main__":
    unittest.main()
