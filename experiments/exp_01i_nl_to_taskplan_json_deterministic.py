"""
experiments/exp_01i_nl_to_taskplan_json_deterministic.py
用法：
  python -m experiments.exp_01i_nl_to_taskplan_json_deterministic --provider-profile <profile_name> --limit 1 --workers 8

Phase 1I：标准语义自然语言 → LLM 出 task_plan JSON 骨架 → Python 确定性构图 → 验证。

与 1B/1H 的根本区别：
  1B/1H 让 LLM **同时**生成结构和系统参数(duration/energy/ammo/capability/资源上限)，
  参数靠预测，存在随机性。1I 让 LLM **只**输出作战语义骨架(actor/action/target/relation/
  time_window)，所有系统参数由 build_graph_from_task_plan 从 action_templates.yaml /
  capability_model.yaml 确定性查表填入，彻底消除参数幻觉。

  评分用与 1B/1H 完全一致的 evaluate_graph_against_expected(node/edge/constraint/l3/
  first_pass)，可三方横向对照。expected_patterns / expected_result / tags 绝不进 prompt。

预期输出：
  out/phase1_generation/<run_dir>/exp_01i_nl_to_taskplan_json_deterministic/ 下输出
  原始回复、抽取的 plan JSON、验证报告、汇总 metrics.json。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agents.llm_client import LLMClient, LLMConfigError
from agents.plan_extractor_agent import PlanExtractorAgent
from gcjp.task_plan_loader import (
    build_graph_from_task_plan,
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


EXPERIMENT_NAME = "exp_01i_nl_to_taskplan_json_deterministic"

RATE_KEYS = [
    "json_parse_ok",
    "schema_valid",
    "build_success",
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


def _looks_like_plan(plan: Any) -> bool:
    """最小结构校验:tasks 非空且每个 task 含 task_id/actor/action/target。"""
    if not isinstance(plan, dict):
        return False
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return False
    for task in tasks:
        if not isinstance(task, dict):
            return False
        if not all(task.get(k) for k in ("task_id", "actor", "action", "target")):
            return False
    relations = plan.get("relations")
    if relations is not None and not isinstance(relations, list):
        return False
    return True


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
        default=Path("prompts") / "standard_nl_to_task_plan_json_prompt.md",
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
    # 仅供 prompt 校验 action/actor 名称;真值数字不要求 LLM 复制。
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

    agent = PlanExtractorAgent(LLMClient(config))
    prompt_template = read_prompt_template(args.prompt)
    dirs = _ensure_dirs(run_output["run_dir"])

    def _worker(case: dict[str, Any]) -> dict[str, Any]:
        sample_id = case["sample_id"]
        try:
            gen = agent.extract_plan(
                sample_id=sample_id,
                prompt_template=prompt_template,
                standard_instruction=case["standard_instruction"],
                case_payload=prompt_context,
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
    plan = gen.parsed_plan
    json_ok = bool(plan)
    schema_valid = _looks_like_plan(plan)

    graph = None
    report = None
    build_error = None
    error_type = "SUCCESS"

    if not json_ok:
        error_type = "JSON_PARSE_FAILED"
    elif not schema_valid:
        error_type = "SCHEMA_INVALID"
    else:
        try:
            graph = build_graph_from_task_plan(
                plan,
                segment_id=str(plan.get("plan_id") or sample_id),
                action_defaults=action_defaults,
                capability_model=capability_model,
            )
        except Exception as exc:  # noqa: BLE001 - 结构/动作/actor 错误一律记为构图失败
            build_error = f"{type(exc).__name__}: {exc}"
            error_type = type(exc).__name__

    build_success = graph is not None
    if graph is not None:
        with Z3_LOCK:
            report = VerificationPipeline(z3_timeout_ms=15_000).verify_graph(graph)

    graph_eval = (
        evaluate_graph_against_expected(case, graph, report)
        if graph is not None
        else dict(_EMPTY_GRAPH_EVAL)
    )

    evaluation = {
        "json_parse_ok": json_ok,
        "schema_valid": schema_valid,
        "build_success": build_success,
        **graph_eval,
    }

    return {
        "sample_id": sample_id,
        "case": case,
        "generation": asdict(gen),
        "build_error": build_error,
        "execution_error_type": error_type,
        "report": report.to_dict() if report else None,
        "evaluation": evaluation,
    }


def _error_record(case: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "sample_id": case["sample_id"],
        "case": case,
        "generation": None,
        "build_error": None,
        "execution_error_type": type(exc).__name__,
        "report": None,
        "evaluation": {
            "json_parse_ok": False,
            "schema_valid": False,
            "build_success": False,
            **_EMPTY_GRAPH_EVAL,
        },
        "error": f"{type(exc).__name__}: {exc}",
    }


def _ensure_dirs(root: Path) -> dict[str, Path]:
    exp_root = root / EXPERIMENT_NAME
    dirs = {
        "root": exp_root,
        "raw": exp_root / "raw_outputs",
        "plan": exp_root / "generated_plan",
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
    (dirs["plan"] / f"{sid}.json").write_text(
        json.dumps(generation.get("parsed_plan"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (dirs["reports"] / f"{sid}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _summary_line(record: dict[str, Any]) -> str:
    ev = record["evaluation"]
    mark = "通过" if ev.get("first_pass") else "失败"
    line = (
        f"[{mark}] {record['sample_id']} "
        f"json={ev.get('json_parse_ok')} schema={ev.get('schema_valid')} "
        f"build={ev.get('build_success')} l3={ev.get('l3_expected_result')}"
    )
    if ev.get("first_pass"):
        return line
    err = record.get("build_error") or record.get("error")
    if err:
        line += f" err={err[:120]}"
    return line


if __name__ == "__main__":
    raise SystemExit(main())
