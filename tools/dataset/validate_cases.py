from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

from gcjp.api_spec import VALID_CONSTRAINT_TYPES
from gcjp.task_plan_loader import (
    build_graph_from_task_plan,
    load_action_defaults_from_yaml,
    load_capability_model_from_yaml,
)
from tools.dataset.common import (
    build_task_plan_for_loader,
    has_system_param_fields,
    load_jsonl,
)
from verifier.pipeline import VerificationPipeline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("datasets") / "v2" / "phase1_master_cases.jsonl",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path("schemas") / "phase1_master_case_schema.json",
    )
    parser.add_argument(
        "--action-templates",
        type=Path,
        default=Path("configs") / "action_templates.yaml",
    )
    parser.add_argument(
        "--capability-model",
        type=Path,
        default=Path("configs") / "capability_model.yaml",
    )
    parser.add_argument(
        "--allow-draft",
        action="store_true",
        help="Downgrade raw-case missing canonical_task_plan diagnostics to warnings.",
    )
    parser.add_argument(
        "--skip-z3",
        action="store_true",
        help="Skip deterministic graph build and Z3 expected_result checks.",
    )
    args = parser.parse_args()

    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)
    cases = load_jsonl(args.dataset)
    action_defaults = load_action_defaults_from_yaml(args.action_templates)
    capability_model = load_capability_model_from_yaml(args.capability_model)

    errors: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    pipeline = VerificationPipeline(z3_timeout_ms=10_000)

    for idx, case in enumerate(cases, start=1):
        prefix = f"{args.dataset}:{idx} ({case.get('sample_id', '<missing>')})"
        _validate_schema(prefix, case, validator, errors)
        _validate_unique_id(prefix, case, seen, errors)
        _validate_case_contract(prefix, case, errors, warnings, allow_draft=args.allow_draft)
        _validate_plan_static(prefix, case, action_defaults, capability_model, errors)
        _validate_expected_graph(prefix, case, errors)
        if not args.skip_z3:
            _validate_z3(prefix, case, action_defaults, capability_model, pipeline, errors)

    for warning in warnings:
        print(f"[WARN] {warning}")
    for error in errors:
        print(f"[ERROR] {error}")
    print(
        f"[validate] dataset={args.dataset} cases={len(cases)} "
        f"errors={len(errors)} warnings={len(warnings)}"
    )
    return 1 if errors else 0


def _validate_schema(
    prefix: str,
    case: dict[str, Any],
    validator: Draft7Validator,
    errors: list[str],
) -> None:
    for error in sorted(validator.iter_errors(case), key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in error.path) or "<root>"
        errors.append(f"{prefix}: schema {path}: {error.message}")


def _validate_unique_id(
    prefix: str,
    case: dict[str, Any],
    seen: set[str],
    errors: list[str],
) -> None:
    sample_id = case.get("sample_id")
    if not isinstance(sample_id, str) or not sample_id:
        errors.append(f"{prefix}: sample_id must be non-empty")
        return
    if sample_id in seen:
        errors.append(f"{prefix}: duplicate sample_id")
    seen.add(sample_id)


def _validate_case_contract(
    prefix: str,
    case: dict[str, Any],
    errors: list[str],
    warnings: list[str],
    *,
    allow_draft: bool,
) -> None:
    case_type = case.get("case_type")
    plan = case.get("canonical_task_plan")
    standard_instruction = case.get("standard_instruction")
    raw_instruction = case.get("raw_instruction")
    normalization = case.get("normalization")

    if case_type == "standard_complete":
        if not isinstance(standard_instruction, str) or not standard_instruction.strip():
            errors.append(f"{prefix}: standard_complete requires standard_instruction")
        if plan is None:
            errors.append(f"{prefix}: standard_complete requires canonical_task_plan")
        if raw_instruction is not None:
            warnings.append(f"{prefix}: standard_complete raw_instruction is usually null")
    elif case_type in {"raw_complete", "raw_incomplete"}:
        if not isinstance(raw_instruction, str) or not raw_instruction.strip():
            errors.append(f"{prefix}: raw case requires raw_instruction")
        if not isinstance(normalization, dict):
            errors.append(f"{prefix}: raw case requires normalization object")
        if plan is None:
            message = f"{prefix}: raw case canonical_task_plan not backfilled yet"
            if allow_draft:
                warnings.append(message)
            else:
                errors.append(message)
        if case_type == "raw_incomplete":
            clarifications = (normalization or {}).get("scripted_clarifications", [])
            if not clarifications:
                errors.append(f"{prefix}: raw_incomplete requires scripted_clarifications")
    elif case_type == "adversarial_contract":
        if not case.get("generation_contract"):
            errors.append(f"{prefix}: adversarial_contract requires generation_contract")


def _validate_plan_static(
    prefix: str,
    case: dict[str, Any],
    action_defaults: dict[str, dict[str, Any]],
    capability_model: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    plan = case.get("canonical_task_plan")
    if plan is None:
        return

    system_fields = has_system_param_fields(plan.get("tasks", []))
    if system_fields:
        errors.append(f"{prefix}: canonical_task_plan.tasks contains system params {system_fields}")

    participants = {item.get("actor_id") for item in plan.get("participants", []) or []}
    task_ids: set[str] = set()
    for task in plan.get("tasks", []) or []:
        task_id = task.get("task_id")
        if task_id in task_ids:
            errors.append(f"{prefix}: duplicate task_id {task_id!r}")
        task_ids.add(task_id)
        actor = task.get("actor")
        action = task.get("action")
        if actor not in participants:
            errors.append(f"{prefix}: task {task_id!r} actor {actor!r} not in participants")
        if actor not in capability_model:
            errors.append(f"{prefix}: actor {actor!r} not found in capability_model")
        if action not in action_defaults:
            errors.append(f"{prefix}: action {action!r} not found in action_templates")

    for rel in plan.get("relations", []) or []:
        for endpoint in ("source", "target"):
            if rel.get(endpoint) not in task_ids:
                errors.append(
                    f"{prefix}: relation {endpoint} {rel.get(endpoint)!r} not found in tasks"
                )
    for constraint in plan.get("explicit_constraints", []) or []:
        _validate_constraint_refs(prefix, constraint, task_ids, participants, errors)


def _validate_constraint_refs(
    prefix: str,
    constraint: dict[str, Any],
    task_ids: set[str],
    participants: set[str],
    errors: list[str],
) -> None:
    ctype = constraint.get("type")
    if ctype in {"physical_feasibility", "time_window", "capability"}:
        task_id = constraint.get("task_id")
        if task_id not in task_ids:
            errors.append(f"{prefix}: {ctype} task_id {task_id!r} not found in tasks")
    if ctype == "group_sync":
        for task_id in constraint.get("task_ids", []) or []:
            if task_id not in task_ids:
                errors.append(f"{prefix}: group_sync task_id {task_id!r} not found in tasks")
    if ctype == "resource":
        actor = constraint.get("actor")
        if actor not in participants:
            errors.append(f"{prefix}: resource actor {actor!r} not in participants")


def _validate_expected_graph(
    prefix: str,
    case: dict[str, Any],
    errors: list[str],
) -> None:
    plan = case.get("canonical_task_plan")
    expected = case.get("expected_graph") or {}
    if plan is None:
        return
    tasks = plan.get("tasks", []) or []
    relations = plan.get("relations", []) or []
    if expected.get("node_count") != len(tasks):
        errors.append(f"{prefix}: expected_graph.node_count mismatch")
    if expected.get("edge_count") != len(relations):
        errors.append(f"{prefix}: expected_graph.edge_count mismatch")

    expected_nodes = {
        (node.get("task_id"), node.get("actor"), node.get("action"), node.get("target"))
        for node in expected.get("nodes", []) or []
    }
    actual_nodes = {
        (task.get("task_id"), task.get("actor"), task.get("action"), task.get("target"))
        for task in tasks
    }
    if expected_nodes != actual_nodes:
        errors.append(f"{prefix}: expected_graph.nodes mismatch")

    expected_edges = {
        (edge.get("source"), edge.get("target"), edge.get("relation"))
        for edge in expected.get("edges", []) or []
    }
    actual_edges = {
        (rel.get("source"), rel.get("target"), rel.get("type"))
        for rel in relations
    }
    if expected_edges != actual_edges:
        errors.append(f"{prefix}: expected_graph.edges mismatch")

    # Constraint-type tokens must be real verifier constraint types. Catches mislabels
    # like "condition" (a condition_trigger edge attribute, not a graph constraint) that
    # would make constraint_complete unsatisfiable downstream regardless of the model.
    verification = case.get("expected_verification") or {}
    for field_name, values in (
        ("expected_graph.constraint_types", expected.get("constraint_types") or []),
        ("expected_verification.z3_relevant_constraints", verification.get("z3_relevant_constraints") or []),
    ):
        for ctype in values:
            if ctype not in VALID_CONSTRAINT_TYPES:
                errors.append(
                    f"{prefix}: {field_name} contains invalid constraint_type {ctype!r} "
                    f"(valid: {sorted(VALID_CONSTRAINT_TYPES)})"
                )


def _validate_z3(
    prefix: str,
    case: dict[str, Any],
    action_defaults: dict[str, dict[str, Any]],
    capability_model: dict[str, dict[str, Any]],
    pipeline: VerificationPipeline,
    errors: list[str],
) -> None:
    plan = case.get("canonical_task_plan")
    expected_result = (case.get("expected_verification") or {}).get("expected_result")
    if plan is None or expected_result not in {"sat", "unsat"}:
        return
    try:
        graph = build_graph_from_task_plan(
            build_task_plan_for_loader(plan),
            segment_id=plan.get("plan_id"),
            action_defaults=action_defaults,
            capability_model=capability_model,
        )
        report = pipeline.verify_graph(graph)
    except Exception as exc:
        errors.append(f"{prefix}: failed to build/verify canonical_task_plan: {type(exc).__name__}: {exc}")
        return

    # Realizability: every declared constraint_type must actually be emitted by the built
    # graph, otherwise constraint_complete can never pass for this case (expected ⊆ actual).
    expected_ctypes = (case.get("expected_graph") or {}).get("constraint_types") or []
    actual_ctypes = {c.constraint_type for c in graph.constraints}
    for ctype in expected_ctypes:
        if ctype in VALID_CONSTRAINT_TYPES and ctype not in actual_ctypes:
            errors.append(
                f"{prefix}: expected_graph.constraint_types {ctype!r} declared but not "
                f"emitted by the built graph (actual={sorted(actual_ctypes)})"
            )

    layer3 = None
    for layer in report.layers:
        if layer.layer == 3:
            layer3 = layer
            break
    actual = (layer3.details or {}).get("z3_result") if layer3 else None
    if actual != expected_result:
        errors.append(
            f"{prefix}: z3_result mismatch actual={actual!r} expected={expected_result!r}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
