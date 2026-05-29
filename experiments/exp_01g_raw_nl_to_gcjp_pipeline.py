"""
experiments/exp_01g_raw_nl_to_gcjp_pipeline.py

Phase 1G：原始 NL → 指令规范化（含澄清闭环）→ GCJP 生成 → 验证 端到端管道。

流程：
  raw NL → ClarificationLoop → if complete → PlannerAgent → GCJP → verify
                               if not complete → 记录失败，不生成 GCJP

用法：
  # 默认从 configs/llm_providers.local.yaml 读取 profile
  python -m experiments.exp_01g_raw_nl_to_gcjp_pipeline --provider-profile <profile_name> --workers 4

  # 覆盖 eval/dev 数据集或进入交互澄清
  python -m experiments.exp_01g_raw_nl_to_gcjp_pipeline --provider-profile <profile_name> --dataset datasets/phase1_instruction_normalization_dev.jsonl
  python -m experiments.exp_01g_raw_nl_to_gcjp_pipeline --provider-profile <profile_name> --interactive --workers 1

  # 临时指定其他 provider 配置文件
  python -m experiments.exp_01g_raw_nl_to_gcjp_pipeline --config configs/llm_providers.local.yaml --provider-profile <profile_name>
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
from gcjp.task_plan_loader import (
    load_action_defaults_from_yaml,
    load_capability_model_from_yaml,
)
from verifier.pipeline import VerificationPipeline
from experiments.phase1_common import (
    Z3_LOCK,
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


EXPERIMENT_NAME = "exp_01g_raw_nl_to_gcjp_pipeline"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("datasets") / "phase1_instruction_normalization_eval.jsonl",
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
    parser.add_argument(
        "--action-templates",
        type=Path,
        default=Path("configs") / "action_templates.yaml",
        help="Action defaults used by the GCJP generation prompt.",
    )
    parser.add_argument(
        "--capability-model",
        type=Path,
        default=Path("configs") / "capability_model.yaml",
        help="Fleet capability/resource model used by the GCJP generation prompt.",
    )
    parser.add_argument("--max-clarification-rounds", type=int, default=5)
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Use terminal input for clarification instead of scripted answers.",
    )
    args = parser.parse_args()

    try:
        print_provider_summary_from_args(args)
        run_pipeline_experiment(args)
    except LLMConfigError as exc:
        return handle_config_error(exc)
    return 0


def run_pipeline_experiment(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_jsonl(args.dataset, limit=args.limit)
    if not cases:
        raise ValueError(f"No cases loaded from {args.dataset}")

    workers = getattr(args, "workers", 8)
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
    generation_context = _build_generation_config_context(
        action_templates_path=args.action_templates,
        capability_model_path=args.capability_model,
    )

    run_output = resolve_phase1_run_output(
        output_dir=args.output_dir,
        provider_summary=provider_summary,
        run_label=args.run_label,
        no_run_timestamp=args.no_run_timestamp,
    )
    exp_dir = run_output["run_dir"] / EXPERIMENT_NAME
    for sub in ("normalization", "generated_code", "reports"):
        (exp_dir / sub).mkdir(parents=True, exist_ok=True)

    def _worker(case: dict[str, Any]) -> dict[str, Any]:
        try:
            return _run_case(
                case=case,
                normalizer=normalizer,
                planner=planner,
                norm_prompt=norm_prompt,
                gen_prompt=gen_prompt,
                generation_context=generation_context,
                max_rounds=args.max_clarification_rounds,
                interactive=args.interactive,
            )
        except Exception as exc:
            return {
                "sample_id": case["sample_id"],
                "stage": "error",
                "evaluation": {"error": f"{type(exc).__name__}: {exc}"},
            }

    def _on_complete(record: dict[str, Any]) -> None:
        _write_outputs(exp_dir, record)
        print(_summary_line(record))

    records = run_cases_concurrent(
        cases,
        _worker,
        workers=workers,
        on_complete=_on_complete,
        show_usage=getattr(args, "show_usage", False),
    )

    metrics = _aggregate_metrics(records)
    metrics.update(phase1_run_metadata_json(run_output))
    metrics["output_dir"] = str(exp_dir)
    metrics["interactive"] = args.interactive

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

    if args.save_baseline:
        save_baseline_json(EXPERIMENT_NAME, metrics)
        append_baseline_markdown(EXPERIMENT_NAME, metrics)

    return metrics


def _run_case(
    *,
    case: dict[str, Any],
    normalizer: InstructionNormalizerAgent,
    planner: PlannerAgent,
    norm_prompt: str,
    gen_prompt: str,
    generation_context: dict[str, Any],
    max_rounds: int,
    interactive: bool,
) -> dict[str, Any]:
    sample_id = case["sample_id"]

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
        raw_instruction=case["raw_instruction"],
    )

    normalization_data = {
        "final_status": loop_result.final_status,
        "total_rounds": loop_result.total_rounds,
        "clarification_history": loop_result.clarification_history,
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
                "normalization_complete": False,
                "rejected_as_incomplete": True,
                "gcjp_generated": False,
                "gcjp_verified": False,
                "end_to_end_pass": False,
                "failure_attribution": "normalization",
            },
        }

    standard_instruction = loop_result.final_result.standard_instruction

    generation = planner.generate_gcjp(
        sample_id=sample_id,
        prompt_template=gen_prompt,
        case_payload=generation_context,
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
                "normalization_complete": True,
                "rejected_as_incomplete": False,
                "gcjp_generated": False,
                "gcjp_verified": False,
                "end_to_end_pass": False,
                "failure_attribution": "generation",
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
            "normalization_complete": True,
            "rejected_as_incomplete": False,
            "gcjp_generated": True,
            "gcjp_verified": verified,
            "end_to_end_pass": verified,
            "failure_attribution": None if verified else "verification",
        },
    }


def _build_generation_config_context(
    *,
    action_templates_path: Path,
    capability_model_path: Path,
) -> dict[str, Any]:
    action_defaults = load_action_defaults_from_yaml(action_templates_path)
    capability_model = load_capability_model_from_yaml(capability_model_path)
    return {
        "parameter_source": {
            "action_templates": str(action_templates_path),
            "capability_model": str(capability_model_path),
            "policy": (
                "Use normalized command semantics for actor/action/target/relation. "
                "Use action_defaults for duration_lb, energy_cost, ammo_cost and "
                "required_capability. Use capability_model for actor resource limits "
                "and actor capabilities."
            ),
        },
        "action_defaults": action_defaults,
        "capability_model": capability_model,
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
    report = record.get("report")
    (exp_dir / "reports" / f"{sid}.json").write_text(
        json.dumps(
            {"evaluation": record.get("evaluation"), "report": report},
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

    norm_complete = sum(1 for e in evals if e.get("normalization_complete"))
    rejected = sum(1 for e in evals if e.get("rejected_as_incomplete"))
    gcjp_gen = sum(1 for e in evals if e.get("gcjp_generated"))
    gcjp_verified = sum(1 for e in evals if e.get("gcjp_verified"))
    e2e_pass = sum(1 for e in evals if e.get("end_to_end_pass"))

    attribution: dict[str, int] = {}
    for e in evals:
        attr = e.get("failure_attribution")
        if attr:
            attribution[attr] = attribution.get(attr, 0) + 1

    avg_rounds = 0.0
    round_records = [
        r["normalization"]["total_rounds"]
        for r in records
        if r.get("normalization")
    ]
    if round_records:
        avg_rounds = sum(round_records) / len(round_records)

    rates: dict[str, float] = {
        "normalization_complete_rate": norm_complete / total if total else 0.0,
        "incomplete_rejection_rate": rejected / total if total else 0.0,
        "gcjp_generation_rate": gcjp_gen / total if total else 0.0,
        "gcjp_verified_rate": gcjp_verified / total if total else 0.0,
        "end_to_end_pass_rate": e2e_pass / total if total else 0.0,
        "avg_total_rounds": avg_rounds,
    }
    if norm_complete > 0:
        rates["raw_to_gcjp_verified_rate"] = gcjp_verified / norm_complete

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
    stage = record.get("stage", "?")
    e2e = ev.get("end_to_end_pass", False)
    attr = ev.get("failure_attribution", "-")
    norm = record.get("normalization", {})
    rounds = norm.get("total_rounds", "?") if norm else "?"
    return (
        f"[{EXPERIMENT_NAME}]  {sid}  stage={stage}  "
        f"e2e={e2e}  attribution={attr}  rounds={rounds}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
