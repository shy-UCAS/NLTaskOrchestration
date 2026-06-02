"""
experiments/exp_01e_simulated_natural_failure_generation.py
用法：
  # 默认从 configs/llm_providers.local.yaml 读取 profile
  python -m experiments.exp_01e_simulated_natural_failure_generation --provider-profile <profile_name> --workers 8

  # 覆盖 specs 数据集或 prompt
  python -m experiments.exp_01e_simulated_natural_failure_generation --provider-profile <profile_name> --dataset datasets/phase1_simulated_failure_specs.jsonl --prompt prompts/gcjp_simulated_natural_failure_prompt.md

Phase 1E：LLM 模拟自然失败报告生成实验。

场景：
  从 datasets/phase1_simulated_failure_specs.jsonl 读取失败规格（bug_spec），
  经 prompts/gcjp_simulated_natural_failure_prompt.md 模板渲染后发送给 LLM，
  要求 LLM 生成包含指定 bug 的可提取 GCJP 代码。
  有效的模拟失败写入 reports/ 供 Phase 1C/1D 消费；无效生成写入 invalid_reports/ 供审计。

预期输出：
  out/phase1_generation/exp_01e_simulated_natural_failure_generation/ 下输出
  reports/（有效失败报告）、invalid_reports/（无效生成）、汇总 metrics.json。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from agents.code_extraction import extract_gcjp_code
from agents.llm_client import LLMClient, LLMConfigError
from agents.planner_agent import PlannerGeneration
from experiments.phase1_common import (
    _aggregate_metrics,
    _ensure_output_dirs,
    _error_record,
    _evaluate_generation,
    _summary_line,
    _write_case_outputs,
    add_common_args,
    handle_config_error,
    load_config_from_args,
    load_jsonl,
    phase1_run_metadata_json,
    print_provider_summary_from_args,
    read_prompt_template,
    resolve_phase1_run_output,
    run_cases_concurrent,
    write_latest_run_index,
)


EXPERIMENT_NAME = "exp_01e_simulated_natural_failure_generation"
DEFAULT_DATASET = Path("datasets") / "phase1_simulated_failure_specs.jsonl"
DEFAULT_PROMPT = Path("prompts") / "gcjp_simulated_natural_failure_prompt.md"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    args = parser.parse_args()

    try:
        print_provider_summary_from_args(args)
        run_simulated_failure_experiment(args)
    except LLMConfigError as exc:
        return handle_config_error(exc)
    return 0


def run_simulated_failure_experiment(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_jsonl(args.dataset, limit=args.limit)
    if not cases:
        raise ValueError(f"No simulated failure specs loaded from {args.dataset}")

    config = load_config_from_args(args)
    provider_summary = config.safe_summary()
    run_output = resolve_phase1_run_output(
        output_dir=args.output_dir,
        provider_summary=provider_summary,
        run_label=args.run_label,
        no_run_timestamp=args.no_run_timestamp,
    )
    client = LLMClient(config)
    prompt_template = read_prompt_template(args.prompt)
    output_dirs = _ensure_output_dirs(run_output["run_dir"], EXPERIMENT_NAME)
    output_dirs["invalid_reports"] = output_dirs["root"] / "invalid_reports"
    output_dirs["invalid_reports"].mkdir(parents=True, exist_ok=True)

    def _worker(case: dict[str, Any]) -> dict[str, Any]:
        try:
            generation = _generate_simulated_failure(
                client=client,
                prompt_template=prompt_template,
                case=case,
            )
            record = _evaluate_generation(case, generation)
        except Exception as exc:
            record = _error_record(case, exc)
        _attach_simulation_metadata(record)
        return record

    def _on_complete(record: dict[str, Any]) -> None:
        sample_id = record["sample_id"]
        if _is_valid_simulated_failure(record):
            _write_case_outputs(output_dirs, sample_id, record)
        else:
            invalid_output_dirs = dict(output_dirs)
            invalid_output_dirs["reports"] = output_dirs["invalid_reports"]
            _write_case_outputs(invalid_output_dirs, sample_id, record)
        print(_summary_line(record) + _simulation_suffix(record))

    records = run_cases_concurrent(
        cases,
        _worker,
        workers=getattr(args, "workers", 1),
        on_complete=_on_complete,
        show_usage=getattr(args, "show_usage", False),
    )
    valid_records = [r for r in records if _is_valid_simulated_failure(r)]

    metrics = _aggregate_simulated_metrics(records, valid_records)
    metrics.update(phase1_run_metadata_json(run_output))
    metrics["output_dir"] = str(output_dirs["root"])
    metrics["reports_dir"] = str(output_dirs["reports"])
    metrics["invalid_reports_dir"] = str(output_dirs["invalid_reports"])

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

    print(f"\n[{EXPERIMENT_NAME}] 汇总指标 -> {metrics_path}")
    print(json.dumps(metrics["rates"], ensure_ascii=False, indent=2))
    return metrics


def _generate_simulated_failure(
    *,
    client: LLMClient,
    prompt_template: str,
    case: dict[str, Any],
) -> PlannerGeneration:
    prompt = _render_simulated_failure_prompt(prompt_template, case)
    response = client.generate(
        [
            {
                "role": "system",
                "content": (
                    "Generate extractable GCJP v1 Python code that intentionally "
                    "contains the requested realistic bug. Output code only."
                ),
            },
            {"role": "user", "content": prompt},
        ]
    )
    extraction = extract_gcjp_code(response.text)
    return PlannerGeneration(
        sample_id=case["sample_id"],
        prompt=prompt,
        raw_response=response.text,
        extracted_code=extraction.code,
        extraction={
            "ok": extraction.ok,
            "code": extraction.code,
            "method": extraction.method,
            "error": extraction.error,
        },
        model=response.model,
        model_source=response.model_source,
        provider=response.provider,
        usage=response.usage,
    )


def _render_simulated_failure_prompt(
    prompt_template: str,
    case: dict[str, Any],
) -> str:
    source_case = case.get("source_case") or {}
    bug_spec = case.get("bug_spec") or {}
    replacements = {
        "{{CASE_JSON}}": json.dumps(case, ensure_ascii=False, indent=2),
        "{{SOURCE_CASE_JSON}}": json.dumps(source_case, ensure_ascii=False, indent=2),
        "{{BUG_SPEC_JSON}}": json.dumps(bug_spec, ensure_ascii=False, indent=2),
        "{{STANDARD_INSTRUCTION}}": source_case.get("standard_instruction", ""),
    }
    prompt = prompt_template
    for key, value in replacements.items():
        prompt = prompt.replace(key, value)
    return prompt


def _attach_simulation_metadata(record: dict[str, Any]) -> None:
    case = record.get("case") or {}
    bug_spec = case.get("bug_spec") or {}
    observed_layer = _observed_failure_layer(record)
    expected_layer = bug_spec.get("expected_failure_layer")
    valid = _is_valid_simulated_failure(record)
    record["simulation"] = {
        "bug_type": bug_spec.get("bug_type"),
        "target": bug_spec.get("target"),
        "expected_failure_layer": expected_layer,
        "observed_failure_layer": observed_layer,
        "expected_failure_layer_match": _failure_layer_matches(
            expected_layer,
            observed_layer,
        ),
        "valid_simulated_failure": valid,
        "invalid_reason": None if valid else _invalid_reason(record),
    }


def _is_valid_simulated_failure(record: dict[str, Any]) -> bool:
    evaluation = record.get("evaluation") or {}
    return (
        bool(evaluation.get("syntax_extract"))
        and not bool(evaluation.get("first_pass"))
        and record.get("execution_error_type") != "NO_CODE"
    )


def _invalid_reason(record: dict[str, Any]) -> str:
    evaluation = record.get("evaluation") or {}
    if not evaluation.get("syntax_extract"):
        return "not_extractable"
    if record.get("execution_error_type") == "NO_CODE":
        return "no_code"
    if evaluation.get("first_pass"):
        return "first_pass"
    return "unknown"


def _observed_failure_layer(record: dict[str, Any]) -> str:
    evaluation = record.get("evaluation") or {}
    if not evaluation.get("syntax_extract"):
        return "extraction"
    if not evaluation.get("safety_pass"):
        return "safety"
    if not evaluation.get("execution_success"):
        return "execution"
    if not evaluation.get("l2_graph_pass"):
        return "l2"
    if not evaluation.get("l3_expected_result"):
        return "l3"
    if not evaluation.get("first_pass"):
        return "pattern"
    return "first_pass"


def _failure_layer_matches(expected: Any, observed: str) -> bool:
    if not expected:
        return False
    expected_text = str(expected).strip().lower()
    aliases = {
        "layer1": "safety",
        "safety_check": "safety",
        "exec": "execution",
        "layer2": "l2",
        "layer3": "l3",
    }
    return aliases.get(expected_text, expected_text) == observed


def _aggregate_simulated_metrics(
    records: list[dict[str, Any]],
    valid_records: list[dict[str, Any]],
) -> dict[str, Any]:
    metrics = _aggregate_metrics(EXPERIMENT_NAME, records)
    total = len(records)
    valid_total = len(valid_records)

    bug_dist = Counter(
        (r.get("simulation") or {}).get("bug_type") or "UNKNOWN"
        for r in records
    )
    valid_bug_dist = Counter(
        (r.get("simulation") or {}).get("bug_type") or "UNKNOWN"
        for r in valid_records
    )
    invalid_reason_dist = Counter(
        (r.get("simulation") or {}).get("invalid_reason") or "valid"
        for r in records
    )
    expected_layer_cases = [
        r for r in records
        if (r.get("simulation") or {}).get("expected_failure_layer")
    ]
    layer_matches = sum(
        1
        for r in expected_layer_cases
        if (r.get("simulation") or {}).get("expected_failure_layer_match")
    )

    rates = metrics["rates"]
    rates["simulated_valid_failure_rate"] = valid_total / total if total else 0.0
    rates["no_code_rate"] = (
        sum(1 for r in records if r.get("execution_error_type") == "NO_CODE") / total
        if total else 0.0
    )
    rates["expected_failure_layer_match_rate"] = (
        layer_matches / len(expected_layer_cases)
        if expected_layer_cases else 0.0
    )

    metrics["valid_report_cases"] = valid_total
    metrics["invalid_report_cases"] = total - valid_total
    metrics["bug_type_distribution"] = dict(sorted(bug_dist.items()))
    metrics["valid_bug_type_distribution"] = dict(sorted(valid_bug_dist.items()))
    metrics["invalid_reason_distribution"] = dict(sorted(invalid_reason_dist.items()))
    metrics["records"] = [
        {
            "sample_id": r["sample_id"],
            "evaluation": r["evaluation"],
            "execution_error_type": r.get("execution_error_type"),
            "simulation": r.get("simulation"),
        }
        for r in records
    ]
    return metrics


def _simulation_suffix(record: dict[str, Any]) -> str:
    simulation = record.get("simulation") or {}
    valid = simulation.get("valid_simulated_failure")
    bug_type = simulation.get("bug_type")
    observed = simulation.get("observed_failure_layer")
    reason = simulation.get("invalid_reason")
    parts = [
        f"sim_valid={valid}",
        f"bug_type={bug_type}",
        f"observed_layer={observed}",
    ]
    if reason:
        parts.append(f"invalid_reason={reason}")
    return " " + " ".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
