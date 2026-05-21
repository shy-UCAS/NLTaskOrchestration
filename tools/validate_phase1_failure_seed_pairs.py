"""
Validate paired Phase 1 failure seed JSONL files.

Usage:
    python tools/validate_phase1_failure_seed_pairs.py \
        datasets/phase1_failure_seed_structured_cases_v2.jsonl \
        datasets/phase1_failure_seed_standard_nl_cases_v2.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("structured_jsonl", type=Path)
    parser.add_argument("standard_nl_jsonl", type=Path)
    args = parser.parse_args()

    errors = validate_pair_files(args.structured_jsonl, args.standard_nl_jsonl)
    if errors:
        print("[INVALID] Phase 1 failure seed pair validation failed")
        for error in errors:
            print(f"- {error}")
        return 1

    structured = _load_jsonl(args.structured_jsonl)
    standard = _load_jsonl(args.standard_nl_jsonl)
    print(
        "[VALID] Phase 1 failure seed pairs passed: "
        f"{len(structured)} structured / {len(standard)} standard_nl"
    )
    return 0


def validate_pair_files(
    structured_jsonl: Path,
    standard_nl_jsonl: Path,
) -> list[str]:
    errors: list[str] = []
    try:
        structured = _load_jsonl(structured_jsonl)
    except Exception as exc:
        return [f"failed to read structured JSONL: {type(exc).__name__}: {exc}"]
    try:
        standard = _load_jsonl(standard_nl_jsonl)
    except Exception as exc:
        return [f"failed to read standard NL JSONL: {type(exc).__name__}: {exc}"]

    structured_by_key = _index_by_pair_key(structured, "1a_", errors)
    standard_by_key = _index_by_pair_key(standard, "1b_", errors)

    structured_keys = set(structured_by_key)
    standard_keys = set(standard_by_key)
    for key in sorted(structured_keys - standard_keys):
        errors.append(f"missing standard NL pair for key: {key}")
    for key in sorted(standard_keys - structured_keys):
        errors.append(f"missing structured pair for key: {key}")

    for key in sorted(structured_keys & standard_keys):
        structured_record = structured_by_key[key]
        standard_record = standard_by_key[key]
        _validate_pair(key, structured_record, standard_record, errors)
    return errors


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{lineno}: record must be an object")
            records.append(record)
    return records


def _index_by_pair_key(
    records: list[dict[str, Any]],
    prefix: str,
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    indexed = {}
    for record in records:
        sample_id = record.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id:
            errors.append(f"record missing non-empty sample_id: {record}")
            continue
        if not sample_id.startswith(prefix):
            errors.append(f"sample_id {sample_id!r} does not start with {prefix!r}")
            continue
        key = sample_id[len(prefix):]
        if key in indexed:
            errors.append(f"duplicate pair key {key!r} from sample_id {sample_id!r}")
            continue
        indexed[key] = record
    return indexed


def _validate_pair(
    key: str,
    structured: dict[str, Any],
    standard: dict[str, Any],
    errors: list[str],
) -> None:
    if structured.get("expected_result") != standard.get("expected_result"):
        errors.append(
            f"{key}: expected_result mismatch: "
            f"{structured.get('expected_result')!r} != {standard.get('expected_result')!r}"
        )
    if structured.get("expected_patterns") != standard.get("expected_patterns"):
        errors.append(f"{key}: expected_patterns mismatch")

    input_payload = structured.get("input_payload")
    if not isinstance(input_payload, dict):
        errors.append(f"{key}: structured input_payload must be an object")
        return
    segment_id = input_payload.get("segment_id")
    instruction = standard.get("standard_instruction")
    if not isinstance(segment_id, str) or not segment_id:
        errors.append(f"{key}: structured input_payload.segment_id is missing")
    elif not isinstance(instruction, str) or segment_id not in instruction:
        errors.append(f"{key}: standard_instruction does not mention {segment_id!r}")

    if not isinstance(input_payload.get("tasks"), list) or not input_payload["tasks"]:
        errors.append(f"{key}: structured input_payload.tasks must be a non-empty list")
    if not isinstance(input_payload.get("relations"), list):
        errors.append(f"{key}: structured input_payload.relations must be a list")
    if not isinstance(input_payload.get("constraints"), list):
        errors.append(f"{key}: structured input_payload.constraints must be a list")


if __name__ == "__main__":
    raise SystemExit(main())
