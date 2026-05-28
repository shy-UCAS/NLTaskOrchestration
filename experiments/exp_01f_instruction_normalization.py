"""
experiments/exp_01f_instruction_normalization.py

Phase 1F：指令规范化实验。

两种运行模式：
  --mode single-shot（默认）：单次分析，评估首轮识别能力
  --mode clarification-loop：走完整澄清闭环，评估多轮交互效果

用法：
  python -m experiments.exp_01f_instruction_normalization --local-provider claude --limit 2
  python -m experiments.exp_01f_instruction_normalization --local-provider claude --mode clarification-loop
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agents.clarification_loop import ClarificationLoop, ClarificationLoopResult, scripted_input_fn
from agents.instruction_normalizer_agent import (
    InstructionNormalizerAgent,
    NormalizationResult,
)
from agents.llm_client import LLMClient, LLMConfigError
from experiments.phase1_common import (
    add_common_args,
    append_baseline_markdown,
    handle_config_error,
    load_config_from_args,
    load_jsonl,
    phase1_run_metadata_json,
    print_provider_summary_from_args,
    read_prompt_template,
    resolve_phase1_run_output,
    save_baseline_json,
    write_latest_run_index,
)


EXPERIMENT_NAME = "exp_01f_instruction_normalization"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("datasets") / "phase1_ambiguous_nl_cases.jsonl",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        default=Path("prompts") / "instruction_normalization_prompt.md",
    )
    parser.add_argument(
        "--mode",
        choices=["single-shot", "clarification-loop"],
        default="single-shot",
    )
    parser.add_argument("--max-clarification-rounds", type=int, default=5)
    args = parser.parse_args()

    try:
        print_provider_summary_from_args(args)
        run_normalization_experiment(args)
    except LLMConfigError as exc:
        return handle_config_error(exc)
    return 0


def run_normalization_experiment(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_jsonl(args.dataset, limit=args.limit)
    if not cases:
        raise ValueError(f"No cases loaded from {args.dataset}")

    config = load_config_from_args(args)
    provider_summary = config.safe_summary()
    agent = InstructionNormalizerAgent(LLMClient(config))
    prompt_template = read_prompt_template(args.prompt)

    run_output = resolve_phase1_run_output(
        output_dir=args.output_dir,
        provider_summary=provider_summary,
        run_label=args.run_label,
        no_run_timestamp=args.no_run_timestamp,
    )
    exp_dir = run_output["run_dir"] / EXPERIMENT_NAME
    exp_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = exp_dir / "raw_outputs"
    parsed_dir = exp_dir / "parsed_outputs"
    raw_dir.mkdir(exist_ok=True)
    parsed_dir.mkdir(exist_ok=True)

    records = []
    for case in cases:
        sample_id = case["sample_id"]
        try:
            if args.mode == "single-shot":
                record = _run_single_shot(
                    case, agent, prompt_template, sample_id,
                )
            else:
                record = _run_clarification_loop(
                    case, agent, prompt_template, sample_id,
                    max_rounds=args.max_clarification_rounds,
                )
        except Exception as exc:
            record = _error_record(case, exc)

        (raw_dir / f"{sample_id}.txt").write_text(
            record.get("raw_response", ""), encoding="utf-8",
        )
        parsed = record.get("parsed_output")
        (parsed_dir / f"{sample_id}.json").write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2) if parsed else "null",
            encoding="utf-8",
        )
        records.append(record)
        print(_summary_line(record))

    metrics = _aggregate_metrics(records, args.mode)
    metrics.update(phase1_run_metadata_json(run_output))
    metrics["mode"] = args.mode
    metrics["output_dir"] = str(exp_dir)

    metrics_path = exp_dir / "metrics.json"
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    write_latest_run_index(
        run_output=run_output,
        experiment_name=EXPERIMENT_NAME,
        experiment_dir=exp_dir,
        reports_dir=None,
        summary_path=metrics_path,
    )

    print(f"\n[{EXPERIMENT_NAME}] 汇总指标 ({args.mode}) -> {metrics_path}")
    print(json.dumps(metrics["rates"], ensure_ascii=False, indent=2))

    if args.save_baseline:
        save_baseline_json(EXPERIMENT_NAME, metrics)
        append_baseline_markdown(EXPERIMENT_NAME, metrics)

    return metrics


def _run_single_shot(
    case: dict[str, Any],
    agent: InstructionNormalizerAgent,
    prompt_template: str,
    sample_id: str,
) -> dict[str, Any]:
    result = agent.normalize(
        sample_id=sample_id,
        prompt_template=prompt_template,
        raw_instruction=case["raw_instruction"],
    )
    evaluation = _evaluate_normalization(case, result)
    return {
        "sample_id": sample_id,
        "mode": "single-shot",
        "raw_response": result.raw_response,
        "parsed_output": result.parsed_output,
        "extraction": result.extraction,
        "predicted_status": result.status,
        "evaluation": evaluation,
    }


def _run_clarification_loop(
    case: dict[str, Any],
    agent: InstructionNormalizerAgent,
    prompt_template: str,
    sample_id: str,
    *,
    max_rounds: int = 5,
) -> dict[str, Any]:
    answers = case.get("scripted_clarifications", [])
    loop = ClarificationLoop(
        agent,
        max_rounds=max_rounds,
        input_fn=scripted_input_fn(answers),
    )
    loop_result = loop.run(
        sample_id=sample_id,
        prompt_template=prompt_template,
        raw_instruction=case["raw_instruction"],
    )

    final = loop_result.final_result
    evaluation = _evaluate_normalization(case, final) if final else {}
    evaluation["loop_final_status"] = loop_result.final_status
    evaluation["total_rounds"] = loop_result.total_rounds
    evaluation["clarification_success"] = loop_result.final_status == "complete"

    expected_after = case.get("expected_status_after_clarification", "complete")
    evaluation["post_clarification_correct"] = (
        loop_result.final_status == "complete"
        if expected_after == "complete"
        else loop_result.final_status != "complete"
    )

    return {
        "sample_id": sample_id,
        "mode": "clarification-loop",
        "raw_response": final.raw_response if final else "",
        "parsed_output": final.parsed_output if final else None,
        "extraction": final.extraction if final else {},
        "predicted_status": final.status if final else None,
        "clarification_history": loop_result.clarification_history,
        "total_rounds": loop_result.total_rounds,
        "loop_final_status": loop_result.final_status,
        "evaluation": evaluation,
    }


def _evaluate_normalization(
    case: dict[str, Any],
    result: NormalizationResult | None,
) -> dict[str, Any]:
    if result is None:
        return {
            "json_parse_success": False,
            "status_correct": False,
            "missing_field_detected": False,
            "ambiguity_detected": False,
            "false_complete": False,
        }

    expected_status = case["expected_status"]
    predicted_status = result.status

    json_ok = result.extraction.get("ok", False)
    status_correct = predicted_status == expected_status

    expected_missing = set(case.get("expected_missing_fields", []))
    predicted_missing = set(result.missing_fields or [])
    missing_detected = (
        expected_missing.issubset(predicted_missing) if expected_missing else True
    )

    expected_ambiguities = case.get("expected_ambiguity_spans", [])
    predicted_ambiguities = result.ambiguities or []
    ambiguity_detected = (
        len(predicted_ambiguities) >= len(expected_ambiguities)
        if expected_ambiguities
        else True
    )

    false_complete = (
        expected_status == "incomplete" and predicted_status == "complete"
    )

    return {
        "json_parse_success": json_ok,
        "status_correct": status_correct,
        "missing_field_detected": missing_detected,
        "ambiguity_detected": ambiguity_detected,
        "false_complete": false_complete,
    }


def _aggregate_metrics(
    records: list[dict[str, Any]], mode: str,
) -> dict[str, Any]:
    total = len(records)
    if total == 0:
        return {"experiment": EXPERIMENT_NAME, "total_cases": 0, "rates": {}}

    evals = [r["evaluation"] for r in records]

    rates: dict[str, float] = {
        "json_parse_success_rate": (
            sum(1 for e in evals if e.get("json_parse_success")) / total
        ),
        "status_accuracy_rate": (
            sum(1 for e in evals if e.get("status_correct")) / total
        ),
    }

    incomplete_cases = [
        (r, e) for r, e in zip(records, evals)
        if r.get("sample_id", "").startswith("1f_")
        and _case_is_incomplete(r)
    ]
    if incomplete_cases:
        rates["missing_field_detection_rate"] = (
            sum(1 for _, e in incomplete_cases if e.get("missing_field_detected"))
            / len(incomplete_cases)
        )
        ambiguity_cases = [
            (r, e) for r, e in incomplete_cases
        ]
        rates["ambiguity_detection_rate"] = (
            sum(1 for _, e in ambiguity_cases if e.get("ambiguity_detected"))
            / len(ambiguity_cases)
        )
        rates["false_complete_rate"] = (
            sum(1 for _, e in incomplete_cases if e.get("false_complete"))
            / len(incomplete_cases)
        )

    if mode == "clarification-loop":
        loop_records = [r for r in records if r.get("mode") == "clarification-loop"]
        if loop_records:
            loop_evals = [r["evaluation"] for r in loop_records]
            rates["clarification_success_rate"] = (
                sum(1 for e in loop_evals if e.get("clarification_success"))
                / len(loop_records)
            )
            rounds = [r.get("total_rounds", 1) for r in loop_records]
            rates["avg_clarification_rounds"] = sum(rounds) / len(rounds)
            rates["clarification_efficiency"] = (
                sum(1 for r in rounds if r <= 1) / len(rounds)
            )

    return {
        "experiment": EXPERIMENT_NAME,
        "total_cases": total,
        "rates": rates,
        "records": [
            {
                "sample_id": r["sample_id"],
                "predicted_status": r.get("predicted_status"),
                "evaluation": r["evaluation"],
            }
            for r in records
        ],
    }


def _case_is_incomplete(record: dict[str, Any]) -> bool:
    """判断原始 case 是否为 incomplete 类型（从 sample_id 的 tag 或 evaluation 推断）。"""
    eval_data = record.get("evaluation", {})
    return eval_data.get("false_complete") is not None and not eval_data.get(
        "status_correct", True
    ) or "incomplete" in record.get("sample_id", "")


def _summary_line(record: dict[str, Any]) -> str:
    ev = record.get("evaluation", {})
    sid = record["sample_id"]
    status = record.get("predicted_status", "?")
    json_ok = ev.get("json_parse_success", False)
    correct = ev.get("status_correct", False)
    mode = record.get("mode", "single-shot")
    parts = [
        f"[{EXPERIMENT_NAME}]",
        sid,
        f"json={json_ok}",
        f"status={status}",
        f"correct={correct}",
    ]
    if mode == "clarification-loop":
        parts.append(f"rounds={record.get('total_rounds', '?')}")
        parts.append(f"loop={record.get('loop_final_status', '?')}")
    return "  ".join(parts)


def _error_record(case: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "sample_id": case["sample_id"],
        "mode": "error",
        "raw_response": "",
        "parsed_output": None,
        "extraction": {},
        "predicted_status": None,
        "evaluation": {
            "json_parse_success": False,
            "status_correct": False,
            "missing_field_detected": False,
            "ambiguity_detected": False,
            "false_complete": False,
            "error": f"{type(exc).__name__}: {exc}",
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
