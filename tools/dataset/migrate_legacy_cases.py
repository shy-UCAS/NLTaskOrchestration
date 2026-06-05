from __future__ import annotations

import argparse
import copy
import re
from pathlib import Path
from typing import Any

from tools.dataset.common import (
    convert_payload_to_canonical_plan,
    expected_graph_from_plan,
    infer_difficulty,
    infer_language,
    load_jsonl,
    normalize_tags,
    write_jsonl,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--standard",
        type=Path,
        default=Path("datasets") / "phase1_standard_nl_cases.jsonl",
    )
    parser.add_argument(
        "--normalization-dev",
        type=Path,
        default=Path("datasets") / "phase1_instruction_normalization_dev.jsonl",
    )
    parser.add_argument(
        "--normalization-eval",
        type=Path,
        default=Path("datasets") / "phase1_instruction_normalization_eval.jsonl",
    )
    parser.add_argument(
        "--structured",
        type=Path,
        action="append",
        default=[
            Path("datasets") / "phase1_structured_cases.jsonl",
            Path("datasets") / "phase1_failure_seed_structured_cases_v2.jsonl",
            Path("datasets") / "phase1_failure_seed_structured_cases.jsonl",
        ],
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("datasets") / "v2" / "phase1_master_cases.migrated_draft.jsonl",
    )
    args = parser.parse_args()

    structured_index = _load_structured_index(args.structured)
    structured_records = _load_structured_records(args.structured)
    records: list[dict[str, Any]] = []

    for case in load_jsonl(args.standard):
        records.append(
            _migrate_standard_case(
                case,
                source_path=args.standard,
                structured_index=structured_index,
                structured_records=structured_records,
            )
        )

    for split, path in (("dev", args.normalization_dev), ("eval", args.normalization_eval)):
        for case in load_jsonl(path):
            records.append(_migrate_normalization_case(case, source_path=path, split=split))

    count = write_jsonl(args.out, records)
    print(f"[migrate] wrote {count} master draft cases to {args.out}")
    return 0


def _load_structured_index(paths: list[Path]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for path in paths:
        if not path.exists():
            continue
        for record in load_jsonl(path):
            sample_id = str(record.get("sample_id", ""))
            for key in _structured_pair_keys(sample_id):
                indexed.setdefault(key, record)
    return indexed


def _load_structured_records(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        if path.exists():
            records.extend(load_jsonl(path))
    return records


def _structured_pair_keys(sample_id: str) -> list[str]:
    keys = [sample_id]
    if sample_id.startswith("1a_"):
        keys.append("1b_" + sample_id[len("1a_"):])
    if sample_id.startswith("1a_failseed"):
        keys.append("1b" + sample_id[len("1a"):])
    return keys


def _migrate_standard_case(
    case: dict[str, Any],
    *,
    source_path: Path,
    structured_index: dict[str, dict[str, Any]],
    structured_records: list[dict[str, Any]],
) -> dict[str, Any]:
    sample_id = str(case["sample_id"])
    structured = (
        structured_index.get(sample_id)
        or _find_structured_by_signature(case, structured_records)
    )
    manual_plan = _manual_standard_plan(case)
    plan = None
    notes = "migrated from standard NL legacy case"
    source_refs = [{"path": str(source_path), "sample_id": sample_id}]
    if structured and isinstance(structured.get("input_payload"), dict):
        plan = convert_payload_to_canonical_plan(structured["input_payload"])
        _align_plan_id_with_standard_instruction(plan, case.get("standard_instruction"))
        source_refs.append(
            {
                "path": _guess_structured_path(structured.get("sample_id", "")),
                "sample_id": str(structured.get("sample_id", "")),
            }
        )
    elif manual_plan is not None:
        plan = manual_plan
        notes += "; canonical_task_plan manually backfilled from standard_instruction"
    else:
        notes += "; canonical_task_plan requires manual backfill"

    expected_graph = expected_graph_from_plan(plan, case.get("expected_patterns", {}))
    expected_result = str(case.get("expected_result", "unknown"))
    return {
        "schema_version": "phase1_v2",
        "sample_id": sample_id,
        "split": "dev",
        "case_type": "standard_complete",
        "difficulty": infer_difficulty(plan, case.get("tags", [])),
        "task_family": _task_family_from_tags(case.get("tags", [])),
        "language": infer_language(case.get("standard_instruction")),
        "raw_instruction": None,
        "standard_instruction": case.get("standard_instruction"),
        "normalization": None,
        "canonical_task_plan": plan,
        "expected_graph": expected_graph,
        "expected_verification": _expected_verification(expected_result, expected_graph),
        "generation_contract": _generation_contract(),
        "tags": normalize_tags(case.get("tags", []), ["migrated", "standard_complete"]),
        "source_refs": source_refs,
        "notes": notes,
    }


def _find_structured_by_signature(
    standard_case: dict[str, Any],
    structured_records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    standard_patterns = standard_case.get("expected_patterns", {}) or {}
    standard_tags = set(standard_case.get("tags", []) or [])
    candidates = []
    for record in structured_records:
        if record.get("expected_result") != standard_case.get("expected_result"):
            continue
        patterns = record.get("expected_patterns", {}) or {}
        if _patterns_key(patterns) != _patterns_key(standard_patterns):
            continue
        tags = set(record.get("tags", []) or [])
        overlap = len((standard_tags - {"standard_nl", "sat", "unsat"}) & tags)
        candidates.append((overlap, record))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return copy.deepcopy(candidates[0][1])


def _patterns_key(patterns: dict[str, Any]) -> tuple[Any, tuple[str, ...], tuple[str, ...]]:
    return (
        patterns.get("node_count"),
        tuple(sorted(patterns.get("edge_relations", []) or [])),
        tuple(sorted(patterns.get("constraint_types", []) or [])),
    )


def _align_plan_id_with_standard_instruction(
    plan: dict[str, Any],
    standard_instruction: str | None,
) -> None:
    if not standard_instruction:
        return
    match = re.search(r"\bSegment\s+([A-Za-z0-9_]+)", standard_instruction)
    if match:
        plan["plan_id"] = match.group(1)


def _manual_standard_plan(case: dict[str, Any]) -> dict[str, Any] | None:
    sample_id = case.get("sample_id")
    if sample_id != "1b_010_condition_resource_unsat":
        return None
    return {
        "plan_id": "seg_1b_010",
        "participants": [{"actor_id": "fleet_9", "type": "fleet"}],
        "tasks": [
            {
                "task_id": "t1_recon_target_d",
                "actor": "fleet_9",
                "action": "reconnaissance",
                "target": "target_D",
                "condition": None,
                "time_window": None,
                "metadata": {},
            },
            {
                "task_id": "t2_strike_target_d",
                "actor": "fleet_9",
                "action": "strike",
                "target": "target_D",
                "condition": "target_D_confirmed",
                "time_window": None,
                "metadata": {},
            },
            {
                "task_id": "t3_strike_target_e",
                "actor": "fleet_9",
                "action": "strike",
                "target": "target_E",
                "condition": None,
                "time_window": None,
                "metadata": {},
            },
            {
                "task_id": "t4_strike_target_f",
                "actor": "fleet_9",
                "action": "strike",
                "target": "target_F",
                "condition": None,
                "time_window": None,
                "metadata": {},
            },
        ],
        "relations": [
            {
                "source": "t1_recon_target_d",
                "target": "t2_strike_target_d",
                "type": "condition_trigger",
                "sync_tolerance": None,
                "condition": "target_D_confirmed",
            },
            {
                "source": "t2_strike_target_d",
                "target": "t3_strike_target_e",
                "type": "sequence",
                "sync_tolerance": None,
                "condition": None,
            },
            {
                "source": "t3_strike_target_e",
                "target": "t4_strike_target_f",
                "type": "sequence",
                "sync_tolerance": None,
                "condition": None,
            },
        ],
        "global_constraints": {},
        "explicit_constraints": [],
    }


def _migrate_normalization_case(
    case: dict[str, Any],
    *,
    source_path: Path,
    split: str,
) -> dict[str, Any]:
    status = str(case.get("expected_status", "incomplete"))
    case_type = "raw_complete" if status == "complete" else "raw_incomplete"
    raw_instruction = case.get("raw_instruction")
    expected_graph = expected_graph_from_plan(None, {})
    return {
        "schema_version": "phase1_v2",
        "sample_id": str(case["sample_id"]),
        "split": split,
        "case_type": case_type,
        "difficulty": "unknown",
        "task_family": _task_family_from_tags(case.get("tags", [])),
        "language": infer_language(raw_instruction),
        "raw_instruction": raw_instruction,
        "standard_instruction": None,
        "normalization": {
            "expected_status": status,
            "expected_missing_fields": list(case.get("expected_missing_fields", []) or []),
            "expected_ambiguity_spans": list(case.get("expected_ambiguity_spans", []) or []),
            "scripted_clarifications": list(case.get("scripted_clarifications", []) or []),
            "expected_status_after_clarification": case.get(
                "expected_status_after_clarification"
            ),
            "standard_instruction_after_clarification": case.get(
                "standard_instruction_after_clarification"
            ),
        },
        "canonical_task_plan": None,
        "expected_graph": expected_graph,
        "expected_verification": _expected_verification("unknown", expected_graph),
        "generation_contract": _generation_contract(),
        "tags": normalize_tags(case.get("tags", []), ["migrated", case_type]),
        "source_refs": [{"path": str(source_path), "sample_id": str(case["sample_id"])}],
        "notes": "migrated from instruction-normalization legacy case; canonical_task_plan requires manual backfill",
    }


def _expected_verification(
    expected_result: str,
    expected_graph: dict[str, Any],
) -> dict[str, Any]:
    if expected_result not in {"sat", "unsat"}:
        expected_result = "unknown"
    return {
        "expected_result": expected_result,
        "expected_unsat_reason": None,
        "expected_unsat_core_contains": [],
        "z3_relevant_constraints": list(expected_graph.get("constraint_types", []) or []),
        "notes": "",
    }


def _generation_contract() -> dict[str, Any]:
    return {
        "forbid_system_params": True,
        "forbid_resource_capability_calls": True,
        "expected_param_leak": False,
        "contract_profile": "gcjp_apifill_v1",
    }


def _task_family_from_tags(tags: list[str]) -> list[str]:
    ignored = {"standard_nl", "structured", "migrated", "sat", "unsat", "complete", "incomplete", "eval"}
    result = [str(tag) for tag in tags if str(tag) not in ignored]
    if not result:
        result = ["uncategorized"]
    return result


def _guess_structured_path(sample_id: str) -> str:
    if sample_id.startswith("1a_failseed_v2_"):
        return "datasets/phase1_failure_seed_structured_cases_v2.jsonl"
    if sample_id.startswith("1a_failseed_"):
        return "datasets/phase1_failure_seed_structured_cases.jsonl"
    return "datasets/phase1_structured_cases.jsonl"


if __name__ == "__main__":
    raise SystemExit(main())
