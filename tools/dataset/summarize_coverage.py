from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

from tools.dataset.common import load_jsonl


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("datasets") / "v2" / "phase1_master_cases.jsonl",
    )
    parser.add_argument(
        "--markdown-out",
        type=Path,
        default=Path("out") / "dataset_coverage" / "phase1_master_coverage.md",
    )
    args = parser.parse_args()

    cases = load_jsonl(args.dataset)
    summary = summarize(cases)
    text = _format_summary(args.dataset, summary)
    print(text)
    args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_out.write_text(text, encoding="utf-8")
    print(f"[coverage] wrote {args.markdown_out}")
    return 0


def summarize(cases: list[dict[str, Any]]) -> dict[str, Counter[str]]:
    summary: dict[str, Counter[str]] = {
        "split": Counter(),
        "case_type": Counter(),
        "difficulty": Counter(),
        "task_family": Counter(),
        "expected_result": Counter(),
        "actor": Counter(),
        "action": Counter(),
        "relation": Counter(),
        "constraint_type": Counter(),
        "missing_field": Counter(),
    }
    for case in cases:
        summary["split"][str(case.get("split", "<missing>"))] += 1
        summary["case_type"][str(case.get("case_type", "<missing>"))] += 1
        summary["difficulty"][str(case.get("difficulty", "<missing>"))] += 1
        for item in case.get("task_family", []) or []:
            summary["task_family"][str(item)] += 1
        expected = (case.get("expected_verification") or {}).get("expected_result", "<missing>")
        summary["expected_result"][str(expected)] += 1

        plan = case.get("canonical_task_plan") or {}
        for task in plan.get("tasks", []) or []:
            summary["actor"][str(task.get("actor", "<missing>"))] += 1
            summary["action"][str(task.get("action", "<missing>"))] += 1
        for rel in plan.get("relations", []) or []:
            summary["relation"][str(rel.get("type", "<missing>"))] += 1
        for ctype in (case.get("expected_graph") or {}).get("constraint_types", []) or []:
            summary["constraint_type"][str(ctype)] += 1
        normalization = case.get("normalization") or {}
        for field in normalization.get("expected_missing_fields", []) or []:
            summary["missing_field"][str(field)] += 1
    return summary


def _format_summary(dataset: Path, summary: dict[str, Counter[str]]) -> str:
    lines = [f"# Phase 1 Dataset Coverage", "", f"Dataset: `{dataset}`", ""]
    total = sum(summary["case_type"].values())
    lines.extend([f"Total cases: **{total}**", ""])
    for name, counter in summary.items():
        lines.extend([f"## {name}", "", "| Value | Count |", "|---|---:|"])
        for value, count in sorted(counter.items()):
            lines.append(f"| {value} | {count} |")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
