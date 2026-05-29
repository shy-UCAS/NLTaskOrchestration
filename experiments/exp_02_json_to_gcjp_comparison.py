"""
experiments/exp_02_json_to_gcjp_comparison.py

Phase 2：JSON → GCJP Reference 对比实验。

对每条结构化样本同时生成 reference（task_plan_loader 确定性翻译）和
prediction（LLM 生成 GCJP 代码执行），然后做 Node-F1 / Edge-F1 /
Constraint-F1 对比。

用法：
  # 默认从 configs/llm_providers.local.yaml 读取 profile
  python -m experiments.exp_02_json_to_gcjp_comparison --provider-profile <profile_name> --limit 2 --workers 8

  # 覆盖结构化数据集或 prompt
  python -m experiments.exp_02_json_to_gcjp_comparison --provider-profile <profile_name> --dataset datasets/phase1_structured_cases.jsonl --prompt prompts/gcjp_generation_prompt.md
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agents.llm_client import LLMClient, LLMConfigError
from agents.planner_agent import PlannerAgent
from evaluation.graph_comparison import GraphComparisonResult, compare_graphs
from gcjp.code_executor import execute_gcjp_code
from gcjp.task_plan_loader import build_graph_from_task_plan
from verifier.pipeline import VerificationPipeline
from experiments.phase1_common import (
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


EXPERIMENT_NAME = "exp_02_json_to_gcjp_comparison"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("datasets") / "phase1_structured_cases.jsonl",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        default=Path("prompts") / "gcjp_generation_prompt.md",
    )
    args = parser.parse_args()

    try:
        print_provider_summary_from_args(args)
        run_comparison_experiment(args)
    except LLMConfigError as exc:
        return handle_config_error(exc)
    return 0


def run_comparison_experiment(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_jsonl(args.dataset, limit=args.limit)
    if not cases:
        raise ValueError(f"No cases loaded from {args.dataset}")

    config = load_config_from_args(args)
    provider_summary = config.safe_summary()
    agent = PlannerAgent(LLMClient(config))
    prompt_template = read_prompt_template(args.prompt)

    run_output = resolve_phase1_run_output(
        output_dir=args.output_dir,
        provider_summary=provider_summary,
        run_label=args.run_label,
        no_run_timestamp=args.no_run_timestamp,
    )
    exp_dir = run_output["run_dir"] / EXPERIMENT_NAME
    for sub in ("generated_code", "comparisons", "reports"):
        (exp_dir / sub).mkdir(parents=True, exist_ok=True)

    def _worker(case: dict[str, Any]) -> dict[str, Any]:
        try:
            return _run_case(case, agent, prompt_template)
        except Exception as exc:
            return {
                "sample_id": case["sample_id"],
                "ref_built": False,
                "pred_built": False,
                "comparison": None,
                "error": f"{type(exc).__name__}: {exc}",
            }

    def _on_complete(record: dict[str, Any]) -> None:
        _write_outputs(exp_dir, record)
        print(_summary_line(record))

    records = run_cases_concurrent(
        cases,
        _worker,
        workers=getattr(args, "workers", 1),
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
    return metrics


def _run_case(
    case: dict[str, Any],
    agent: PlannerAgent,
    prompt_template: str,
) -> dict[str, Any]:
    sample_id = case["sample_id"]
    payload = case["input_payload"]

    ref_graph = build_graph_from_task_plan(payload, segment_id=payload.get("segment_id"))

    generation = agent.generate_gcjp(
        sample_id=sample_id,
        prompt_template=prompt_template,
        case_payload=payload,
    )

    extraction_ok = bool(generation.extraction.get("ok"))
    code = generation.extracted_code
    pred_graph = None

    if extraction_ok:
        exec_result = execute_gcjp_code(code)
        pred_graph = exec_result.graph if exec_result and exec_result.graph else None

    comparison = None
    comparison_dict = None
    if ref_graph and pred_graph:
        comparison = compare_graphs(ref_graph, pred_graph)
        comparison_dict = asdict(comparison)

    return {
        "sample_id": sample_id,
        "ref_built": ref_graph is not None,
        "pred_built": pred_graph is not None,
        "extraction_ok": extraction_ok,
        "extracted_code": code,
        "comparison": comparison_dict,
        "usage": generation.usage,
        "generation_summary": {
            "model": generation.model,
            "extraction": generation.extraction,
        },
    }


def _write_outputs(exp_dir: Path, record: dict[str, Any]) -> None:
    sid = record["sample_id"]
    code = record.get("extracted_code", "")
    if code:
        (exp_dir / "generated_code" / f"{sid}.py").write_text(
            code, encoding="utf-8",
        )
    comp = record.get("comparison")
    (exp_dir / "comparisons" / f"{sid}.json").write_text(
        json.dumps(comp, ensure_ascii=False, indent=2) if comp else "null",
        encoding="utf-8",
    )
    (exp_dir / "reports" / f"{sid}.json").write_text(
        json.dumps(
            {k: v for k, v in record.items() if k != "extracted_code"},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _aggregate_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    if total == 0:
        return {"experiment": EXPERIMENT_NAME, "total_cases": 0, "rates": {}}

    both_built = [r for r in records if r.get("ref_built") and r.get("pred_built")]
    comparisons = [r["comparison"] for r in both_built if r.get("comparison")]

    node_f1s = [c["node_set_f1"] for c in comparisons]
    edge_f1s = [c["edge_set_f1"] for c in comparisons]
    constraint_f1s = [c["constraint_f1"] for c in comparisons]

    def mean(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    rates = {
        "both_graphs_available_rate": len(both_built) / total if total else 0.0,
        "comparison_count": len(comparisons),
        "mean_node_f1": mean(node_f1s),
        "mean_edge_f1": mean(edge_f1s),
        "mean_constraint_f1": mean(constraint_f1s),
    }
    if comparisons:
        attr_accs = [
            c.get("attribute_match", {}).get("overall_accuracy", 0.0)
            for c in comparisons
        ]
        rates["mean_attribute_accuracy"] = mean(attr_accs)

    return {
        "experiment": EXPERIMENT_NAME,
        "total_cases": total,
        "rates": rates,
        "records": [
            {
                "sample_id": r["sample_id"],
                "ref_built": r.get("ref_built"),
                "pred_built": r.get("pred_built"),
                "node_f1": r["comparison"]["node_set_f1"] if r.get("comparison") else None,
                "edge_f1": r["comparison"]["edge_set_f1"] if r.get("comparison") else None,
                "constraint_f1": r["comparison"]["constraint_f1"] if r.get("comparison") else None,
            }
            for r in records
        ],
    }


def _summary_line(record: dict[str, Any]) -> str:
    sid = record["sample_id"]
    comp = record.get("comparison")
    if comp:
        return (
            f"[{EXPERIMENT_NAME}]  {sid}  "
            f"node_f1={comp['node_set_f1']:.3f}  "
            f"edge_f1={comp['edge_set_f1']:.3f}  "
            f"constraint_f1={comp['constraint_f1']:.3f}"
        )
    err = record.get("error", "")
    ref = record.get("ref_built", False)
    pred = record.get("pred_built", False)
    return (
        f"[{EXPERIMENT_NAME}]  {sid}  "
        f"ref={ref}  pred={pred}  "
        f"{'error=' + err if err else 'no comparison'}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
