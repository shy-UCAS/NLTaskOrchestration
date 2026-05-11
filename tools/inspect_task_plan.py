"""
tools/inspect_task_plan.py

Inspect a standardized task plan JSON file.

Usage:
    python tools/inspect_task_plan.py demos/demo_01_simple_task_plan.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage:")
        print("  python tools/inspect_task_plan.py <task_plan_path>")
        return 1

    task_plan_path = Path(sys.argv[1])

    if not task_plan_path.exists():
        print(f"[ERROR] Task plan file not found: {task_plan_path}")
        return 1

    try:
        plan = load_json(task_plan_path)
    except json.JSONDecodeError as e:
        print(f"[ERROR] Invalid JSON: {e}")
        return 1

    print("=" * 60)
    print("Task Plan Inspection")
    print("=" * 60)

    print(f"Plan ID: {plan.get('plan_id')}")
    print(f"Scenario ID: {plan.get('scenario_id')}")
    print(f"Parse Confidence: {plan.get('parse_confidence')}")
    print()

    print("Source Instruction:")
    print(f"  {plan.get('source_instruction')}")
    print()

    print("Standardized Task Plan:")
    print(f"  {plan.get('standard_task_plan_text')}")
    print()

    participants = plan.get("participants", [])
    tasks = plan.get("tasks", [])
    relations = plan.get("relations", [])
    ambiguities = plan.get("ambiguities", [])
    missing_fields = plan.get("missing_fields", [])

    print(f"Participants ({len(participants)}):")
    for p in participants:
        print(f"  - {p.get('actor_id')} | type={p.get('type')} | role={p.get('role')}")
    print()

    print(f"Tasks ({len(tasks)}):")
    for t in tasks:
        print(
            f"  - {t.get('task_id')}: "
            f"{t.get('actor')} -> {t.get('action')}({t.get('target')})"
        )
        if t.get("condition"):
            print(f"      condition: {t.get('condition')}")
        if t.get("expected_output"):
            print(f"      expected_output: {t.get('expected_output')}")
    print()

    print(f"Relations ({len(relations)}):")
    for r in relations:
        print(
            f"  - {r.get('source')} -> {r.get('target')} "
            f"[{r.get('type')}]"
        )
        if r.get("condition"):
            print(f"      condition: {r.get('condition')}")
    print()

    print(f"Ambiguities ({len(ambiguities)}):")
    for a in ambiguities:
        print(f"  - {a.get('ambiguity_id')}: {a.get('description')}")
    print()

    print(f"Missing Fields ({len(missing_fields)}):")
    for m in missing_fields:
        print(f"  - {m}")

    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())