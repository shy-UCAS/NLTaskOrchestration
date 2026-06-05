"""Generate a human review sheet for a Phase-1 master dataset.

Read-only, deterministic, no LLM. For each case it lays out — side by side — every
signal a human needs to make a final call on sample correctness:

  * the natural-language instruction,
  * verbalize(plan): the plan rendered back into readable language (for NL<->plan compare),
  * expected_result / expected_unsat_reason,
  * declared vs actually-emitted constraint_types (realizability),
  * for unsat cases, the Z3 unsat_core + attribution (is it unsat for the labelled reason?),
  * AUTO-FLAGS: deterministic suspicions (NL<->plan drift, unrealizable constraint_types,
    unsat-reason mismatch),
  * a per-case-type REVIEW question telling the reviewer what to verify.

    python -m tools.dataset.review_sheet --dataset datasets/v2/phase1_master_cases.jsonl \
        --out out/dataset_review/phase1_review.md
    python -m tools.dataset.review_sheet --flagged-only   # only cases with auto-flags
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Optional

from gcjp.api_spec import VALID_CONSTRAINT_TYPES
from gcjp.task_plan_loader import (
    build_graph_from_task_plan,
    load_action_defaults_from_yaml,
    load_capability_model_from_yaml,
)
from tools.dataset.common import build_task_plan_for_loader, load_jsonl
from verifier.pipeline import VerificationPipeline
from verifier.semantic_reverse import tier1_check, verbalize

# expected_unsat_reason -> keywords expected to show up in the Z3 attribution / unsat_core.
_REASON_HINTS: dict[str, tuple[str, ...]] = {
    "resource_exceeded": ("资源", "resource", "ammo", "弹药", "energy", "能量"),
    "deadline_too_tight": ("时间预算", "时间窗", "deadline", "time_window", "dur_lb", "顺序", "时序"),
    "physical_infeasible": ("物理", "phys", "飞行", "距离"),
    "capability_mismatch": ("能力", "capability", "capable"),
    "cyclic_dependency": ("环", "cycle"),
    "group_sync_conflict": ("组同步", "group_sync", "同步"),
}


def _actual_constraint_types(plan, ad, cm) -> tuple[Optional[set[str]], Optional[Any], str]:
    """Build the graph; return (actual_constraint_types, report, error)."""
    try:
        graph = build_graph_from_task_plan(
            build_task_plan_for_loader(plan),
            segment_id=plan.get("plan_id"),
            action_defaults=ad,
            capability_model=cm,
        )
        report = VerificationPipeline(z3_timeout_ms=10_000).verify_graph(graph)
        return {c.constraint_type for c in graph.constraints}, report, ""
    except Exception as exc:  # noqa: BLE001 - surface to reviewer
        return None, None, f"{type(exc).__name__}: {exc}"


def _unsat_info(report) -> tuple[list[str], list[str]]:
    if report is None:
        return [], []
    for layer in report.layers:
        if layer.layer == 3 and not layer.passed:
            details = layer.details or {}
            return (
                list(details.get("unsat_core") or details.get("unsat_core_semantic") or []),
                list(details.get("attribution") or report.attribution or []),
            )
    return [], list(report.attribution or [])


def review_case(case: dict[str, Any], ad, cm) -> dict[str, Any]:
    sid = str(case.get("sample_id", "<missing>"))
    case_type = case.get("case_type", "?")
    plan = case.get("canonical_task_plan")
    nl = case.get("standard_instruction") or case.get("raw_instruction") or ""
    ev = case.get("expected_verification") or {}
    result = ev.get("expected_result")
    reason = ev.get("expected_unsat_reason")
    expected_ctypes = list((case.get("expected_graph") or {}).get("constraint_types") or [])

    flags: list[str] = []
    actual_ctypes: Optional[set[str]] = None
    unsat_core: list[str] = []
    attribution: list[str] = []
    build_error = ""

    if plan:
        actual_ctypes, report, build_error = _actual_constraint_types(plan, ad, cm)
        if build_error:
            flags.append(f"build/verify failed: {build_error}")
        else:
            # constraint_type realizability + validity
            for ct in expected_ctypes:
                if ct not in VALID_CONSTRAINT_TYPES:
                    flags.append(f"constraint_type {ct!r} is INVALID (not a real type)")
                elif actual_ctypes is not None and ct not in actual_ctypes:
                    flags.append(f"constraint_type {ct!r} declared but NOT emitted by graph")
            if result == "unsat":
                unsat_core, attribution = _unsat_info(report)
                # unsat-reason sanity
                if reason:
                    blob = " ".join(unsat_core + attribution)
                    hints = _REASON_HINTS.get(reason, ())
                    if hints and not any(h in blob for h in hints):
                        flags.append(
                            f"unsat reason {reason!r} not reflected in unsat_core/attribution"
                        )
        # NL <-> plan drift (reuse Tier-1)
        for d in tier1_check(plan, nl):
            flags.append(f"NL<->plan {d.kind}({d.severity}): {d.detail}")

    return {
        "sample_id": sid,
        "case_type": case_type,
        "difficulty": case.get("difficulty"),
        "split": case.get("split"),
        "tags": case.get("tags", []),
        "nl": nl,
        "verbalized": verbalize(plan),
        "result": result,
        "reason": reason,
        "expected_ctypes": expected_ctypes,
        "actual_ctypes": sorted(actual_ctypes) if actual_ctypes is not None else None,
        "unsat_core": unsat_core,
        "attribution": attribution,
        "flags": flags,
        "is_migrated": sid.startswith("1b_") or "migrated" in (case.get("tags") or []),
        "has_plan": bool(plan),
    }


def _review_question(r: dict[str, Any]) -> str:
    if not r["has_plan"]:
        return "（raw 样本，未回填 plan）NL 是否符合 normalization 标注的 status / 缺失字段？"
    if r["is_migrated"]:
        return "迁移样本（高危）：NL 与 verbalized plan 是否描述**同一个任务**？actor/action/target/relation/deadline 全对上？"
    if r["result"] == "unsat":
        return f"unsat 是否**因为** expected_unsat_reason={r['reason']!r}？看 unsat_core/attribution 是否单一且命中该原因。"
    return "NL 与 verbalized plan 的 actor/action/target/relation/condition/deadline 是否一致？constraint_types 是否可实现？"


def render(dataset: Path, reviews: list[dict[str, Any]], flagged_only: bool) -> str:
    flagged = [r for r in reviews if r["flags"]]
    lines = [
        f"# 数据审查表 — `{dataset}`",
        "",
        f"cases={len(reviews)}  auto_flagged={len(flagged)}  "
        f"migrated={sum(1 for r in reviews if r['is_migrated'])}  "
        f"unsat={sum(1 for r in reviews if r['result'] == 'unsat')}",
        "",
        "> AUTO-FLAGS 是确定性可疑信号（可能误报，供 triage）；REVIEW 是该条要人工确认的问题。",
        "",
    ]
    if flagged:
        lines.append("## ⚠ 自动标记的可疑样本（优先看）")
        for r in flagged:
            lines.append(f"- `{r['sample_id']}` — " + "；".join(r["flags"]))
        lines.append("")

    shown = flagged if flagged_only else reviews
    lines.append("---")
    for r in shown:
        head = f"## {r['sample_id']}  [{r['case_type']}, {r['difficulty']}, {r['result']}"
        head += f"/{r['reason']}]" if r["reason"] else "]"
        lines.append(head)
        lines.append(f"tags: {r['tags']}")
        lines.append("")
        lines.append(f"**NL**: {r['nl']}")
        lines.append("")
        lines.append("**PLAN (verbalized)**:")
        lines.append("```")
        lines.append(r["verbalized"])
        lines.append("```")
        if r["has_plan"]:
            mark = "✓" if (r["actual_ctypes"] is not None and
                           all(c in r["actual_ctypes"] for c in r["expected_ctypes"])) else "⚠"
            lines.append(
                f"**constraint_types** {mark}  expected={r['expected_ctypes']}  "
                f"actual={r['actual_ctypes']}"
            )
            if r["result"] == "unsat":
                lines.append(f"**unsat_core**: {r['unsat_core']}")
                lines.append(f"**attribution**: {r['attribution']}")
        if r["flags"]:
            lines.append("")
            lines.append("**AUTO-FLAGS**:")
            for f in r["flags"]:
                lines.append(f"- ⚠ {f}")
        lines.append("")
        lines.append(f"**REVIEW**: {_review_question(r)}")
        lines.append("")
        lines.append("---")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("datasets") / "v2" / "phase1_master_cases.jsonl")
    parser.add_argument("--action-templates", type=Path, default=Path("configs") / "action_templates.yaml")
    parser.add_argument("--capability-model", type=Path, default=Path("configs") / "capability_model.yaml")
    parser.add_argument("--case-type", default=None, help="Filter to a single case_type.")
    parser.add_argument("--flagged-only", action="store_true", help="Render only auto-flagged cases.")
    parser.add_argument("--out", type=Path, default=Path("out") / "dataset_review" / "phase1_review.md")
    args = parser.parse_args()

    if not args.dataset.exists():
        print(f"[review_sheet] dataset not found: {args.dataset}")
        return 1

    cases = load_jsonl(args.dataset)
    if args.case_type:
        cases = [c for c in cases if c.get("case_type") == args.case_type]
    ad = load_action_defaults_from_yaml(args.action_templates)
    cm = load_capability_model_from_yaml(args.capability_model)

    reviews = [review_case(c, ad, cm) for c in cases]
    report_md = render(args.dataset, reviews, args.flagged_only)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report_md, encoding="utf-8")

    flagged = [r for r in reviews if r["flags"]]
    for r in flagged:
        print(f"[FLAG] {r['sample_id']}: " + " | ".join(r["flags"]))
    print(
        f"[review_sheet] dataset={args.dataset} cases={len(reviews)} "
        f"auto_flagged={len(flagged)} out={args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
