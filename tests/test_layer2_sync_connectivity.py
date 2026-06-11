"""Regression tests for Layer2 sync/group_sync connectivity consistency.

Verifies that standalone sync constraints (add_sync_constraint) and
group_sync constraints (add_group_sync_constraint) are both treated as
connectivity evidence in the isolated-node check, while truly isolated
multi-task graphs still fail.
"""
import unittest

from gcjp.mission_graph import TaskGraphBuilder
from verifier.pipeline import Layer2GraphVerifier


def _build_two_task_graph():
    """Return a builder with two rendezvous tasks, no edges or constraints."""
    g = TaskGraphBuilder(
        segment_id="seg_l2_sync_connectivity",
        assigned_actors=["fleet_1", "fleet_2"],
    )
    g.add_task(
        "t1", actor="fleet_1", action="rendezvous", target="point_A",
        duration_lb=1.0, required_capability=[], energy_cost=1.0, ammo_cost=0,
    )
    g.add_task(
        "t2", actor="fleet_2", action="rendezvous", target="point_A",
        duration_lb=1.0, required_capability=[], energy_cost=1.0, ammo_cost=0,
    )
    return g


class TestLayer2SyncConnectivity(unittest.TestCase):

    def test_standalone_sync_constraint_passes_l2(self):
        """add_sync_constraint alone (no edge) should NOT be judged isolated."""
        g = _build_two_task_graph()
        g.add_sync_constraint("t1", "t2", tolerance=0.5)
        graph = g.build()

        self.assertEqual(len(graph.edges), 0)
        self.assertTrue(
            any(c.constraint_type == "sync" for c in graph.constraints)
        )

        result = Layer2GraphVerifier().verify(graph)
        self.assertTrue(result.passed, f"L2 should pass but got: {result.error_msg}")

    def test_standalone_group_sync_constraint_passes_l2(self):
        """add_group_sync_constraint alone (no edge) should still pass L2."""
        g = _build_two_task_graph()
        g.add_group_sync_constraint(["t1", "t2"], tolerance=0.5)
        graph = g.build()

        self.assertEqual(len(graph.edges), 0)
        self.assertTrue(
            any(c.constraint_type == "group_sync" for c in graph.constraints)
        )

        result = Layer2GraphVerifier().verify(graph)
        self.assertTrue(result.passed, f"L2 should pass but got: {result.error_msg}")

    def test_add_dependency_sync_passes_l2(self):
        """add_dependency(relation='sync') builds edge + constraint, L2 passes."""
        g = _build_two_task_graph()
        g.add_dependency("t1", "t2", relation="sync", sync_tolerance=0.5)
        graph = g.build()

        self.assertEqual(len(graph.edges), 1)
        self.assertEqual(graph.edges[0].relation, "sync")
        self.assertTrue(
            any(c.constraint_type == "sync" for c in graph.constraints)
        )

        result = Layer2GraphVerifier().verify(graph)
        self.assertTrue(result.passed, f"L2 should pass but got: {result.error_msg}")

    def test_no_edge_no_sync_constraint_fails_l2(self):
        """Two tasks with no edges and no sync constraints should fail L2."""
        g = _build_two_task_graph()
        graph = g.build()

        self.assertEqual(len(graph.edges), 0)
        self.assertEqual(len(graph.constraints), 0)

        result = Layer2GraphVerifier().verify(graph)
        self.assertFalse(result.passed)
        self.assertIn("孤立节点", result.error_msg or "")


if __name__ == "__main__":
    unittest.main()
