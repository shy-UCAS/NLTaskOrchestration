"""
experiments/exp_03_single_agent_nl_pipeline.py

NL 全串联单 Agent 原型：raw NL → 指令规范化（含澄清闭环）→ GCJP 生成 → Z3 验证。

这是 M-2 决策点实验：端到端 Z3 通过率 ≥ 60% 则进入 Layer 4/5。

数据源：
  - datasets/phase1_ambiguous_nl_cases.jsonl 中的 complete 样本
  - datasets/seed/gcjp_seed.jsonl 中的 nl_instruction

用法：
  # 默认从 configs/llm_providers.local.yaml 读取 profile
  python -m experiments.exp_03_single_agent_nl_pipeline --provider-profile <profile_name> --limit 2 --workers 4

  # 覆盖数据集、NL 字段，或进入交互澄清
  python -m experiments.exp_03_single_agent_nl_pipeline --provider-profile <profile_name> --dataset datasets/seed/gcjp_seed.jsonl --nl-field nl_instruction
  python -m experiments.exp_03_single_agent_nl_pipeline --provider-profile <profile_name> --interactive --workers 1
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agents.clarification_loop import ClarificationLoop, scripted_input_fn
from agents.instruction_normalizer_agent import InstructionNormalizerAgent
from agents.llm_client import LLMClient, LLMConfigError
from agents.planner_agent import PlannerAgent
from gcjp.code_executor import execute_gcjp_code
from verifier.pipeline import VerificationPipeline
from experiments.phase1_common import (
    Z3_LOCK,
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


EXPERIMENT_NAME = "exp_03_single_agent_nl_pipeline"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("datasets") / "phase1_ambiguous_nl_cases.jsonl",
    )
    parser.add_argument(
        "--nl-field",
        default="raw_instruction",
        help="JSONL field containing the NL instruction (default: raw_instruction)",
    )
    parser.add_argument(
        "--normalization-prompt",
        type=Path,
        default=Path("prompts") / "instruction_normalization_prompt.md",
    )
    parser.add_argument(
        "--generation-prompt",
        type=Path,
        default=Path("prompts") / "standard_nl_to_gcjp_prompt.md",
    )
    parser.add_argument("--max-clarification-rounds", type=int, default=5)
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Use terminal input for clarification.",
    )
    args = parser.parse_args()

    try:
        print_provider_summary_from_args(args)
        run_e2e_experiment(args)
    except LLMConfigError as exc:
        return handle_config_error(exc)
    return 0


def run_e2e_experiment(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_jsonl(args.dataset, limit=args.limit)
    if not cases:
        raise ValueError(f"No cases loaded from {args.dataset}")

    workers = getattr(args, "workers", 1)
    if args.interactive and workers > 1:
        print(
            f"[{EXPERIMENT_NAME}] interactive 模式与 --workers>1 互斥,"
            f"自动回退到 --workers 1。"
        )
        workers = 1

    config = load_config_from_args(args)
    provider_summary = config.safe_summary()
    client = LLMClient(config)

    normalizer = InstructionNormalizerAgent(client)
    planner = PlannerAgent(client)
    norm_prompt = read_prompt_template(args.normalization_prompt)
    gen_prompt = read_prompt_template(args.generation_prompt)

    run_output = resolve_phase1_run_output(
        output_dir=args.output_dir,
        provider_summary=provider_summary,
        run_label=args.run_label,
        no_run_timestamp=args.no_run_timestamp,
    )
    exp_dir = run_output["run_dir"] / EXPERIMENT_NAME
    for sub in ("normalization", "generated_code", "reports"):
        (exp_dir / sub).mkdir(parents=True, exist_ok=True)

    runnable_cases = [
        case for case in cases if case.get(args.nl_field, "")
    ]

    def _worker(case: dict[str, Any]) -> dict[str, Any]:
        sample_id = case["sample_id"]
        nl_instruction = case.get(args.nl_field, "")
        try:
            return _run_case(
                sample_id=sample_id,
                nl_instruction=nl_instruction,
                case=case,
                normalizer=normalizer,
                planner=planner,
                norm_prompt=norm_prompt,
                gen_prompt=gen_prompt,
                max_rounds=args.max_clarification_rounds,
                interactive=args.interactive,
            )
        except Exception as exc:
            return {
                "sample_id": sample_id,
                "stage": "error",
                "normalization": None,
                "generation": None,
                "report": None,
                "evaluation": {
                    "end_to_end_pass": False,
                    "failure_attribution": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            }

    def _on_complete(record: dict[str, Any]) -> None:
        _write_outputs(exp_dir, record)
        print(_summary_line(record))

    records = run_cases_concurrent(
        runnable_cases,
        _worker,
        workers=workers,
        on_complete=_on_complete,
        show_usage=getattr(args, "show_usage", False),
    )

    metrics = _aggregate_metrics(records)
    metrics.update(phase1_run_metadata_json(run_output))
    metrics["output_dir"] = str(exp_dir)

    metrics_path = exp_dir / "metrics.json"
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    write_latest_run_index(
        run_output=run_output,
        experiment_name=EXPERIMENT_NAME,
        experiment_dir=exp_dir,
        reports_dir=exp_dir / "reports",
        summary_path=metrics_path,
    )

    print(f"\n[{EXPERIMENT_NAME}] 汇总指标 -> {metrics_path}")
    print(json.dumps(metrics["rates"], ensure_ascii=False, indent=2))

    e2e_rate = metrics["rates"].get("end_to_end_z3_pass_rate", 0.0)
    print(f"\n{'='*50}")
    if e2e_rate >= 0.6:
        print(f"  M-2 决策: PASS (e2e={e2e_rate:.2%} >= 60%)")
        print("  建议: 进入 Layer 4/5 (多Agent + HITL)")
    elif e2e_rate >= 0.4:
        print(f"  M-2 决策: MARGINAL (e2e={e2e_rate:.2%})")
        print("  建议: 分析 failure_attribution，针对性增强 prompt")
    else:
        print(f"  M-2 决策: BELOW THRESHOLD (e2e={e2e_rate:.2%} < 40%)")
        print("  建议: 回退到 JSON→确定性翻译模式")
    print(f"{'='*50}")

    return metrics


def _run_case(
    *,
    sample_id: str,
    nl_instruction: str,
    case: dict[str, Any],
    normalizer: InstructionNormalizerAgent,
    planner: PlannerAgent,
    norm_prompt: str,
    gen_prompt: str,
    max_rounds: int,
    interactive: bool,
) -> dict[str, Any]:

    if interactive:
        input_fn = None
    else:
        answers = case.get("scripted_clarifications", [])
        input_fn = scripted_input_fn(answers)

    loop = ClarificationLoop(
        normalizer, max_rounds=max_rounds, input_fn=input_fn,
    )
    loop_result = loop.run(
        sample_id=sample_id,
        prompt_template=norm_prompt,
        raw_instruction=nl_instruction,
    )

    normalization_data = {
        "final_status": loop_result.final_status,
        "total_rounds": loop_result.total_rounds,
        "standard_instruction": (
            loop_result.final_result.standard_instruction
            if loop_result.final_result else None
        ),
        "usage": [r.usage for r in loop_result.all_results if r.usage],
    }

    if loop_result.final_status != "complete":
        return {
            "sample_id": sample_id,
            "stage": "normalization",
            "normalization": normalization_data,
            "generation": None,
            "report": None,
            "evaluation": {
                "end_to_end_pass": False,
                "normalization_complete": False,
                "normalization_error": True,
                "code_generation_error": False,
                "verification_pass": False,
                "failure_attribution": "normalization",
                "total_rounds": loop_result.total_rounds,
            },
        }

    standard_instruction = loop_result.final_result.standard_instruction

    generation = planner.generate_gcjp(
        sample_id=sample_id,
        prompt_template=gen_prompt,
        case_payload={},
        standard_instruction=standard_instruction,
    )

    extraction_ok = bool(generation.extraction.get("ok"))
    code = generation.extracted_code

    if not extraction_ok:
        return {
            "sample_id": sample_id,
            "stage": "generation",
            "normalization": normalization_data,
            "generation": asdict(generation),
            "report": None,
            "evaluation": {
                "end_to_end_pass": False,
                "normalization_complete": True,
                "normalization_error": False,
                "code_generation_error": True,
                "verification_pass": False,
                "failure_attribution": "generation",
                "total_rounds": loop_result.total_rounds,
            },
        }

    with Z3_LOCK:
        report = VerificationPipeline(z3_timeout_ms=15_000).verify_gcjp_code(code)
    verified = report.overall_passed if report else False

    return {
        "sample_id": sample_id,
        "stage": "verification",
        "normalization": normalization_data,
        "generation": asdict(generation),
        "report": report.to_dict() if report else None,
        "evaluation": {
            "end_to_end_pass": verified,
            "normalization_complete": True,
            "normalization_error": False,
            "code_generation_error": False,
            "verification_pass": verified,
            "failure_attribution": None if verified else "verification",
            "total_rounds": loop_result.total_rounds,
        },
    }


def _write_outputs(exp_dir: Path, record: dict[str, Any]) -> None:
    sid = record["sample_id"]
    norm = record.get("normalization")
    if norm:
        (exp_dir / "normalization" / f"{sid}.json").write_text(
            json.dumps(norm, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    gen = record.get("generation")
    if gen and gen.get("extracted_code"):
        (exp_dir / "generated_code" / f"{sid}.py").write_text(
            gen["extracted_code"], encoding="utf-8",
        )
    (exp_dir / "reports" / f"{sid}.json").write_text(
        json.dumps(
            {"evaluation": record.get("evaluation"), "report": record.get("report")},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _aggregate_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    if total == 0:
        return {"experiment": EXPERIMENT_NAME, "total_cases": 0, "rates": {}}

    evals = [r.get("evaluation", {}) for r in records]

    e2e_pass = sum(1 for e in evals if e.get("end_to_end_pass"))
    norm_complete = sum(1 for e in evals if e.get("normalization_complete"))
    norm_error = sum(1 for e in evals if e.get("normalization_error"))
    gen_error = sum(1 for e in evals if e.get("code_generation_error"))
    verified = sum(1 for e in evals if e.get("verification_pass"))

    attribution: dict[str, int] = {}
    for e in evals:
        attr = e.get("failure_attribution")
        if attr:
            attribution[attr] = attribution.get(attr, 0) + 1

    round_vals = [
        e.get("total_rounds", 1) for e in evals
    ]
    avg_rounds = sum(round_vals) / len(round_vals) if round_vals else 0.0

    rates = {
        "end_to_end_z3_pass_rate": e2e_pass / total if total else 0.0,
        "normalization_complete_rate": norm_complete / total if total else 0.0,
        "normalization_error_rate": norm_error / total if total else 0.0,
        "code_generation_error_rate": gen_error / total if total else 0.0,
        "verification_pass_rate": verified / total if total else 0.0,
        "avg_total_rounds": avg_rounds,
    }

    return {
        "experiment": EXPERIMENT_NAME,
        "total_cases": total,
        "rates": rates,
        "failure_attribution_distribution": attribution,
        "records": [
            {
                "sample_id": r["sample_id"],
                "stage": r.get("stage"),
                "evaluation": r.get("evaluation"),
            }
            for r in records
        ],
    }


def _summary_line(record: dict[str, Any]) -> str:
    ev = record.get("evaluation", {})
    sid = record["sample_id"]
    e2e = ev.get("end_to_end_pass", False)
    attr = ev.get("failure_attribution", "-")
    stage = record.get("stage", "?")
    rounds = ev.get("total_rounds", "?")
    return (
        f"[{EXPERIMENT_NAME}]  {sid}  stage={stage}  "
        f"e2e={e2e}  attribution={attr}  rounds={rounds}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
