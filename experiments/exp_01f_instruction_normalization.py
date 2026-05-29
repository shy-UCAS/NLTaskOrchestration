"""
experiments/exp_01f_instruction_normalization.py

Phase 1F：指令规范化实验。

两种运行模式：
  --mode single-shot（默认）：单次分析，评估首轮识别能力
  --mode clarification-loop：走完整澄清闭环，评估多轮交互效果

用法：
  # 默认从 configs/llm_providers.local.yaml 读取 profile
  python -m experiments.exp_01f_instruction_normalization --provider-profile <profile_name> --workers 8

  # 跑 dev/smoke 集，或切到澄清闭环
  python -m experiments.exp_01f_instruction_normalization --provider-profile <profile_name> --dataset datasets/phase1_instruction_normalization_dev.jsonl
  python -m experiments.exp_01f_instruction_normalization --provider-profile <profile_name> --mode clarification-loop --dataset datasets/phase1_instruction_normalization_eval.jsonl

  # 临时指定其他 provider 配置文件
  python -m experiments.exp_01f_instruction_normalization --config configs/llm_providers.local.yaml --provider-profile <profile_name>
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
    run_cases_concurrent,
    save_baseline_json,
    write_latest_run_index,
)


EXPERIMENT_NAME = "exp_01f_instruction_normalization"

GCJP_TASK_REQUIRED_FIELDS = (
    "actor",
    "action",
    "target",
    "duration_lb",
    "energy_cost",
    "ammo_cost",
)

MISSING_FIELD_ALIASES = {
    "actor": "assigned_actors",
    "actors": "assigned_actors",
    "assigned_actor": "assigned_actors",
    "assigned_actors": "assigned_actors",
    "duration": "duration_lb",
    "duration_lb": "duration_lb",
    "task_duration": "duration_lb",
    "time": "duration_lb",
    "missing_time": "duration_lb",
    "energy": "energy_cost",
    "energy_consumption": "energy_cost",
    "energy_cost": "energy_cost",
    "ammo": "ammo_cost",
    "ammo_consumption": "ammo_cost",
    "ammo_cost": "ammo_cost",
    "resource": "resource_constraints",
    "resources": "resource_constraints",
    "resource_constraint": "resource_constraints",
    "resource_constraints": "resource_constraints",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("datasets") / "phase1_instruction_normalization_eval.jsonl",
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

    def _worker(case: dict[str, Any]) -> dict[str, Any]:
        sample_id = case["sample_id"]
        try:
            if args.mode == "single-shot":
                return _run_single_shot(
                    case, agent, prompt_template, sample_id,
                )
            return _run_clarification_loop(
                case, agent, prompt_template, sample_id,
                max_rounds=args.max_clarification_rounds,
            )
        except Exception as exc:
            return _error_record(case, exc)

    def _on_complete(record: dict[str, Any]) -> None:
        sample_id = record["sample_id"]
        (raw_dir / f"{sample_id}.txt").write_text(
            record.get("raw_response", ""), encoding="utf-8",
        )
        parsed = record.get("parsed_output")
        (parsed_dir / f"{sample_id}.json").write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2) if parsed else "null",
            encoding="utf-8",
        )
        print(_summary_line(record))

    records = run_cases_concurrent(
        cases,
        _worker,
        workers=getattr(args, "workers", 1),
        on_complete=_on_complete,
        show_usage=getattr(args, "show_usage", False),
    )

    metrics = _aggregate_metrics(records, args.mode, cases)
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
        "expected_status": case.get("expected_status"),
        "raw_response": result.raw_response,
        "parsed_output": result.parsed_output,
        "extraction": result.extraction,
        "predicted_status": result.status,
        "model_reported_status": result.model_reported_status,
        "status_overridden_by_invariant": result.status_overridden_by_invariant,
        "usage": result.usage,
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
    expected_after = case.get("expected_status_after_clarification", "complete")
    eval_case = {
        **case,
        "expected_status": expected_after,
    }
    if expected_after == "complete":
        eval_case["expected_missing_fields"] = []
    evaluation = _evaluate_normalization(eval_case, final) if final else {}
    evaluation["loop_final_status"] = loop_result.final_status
    evaluation["total_rounds"] = loop_result.total_rounds
    evaluation["clarification_success"] = (
        loop_result.final_status == "complete"
        and evaluation.get("gcjp_ready_structure") is not False
    )
    evaluation["post_clarification_correct"] = evaluation.get(
        "status_correct", False,
    )

    return {
        "sample_id": sample_id,
        "mode": "clarification-loop",
        "expected_status": expected_after,
        "raw_response": final.raw_response if final else "",
        "parsed_output": final.parsed_output if final else None,
        "extraction": final.extraction if final else {},
        "predicted_status": final.status if final else None,
        "model_reported_status": final.model_reported_status if final else None,
        "status_overridden_by_invariant": (
            final.status_overridden_by_invariant if final else False
        ),
        "clarification_history": loop_result.clarification_history,
        "total_rounds": loop_result.total_rounds,
        "loop_final_status": loop_result.final_status,
        "usage": [r.usage for r in loop_result.all_results if r.usage],
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
            "gcjp_ready_structure": None,
            "missing_gcjp_task_fields": [],
        }

    expected_status = case["expected_status"]
    predicted_status = result.status

    json_ok = result.extraction.get("ok", False)
    gcjp_ready_structure = None
    missing_gcjp_task_fields: list[str] = []
    if predicted_status == "complete":
        gcjp_ready_structure, missing_gcjp_task_fields = (
            _complete_output_is_gcjp_ready(result.parsed_output)
        )

    status_correct = predicted_status == expected_status
    if predicted_status == "complete" and not gcjp_ready_structure:
        status_correct = False

    expected_missing = _canonical_missing_fields(
        case.get("expected_missing_fields", []),
    )
    predicted_missing = _canonical_missing_fields(result.missing_fields or [])
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
        "gcjp_ready_structure": gcjp_ready_structure,
        "missing_gcjp_task_fields": missing_gcjp_task_fields,
    }


def _canonical_missing_fields(fields: list[Any]) -> set[str]:
    canonical: set[str] = set()
    for field in fields:
        normalized = _canonical_missing_field(field)
        if normalized:
            canonical.add(normalized)
    return canonical


def _canonical_missing_field(field: Any) -> str:
    if not isinstance(field, str):
        return ""

    name = field.strip().lower().replace("-", "_").replace(" ", "_")
    if not name:
        return ""

    direct = MISSING_FIELD_ALIASES.get(name)
    if direct:
        return direct

    if "duration" in name or "持续" in name or "时间" in name:
        return "duration_lb"
    if "energy_cost" in name or "energy_consumption" in name or "能量消耗" in name:
        return "energy_cost"
    if "ammo_cost" in name or "ammo_consumption" in name or "弹药消耗" in name:
        return "ammo_cost"
    if "resource" in name or "资源" in name:
        return "resource_constraints"
    if "actor" in name or "编队" in name or "主体" in name:
        return "assigned_actors"

    return name


def _complete_output_is_gcjp_ready(
    parsed_output: dict[str, Any] | None,
) -> tuple[bool, list[str]]:
    missing: list[str] = []
    if not isinstance(parsed_output, dict):
        return False, ["parsed_output"]

    resolved = parsed_output.get("resolved_fields")
    if not isinstance(resolved, dict):
        return False, ["resolved_fields"]

    actors = resolved.get("assigned_actors")
    if not isinstance(actors, list) or not actors:
        missing.append("assigned_actors")

    tasks = resolved.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        missing.append("tasks")
        return False, missing

    for idx, task in enumerate(tasks):
        if not isinstance(task, dict):
            missing.append(f"tasks[{idx}]")
            continue
        for field_name in GCJP_TASK_REQUIRED_FIELDS:
            if not _task_field_has_gcjp_value(field_name, task.get(field_name)):
                missing.append(f"tasks[{idx}].{field_name}")

    return not missing, missing


def _task_field_has_gcjp_value(field_name: str, value: Any) -> bool:
    if field_name in {"actor", "action", "target"}:
        return isinstance(value, str) and bool(value.strip())

    if field_name == "duration_lb":
        number = _as_float(value)
        return number is not None and number > 0

    if field_name == "energy_cost":
        number = _as_float(value)
        return number is not None and number >= 0

    if field_name == "ammo_cost":
        number = _as_float(value)
        return number is not None and number >= 0 and number.is_integer()

    return value is not None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _aggregate_metrics(
    records: list[dict[str, Any]],
    mode: str,
    cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    total = len(records)
    if total == 0:
        return {"experiment": EXPERIMENT_NAME, "total_cases": 0, "rates": {}}

    evals = [r["evaluation"] for r in records]
    case_index = {c["sample_id"]: c for c in (cases or [])}

    rates: dict[str, float | dict[str, int]] = {
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

        false_complete_records = [
            r for r, e in incomplete_cases if e.get("false_complete")
        ]
        if false_complete_records:
            by_category = {"missing_field": 0, "ambiguous_only": 0, "other": 0}
            for r in false_complete_records:
                case = case_index.get(r["sample_id"], {})
                if case.get("expected_missing_fields"):
                    by_category["missing_field"] += 1
                elif case.get("expected_ambiguity_spans"):
                    by_category["ambiguous_only"] += 1
                else:
                    by_category["other"] += 1
            rates["false_complete_by_category"] = by_category

    consistency_total = sum(
        1 for r in records if r.get("model_reported_status") is not None
    )
    if consistency_total:
        overrides = sum(
            1 for r in records if r.get("status_overridden_by_invariant")
        )
        rates["invariant_override_rate"] = overrides / consistency_total
        rates["model_self_consistency_rate"] = (
            consistency_total - overrides
        ) / consistency_total

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

    complete_records = [
        (r, e) for r, e in zip(records, evals)
        if r.get("predicted_status") == "complete"
    ]
    if complete_records:
        rates["complete_structure_success_rate"] = (
            sum(
                1 for _, e in complete_records
                if e.get("gcjp_ready_structure")
            )
            / len(complete_records)
        )

    return {
        "experiment": EXPERIMENT_NAME,
        "total_cases": total,
        "rates": rates,
        "records": [
            {
                "sample_id": r["sample_id"],
                "expected_status": r.get("expected_status"),
                "predicted_status": r.get("predicted_status"),
                "model_reported_status": r.get("model_reported_status"),
                "status_overridden_by_invariant": r.get(
                    "status_overridden_by_invariant", False,
                ),
                "evaluation": r["evaluation"],
            }
            for r in records
        ],
    }


def _case_is_incomplete(record: dict[str, Any]) -> bool:
    """判断原始 case 是否为 incomplete 类型。"""
    expected_status = record.get("expected_status")
    if expected_status is not None:
        return expected_status == "incomplete"
    return "incomplete" in record.get("sample_id", "")


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
        "expected_status": case.get("expected_status"),
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
            "gcjp_ready_structure": None,
            "missing_gcjp_task_fields": [],
            "error": f"{type(exc).__name__}: {exc}",
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
