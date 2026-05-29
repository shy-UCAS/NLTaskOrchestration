"""
Validate Phase 1F instruction-normalization JSONL datasets.

This is a lightweight static check; it does not call an LLM.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


REQUIRED_KEYS = {
    "sample_id",
    "raw_instruction",
    "expected_status",
    "expected_missing_fields",
    "expected_ambiguity_spans",
    "scripted_clarifications",
    "expected_status_after_clarification",
    "tags",
}

CANONICAL_MISSING_FIELDS = {
    "assigned_actors",
    "target",
    "action",
    "relation",
    "condition",
    "split_assignment",
}

ARTIFICIAL_PARAMETER_PHRASES = (
    "能量消耗",
    "弹药消耗",
    "能量上限",
    "弹药上限",
    "资源上限",
)

FLEET_ID_RE = re.compile(r"\bfleet_\d+\b")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--eval",
        dest="eval_path",
        type=Path,
        default=Path("datasets") / "phase1_instruction_normalization_eval.jsonl",
    )
    parser.add_argument(
        "--dev",
        dest="dev_path",
        type=Path,
        default=Path("datasets") / "phase1_instruction_normalization_dev.jsonl",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        default=Path("prompts") / "instruction_normalization_prompt.md",
    )
    parser.add_argument(
        "--capability-model",
        type=Path,
        default=Path("configs") / "capability_model.yaml",
    )
    args = parser.parse_args()

    errors: list[str] = []
    dev_cases = _load_jsonl(args.dev_path, errors)
    eval_cases = _load_jsonl(args.eval_path, errors)
    known_actors = _load_capability_actors(args.capability_model, errors)

    _validate_cases(
        args.dev_path, dev_cases, errors,
        require_eval_tags=False,
        known_actors=known_actors,
    )
    _validate_cases(
        args.eval_path, eval_cases, errors,
        require_eval_tags=True,
        known_actors=known_actors,
    )
    _validate_prompt_leakage(args.prompt, eval_cases, errors)

    if errors:
        for err in errors:
            print(f"[ERROR] {err}")
        return 1

    print(
        "instruction normalization datasets ok: "
        f"dev={len(dev_cases)} eval={len(eval_cases)}"
    )
    return 0


def _load_jsonl(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    if not path.exists():
        errors.append(f"{path}: file not found")
        return cases

    with path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append(f"{path}:{line_num}: invalid JSON: {exc}")
                continue
            if not isinstance(data, dict):
                errors.append(f"{path}:{line_num}: JSONL row must be an object")
                continue
            data["_line_num"] = line_num
            cases.append(data)
    return cases


def _validate_cases(
    path: Path,
    cases: list[dict[str, Any]],
    errors: list[str],
    *,
    require_eval_tags: bool,
    known_actors: set[str],
) -> None:
    seen: set[str] = set()
    for case in cases:
        line_num = case.get("_line_num", "?")
        sid = str(case.get("sample_id", f"<line {line_num}>"))
        prefix = f"{path}:{line_num} ({sid})"

        missing_keys = REQUIRED_KEYS - set(case)
        if missing_keys:
            errors.append(f"{prefix}: missing keys {sorted(missing_keys)}")

        if sid in seen:
            errors.append(f"{prefix}: duplicate sample_id")
        seen.add(sid)

        status = case.get("expected_status")
        if status not in {"complete", "incomplete"}:
            errors.append(f"{prefix}: invalid expected_status {status!r}")

        tags = case.get("tags")
        if not isinstance(tags, list):
            errors.append(f"{prefix}: tags must be a list")
        elif require_eval_tags and "eval" not in tags:
            errors.append(f"{prefix}: eval dataset rows must include tag 'eval'")

        expected_missing = case.get("expected_missing_fields")
        if not isinstance(expected_missing, list):
            errors.append(f"{prefix}: expected_missing_fields must be a list")
        else:
            invalid_fields = [
                field for field in expected_missing
                if field not in CANONICAL_MISSING_FIELDS
            ]
            if invalid_fields:
                errors.append(
                    f"{prefix}: non-canonical missing fields {invalid_fields}"
                )

        raw_instruction = case.get("raw_instruction")
        if not isinstance(raw_instruction, str) or not raw_instruction.strip():
            errors.append(f"{prefix}: raw_instruction must be non-empty text")
        else:
            _validate_realistic_instruction(prefix, raw_instruction, errors)
            _validate_actor_references(
                prefix,
                raw_instruction,
                errors,
                known_actors=known_actors,
            )

        clarifications = case.get("scripted_clarifications")
        if not isinstance(clarifications, list):
            errors.append(f"{prefix}: scripted_clarifications must be a list")
        elif status == "incomplete" and not clarifications:
            errors.append(f"{prefix}: incomplete rows need scripted clarifications")
        elif clarifications:
            for idx, clarification in enumerate(clarifications, start=1):
                if not isinstance(clarification, str):
                    errors.append(
                        f"{prefix}: clarification {idx} must be a string"
                    )
                    continue
                _validate_actor_references(
                    f"{prefix} clarification {idx}",
                    clarification,
                    errors,
                    known_actors=known_actors,
                )


def _validate_realistic_instruction(
    prefix: str,
    raw_instruction: str,
    errors: list[str],
) -> None:
    found_phrases = [
        phrase for phrase in ARTIFICIAL_PARAMETER_PHRASES
        if phrase in raw_instruction
    ]
    if found_phrases:
        errors.append(
            f"{prefix}: raw instruction contains artificial parameter phrases "
            f"{found_phrases}"
        )


def _validate_actor_references(
    prefix: str,
    text: str,
    errors: list[str],
    *,
    known_actors: set[str],
) -> None:
    if not known_actors:
        return
    for actor in sorted(set(FLEET_ID_RE.findall(text))):
        if actor not in known_actors:
            errors.append(
                f"{prefix}: actor {actor!r} not defined in capability_model"
            )


def _load_capability_actors(path: Path, errors: list[str]) -> set[str]:
    if not path.exists():
        errors.append(f"{path}: file not found")
        return set()
    try:
        import yaml
    except ImportError as exc:
        errors.append(f"{path}: PyYAML is required: {exc}")
        return set()

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        errors.append(f"{path}: failed to parse YAML: {exc}")
        return set()
    fleets = raw.get("fleets") or {}
    if not isinstance(fleets, dict):
        errors.append(f"{path}: fleets must be a mapping")
        return set()
    return {str(actor) for actor in fleets}


def _validate_prompt_leakage(
    prompt_path: Path,
    eval_cases: list[dict[str, Any]],
    errors: list[str],
) -> None:
    if not prompt_path.exists():
        errors.append(f"{prompt_path}: file not found")
        return

    prompt_text = prompt_path.read_text(encoding="utf-8")
    for case in eval_cases:
        raw = case.get("raw_instruction")
        if not isinstance(raw, str):
            continue
        if raw and raw in prompt_text:
            errors.append(
                f"{prompt_path}: eval raw instruction leaked into prompt: "
                f"{case.get('sample_id')}"
            )


if __name__ == "__main__":
    raise SystemExit(main())
