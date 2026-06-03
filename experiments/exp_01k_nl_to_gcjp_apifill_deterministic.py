"""
experiments/exp_01k_nl_to_gcjp_apifill_deterministic.py
用法：
  conda run -n llm --no-capture-output python -m experiments.exp_01k_nl_to_gcjp_apifill_deterministic --provider-profile <profile_name> --limit 1 --workers 4

Phase 1K：标准语义自然语言 → LLM 出 GCJP API-fill 受限代码 →
运行时 YAML config 确定性注入系统参数 → 受限执行 + 完整三层验证。

与 01J 的区别：
  01J 让 LLM 在系统参数槽位写 FILL_xxx sentinel，再由 AST filler 替换；
  01K 直接从 LLM 可见 API 中移除系统参数槽位。LLM 只写任务结构，
  TaskGraphBuilder 在 execute_gcjp_code 的 runtime config 中解析
  duration_lb / energy_cost / ammo_cost / required_capability，并在 build()
  自动派生 resource/capability 约束。

expected_patterns / expected_result / tags 绝不进 prompt。
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
from gcjp.mission_graph import BuiltGraph
from gcjp.safety_checker import check_gcjp_apifill_contract
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


EXPERIMENT_NAME = "exp_01k_nl_to_gcjp_apifill_deterministic"

RATE_KEYS = [
    "code_extract",
    "no_param_violation",
    "config_param_conformance",
    "safety_pass",
    "execution_success",
    "builtgraph_success",
    "l2_graph_pass",
    "l3_expected_result",
    "node_complete",
    "edge_complete",
    "constraint_complete",
    "first_pass",
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
        default=Path("prompts") / "standard_nl_to_gcjp_apifill_prompt.md",
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
        "allowed_actions": sorted(action_defaults.keys()),
        "allowed_actors": sorted(capability_model.keys()),
        "parameter_policy": (
            "Do not copy numeric system parameters into GCJP code. "
            "duration_lb, energy_cost, ammo_cost, required_capability, "
            "resource limits and actor capabilities are injected by runtime config."
        ),
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
    code_extract = bool(gen.extraction.get("ok"))
    code = gen.extracted_code
    error_type = "SUCCESS"

    contract = None
    exec_result = None
    report = None
    graph = None
    config_check = {"ok": False, "mismatches": []}

    if not code_extract:
        error_type = "NO_CODE"
    else:
        contract = check_gcjp_apifill_contract(code)
        if not contract.passed:
            error_type = "PARAM_LEAK"
        else:
            exec_result = execute_gcjp_code(
                code,
                action_defaults=action_defaults,
                capability_model=capability_model,
            )
            graph = exec_result.graph if exec_result and exec_result.graph else None
            error_type = exec_result.error_type or error_type
            if exec_result.passed and graph is not None:
                with Z3_LOCK:
                    report = VerificationPipeline(z3_timeout_ms=15_000).verify_gcjp_code(
                        code,
                        action_defaults=action_defaults,
                        capability_model=capability_model,
                    )
                config_check = check_config_param_conformance(
                    graph,
                    action_defaults=action_defaults,
                )
                if not config_check["ok"]:
                    error_type = "CONFIG_PARAM_MISMATCH"

    safety_pass = bool(exec_result and exec_result.safety and exec_result.safety.passed)
    execution_success = bool(exec_result and exec_result.passed)
    no_param_violation = bool(contract and contract.passed)
    graph_eval = (
        evaluate_graph_against_expected(case, graph, report)
        if graph is not None
        else dict(_EMPTY_GRAPH_EVAL)
    )

    evaluation = {
        "code_extract": code_extract,
        "no_param_violation": no_param_violation,
        "config_param_conformance": bool(config_check["ok"]),
        "safety_pass": safety_pass,
        "execution_success": execution_success,
        **graph_eval,
    }

    return {
        "sample_id": case["sample_id"],
        "case": case,
        "generation": asdict(gen),
        "contract_check": _contract_to_dict(contract),
        "config_param_conformance": config_check,
        "execution_error_type": error_type,
        "execution_error_msg": exec_result.error_msg if exec_result else None,
        "report": report.to_dict() if report else None,
        "evaluation": evaluation,
    }


def check_config_param_conformance(
    graph: BuiltGraph,
    *,
    action_defaults: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    for task_id, node in graph.nodes.items():
        expected = action_defaults.get(node.action)
        if expected is None:
            mismatches.append(
                {
                    "task_id": task_id,
                    "action": node.action,
                    "field": "action",
                    "actual": node.action,
                    "expected": sorted(action_defaults.keys()),
                }
            )
            continue

        comparisons = {
            "duration_lb": (node.duration_lb, float(expected["duration_lb"])),
            "energy_cost": (node.energy_cost, float(expected["energy_cost"])),
            "ammo_cost": (node.ammo_cost, int(expected["ammo_cost"])),
            "required_capability": (
                list(node.required_capability),
                list(expected.get("required_capability", [])),
            ),
        }
        for field, (actual, expected_value) in comparisons.items():
            if actual == expected_value:
                continue
            mismatches.append(
                {
                    "task_id": task_id,
                    "action": node.action,
                    "field": field,
                    "actual": actual,
                    "expected": expected_value,
                }
            )

    return {"ok": not mismatches, "mismatches": mismatches}


def _contract_to_dict(contract) -> dict[str, Any] | None:
    if contract is None:
        return None
    return {
        "passed": contract.passed,
        "violations": contract.violations,
        "warnings": contract.warnings,
        "structured_violations": [
            asdict(item) for item in contract.structured_violations
        ],
    }


def _error_record(case: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "sample_id": case["sample_id"],
        "case": case,
        "generation": None,
        "contract_check": None,
        "config_param_conformance": {"ok": False, "mismatches": []},
        "execution_error_type": type(exc).__name__,
        "execution_error_msg": f"{type(exc).__name__}: {exc}",
        "report": None,
        "evaluation": {
            "code_extract": False,
            "no_param_violation": False,
            "config_param_conformance": False,
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
        "extracted": exp_root / "extracted_code",
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
    (dirs["extracted"] / f"{sid}.py").write_text(
        generation.get("extracted_code") or "", encoding="utf-8"
    )
    (dirs["reports"] / f"{sid}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _summary_line(record: dict[str, Any]) -> str:
    ev = record["evaluation"]
    mark = "通过" if ev.get("first_pass") else "失败"
    line = (
        f"[{mark}] {record['sample_id']} "
        f"code={ev.get('code_extract')} no_param={ev.get('no_param_violation')} "
        f"cfg={ev.get('config_param_conformance')} "
        f"exec={ev.get('execution_success')} l3={ev.get('l3_expected_result')}"
    )
    if ev.get("first_pass"):
        return line
    err = record.get("execution_error_msg") or record.get("error")
    if err:
        line += f" err={err[:120]}"
    return line


if __name__ == "__main__":
    raise SystemExit(main())
