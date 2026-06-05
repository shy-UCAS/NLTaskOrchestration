"""Author Phase 1 v2 master-schema cases from compact specs.

Two modes (see docs/phase1_dataset_standardization design plan section 10.3):

1. Template mode -- read one or more compact case specs from a YAML/JSON file,
   assemble full master cases, schema-validate, run a deterministic Z3 self-check,
   and append the passing cases to a JSONL dataset::

       python -m tools.dataset.make_case \
           --template tools/dataset/templates/sequence_recon_strike.yaml \
           --out datasets/v2/phase1_master_cases.jsonl

2. Interactive mode -- step through the same spec fields on the command line::

       python -m tools.dataset.make_case --interactive --out datasets/v2/new_cases.jsonl

A compact spec only carries task semantics; this tool fills the derived fields
(``expected_graph``, ``expected_verification``, ``difficulty``, ``language``,
``task_family``, ``generation_contract``) so authors never hand-write them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

from gcjp.task_plan_loader import (
    build_graph_from_task_plan,
    load_action_defaults_from_yaml,
    load_capability_model_from_yaml,
)
from tools.dataset.common import (
    build_task_plan_for_loader,
    convert_payload_to_canonical_plan,
    expected_graph_from_plan,
    has_system_param_fields,
    infer_difficulty,
    infer_language,
    load_jsonl,
    normalize_tags,
)
from verifier.pipeline import VerificationPipeline

DEFAULT_SCHEMA = Path("schemas") / "phase1_master_case_schema.json"
DEFAULT_ACTION_TEMPLATES = Path("configs") / "action_templates.yaml"
DEFAULT_CAPABILITY_MODEL = Path("configs") / "capability_model.yaml"

_TASK_FAMILY_IGNORE = {
    "standard_nl",
    "raw_nl",
    "contract",
    "structured",
    "migrated",
    "sat",
    "unsat",
    "complete",
    "incomplete",
    "eval",
    "dev",
    "easy",
    "medium",
    "hard",
}


class CaseError(ValueError):
    """Raised when a spec cannot be assembled into a valid master case."""


# --------------------------------------------------------------------------- #
# Spec -> master case assembly
# --------------------------------------------------------------------------- #
def assemble_case(spec: dict[str, Any]) -> dict[str, Any]:
    """Build a full master-schema case from a compact author spec."""
    sample_id = str(spec.get("sample_id") or "").strip()
    if not sample_id:
        raise CaseError("spec requires a non-empty sample_id")

    case_type = str(spec.get("case_type", "standard_complete"))
    split = str(spec.get("split", "dev"))

    plan = _build_plan(spec, sample_id)
    constraint_hint = {"constraint_types": list(spec.get("constraint_types", []) or [])}
    expected_graph = expected_graph_from_plan(plan, constraint_hint)

    tags = normalize_tags(spec.get("tags", []))
    expected_result = str(spec.get("expected_result", "unknown"))
    if expected_result not in {"sat", "unsat", "unknown"}:
        raise CaseError(f"{sample_id}: expected_result must be sat|unsat|unknown")

    case = {
        "schema_version": "phase1_v2",
        "sample_id": sample_id,
        "split": split,
        "case_type": case_type,
        "difficulty": str(spec.get("difficulty") or infer_difficulty(plan, tags)),
        "task_family": _task_family(spec, tags),
        "language": str(
            spec.get("language")
            or infer_language(spec.get("standard_instruction") or spec.get("raw_instruction"))
        ),
        "raw_instruction": spec.get("raw_instruction"),
        "standard_instruction": spec.get("standard_instruction"),
        "normalization": _normalization(spec, case_type),
        "canonical_task_plan": plan,
        "expected_graph": expected_graph,
        "expected_verification": _expected_verification(spec, expected_result, expected_graph),
        "generation_contract": _generation_contract(spec),
        "tags": tags,
        "source_refs": list(spec.get("source_refs", []) or []),
        "notes": str(spec.get("notes", "")),
    }
    return case


def _build_plan(spec: dict[str, Any], sample_id: str) -> dict[str, Any] | None:
    tasks = spec.get("tasks")
    if not tasks:
        # Raw cases may legitimately defer the plan; keep it null for now.
        return None

    payload = {
        "plan_id": str(spec.get("plan_id") or f"seg_{sample_id}"),
        "assigned_actors": [str(a) for a in (spec.get("assigned_actors") or [])],
        "tasks": tasks,
        "relations": spec.get("relations", []) or [],
        "constraints": spec.get("explicit_constraints", []) or [],
    }
    plan = convert_payload_to_canonical_plan(payload)

    # Preserve explicit participant typing when the author supplied it.
    participants = spec.get("participants")
    if participants:
        plan["participants"] = [
            {"actor_id": str(p["actor_id"]), "type": str(p.get("type", "fleet"))}
            for p in participants
        ]

    leaked = has_system_param_fields(plan.get("tasks", []))
    if leaked:
        raise CaseError(f"{sample_id}: tasks contain forbidden system params {leaked}")
    return plan


def _normalization(spec: dict[str, Any], case_type: str) -> dict[str, Any] | None:
    if case_type == "standard_complete":
        return spec.get("normalization")  # usually null
    norm = spec.get("normalization")
    if norm is None:
        return None
    # Backfill the required normalization keys so the schema is satisfied.
    return {
        "expected_status": norm.get("expected_status", "incomplete"),
        "expected_missing_fields": list(norm.get("expected_missing_fields", []) or []),
        "expected_ambiguity_spans": list(norm.get("expected_ambiguity_spans", []) or []),
        "scripted_clarifications": list(norm.get("scripted_clarifications", []) or []),
        "expected_status_after_clarification": norm.get("expected_status_after_clarification"),
        "standard_instruction_after_clarification": norm.get(
            "standard_instruction_after_clarification"
        ),
    }


def _expected_verification(
    spec: dict[str, Any],
    expected_result: str,
    expected_graph: dict[str, Any],
) -> dict[str, Any]:
    return {
        "expected_result": expected_result,
        "expected_unsat_reason": spec.get("expected_unsat_reason"),
        "expected_unsat_core_contains": list(spec.get("expected_unsat_core_contains", []) or []),
        "z3_relevant_constraints": list(
            spec.get("z3_relevant_constraints")
            or expected_graph.get("constraint_types", [])
            or []
        ),
        "notes": str(spec.get("verification_notes", "")),
    }


def _generation_contract(spec: dict[str, Any]) -> dict[str, Any]:
    override = spec.get("generation_contract") or {}
    return {
        "forbid_system_params": bool(override.get("forbid_system_params", True)),
        "forbid_resource_capability_calls": bool(
            override.get("forbid_resource_capability_calls", True)
        ),
        "expected_param_leak": bool(override.get("expected_param_leak", False)),
        "contract_profile": override.get("contract_profile", "gcjp_apifill_v1"),
    }


def _task_family(spec: dict[str, Any], tags: list[str]) -> list[str]:
    explicit = spec.get("task_family")
    if explicit:
        seen: list[str] = []
        for item in explicit:
            text = str(item)
            if text and text not in seen:
                seen.append(text)
        return seen
    result = [tag for tag in tags if tag not in _TASK_FAMILY_IGNORE]
    return result or ["uncategorized"]


# --------------------------------------------------------------------------- #
# Validation gate (schema + deterministic Z3 self-check)
# --------------------------------------------------------------------------- #
class CaseValidator:
    def __init__(
        self,
        schema_path: Path,
        action_templates: Path,
        capability_model: Path,
        *,
        run_z3: bool = True,
    ) -> None:
        self._validator = Draft7Validator(json.loads(schema_path.read_text(encoding="utf-8")))
        self._action_defaults = load_action_defaults_from_yaml(action_templates)
        self._capability_model = load_capability_model_from_yaml(capability_model)
        self._run_z3 = run_z3
        self._pipeline = VerificationPipeline(z3_timeout_ms=10_000) if run_z3 else None

    def check(self, case: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        for error in sorted(self._validator.iter_errors(case), key=lambda e: list(e.path)):
            path = ".".join(str(p) for p in error.path) or "<root>"
            errors.append(f"schema {path}: {error.message}")
        errors.extend(self._check_plan_refs(case))
        if self._run_z3 and not errors:
            errors.extend(self._check_z3(case))
        return errors

    def _check_plan_refs(self, case: dict[str, Any]) -> list[str]:
        plan = case.get("canonical_task_plan")
        if plan is None:
            return []
        errors: list[str] = []
        participants = {p.get("actor_id") for p in plan.get("participants", []) or []}
        task_ids: set[str] = set()
        for task in plan.get("tasks", []) or []:
            tid = task.get("task_id")
            if tid in task_ids:
                errors.append(f"duplicate task_id {tid!r}")
            task_ids.add(tid)
            actor, action = task.get("actor"), task.get("action")
            if actor not in participants:
                errors.append(f"task {tid!r} actor {actor!r} not in participants")
            if actor not in self._capability_model:
                errors.append(f"actor {actor!r} not in capability_model")
            if action not in self._action_defaults:
                errors.append(f"action {action!r} not in action_templates")
        for rel in plan.get("relations", []) or []:
            for endpoint in ("source", "target"):
                if rel.get(endpoint) not in task_ids:
                    errors.append(f"relation {endpoint} {rel.get(endpoint)!r} not in tasks")
        return errors

    def _check_z3(self, case: dict[str, Any]) -> list[str]:
        plan = case.get("canonical_task_plan")
        expected = (case.get("expected_verification") or {}).get("expected_result")
        if plan is None or expected not in {"sat", "unsat"}:
            return []
        try:
            graph = build_graph_from_task_plan(
                build_task_plan_for_loader(plan),
                segment_id=plan.get("plan_id"),
                action_defaults=self._action_defaults,
                capability_model=self._capability_model,
            )
            report = self._pipeline.verify_graph(graph)
        except Exception as exc:  # noqa: BLE001 - surfaced to the author verbatim
            return [f"failed to build/verify plan: {type(exc).__name__}: {exc}"]
        layer3 = next((layer for layer in report.layers if layer.layer == 3), None)
        actual = (layer3.details or {}).get("z3_result") if layer3 else None
        if actual != expected:
            return [f"z3_result mismatch actual={actual!r} expected={expected!r}"]
        return []


# --------------------------------------------------------------------------- #
# Spec loading (template + interactive)
# --------------------------------------------------------------------------- #
def load_specs_from_template(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        import yaml  # local import keeps json-only workflows dependency-free

        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if isinstance(data, dict) and "cases" in data:
        data = data["cases"]
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise CaseError(f"{path}: template must be a case object or a list of cases")
    return [dict(item) for item in data]


def collect_spec_interactive() -> dict[str, Any]:
    print("== make_case interactive ==  (blank = skip/default)")
    spec: dict[str, Any] = {}
    spec["sample_id"] = _ask("sample_id", required=True)
    spec["case_type"] = _ask("case_type [standard_complete]") or "standard_complete"
    spec["split"] = _ask("split [dev]") or "dev"
    spec["standard_instruction"] = _ask("standard_instruction") or None
    raw = _ask("raw_instruction")
    if raw:
        spec["raw_instruction"] = raw

    spec["plan_id"] = _ask("plan_id [seg_<sample_id>]") or None
    spec["tasks"] = _ask_tasks()
    spec["relations"] = _ask_relations()
    spec["explicit_constraints"] = _ask_constraints()

    ctypes = _ask("constraint_types (comma, e.g. time_order,resource)")
    if ctypes:
        spec["constraint_types"] = _split_csv(ctypes)
    spec["expected_result"] = _ask("expected_result [unknown]") or "unknown"
    reason = _ask("expected_unsat_reason")
    if reason:
        spec["expected_unsat_reason"] = reason
    tags = _ask("tags (comma)")
    if tags:
        spec["tags"] = _split_csv(tags)
    return {k: v for k, v in spec.items() if v not in (None, "", [])}


def _ask(label: str, *, required: bool = False) -> str:
    while True:
        value = input(f"  {label}: ").strip()
        if value or not required:
            return value
        print("    (required)")


def _split_csv(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def _ask_tasks() -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    print("  -- tasks (blank task_id to finish) --")
    while True:
        task_id = input("    task_id: ").strip()
        if not task_id:
            break
        task: dict[str, Any] = {
            "task_id": task_id,
            "actor": input("    actor: ").strip(),
            "action": input("    action: ").strip(),
            "target": input("    target: ").strip(),
        }
        condition = input("    condition [none]: ").strip()
        if condition:
            task["condition"] = condition
        deadline = input("    deadline [none]: ").strip()
        if deadline:
            task["time_window"] = {"deadline": float(deadline)}
        tasks.append(task)
    return tasks


def _ask_relations() -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
    print("  -- relations (blank source to finish) --")
    while True:
        source = input("    source: ").strip()
        if not source:
            break
        relations.append(
            {
                "source": source,
                "target": input("    target: ").strip(),
                "type": input("    type [sequence]: ").strip() or "sequence",
            }
        )
    return relations


def _ask_constraints() -> list[dict[str, Any]]:
    constraints: list[dict[str, Any]] = []
    print("  -- explicit_constraints (blank type to finish) --")
    while True:
        ctype = input("    type [e.g. resource]: ").strip()
        if not ctype:
            break
        if ctype == "resource":
            constraints.append(
                {
                    "type": "resource",
                    "actor": input("      actor: ").strip(),
                    "resource_type": input("      resource_type [ammo|energy_kwh]: ").strip(),
                    "max_value": float(input("      max_value: ").strip()),
                }
            )
        else:
            raw = input("      json body: ").strip()
            body = json.loads(raw) if raw else {}
            body["type"] = ctype
            constraints.append(body)
    return constraints


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def append_cases(out_path: Path, cases: list[dict[str, Any]]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8", newline="\n") as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")


def existing_sample_ids(out_path: Path) -> set[str]:
    if not out_path.exists():
        return set()
    return {str(case.get("sample_id")) for case in load_jsonl(out_path)}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--template", type=Path, help="YAML/JSON file with one or more case specs")
    source.add_argument("--interactive", action="store_true", help="Author one case via prompts")
    parser.add_argument("--out", type=Path, required=True, help="Destination JSONL (appended)")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--action-templates", type=Path, default=DEFAULT_ACTION_TEMPLATES)
    parser.add_argument("--capability-model", type=Path, default=DEFAULT_CAPABILITY_MODEL)
    parser.add_argument("--skip-z3", action="store_true", help="Skip the Z3 self-check gate")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Assemble and validate but do not write to --out",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Write even if a sample_id already exists in --out",
    )
    args = parser.parse_args()

    if args.interactive:
        specs = [collect_spec_interactive()]
    else:
        specs = load_specs_from_template(args.template)
    if not specs:
        print("[make_case] no specs found")
        return 1

    validator = CaseValidator(
        args.schema,
        args.action_templates,
        args.capability_model,
        run_z3=not args.skip_z3,
    )

    known_ids = existing_sample_ids(args.out)
    batch_ids: set[str] = set()
    accepted: list[dict[str, Any]] = []
    failed = 0

    for index, spec in enumerate(specs, start=1):
        label = spec.get("sample_id") or f"<spec #{index}>"
        try:
            case = assemble_case(spec)
        except CaseError as exc:
            print(f"[FAIL] {label}: {exc}")
            failed += 1
            continue

        sample_id = case["sample_id"]
        if sample_id in batch_ids:
            print(f"[FAIL] {sample_id}: duplicate sample_id within template batch")
            failed += 1
            continue
        if sample_id in known_ids and not args.force:
            print(f"[FAIL] {sample_id}: sample_id already exists in {args.out} (use --force)")
            failed += 1
            continue

        problems = validator.check(case)
        if problems:
            for problem in problems:
                print(f"[FAIL] {sample_id}: {problem}")
            failed += 1
            continue

        batch_ids.add(sample_id)
        accepted.append(case)
        print(f"[OK]   {sample_id} ({case['case_type']}, {case['expected_verification']['expected_result']})")

    if accepted and not args.dry_run:
        append_cases(args.out, accepted)
        print(f"[make_case] appended {len(accepted)} case(s) to {args.out}")
    elif accepted:
        print(f"[make_case] dry-run: {len(accepted)} case(s) passed, nothing written")

    if failed:
        print(f"[make_case] {failed} spec(s) rejected")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
