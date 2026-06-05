"""Advisory NL<->plan semantic consistency check over a Phase-1 master dataset.

Runs the reverse-verbalization engine (verifier/semantic_reverse.py) and writes a drift
report of cases where the canonical_task_plan may not match its standard_instruction. This
tool is **advisory**: it never modifies cases and its exit code reflects only operational
errors (e.g. a missing dataset), never drift findings.

    # Tier-1 only (deterministic, offline, default)
    python -m tools.dataset.check_semantics \
        --dataset datasets/v2/phase1_master_cases.jsonl \
        --out out/semantic/phase1_drift.md

    # Tier-1 + Tier-2 LLM judge (opt-in), only on free-form instructions
    python -m tools.dataset.check_semantics --tier both --llm-scope freeform \
        --profile semantic_judge --out out/semantic/phase1_drift.md
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Optional

from tools.dataset.common import load_jsonl
from verifier.semantic_reverse import CaseReport, check_case, tier2_check, verbalize


def _build_client(profile: Optional[str]) -> tuple[Any, Optional[str]]:
    """Construct an LLM client; return (client_or_None, warning_or_None)."""
    try:
        from agents.llm_client import LLMClient, LLMConfigError, load_provider_config
    except Exception as exc:  # agents package / SDK unavailable
        return None, f"LLM stack unavailable ({type(exc).__name__}: {exc}); Tier-2 skipped"
    try:
        config = load_provider_config(profile=profile)
        return LLMClient(config), None
    except LLMConfigError as exc:
        return None, f"LLM config error ({exc}); Tier-2 skipped"
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, never block
        return None, f"LLM client init failed ({type(exc).__name__}: {exc}); Tier-2 skipped"


def _scope_allows(scope: str, instruction: str, tier1_flagged: bool) -> bool:
    if scope == "all":
        return True
    if scope == "flagged":
        return tier1_flagged
    if scope == "freeform":
        # Templated cases authored via make_case all open with "Segment <id> uses actor...".
        return not instruction.strip().startswith("Segment ")
    return True


def analyze(
    cases: list[dict[str, Any]],
    *,
    tier: str,
    llm_scope: str,
    client: Any,
) -> list[CaseReport]:
    tier1_enabled = tier in {"lexical", "both"}
    tier2_enabled = tier in {"llm", "both"}
    reports: list[CaseReport] = []

    for case in cases:
        # Tier-1 is always computed (cheap); it doubles as the scope signal for Tier-2.
        tier1_rep = check_case(case, run_llm=False)
        rep = CaseReport(
            sample_id=tier1_rep.sample_id,
            consistent=True,
            verbalized=tier1_rep.verbalized,
        )
        if tier1_enabled:
            rep.discrepancies.extend(tier1_rep.discrepancies)

        plan = case.get("canonical_task_plan")
        instruction = case.get("standard_instruction") or case.get("raw_instruction")
        if tier2_enabled and plan and instruction:
            if _scope_allows(llm_scope, str(instruction), bool(tier1_rep.discrepancies)):
                llm = tier2_check(plan, instruction, client)
                if llm is None:
                    rep.llm_skipped = True
                else:
                    rep.discrepancies.extend(llm)

        rep.consistent = not rep.discrepancies
        reports.append(rep)
    return reports


def render_report(dataset: Path, tier: str, reports: list[CaseReport]) -> str:
    flagged = [r for r in reports if not r.consistent]
    skipped = sum(1 for r in reports if r.llm_skipped)
    lines = [
        f"# Semantic consistency report — `{dataset}`",
        "",
        f"tier={tier}  cases={len(reports)}  flagged={len(flagged)}  "
        f"clean={len(reports) - len(flagged)}"
        + (f"  llm_skipped={skipped}" if skipped else ""),
        "",
    ]
    if not flagged:
        lines.append("No drift detected.")
        return "\n".join(lines) + "\n"

    for rep in flagged:
        worst = "strong" if any(d.severity == "strong" for d in rep.discrepancies) else "soft"
        lines.append(f"## [DRIFT] {rep.sample_id}   ({worst})")
        for d in rep.discrepancies:
            lines.append(
                f"- `{d.kind}` ({d.severity}, tier={d.tier})"
                + (f" @ {d.locus}" if d.locus else "")
                + f": {d.detail}"
            )
        lines.append("")
        lines.append("```")
        lines.append(rep.verbalized)
        lines.append("```")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("datasets") / "v2" / "phase1_master_cases.jsonl",
    )
    parser.add_argument("--tier", choices=["lexical", "llm", "both"], default="lexical")
    parser.add_argument(
        "--llm-scope",
        choices=["all", "flagged", "freeform"],
        default="all",
        help="Which cases Tier-2 runs on (only relevant for --tier llm/both).",
    )
    parser.add_argument("--profile", default=None, help="LLM provider profile name for Tier-2.")
    parser.add_argument("--case-type", default=None, help="Filter to a single case_type.")
    parser.add_argument("--out", type=Path, default=None, help="Write Markdown report here.")
    args = parser.parse_args()

    if not args.dataset.exists():
        print(f"[check_semantics] dataset not found: {args.dataset}")
        return 1

    cases = load_jsonl(args.dataset)
    if args.case_type:
        cases = [c for c in cases if c.get("case_type") == args.case_type]

    client = None
    if args.tier in {"llm", "both"}:
        client, warning = _build_client(args.profile)
        if warning:
            print(f"[WARN] {warning}")

    reports = analyze(cases, tier=args.tier, llm_scope=args.llm_scope, client=client)
    report_md = render_report(args.dataset, args.tier, reports)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report_md, encoding="utf-8")

    flagged = [r for r in reports if not r.consistent]
    skipped = sum(1 for r in reports if r.llm_skipped)
    for rep in flagged:
        worst = "strong" if any(d.severity == "strong" for d in rep.discrepancies) else "soft"
        print(f"[DRIFT] {rep.sample_id} ({worst}, {len(rep.discrepancies)} issue(s))")
    print(
        f"[check_semantics] dataset={args.dataset} tier={args.tier} "
        f"cases={len(reports)} flagged={len(flagged)} "
        f"clean={len(reports) - len(flagged)}"
        + (f" llm_skipped={skipped}" if skipped else "")
        + (f" report={args.out}" if args.out else "")
    )
    # Advisory: drift never fails the command.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
