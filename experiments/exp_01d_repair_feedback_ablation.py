"""
experiments/exp_01d_repair_feedback_ablation.py

Phase 1D: compare Phase 1C repair feedback modes.

This experiment reuses Phase 1C repair/evaluation behavior, but changes the
verification feedback passed into the repair prompt:

- full_report: current Phase 1C behavior.
- no_report: empty feedback object.
- layer1_only: only Layer 1 diagnostics.
- error_summary_only: compact structured error summary.
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
    phase1_run_metadata_json,
    print_provider_summary_from_args,
    read_prompt_template,
    resolve_phase1_run_output,
    write_latest_run_index,
)
from experiments import exp_01c_repair_loop as repair_loop


EXPERIMENT_NAME = "exp_01d_repair_feedback_ablation"
FEEDBACK_MODES = (
    "full_report",
    "no_report",
    "layer1_only",
    "error_summary_only",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--source-report-dir",
        type=Path,
        required=True,
        help="Reports directory from 1A/1B generation; failed records are repaired.",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        default=Path("prompts") / "gcjp_repair_prompt.md",
    )
    parser.add_argument("--max-repair-rounds", type=int, default=2)
    parser.add_argument(
        "--feedback-modes",
        nargs="+",
        choices=FEEDBACK_MODES,
        default=list(FEEDBACK_MODES),
        help="Feedback modes to compare.",
    )
    args = parser.parse_args()

    try:
        print_provider_summary_from_args(args)
        summary = run_feedback_ablation(args)
    except LLMConfigError as exc:
        return handle_config_error(exc)

    print(f"\n[{EXPERIMENT_NAME}] 汇总 -> {summary['summary_json']}")
    print(json.dumps(summary["mode_rates"], ensure_ascii=False, indent=2))
    return 0


def run_feedback_ablation(args: argparse.Namespace) -> dict[str, Any]:
    cases = repair_loop._load_failed_source_reports(
        args.source_report_dir,
        args.limit,
    )
    if not cases:
        raise ValueError(f"No failed source reports loaded from {args.source_report_dir}")

    prompt_template = read_prompt_template(args.prompt)
    config = load_config_from_args(args)
    provider_summary = config.safe_summary()
    agent = RepairAgent(LLMClient(config))

    run_output = resolve_phase1_run_output(
        output_dir=args.output_dir,
        provider_summary=provider_summary,
        run_label=args.run_label,
        no_run_timestamp=args.no_run_timestamp,
    )
    base_output_dir = run_output["run_dir"]
    base_output_dir.mkdir(parents=True, exist_ok=True)

    mode_summaries = []
    for mode in args.feedback_modes:
        mode_summary = _run_mode(
            mode=mode,
            cases=cases,
            agent=agent,
            prompt_template=prompt_template,
            max_repair_rounds=args.max_repair_rounds,
            output_root=base_output_dir / mode,
            source_report_dir=args.source_report_dir,
            run_output=run_output,
        )
        mode_summaries.append(mode_summary)

    summary = {
        "experiment": EXPERIMENT_NAME,
        "source_report_dir": str(args.source_report_dir),
        "output_dir": str(base_output_dir),
        **phase1_run_metadata_json(run_output),
        "feedback_modes": list(args.feedback_modes),
        "modes": mode_summaries,
        "mode_rates": {
            item["feedback_mode"]: item["rates"]
            for item in mode_summaries
        },
    }
    summary_json = base_output_dir / "summary.json"
    summary_md = base_output_dir / "summary.md"
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary_md.write_text(_render_summary_markdown(summary), encoding="utf-8")
    write_latest_run_index(
        run_output=run_output,
        experiment_name=EXPERIMENT_NAME,
        experiment_dir=base_output_dir,
        reports_dir=None,
        summary_path=summary_json,
    )
    summary["summary_json"] = str(summary_json)
    summary["summary_md"] = str(summary_md)
    return summary


def _run_mode(
    *,
    mode: str,
    cases: list[dict[str, Any]],
    agent: RepairAgent,
    prompt_template: str,
    max_repair_rounds: int,
    output_root: Path,
    source_report_dir: Path,
    run_output: dict[str, Any],
) -> dict[str, Any]:
    output_dirs = repair_loop._ensure_output_dirs(output_root)
    records = []
    print(f"\n[{EXPERIMENT_NAME}] feedback_mode={mode}")
    for case in cases:
        record = _run_case_with_feedback_mode(
            case=case,
            agent=agent,
            prompt_template=prompt_template,
            max_repair_rounds=max_repair_rounds,
            feedback_mode=mode,
        )
        repair_loop._write_case_outputs(output_dirs, record)
        records.append(record)
        print(repair_loop._summary_line(record))

    metrics = repair_loop._aggregate_metrics(records)
    metrics.update(phase1_run_metadata_json(run_output))
    metrics["feedback_mode"] = mode
    metrics["source_report_dir"] = str(source_report_dir)
    metrics["output_dir"] = str(output_dirs["root"])
    (output_dirs["root"] / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "feedback_mode": mode,
        "total_cases": metrics["total_cases"],
        "rates": metrics["rates"],
        "recovered_error_type_distribution": (
            metrics["recovered_error_type_distribution"]
        ),
        "unrecovered_error_type_distribution": (
            metrics["unrecovered_error_type_distribution"]
        ),
        "output_dir": metrics["output_dir"],
        "records": metrics["records"],
    }


def _run_case_with_feedback_mode(
    *,
    case: dict[str, Any],
    agent: RepairAgent,
    prompt_template: str,
    max_repair_rounds: int,
    feedback_mode: str,
) -> dict[str, Any]:
    sample_id = case["sample_id"]
    initial_code = case["broken_code"]
    initial_eval = repair_loop._evaluate_code(case, initial_code)

    attempts = []
    current_code = initial_code
    current_report = initial_eval["report"]
    final_eval = initial_eval

    for repair_round in range(1, max_repair_rounds + 1):
        if repair_loop._is_expected_pass(case, final_eval):
            break
        try:
            generation = agent.repair_gcjp(
                sample_id=sample_id,
                repair_round=repair_round,
                prompt_template=prompt_template,
                broken_code=current_code,
                verification_report=_transform_feedback_report(
                    current_report or {},
                    feedback_mode,
                ),
                case_payload=case.get("case_payload") or case,
                prompt_context=case.get("prompt_context"),
            )
            repaired_code = generation.repaired_code
            round_eval = (
                repair_loop._evaluate_code(case, repaired_code)
                if generation.extraction.get("ok")
                else repair_loop._extraction_failed_eval(generation.extraction)
            )
            attempt = {
                "repair_round": repair_round,
                "feedback_mode": feedback_mode,
                "generation": asdict(generation),
                "evaluation": round_eval,
            }
        except Exception as exc:
            round_eval = repair_loop._exception_eval(exc)
            attempt = {
                "repair_round": repair_round,
                "feedback_mode": feedback_mode,
                "generation": None,
                "evaluation": round_eval,
                "error": f"{type(exc).__name__}: {exc}",
            }
        attempts.append(attempt)
        final_eval = round_eval
        current_code = repair_loop._attempt_code(attempt) or current_code
        current_report = final_eval.get("report") or current_report

    return {
        "sample_id": sample_id,
        "feedback_mode": feedback_mode,
        "case": case,
        "initial_code": initial_code,
        "initial": initial_eval,
        "attempts": attempts,
        "final_code": current_code,
        "final": final_eval,
        "evaluation": {
            "initial_pass": repair_loop._is_expected_pass(case, initial_eval),
            "repair_attempted": bool(attempts),
            "repair_success": (
                not repair_loop._is_expected_pass(case, initial_eval)
                and repair_loop._is_expected_pass(case, final_eval)
            ),
            "final_pass": repair_loop._is_expected_pass(case, final_eval),
            "repair_rounds": len(attempts),
        },
    }


def _transform_feedback_report(
    report: dict[str, Any],
    feedback_mode: str,
) -> dict[str, Any]:
    if feedback_mode == "full_report":
        return report or {}
    if feedback_mode == "no_report":
        return {}
    if feedback_mode == "layer1_only":
        return _layer1_only_report(report)
    if feedback_mode == "error_summary_only":
        return _error_summary_report(report)
    raise ValueError(f"Unsupported feedback mode: {feedback_mode}")


def _layer1_only_report(report: dict[str, Any]) -> dict[str, Any]:
    if not report:
        return {"feedback_mode": "layer1_only", "layers": []}
    layers = [
        layer
        for layer in report.get("layers", [])
        if layer.get("layer") == 1
    ]
    return {
        "feedback_mode": "layer1_only",
        "segment_id": report.get("segment_id"),
        "overall_passed": report.get("overall_passed"),
        "layers": layers,
        "total_elapsed_ms": report.get("total_elapsed_ms"),
    }


def _error_summary_report(report: dict[str, Any]) -> dict[str, Any]:
    if not report:
        return {"feedback_mode": "error_summary_only"}

    layer1 = _first_layer(report, 1) or {}
    layer2 = _first_layer(report, 2) or {}
    layer3 = _first_layer(report, 3) or {}
    l1_details = layer1.get("details", {}) or {}
    l2_details = layer2.get("details", {}) or {}
    l3_details = layer3.get("details", {}) or {}

    return {
        "feedback_mode": "error_summary_only",
        "segment_id": report.get("segment_id"),
        "overall_passed": report.get("overall_passed"),
        "first_failed_layer": _first_failed_layer(report),
        "error_type": l1_details.get("error_type"),
        "error_msg": layer1.get("error_msg") or layer2.get("error_msg")
        or layer3.get("error_msg"),
        "gcjp_lineno": l1_details.get("gcjp_lineno"),
        "source_context": l1_details.get("source_context"),
        "traceback_text": l1_details.get("traceback_text"),
        "api_error": l1_details.get("api_error"),
        "structured_violations": l1_details.get("structured_violations", []),
        "layer2_issues": l2_details.get("issues", []),
        "z3_result": l3_details.get("z3_result"),
        "unsat_core": (
            report.get("unsat_core_semantic")
            or l3_details.get("unsat_core_semantic")
            or l3_details.get("unsat_core")
            or []
        ),
        "attribution": report.get("attribution")
        or l3_details.get("attribution")
        or [],
    }


def _first_layer(
    report: dict[str, Any],
    layer_no: int,
) -> dict[str, Any] | None:
    for layer in report.get("layers", []):
        if layer.get("layer") == layer_no:
            return layer
    return None


def _first_failed_layer(report: dict[str, Any]) -> int | None:
    for layer in report.get("layers", []):
        if not layer.get("passed"):
            return layer.get("layer")
    return None


def _render_summary_markdown(summary: dict[str, Any]) -> str:
    provider = summary.get("provider", {})
    lines = [
        "# Phase 1D Repair Feedback Ablation Summary",
        "",
        f"- source_report_dir: `{summary['source_report_dir']}`",
        f"- output_dir: `{summary['output_dir']}`",
        f"- run_label: `{summary.get('run_label')}`",
        f"- run_label_source: `{summary.get('run_label_source')}`",
        f"- run_dir_name: `{summary.get('run_dir_name')}`",
        f"- run_timestamp: `{summary.get('run_timestamp')}`",
        f"- provider_name: `{provider.get('provider_name')}`",
        f"- base_url: `{provider.get('base_url')}`",
        f"- model: `{provider.get('model')}`",
        "",
        "| feedback_mode | total_cases | repair_success_rate | "
        "final_pass_rate | avg_repair_rounds | output_dir |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in summary["modes"]:
        rates = item["rates"]
        lines.append(
            "| {mode} | {total} | {repair:.4g} | {final:.4g} | "
            "{rounds:.4g} | `{out}` |".format(
                mode=item["feedback_mode"],
                total=item["total_cases"],
                repair=rates["repair_success_rate"],
                final=rates["final_pass_rate"],
                rounds=rates["avg_repair_rounds"],
                out=item["output_dir"],
            )
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
