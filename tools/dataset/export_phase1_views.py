from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from tools.dataset.common import expected_patterns_from_graph, load_jsonl, write_jsonl


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--master",
        type=Path,
        default=Path("datasets") / "v2" / "phase1_master_cases.jsonl",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("datasets") / "generated",
    )
    args = parser.parse_args()

    records = load_jsonl(args.master)
    standard = [_standard_view(case) for case in records if case.get("case_type") in {"standard_complete", "verification_stress"}]
    normalization = [_normalization_view(case) for case in records if case.get("case_type") in {"raw_complete", "raw_incomplete"}]
    raw_pipeline = [_raw_to_gcjp_view(case) for case in records if case.get("case_type") in {"raw_complete", "raw_incomplete"}]
    contract = [_contract_view(case) for case in records if case.get("case_type") == "adversarial_contract"]

    outputs = {
        "phase1_standard_nl_cases.v2.jsonl": standard,
        "phase1_instruction_normalization_eval.v2.jsonl": normalization,
        "phase1_raw_to_gcjp_pipeline.v2.jsonl": raw_pipeline,
        "phase1_01k_contract_adversarial.v2.jsonl": contract,
    }
    for name, view_records in outputs.items():
        count = write_jsonl(args.out_dir / name, view_records)
        print(f"[export] {name}: {count}")
    return 0


def _standard_view(case: dict[str, Any]) -> dict[str, Any]:
    expected_graph = case.get("expected_graph", {}) or {}
    expected_verification = case.get("expected_verification", {}) or {}
    return {
        "sample_id": case["sample_id"],
        "standard_instruction": case.get("standard_instruction"),
        "expected_result": expected_verification.get("expected_result", "unknown"),
        "expected_patterns": expected_patterns_from_graph(expected_graph),
        "tags": case.get("tags", []),
    }


def _normalization_view(case: dict[str, Any]) -> dict[str, Any]:
    normalization = case.get("normalization") or {}
    return {
        "sample_id": case["sample_id"],
        "raw_instruction": case.get("raw_instruction"),
        "expected_status": normalization.get("expected_status"),
        "expected_missing_fields": normalization.get("expected_missing_fields", []),
        "expected_ambiguity_spans": normalization.get("expected_ambiguity_spans", []),
        "scripted_clarifications": normalization.get("scripted_clarifications", []),
        "expected_status_after_clarification": normalization.get(
            "expected_status_after_clarification"
        ),
        "tags": case.get("tags", []),
    }


def _raw_to_gcjp_view(case: dict[str, Any]) -> dict[str, Any]:
    record = _normalization_view(case)
    record["standard_instruction"] = (
        case.get("standard_instruction")
        or (case.get("normalization") or {}).get("standard_instruction_after_clarification")
    )
    record["expected_result"] = (case.get("expected_verification") or {}).get(
        "expected_result", "unknown"
    )
    record["expected_patterns"] = expected_patterns_from_graph(case.get("expected_graph", {}) or {})
    return record


def _contract_view(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_id": case["sample_id"],
        "generation_contract": case.get("generation_contract", {}),
        "expected_result": (case.get("expected_verification") or {}).get(
            "expected_result", "unknown"
        ),
        "tags": case.get("tags", []),
    }


if __name__ == "__main__":
    raise SystemExit(main())
