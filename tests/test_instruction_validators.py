import json
import unittest

from agents.instruction_normalizer_agent import _build_result
from agents.instruction_validators import (
    resolve_action,
    validate_normalization_contract,
)
from agents.json_extraction import extract_json_object
from agents.llm_client import LLMResponse


def _candidate(
    *,
    action: str = "jam",
    actor: str = "fleet_7",
    target: str = "site_alpha",
    relations: list[dict] | None = None,
) -> dict:
    return {
        "status": "complete",
        "standard_instruction": "normalized",
        "resolved_fields": {
            "segment_id": "seg_test",
            "assigned_actors": [actor],
            "tasks": [
                {
                    "task_id": "t1",
                    "actor": actor,
                    "action": action,
                    "target": target,
                }
            ],
            "relations": relations or [],
            "constraints": [],
        },
        "missing_fields": [],
        "ambiguities": [],
    }


class InstructionValidatorTests(unittest.TestCase):
    def test_resolve_action_unique_and_ambiguous(self) -> None:
        self.assertEqual(resolve_action("电子压制")["action"], "jam")
        self.assertEqual(resolve_action("压制")["status"], "ambiguous")

    def test_bare_pressure_tightens_complete_to_action_incomplete(self) -> None:
        result = validate_normalization_contract(
            _candidate(action="jam", target="site_amber"),
            raw_instruction="fleet_7 压制 site_amber。",
        )

        self.assertFalse(result.ok)
        self.assertIn("action", result.missing_fields)

    def test_qualified_pressure_is_allowed(self) -> None:
        result = validate_normalization_contract(
            _candidate(action="jam", target="site_amber"),
            raw_instruction="fleet_7 电子压制 site_amber。",
        )

        self.assertTrue(result.ok)

    def test_invalid_actor_and_target_are_flagged(self) -> None:
        result = validate_normalization_contract(
            _candidate(actor="fleet_99", target="前沿通信节点"),
            raw_instruction="fleet_99 侦察 前沿通信节点。",
        )

        self.assertIn("assigned_actors", result.missing_fields)
        self.assertIn("target", result.missing_fields)

    def test_normalizer_build_result_applies_contract_override(self) -> None:
        parsed = _candidate(action="jam", target="site_amber")
        response = LLMResponse(
            text=json.dumps(parsed, ensure_ascii=False),
            raw={},
            model="test-model",
            model_source="test",
            provider={},
        )

        result = _build_result(
            "sample",
            "prompt",
            response,
            extract_json_object(response.text),
            0,
            raw_instruction="fleet_7 压制 site_amber。",
        )

        self.assertEqual(result.model_reported_status, "complete")
        self.assertEqual(result.status, "incomplete")
        self.assertTrue(result.status_overridden_by_invariant)
        self.assertIn("action", result.missing_fields)
        self.assertEqual(result.parsed_output["standard_instruction"], None)


if __name__ == "__main__":
    unittest.main()
