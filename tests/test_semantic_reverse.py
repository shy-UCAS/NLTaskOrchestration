"""Unit tests for verifier/semantic_reverse.py (reverse-verbalization checker)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verifier.semantic_reverse import (  # noqa: E402
    check_case,
    parse_tier2_response,
    tier1_check,
    tier2_check,
    verbalize,
)


def _seq_plan() -> dict:
    """A clean two-step sequence plan matching its instruction below."""
    return {
        "plan_id": "seg_demo",
        "participants": [{"actor_id": "fleet_1", "type": "fleet"}],
        "tasks": [
            {"task_id": "t1_recon_area_a", "actor": "fleet_1", "action": "reconnaissance",
             "target": "area_a", "condition": None, "time_window": None, "metadata": {}},
            {"task_id": "t2_strike_target_a", "actor": "fleet_1", "action": "strike",
             "target": "target_a", "condition": None,
             "time_window": {"deadline": 10.0}, "metadata": {}},
        ],
        "relations": [
            {"source": "t1_recon_area_a", "target": "t2_strike_target_a",
             "type": "sequence", "sync_tolerance": None, "condition": None},
        ],
        "global_constraints": {},
        "explicit_constraints": [],
    }


_SEQ_NL = (
    "fleet_1 first performs reconnaissance on area_a as task t1_recon_area_a. "
    "Then fleet_1 performs strike on target_a as task t2_strike_target_a. "
    "Add a sequence dependency and a deadline of 10.0 for t2_strike_target_a."
)


class TestVerbalize(unittest.TestCase):
    def test_renders_tasks_and_relation(self) -> None:
        text = verbalize(_seq_plan())
        self.assertIn("fleet_1 performs reconnaissance on area_a as t1_recon_area_a", text)
        self.assertIn("--sequence-->", text)
        self.assertIn("deadline=10.0", text)

    def test_no_system_params_leak(self) -> None:
        text = verbalize(_seq_plan())
        for sysparam in ("duration", "energy", "ammo", "capability"):
            self.assertNotIn(sysparam, text.lower())

    def test_empty_plan(self) -> None:
        self.assertEqual(verbalize(None), "(no canonical_task_plan)")


class TestTier1(unittest.TestCase):
    def test_clean_case_no_discrepancies(self) -> None:
        self.assertEqual(tier1_check(_seq_plan(), _SEQ_NL), [])

    def test_relation_mismatch_parallel_vs_then(self) -> None:
        # NL says "simultaneously" (parallel) but the plan encodes only sequence.
        nl = (
            "fleet_1 performs reconnaissance on area_a as t1_recon_area_a and "
            "simultaneously performs strike on target_a as t2_strike_target_a. "
            "deadline of 10.0 for t2_strike_target_a."
        )
        disc = tier1_check(_seq_plan(), nl)
        kinds = {d.kind for d in disc}
        self.assertIn("relation_mismatch", kinds)
        strong = [d for d in disc if d.kind == "relation_mismatch"]
        self.assertEqual(strong[0].severity, "strong")
        self.assertIn("parallel", strong[0].nl_implies)

    def test_target_typo_flagged(self) -> None:
        plan = _seq_plan()
        plan["tasks"][1]["target"] = "target_zzz"  # not mentioned in NL
        disc = tier1_check(plan, _SEQ_NL)
        self.assertIn("target_absent", {d.kind for d in disc})

    def test_missing_deadline_flagged(self) -> None:
        # Plan has deadline 99.0 but the NL only mentions 10.0.
        plan = _seq_plan()
        plan["tasks"][1]["time_window"] = {"deadline": 99.0}
        disc = tier1_check(plan, _SEQ_NL)
        self.assertIn("deadline_absent", {d.kind for d in disc})

    def test_deadline_int_vs_float_not_flagged(self) -> None:
        # Plan deadline 10.0 should match NL "10.0" (and "10").
        self.assertNotIn("deadline_absent", {d.kind for d in tier1_check(_seq_plan(), _SEQ_NL)})

    def test_resource_prose_not_flagged(self) -> None:
        """Gotcha: NL resource/capability prose maps to config, not plan -> never a drift."""
        plan = {
            "plan_id": "seg_res",
            "participants": [{"actor_id": "fleet_9", "type": "fleet"}],
            "tasks": [
                {"task_id": "t1_strike", "actor": "fleet_9", "action": "strike",
                 "target": "target_r1", "condition": None, "time_window": None, "metadata": {}},
                {"task_id": "t2_strike", "actor": "fleet_9", "action": "strike",
                 "target": "target_r2", "condition": None, "time_window": None, "metadata": {}},
                {"task_id": "t3_strike", "actor": "fleet_9", "action": "strike",
                 "target": "target_r3", "condition": None, "time_window": None, "metadata": {}},
            ],
            "relations": [
                {"source": "t1_strike", "target": "t2_strike", "type": "sequence",
                 "sync_tolerance": None, "condition": None},
                {"source": "t2_strike", "target": "t3_strike", "type": "sequence",
                 "sync_tolerance": None, "condition": None},
            ],
            "global_constraints": {},
            "explicit_constraints": [],
        }
        nl = (
            "fleet_9 strikes target_r1, then target_r2, then target_r3 in sequence "
            "(tasks t1_strike, t2_strike, t3_strike). "
            "fleet_9 carries enough ammunition for only two strikes."
        )
        disc = tier1_check(plan, nl)
        # The ammunition sentence must not produce any discrepancy; plan is consistent.
        self.assertEqual(disc, [], msg=f"unexpected drift: {[d.kind for d in disc]}")

    def test_after_keyword_satisfied_by_condition_trigger(self) -> None:
        """'After X is confirmed' is condition_trigger, not a missing sequence relation."""
        plan = {
            "plan_id": "seg_cond",
            "participants": [{"actor_id": "fleet_1", "type": "fleet"}],
            "tasks": [
                {"task_id": "t1_recon", "actor": "fleet_1", "action": "reconnaissance",
                 "target": "target_a", "condition": None, "time_window": None, "metadata": {}},
                {"task_id": "t2_strike", "actor": "fleet_1", "action": "strike",
                 "target": "target_a", "condition": "target_a_confirmed",
                 "time_window": None, "metadata": {}},
            ],
            "relations": [
                {"source": "t1_recon", "target": "t2_strike", "type": "condition_trigger",
                 "sync_tolerance": None, "condition": "target_a_confirmed"},
            ],
            "global_constraints": {},
            "explicit_constraints": [],
        }
        nl = (
            "fleet_1 performs reconnaissance on target_a as t1_recon. After condition "
            "target_a_confirmed, fleet_1 performs strike on target_a as t2_strike. "
            "Add a condition_trigger dependency from t1_recon to t2_strike."
        )
        self.assertNotIn("relation_mismatch", {d.kind for d in tier1_check(plan, nl)})

    def test_group_sync_satisfies_sync_keyword(self) -> None:
        """'synchronized' in NL is satisfied by an explicit group_sync constraint."""
        plan = {
            "plan_id": "seg_gs",
            "participants": [{"actor_id": f"fleet_{i}", "type": "fleet"} for i in (1, 2, 8)],
            "tasks": [
                {"task_id": "t1_rdv", "actor": "fleet_1", "action": "rendezvous",
                 "target": "point_s2", "condition": None, "time_window": None, "metadata": {}},
                {"task_id": "t2_rdv", "actor": "fleet_2", "action": "rendezvous",
                 "target": "point_s2", "condition": None, "time_window": None, "metadata": {}},
                {"task_id": "t3_rdv", "actor": "fleet_8", "action": "rendezvous",
                 "target": "point_s2", "condition": None, "time_window": None, "metadata": {}},
            ],
            "relations": [],
            "global_constraints": {},
            "explicit_constraints": [
                {"type": "group_sync", "task_ids": ["t1_rdv", "t2_rdv", "t3_rdv"],
                 "tolerance": 0.5, "mode": "start", "source_label": "gs"},
            ],
        }
        nl = (
            "fleet_1, fleet_2 and fleet_8 each perform rendezvous at point_s2 "
            "(tasks t1_rdv, t2_rdv, t3_rdv). The three formations must start "
            "synchronized within a tolerance of 0.5."
        )
        self.assertNotIn("relation_mismatch", {d.kind for d in tier1_check(plan, nl)})


class TestTier2Parsing(unittest.TestCase):
    def test_consistent_response_empty(self) -> None:
        result = parse_tier2_response('{"consistent": true, "discrepancies": []}')
        self.assertEqual(result, [])

    def test_discrepancies_mapped(self) -> None:
        raw = (
            '```json\n{"consistent": false, "discrepancies": ['
            '{"kind": "relation_mismatch", "locus": "t1->t2", '
            '"nl_implies": "parallel", "plan_has": "sequence", "detail": "x"}]}\n```'
        )
        result = parse_tier2_response(raw)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].kind, "relation_mismatch")
        self.assertEqual(result[0].tier, "llm")
        self.assertEqual(result[0].severity, "strong")

    def test_unparsable_returns_none(self) -> None:
        self.assertIsNone(parse_tier2_response("not json at all"))
        self.assertIsNone(parse_tier2_response(""))

    def test_tier2_check_degrades_on_client_error(self) -> None:
        class _BoomClient:
            def generate(self, messages):  # noqa: ANN001, ARG002
                raise RuntimeError("network down")

        result = tier2_check(_seq_plan(), _SEQ_NL, _BoomClient())
        self.assertIsNone(result)

    def test_tier2_check_with_stub_client(self) -> None:
        class _StubResp:
            text = '{"consistent": true, "discrepancies": []}'

        class _StubClient:
            def generate(self, messages):  # noqa: ANN001, ARG002
                return _StubResp()

        result = tier2_check(_seq_plan(), _SEQ_NL, _StubClient())
        self.assertEqual(result, [])


class TestCheckCase(unittest.TestCase):
    def test_clean_case_consistent(self) -> None:
        case = {
            "sample_id": "demo_001",
            "canonical_task_plan": _seq_plan(),
            "standard_instruction": _SEQ_NL,
        }
        rep = check_case(case)
        self.assertTrue(rep.consistent, msg=f"{[d.detail for d in rep.discrepancies]}")

    def test_raw_case_without_plan_is_not_drift(self) -> None:
        case = {"sample_id": "raw_001", "canonical_task_plan": None,
                "raw_instruction": "do something vague"}
        rep = check_case(case)
        self.assertTrue(rep.consistent)
        self.assertEqual(rep.discrepancies, [])


if __name__ == "__main__":
    unittest.main()
