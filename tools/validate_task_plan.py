"""
tools/validate_task_plan.py

Validate a standardized task plan JSON file against task_plan_schema.json.

Usage:
    python tools/validate_task_plan.py schemas/task_plan_schema.json demos/demo_01_simple_task_plan.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft7Validator


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage:")
        print("  python tools/validate_task_plan.py <schema_path> <task_plan_path>")
        return 1

    schema_path = Path(sys.argv[1])
    task_plan_path = Path(sys.argv[2])

    if not schema_path.exists():
        print(f"[ERROR] Schema file not found: {schema_path}")
        return 1

    if not task_plan_path.exists():
        print(f"[ERROR] Task plan file not found: {task_plan_path}")
        return 1

    try:
        schema = load_json(schema_path)
        task_plan = load_json(task_plan_path)
    except json.JSONDecodeError as e:
        print(f"[ERROR] Invalid JSON: {e}")
        return 1

    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(task_plan), key=lambda e: e.path)

    if not errors:
        print(f"[VALID] {task_plan_path}")
        return 0

    print(f"[INVALID] {task_plan_path}")
    print(f"Found {len(errors)} schema error(s):")

    for i, error in enumerate(errors, start=1):
        path = ".".join(str(p) for p in error.path)
        path = path if path else "<root>"
        print(f"\n{i}. Path: {path}")
        print(f"   Message: {error.message}")

    return 2


if __name__ == "__main__":
    raise SystemExit(main())