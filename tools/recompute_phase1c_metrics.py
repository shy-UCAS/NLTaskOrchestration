"""Recompute Phase 1C metrics from per-case repair reports.

Usage:
    python tools/recompute_phase1c_metrics.py out/phase1_repair/exp_01c_repair_loop/reports
    python tools/recompute_phase1c_metrics.py .../reports --write .../metrics.recomputed.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EXPERIMENT_NAME = "exp_01c_repair_loop"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports_dir", type=Path)
    parser.add_argument(
        "--write",
        type=Path,
        help="Optional path to write the recomputed metrics JSON.",
    )
    args = parser.parse_args()

    records = load_reports(args.reports_dir)
    metrics = aggregate_metrics(records)
    metrics["reports_dir"] = str(args.reports_dir)

    text = json.dumps(metrics, ensure_ascii=False, indent=2)
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(text, encoding="utf-8")
    print(text)
    return 0


def load_reports(reports_dir: Path) -> list[dict[str, Any]]:
    if not reports_dir.exists():
        raise FileNotFoundError(f"reports_dir not found: {reports_dir}")
    reports = []
    for path in sorted(reports_dir.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        validate_report(record, path)
        reports.append(record)
    if not reports:
        raise ValueError(f"No report JSON files found in {reports_dir}")
    return reports


def validate_report(record: dict[str, Any], path: Path) -> None:
    missing = [
        key
        for key in ("sample_id", "evaluation", "initial", "final")
        if key not in record
    ]
    if missing:
        raise ValueError(f"{path} missing required keys: {missing}")
    evaluation = record.get("evaluation") or {}
    for key in (
        "initial_pass",
        "repair_attempted",
        "repair_success",
        "final_pass",
        "repair_rounds",
    ):
        if key not in evaluation:
            raise ValueError(f"{path} missing evaluation.{key}")


def aggregate_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    initial_pass = sum(1 for r in records if r["evaluation"]["initial_pass"])
    repair_attempt = sum(1 for r in records if r["evaluation"]["repair_attempted"])
    repair_success = sum(1 for r in records if r["evaluation"]["repair_success"])
    final_pass = sum(1 for r in records if r["evaluation"]["final_pass"])
    total_rounds = sum(r["evaluation"]["repair_rounds"] for r in records)

    recovered: dict[str, int] = {}
    unrecovered: dict[str, int] = {}
    for record in records:
        err_type = record["initial"].get("execution_error_type") or "UNKNOWN"
        if record["evaluation"]["repair_success"]:
            recovered[err_type] = recovered.get(err_type, 0) + 1
        elif not record["evaluation"]["final_pass"]:
            unrecovered[err_type] = unrecovered.get(err_type, 0) + 1

    return {
        "experiment": EXPERIMENT_NAME,
        "total_cases": total,
        "rates": {
            "initial_pass_rate": initial_pass / total if total else 0.0,
            "repair_attempt_rate": repair_attempt / total if total else 0.0,
            "repair_success_rate": repair_success / total if total else 0.0,
            "final_pass_rate": final_pass / total if total else 0.0,
            "avg_repair_rounds": total_rounds / total if total else 0.0,
        },
        "recovered_error_type_distribution": recovered,
        "unrecovered_error_type_distribution": unrecovered,
        "records": [
            {
                "sample_id": r["sample_id"],
                "evaluation": r["evaluation"],
                "initial_error_type": r["initial"].get("execution_error_type"),
                "final_error_type": r["final"].get("execution_error_type"),
            }
            for r in records
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
