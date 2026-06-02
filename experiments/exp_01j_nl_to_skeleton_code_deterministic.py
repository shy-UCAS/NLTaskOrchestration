"""
experiments/exp_01j_nl_to_skeleton_code_deterministic.py
用法：
  python -m experiments.exp_01j_nl_to_skeleton_code_deterministic --provider-profile <profile_name> --limit 1 --workers 8

Phase 1J：标准语义自然语言 → LLM 出带占位符的 GCJP 骨架代码 → Python AST 确定性填参 →
受限执行 + 完整三层验证。

与 1I 的区别(同为"LLM 出结构、Python 填参"):
  1I 让 LLM 输出 JSON 骨架,Python 直接构图(绕过 exec),verify_graph 跳过 Layer 1。
  1J 让 LLM 输出**完整 GCJP 代码结构**(系统参数处用裸名 sentinel),Python 用 ast 把
  sentinel 从 YAML 确定性替换为字面量,填好的代码仍走 execute_gcjp_code + 完整
  verify_gcjp_code(L1 子进程执行 + L2 + L3),与 1B/1H 完全同口径。

  指挥官时间语义(deadline 等)由 LLM 写真实数值,填参器不动。
  评分用与 1B/1H/1I 一致的 evaluate_graph_against_expected,可四方横向对照。
  expected_patterns / expected_result / tags 绝不进 prompt。

预期输出：
  out/phase1_generation/<run_dir>/exp_01j_nl_to_skeleton_code_deterministic/ 下输出
  原始回复、骨架代码、填充后代码、验证报告、汇总 metrics.json。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agents.llm_client import LLMClient, LLMConfigError
from agents.planner_agent import PlannerAgent
from gcjp.code_executor import execute_gcjp_code
from gcjp.skeleton_filler import fill_skeleton_code
from gcjp.task_plan_loader import (
    load_action_defaults_from_yaml,
    load_capability_model_from_yaml,
)
from verifier.pipeline import VerificationPipeline
from experiments.phase1_common import (
    Z3_LOCK,
    _aggregate_metrics,
    add_common_args,
    evaluate_graph_against_expected,
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


EXPERIMENT_NAME = "exp_01j_nl_to_skeleton_code_deterministic"

RATE_KEYS = [
    "skeleton_extract",
    "fill_success",
    "safety_pass",
    "execution_success",
    "builtgraph_success",
    "l2_graph_pass",
    "l3_expected_result",
    "first_pass",
    "node_complete",
    "edge_complete",
    "constraint_complete",
]

_EMPTY_GRAPH_EVAL = {
    "builtgraph_success": False,
    "l2_graph_pass": False,
    "l3_expected_result": False,
    "first_pass": False,
    "node_complete": False,
    "edge_complete": False,
    "constraint_complete": False,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("datasets") / "phase1_standard_nl_cases.jsonl",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        default=Path("prompts") / "standard_nl_to_gcjp_skeleton_prompt.md",
    )
    parser.add_argument(
        "--action-templates",
        type=Path,
        default=Path("configs") / "action_templates.yaml",
    )
    parser.add_argument(
        "--capability-model",
        type=Path,
        default=Path("configs") / "capability_model.yaml",
    )
    args = parser.parse_args()

    try:
        print_provider_summary_from_args(args)
        return _run(args)
    except LLMConfigError as exc:
        return handle_config_error(exc)


def _run(args: argparse.Namespace) -> int:
    cases = load_jsonl(args.dataset, limit=args.limit)
    if not cases:
        raise ValueError(f"No cases loaded from {args.dataset}")

    action_defaults = load_action_defaults_from_yaml(args.action_templates)
    capability_model = load_capability_model_from_yaml(args.capability_model)
    prompt_context = {
        "action_defaults": action_defaults,
        "capability_model": capability_model,
    }

    config = load_config_from_args(args)
    provider_summary = config.safe_summary()
    run_output = resolve_phase1_run_output(
        output_dir=args.output_dir,
        provider_summary=provider_summary,
        run_label=args.run_label,
        no_run_timestamp=args.no_run_timestamp,
    )

    agent = PlannerAgent(LLMClient(config))
    prompt_template = read_prompt_template(args.prompt)
    dirs = _ensure_dirs(run_output["run_dir"])

    def _worker(case: dict[str, Any]) -> dict[str, Any]:
        sample_id = case["sample_id"]
        try:
            gen = agent.generate_gcjp(
                sample_id=sample_id,
                prompt_template=prompt_template,
                case_payload=prompt_context,
                standard_instruction=case["standard_instruction"],
            )
            return _evaluate(case, gen, action_defaults, capability_model)
        except Exception as exc:  # noqa: BLE001 - worker 必须自兜底
            return _error_record(case, exc)

    def _on_complete(record: dict[str, Any]) -> None:
        _write_outputs(dirs, record)
        print(_summary_line(record))

    records = run_cases_concurrent(
        cases,
        _worker,
        workers=getattr(args, "workers", 1),
        on_complete=_on_complete,
        show_usage=getattr(args, "show_usage", False),
    )

    metrics = _aggregate_metrics(EXPERIMENT_NAME, records, rate_keys=RATE_KEYS)
    metrics.update(phase1_run_metadata_json(run_output))
    metrics["output_dir"] = str(dirs["root"])
    metrics_path = dirs["root"] / "metrics.json"
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_latest_run_index(
        run_output=run_output,
        experiment_name=EXPERIMENT_NAME,
        experiment_dir=dirs["root"],
        reports_dir=dirs["reports"],
        metrics_path=metrics_path,
    )
    print(f"\n[{EXPERIMENT_NAME}] 汇总指标 -> {metrics_path}")
    print(json.dumps(metrics["rates"], ensure_ascii=False, indent=2))
    return 0


def _evaluate(
    case: dict[str, Any],
    gen,
    action_defaults: dict[str, Any],
    capability_model: dict[str, Any],
) -> dict[str, Any]:
    sample_id = case["sample_id"]
    skeleton_extract = bool(gen.extraction.get("ok"))
    skeleton_code = gen.extracted_code

    fill_error = None
    filled_code = ""
    fill_success = False
    error_type = "SUCCESS"

    if not skeleton_extract:
        error_type = "NO_SKELETON"
    else:
        fill = fill_skeleton_code(
            skeleton_code,
            action_defaults=action_defaults,
            capability_model=capability_model,
        )
        fill_success = fill.ok
        if fill.ok:
            filled_code = fill.code
        else:
            fill_error = fill.error
            error_type = "FILL_FAILED"

    exec_result = execute_gcjp_code(filled_code) if fill_success else None
    graph = exec_result.graph if exec_result and exec_result.graph else None
    safety_pass = bool(exec_result and exec_result.safety and exec_result.safety.passed)
    execution_success = bool(exec_result and exec_result.passed)
    if exec_result is not None:
        error_type = exec_result.error_type or error_type

    report = None
    if fill_success:
        with Z3_LOCK:
            report = VerificationPipeline(z3_timeout_ms=15_000).verify_gcjp_code(filled_code)

    graph_eval = (
        evaluate_graph_against_expected(case, graph, report)
        if graph is not None
        else dict(_EMPTY_GRAPH_EVAL)
    )

    evaluation = {
        "skeleton_extract": skeleton_extract,
        "fill_success": fill_success,
        "safety_pass": safety_pass,
        "execution_success": execution_success,
        **graph_eval,
    }

    return {
        "sample_id": sample_id,
        "case": case,
        "generation": asdict(gen),
        "filled_code": filled_code,
        "fill_error": fill_error,
        "execution_error_type": error_type,
        "report": report.to_dict() if report else None,
        "evaluation": evaluation,
    }


def _error_record(case: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "sample_id": case["sample_id"],
        "case": case,
        "generation": None,
        "filled_code": "",
        "fill_error": None,
        "execution_error_type": type(exc).__name__,
        "report": None,
        "evaluation": {
            "skeleton_extract": False,
            "fill_success": False,
            "safety_pass": False,
            "execution_success": False,
            **_EMPTY_GRAPH_EVAL,
        },
        "error": f"{type(exc).__name__}: {exc}",
    }


def _ensure_dirs(root: Path) -> dict[str, Path]:
    exp_root = root / EXPERIMENT_NAME
    dirs = {
        "root": exp_root,
        "raw": exp_root / "raw_outputs",
        "skeleton": exp_root / "skeleton_code",
        "filled": exp_root / "filled_code",
        "reports": exp_root / "reports",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def _write_outputs(dirs: dict[str, Path], record: dict[str, Any]) -> None:
    sid = record["sample_id"]
    generation = record.get("generation") or {}
    (dirs["raw"] / f"{sid}.txt").write_text(
        generation.get("raw_response") or "", encoding="utf-8"
    )
    (dirs["skeleton"] / f"{sid}.py").write_text(
        generation.get("extracted_code") or "", encoding="utf-8"
    )
    (dirs["filled"] / f"{sid}.py").write_text(
        record.get("filled_code") or "", encoding="utf-8"
    )
    (dirs["reports"] / f"{sid}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _summary_line(record: dict[str, Any]) -> str:
    ev = record["evaluation"]
    mark = "通过" if ev.get("first_pass") else "失败"
    line = (
        f"[{mark}] {record['sample_id']} "
        f"skel={ev.get('skeleton_extract')} fill={ev.get('fill_success')} "
        f"exec={ev.get('execution_success')} l3={ev.get('l3_expected_result')}"
    )
    if ev.get("first_pass"):
        return line
    err = record.get("fill_error") or record.get("error")
    if err:
        line += f" err={err[:120]}"
    return line


if __name__ == "__main__":
    raise SystemExit(main())
