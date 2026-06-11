"""Unit tests for the dag_exact metric core (experiments/phase1_common.py).

dag_exact is the 8th, strictest metric: exact node mapping, exact non-sync edge
endpoints, and sync pair-sets (with the three sync encodings treated as
semantically equivalent) against the master dataset's full ground truth
(expected_graph + canonical_task_plan). These tests pin down:

* exact matches are recognized, including across sync encodings;
* every structural defect class flips it to False — miswired endpoint,
  missing edge, extra edge, wrong node attribute, tolerance mismatch;
* cases without full ground truth yield None (not evaluable), and a missing
  graph with ground truth present yields False.
"""
import unittest

from gcjp.mission_graph import TaskGraphBuilder
from experiments.phase1_common import dag_exact_match


def _gt_case():
    """Master-style ground truth: t0 --sequence--> t1/t2 branches, rdv group_sync."""
    return {
        "sample_id": "ut_dag_exact",
        "expected_graph": {
            "node_count": 3,
            "edge_count": 2,
            "nodes": [
                {"task_id": "t0", "actor": "fleet_1", "action": "reconnaissance", "target": "area_A"},
                {"task_id": "t1", "actor": "fleet_2", "action": "rendezvous", "target": "point_P"},
                {"task_id": "t2", "actor": "fleet_4", "action": "rendezvous", "target": "point_P"},
            ],
            "edges": [
                {"source": "t0", "target": "t1", "relation": "sequence"},
                {"source": "t0", "target": "t2", "relation": "sequence"},
            ],
            "constraint_types": ["time_order", "group_sync"],
        },
        "canonical_task_plan": {
            "relations": [
                {"source": "t0", "target": "t1", "type": "sequence"},
                {"source": "t0", "target": "t2", "type": "sequence"},
            ],
            "explicit_constraints": [
                {"type": "group_sync", "task_ids": ["t1", "t2"], "mode": "start", "tolerance": 1.0},
            ],
        },
    }


def _builder():
    g = TaskGraphBuilder(segment_id="seg_ut_dag", assigned_actors=["fleet_1", "fleet_2", "fleet_4"])
    g.add_task("t0", actor="fleet_1", action="reconnaissance", target="area_A",
               duration_lb=2.0, required_capability=["recon_capable"], energy_cost=3.0, ammo_cost=0)
    g.add_task("t1", actor="fleet_2", action="rendezvous", target="point_P",
               duration_lb=0.5, required_capability=[], energy_cost=1.0, ammo_cost=0)
    g.add_task("t2", actor="fleet_4", action="rendezvous", target="point_P",
               duration_lb=0.5, required_capability=[], energy_cost=1.0, ammo_cost=0)
    return g


class TestDagExactMatch(unittest.TestCase):

    def test_exact_match_group_sync(self):
        g = _builder()
        g.add_dependency("t0", "t1", relation="sequence")
        g.add_dependency("t0", "t2", relation="sequence")
        g.add_group_sync_constraint(["t1", "t2"], tolerance=1.0)
        self.assertTrue(dag_exact_match(_gt_case(), g.build()))

    def test_exact_match_pairwise_sync_encoding(self):
        # GT says group_sync; model uses the equivalent pairwise sync constraint.
        g = _builder()
        g.add_dependency("t0", "t1", relation="sequence")
        g.add_dependency("t0", "t2", relation="sequence")
        g.add_sync_constraint("t1", "t2", tolerance=1.0)
        self.assertTrue(dag_exact_match(_gt_case(), g.build()))

    def test_miswired_edge_endpoint_fails(self):
        # t1 -> t2 instead of t0 -> t2: relation TYPE still present, so the old
        # type-level scorer would pass this — dag_exact must not.
        g = _builder()
        g.add_dependency("t0", "t1", relation="sequence")
        g.add_dependency("t1", "t2", relation="sequence")
        g.add_group_sync_constraint(["t1", "t2"], tolerance=1.0)
        self.assertFalse(dag_exact_match(_gt_case(), g.build()))

    def test_missing_edge_fails(self):
        g = _builder()
        g.add_dependency("t0", "t1", relation="sequence")
        g.add_group_sync_constraint(["t1", "t2"], tolerance=1.0)
        self.assertFalse(dag_exact_match(_gt_case(), g.build()))

    def test_extra_edge_fails(self):
        g = _builder()
        g.add_dependency("t0", "t1", relation="sequence")
        g.add_dependency("t0", "t2", relation="sequence")
        g.add_dependency("t1", "t2", relation="parallel")
        g.add_group_sync_constraint(["t1", "t2"], tolerance=1.0)
        self.assertFalse(dag_exact_match(_gt_case(), g.build()))

    def test_wrong_node_actor_fails(self):
        g = TaskGraphBuilder(segment_id="seg_ut_dag", assigned_actors=["fleet_1", "fleet_2", "fleet_4"])
        g.add_task("t0", actor="fleet_1", action="reconnaissance", target="area_A",
                   duration_lb=2.0, required_capability=["recon_capable"], energy_cost=3.0, ammo_cost=0)
        # actors swapped on t1/t2 relative to ground truth
        g.add_task("t1", actor="fleet_4", action="rendezvous", target="point_P",
                   duration_lb=0.5, required_capability=[], energy_cost=1.0, ammo_cost=0)
        g.add_task("t2", actor="fleet_2", action="rendezvous", target="point_P",
                   duration_lb=0.5, required_capability=[], energy_cost=1.0, ammo_cost=0)
        g.add_dependency("t0", "t1", relation="sequence")
        g.add_dependency("t0", "t2", relation="sequence")
        g.add_group_sync_constraint(["t1", "t2"], tolerance=1.0)
        self.assertFalse(dag_exact_match(_gt_case(), g.build()))

    def test_sync_tolerance_mismatch_fails(self):
        g = _builder()
        g.add_dependency("t0", "t1", relation="sequence")
        g.add_dependency("t0", "t2", relation="sequence")
        g.add_group_sync_constraint(["t1", "t2"], tolerance=0.5)  # GT says 1.0
        self.assertFalse(dag_exact_match(_gt_case(), g.build()))

    def test_missing_sync_fails(self):
        g = _builder()
        g.add_dependency("t0", "t1", relation="sequence")
        g.add_dependency("t0", "t2", relation="sequence")
        self.assertFalse(dag_exact_match(_gt_case(), g.build()))

    def test_no_ground_truth_returns_none(self):
        trimmed = {"sample_id": "x", "expected_patterns": {"node_count": 3}}
        g = _builder()
        g.add_dependency("t0", "t1", relation="sequence")
        self.assertIsNone(dag_exact_match(trimmed, g.build()))

    def test_no_graph_with_ground_truth_is_false(self):
        self.assertFalse(dag_exact_match(_gt_case(), None))


if __name__ == "__main__":
    unittest.main()
