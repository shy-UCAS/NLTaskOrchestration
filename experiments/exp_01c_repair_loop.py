"""
experiments/exp_01c_repair_loop.py
python -m experiments.exp_01c_repair_loop --local-provider claude --limit 2

Phase 1C：LLM 自动修复闭环实验。

默认从 datasets/phase1_repair_cases.jsonl 读取固定坏代码样本；
也可用 --source-report-dir 指向 1A/1B reports 目录，修复其中失败样本。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agents.llm_client import LLMClient, LLMConfigError
from agents.repair_agent import RepairAgent
from experiments.phase1_common import (
    add_common_args,
    handle_config_error,
    load_config_from_args,
    load_jsonl,
    phase1_run_metadata_json,
    print_provider_summary_from_args,
    read_prompt_template,
    resolve_phase1_run_output,
    write_latest_run_index,
    _constraint_complete,
    _edge_complete,
    _first_report_layer as _first_layer,
    _node_complete,
)
from gcjp.code_executor import execute_gcjp_code
from gcjp.mission_graph import BuiltGraph
from verifier.pipeline import VerificationPipeline, VerificationReport


EXPERIMENT_NAME = "exp_01c_repair_loop"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("datasets") / "phase1_repair_cases.jsonl",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        default=Path("prompts") / "gcjp_repair_prompt.md",
    )
    parser.add_argument("--max-repair-rounds", type=int, default=2)
    parser.add_argument(
        "--source-report-dir",
        type=Path,
        help="Optional reports directory from 1A/1B; only failed records are repaired.",
    )
    args = parser.parse_args()

    try:
        print_provider_summary_from_args(args)
        metrics = run_repair_experiment(args)
    except LLMConfigError as exc:
        return handle_config_error(exc)

    metrics_path = Path(metrics["output_dir"]) / "metrics.json"
    print(f"\n[{EXPERIMENT_NAME}] 汇总指标 -> {metrics_path}")
    print(json.dumps(metrics["rates"], ensure_ascii=False, indent=2))
    return 0


def run_repair_experiment(args: argparse.Namespace) -> dict[str, Any]:
    cases = _load_cases(args)
    if not cases:
        if args.source_report_dir:
            raise ValueError(_format_empty_source_report_error(args.source_report_dir))
        raise ValueError("No repair cases loaded from dataset")

    prompt_template = read_prompt_template(args.prompt)
    config = load_config_from_args(args)
    provider_summary = config.safe_summary()
    run_output = resolve_phase1_run_output(
        output_dir=args.output_dir,
        provider_summary=provider_summary,
        run_label=args.run_label,
        no_run_timestamp=args.no_run_timestamp,
    )
    agent = RepairAgent(LLMClient(config))
    output_dirs = ensure_output_dirs(run_output["run_dir"])

    records = []
    for case in cases:
        record = _run_case(
            case=case,
            agent=agent,
            prompt_template=prompt_template,
            max_repair_rounds=args.max_repair_rounds,
        )
        write_case_outputs(output_dirs, record)
        records.append(record)
        print(summary_line(record))

    metrics = aggregate_metrics(records)
    metrics.update(phase1_run_metadata_json(run_output))
    metrics["output_dir"] = str(output_dirs["root"])
    metrics_path = output_dirs["root"] / "metrics.json"
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_latest_run_index(
        run_output=run_output,
        experiment_name=EXPERIMENT_NAME,
        experiment_dir=output_dirs["root"],
        reports_dir=output_dirs["reports"],
        metrics_path=metrics_path,
    )
    return metrics


def _load_cases(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.source_report_dir:
        return load_failed_source_reports(args.source_report_dir, args.limit)
    return load_jsonl(args.dataset, limit=args.limit)


def _format_empty_source_report_error(report_dir: Path) -> str:
    if not report_dir.exists():
        return f"No repair cases loaded: source report dir does not exist: {report_dir}"
    if not report_dir.is_dir():
        return f"No repair cases loaded: source report path is not a directory: {report_dir}"

    reports_found = 0
    skipped_first_pass = 0
    skipped_missing_code = 0
    unreadable_reports = 0

    for path in sorted(report_dir.glob("*.json")):
        reports_found += 1
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            unreadable_reports += 1
            continue
        if (record.get("evaluation") or {}).get("first_pass"):
            skipped_first_pass += 1
            continue
        generation = record.get("generation") or {}
        if not (generation.get("extracted_code") or ""):
            skipped_missing_code += 1

    return (
        "No repair cases loaded from source reports. "
        f"report_dir={report_dir}; reports_found={reports_found}; "
        f"skipped_first_pass={skipped_first_pass}; "
        f"skipped_missing_extracted_code={skipped_missing_code}; "
        f"unreadable_reports={unreadable_reports}. "
        "Phase 1C can only repair failed reports that contain generation.extracted_code. "
        "If syntax_extract_rate is 0.0 or reports show NO_CODE/LLMRequestError, rerun 1A/1B "
        "with a larger --max-tokens and/or retry attempts, or pass a reports dir from a run "
        "that produced extractable code."
    )


def load_failed_source_reports(
    report_dir: Path,
    limit: int | None,
) -> list[dict[str, Any]]:
    cases = []
    for path in sorted(report_dir.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if (record.get("evaluation") or {}).get("first_pass"):
            continue
        generation = record.get("generation") or {}
        broken_code = generation.get("extracted_code") or ""
        if not broken_code:
            continue
        original_case = record.get("case") or {}
        cases.append(
            {
                "sample_id": f"repair_{record.get('sample_id', path.stem)}",
                "broken_code": broken_code,
                "case_payload": original_case,
                "expected_result": original_case.get("expected_result"),
                "expected_fix_patterns": original_case.get("expected_patterns", {}),
                "prompt_context": (generation.get("prompt") or "")[:4000],
                "source_report": str(path),
            }
        )
        if limit is not None and len(cases) >= limit:
            break
    return cases


def _run_case(
    *,
    case: dict[str, Any],
    agent: RepairAgent,
    prompt_template: str,
    max_repair_rounds: int,
) -> dict[str, Any]:
    sample_id = case["sample_id"]
    initial_code = case["broken_code"]
    initial_eval = evaluate_code(case, initial_code)

    attempts = []
    current_code = initial_code
    current_report = initial_eval["report"]
    final_eval = initial_eval

    for repair_round in range(1, max_repair_rounds + 1):
        if is_expected_pass(case, final_eval):
            break
        try:
            generation = agent.repair_gcjp(
                sample_id=sample_id,
                repair_round=repair_round,
                prompt_template=prompt_template,
                broken_code=current_code,
                verification_report=current_report or {},
                case_payload=case.get("case_payload") or case,
                prompt_context=case.get("prompt_context"),
            )
            repaired_code = generation.repaired_code
            round_eval = (
                evaluate_code(case, repaired_code)
                if generation.extraction.get("ok")
                else extraction_failed_eval(generation.extraction)
            )
            attempt = {
                "repair_round": repair_round,
                "generation": asdict(generation),
                "evaluation": round_eval,
            }
        except Exception as exc:
            round_eval = exception_eval(exc)
            attempt = {
                "repair_round": repair_round,
                "generation": None,
                "evaluation": round_eval,
                "error": f"{type(exc).__name__}: {exc}",
            }
        attempts.append(attempt)
        final_eval = round_eval
        current_code = attempt_code(attempt) or current_code
        current_report = final_eval.get("report") or current_report

    return {
        "sample_id": sample_id,
        "case": case,
        "initial_code": initial_code,
        "initial": initial_eval,
        "attempts": attempts,
        "final_code": current_code,
        "final": final_eval,
        "evaluation": {
            "initial_pass": is_expected_pass(case, initial_eval),
            "repair_attempted": bool(attempts),
            "repair_success": (
                not is_expected_pass(case, initial_eval)
                and is_expected_pass(case, final_eval)
            ),
            "final_pass": is_expected_pass(case, final_eval),
            "repair_rounds": len(attempts),
        },
    }


def evaluate_code(case: dict[str, Any], code: str) -> dict[str, Any]:
    exec_result = execute_gcjp_code(code)
    graph = exec_result.graph if exec_result and exec_result.graph else None
    report = VerificationPipeline(z3_timeout_ms=15_000).verify_gcjp_code(code)
    return {
        "extraction_ok": bool(code),
        "execution_success": bool(exec_result and exec_result.passed),
        "execution_error_type": exec_result.error_type if exec_result else "NO_CODE",
        "report": report.to_dict(),
        "expected_pass": _report_matches_expected(case, graph, report),
        "node_complete": _node_complete(graph, case.get("expected_fix_patterns", {})),
        "edge_complete": _edge_complete(graph, case.get("expected_fix_patterns", {})),
        "constraint_complete": _constraint_complete(
            graph,
            case.get("expected_fix_patterns", {}),
        ),
    }


def _report_matches_expected(
    case: dict[str, Any],
    graph: BuiltGraph | None,
    report: VerificationReport,
) -> bool:
    expected_result = case.get("expected_result")
    layer3 = _first_layer(report.to_dict(), 3)
    z3_result = (layer3.get("details", {}) or {}).get("z3_result") if layer3 else None
    if expected_result == "sat":
        l3_ok = bool(report.overall_passed and z3_result == "sat")
    elif expected_result == "unsat":
        l3_ok = z3_result == "unsat"
    else:
        l3_ok = bool(report.overall_passed)
    patterns = case.get("expected_fix_patterns", {})
    return (
        l3_ok
        and _node_complete(graph, patterns)
        and _edge_complete(graph, patterns)
        and _constraint_complete(graph, patterns)
    )


def is_expected_pass(case: dict[str, Any], evaluation: dict[str, Any]) -> bool:
    return bool(evaluation.get("expected_pass"))


def extraction_failed_eval(extraction: dict[str, Any]) -> dict[str, Any]:
    return {
        "extraction_ok": False,
        "execution_success": False,
        "execution_error_type": "EXTRACTION_FAILED",
        "report": None,
        "expected_pass": False,
        "node_complete": False,
        "edge_complete": False,
        "constraint_complete": False,
        "extraction": extraction,
    }


def exception_eval(exc: Exception) -> dict[str, Any]:
    return {
        "extraction_ok": False,
        "execution_success": False,
        "execution_error_type": type(exc).__name__,
        "report": None,
        "expected_pass": False,
        "node_complete": False,
        "edge_complete": False,
        "constraint_complete": False,
        "error": f"{type(exc).__name__}: {exc}",
    }


def attempt_code(attempt: dict[str, Any]) -> str:
    generation = attempt.get("generation") or {}
    return generation.get("repaired_code") or ""




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


def ensure_output_dirs(root: Path) -> dict[str, Path]:
    exp_root = root / EXPERIMENT_NAME
    dirs = {
        "root": exp_root,
        "initial_code": exp_root / "initial_code",
        "final_code": exp_root / "final_code",
        "attempts": exp_root / "repair_attempts",
        "reports": exp_root / "reports",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def write_case_outputs(output_dirs: dict[str, Path], record: dict[str, Any]) -> None:
    sample_id = record["sample_id"]
    (output_dirs["initial_code"] / f"{sample_id}.py").write_text(
        record.get("initial_code") or "",
        encoding="utf-8",
    )
    (output_dirs["final_code"] / f"{sample_id}.py").write_text(
        record.get("final_code") or "",
        encoding="utf-8",
    )
    (output_dirs["attempts"] / f"{sample_id}.json").write_text(
        json.dumps(record.get("attempts") or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dirs["reports"] / f"{sample_id}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def summary_line(record: dict[str, Any]) -> str:
    ev = record["evaluation"]
    mark = "通过" if ev["final_pass"] else "失败"
    return (
        f"[{mark}] {record['sample_id']} "
        f"initial={ev['initial_pass']} "
        f"attempts={ev['repair_rounds']} "
        f"repair_success={ev['repair_success']} "
        f"final={ev['final_pass']}"
    )


# Backward-compat aliases (old underscore-prefixed names)
_load_failed_source_reports = load_failed_source_reports
_evaluate_code = evaluate_code
_is_expected_pass = is_expected_pass
_extraction_failed_eval = extraction_failed_eval
_exception_eval = exception_eval
_attempt_code = attempt_code
_aggregate_metrics = aggregate_metrics
_ensure_output_dirs = ensure_output_dirs
_write_case_outputs = write_case_outputs
_summary_line = summary_line


if __name__ == "__main__":
    raise SystemExit(main())
