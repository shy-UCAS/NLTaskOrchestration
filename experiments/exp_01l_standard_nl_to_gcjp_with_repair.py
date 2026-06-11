"""
experiments/exp_01l_standard_nl_to_gcjp_with_repair.py

端到端实验:标准语义自然语言 → 生成 GCJP 代码 → 受限执行 + 三层验证 →
first_pass 失败则进入闭环修复 → 输出最终 DAG + 合并指标。

把 exp_01b(生成)与 exp_01c(修复)合并成**单次 per-case 流水线**,免去原先"先跑
01b 出 reports/ 再手动把目录喂给 01c --source-report-dir"的两段式割裂。生成 / 修复 /
执行 / 验证 / 评分全部复用现成模块,本文件只做编排:

  生成(PlannerAgent) → 评估(evaluate_graph_against_expected)
    → 若未 first_pass:RepairAgent.repair_gcjp 多轮修复(把生成代码当 broken_code)
    → 记录初始 / 各修复轮 / 最终,导出最终 BuiltGraph 为 DAG。

公平性(与 1B/1I 一致,硬约束):
  expected_patterns / expected_result / tags 绝不进任何 prompt。生成只喂
  standard_instruction + 配置上下文;修复额外只喂 verification_report(Z3/执行的失败
  反馈,属环境信号)与同一份配置上下文。评分器单独从原始 case 读真值,与 prompt 解耦。
  评分用 evaluate_graph_against_expected,与 1A/1B/1H/1I/1J 七项口径完全一致,可横向对照。

用法:
  python -m experiments.exp_01l_standard_nl_to_gcjp_with_repair \
    --provider-profile <profile> --limit 5 --workers 4 \
    --dataset datasets/generated/_trial/phase1_standard_nl_cases.v2.jsonl \
    --max-repair-rounds 2

预期输出:
  out/phase1_generation/<run_dir>/exp_01l_standard_nl_to_gcjp_with_repair/ 下:
    raw_outputs/  initial_code/  final_code/  repair_attempts/  final_dag/  reports/
    metrics.json
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agents.llm_client import LLMClient, LLMConfigError
from agents.planner_agent import PlannerAgent
from agents.repair_agent import RepairAgent
from experiments.exp_01g_raw_nl_to_gcjp_pipeline import _build_generation_config_context
from experiments.phase1_common import (
    Z3_LOCK,
    add_common_args,
    dag_exact_match,
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
from gcjp.code_executor import execute_gcjp_code
from verifier.pipeline import VerificationPipeline


EXPERIMENT_NAME = "exp_01l_standard_nl_to_gcjp_with_repair"

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
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_common_args(parser)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("datasets") / "phase1_standard_nl_cases.jsonl",
    )
    parser.add_argument(
        "--prompt",
        type=Path,
        default=Path("prompts") / "standard_nl_to_gcjp_prompt.md",
        help="Generation prompt (NL -> GCJP).",
    )
    parser.add_argument(
        "--repair-prompt",
        type=Path,
        default=Path("prompts") / "gcjp_repair_prompt.md",
        help="Repair prompt (broken code + verification report -> fixed code).",
    )
    parser.add_argument("--max-repair-rounds", type=int, default=2)
    parser.add_argument(
        "--action-templates",
        type=Path,
        default=Path("configs") / "action_templates.yaml",
        help="Action defaults injected into the generation/repair prompt context.",
    )
    parser.add_argument(
        "--capability-model",
        type=Path,
        default=Path("configs") / "capability_model.yaml",
        help="Fleet capability/resource model injected into the prompt context.",
    )
    parser.add_argument(
        "--master-dataset",
        type=Path,
        default=Path("datasets") / "v2" / "_trial_master.jsonl",
        help=(
            "Master dataset with full ground truth (expected_graph + "
            "canonical_task_plan) for the dag_exact metric. Evaluation-side "
            "only, never enters any prompt. If missing, dag_exact is skipped."
        ),
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
    _attach_ground_truth(cases, getattr(args, "master_dataset", None))

    # 只注入配置上下文(动作/能力真源);expected_* 真值绝不进此 payload。
    gen_context = _build_generation_config_context(
        action_templates_path=args.action_templates,
        capability_model_path=args.capability_model,
    )
    config = load_config_from_args(args)
    provider_summary = config.safe_summary()
    run_output = resolve_phase1_run_output(
        output_dir=args.output_dir,
        provider_summary=provider_summary,
        run_label=args.run_label,
        no_run_timestamp=args.no_run_timestamp,
    )

    client = LLMClient(config)
    planner = PlannerAgent(client)
    repair = RepairAgent(client)
    gen_prompt = read_prompt_template(args.prompt)
    repair_prompt = read_prompt_template(args.repair_prompt)
    dirs = _ensure_dirs(run_output["run_dir"])
    max_rounds = max(0, int(args.max_repair_rounds))

    def _worker(case: dict[str, Any]) -> dict[str, Any]:
        try:
            return _run_case(
                case=case,
                planner=planner,
                repair=repair,
                gen_prompt=gen_prompt,
                repair_prompt=repair_prompt,
                gen_context=gen_context,
                max_rounds=max_rounds,
            )
        except Exception as exc:  # noqa: BLE001 - worker 必须自兜底,保聚合不崩
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

    metrics = _aggregate(records)
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


# --------------------------------------------------------------------------- #
# per-case: 生成 -> 评估 -> 闭环修复
# --------------------------------------------------------------------------- #
def _run_case(
    *,
    case: dict[str, Any],
    planner: PlannerAgent,
    repair: RepairAgent,
    gen_prompt: str,
    repair_prompt: str,
    gen_context: dict[str, Any],
    max_rounds: int,
) -> dict[str, Any]:
    sample_id = case["sample_id"]
    nl = case.get("standard_instruction") or ""

    # round 0: 生成
    gen = planner.generate_gcjp(
        sample_id=sample_id,
        prompt_template=gen_prompt,
        case_payload=gen_context,
        standard_instruction=nl,
    )
    code = gen.extracted_code
    graph, report, exec_result = _exec_and_verify(code)
    eval0 = _evaluate(case, graph, report, exec_result)

    attempts: list[dict[str, Any]] = []
    code_cur, report_cur, eval_cur, graph_cur = code, report, eval0, graph

    for repair_round in range(1, max_rounds + 1):
        if not _repair_actionable(eval_cur, report_cur):
            break
        rgen = repair.repair_gcjp(
            sample_id=sample_id,
            repair_round=repair_round,
            prompt_template=repair_prompt,
            broken_code=code_cur,
            verification_report=(report_cur.to_dict() if report_cur else {}),
            case_payload=gen_context,   # 配置上下文,不含真值
            prompt_context=None,
        )
        code_cur = rgen.repaired_code
        graph_cur, report_cur, exec_result = _exec_and_verify(code_cur)
        eval_cur = _evaluate(case, graph_cur, report_cur, exec_result)
        attempts.append(
            {
                "repair_round": repair_round,
                "generation": asdict(rgen),
                "evaluation": eval_cur,
            }
        )

    return {
        "sample_id": sample_id,
        "case": case,
        "generation": asdict(gen),
        "initial_evaluation": eval0,
        "attempts": attempts,
        "final_evaluation": eval_cur,
        "initial_code": code,
        "final_code": code_cur,
        "final_dag": _export_dag(graph_cur),
        "evaluation": {
            "initial_pass": bool(eval0["first_pass"]),
            "repair_attempted": bool(attempts),
            "repair_success": (not eval0["first_pass"]) and bool(eval_cur["first_pass"]),
            "final_pass": bool(eval_cur["first_pass"]),
            "repair_rounds": len(attempts),
            "initial_error_type": eval0["execution_error_type"],
            "final_error_type": eval_cur["execution_error_type"],
            "initial_dag_exact": eval0.get("dag_exact"),
            "final_dag_exact": eval_cur.get("dag_exact"),
        },
    }


def _exec_and_verify(code: str):
    """受限执行 + 三层验证。返回 (graph, report, exec_result);空代码返回三个 None。"""
    if not code:
        return None, None, None
    exec_result = execute_gcjp_code(code)
    graph = exec_result.graph if exec_result and exec_result.graph else None
    with Z3_LOCK:
        report = VerificationPipeline(z3_timeout_ms=15_000).verify_gcjp_code(code)
    return graph, report, exec_result


def _evaluate(
    case: dict[str, Any],
    graph,
    report,
    exec_result,
) -> dict[str, Any]:
    """执行成败 + 与 1B/1I 同口径的七项图层评分 + dag_exact(真值仅从 case 读,不进 prompt)。

    dag_exact 是第 8 项、最严口径:整图(节点映射/逐边端点/同步组)与 master 真值
    精确匹配;case 未携带完整真值时为 None,不参与 first_pass 判定。
    """
    return {
        "execution_success": bool(exec_result and exec_result.passed),
        "execution_error_type": exec_result.error_type if exec_result else "NO_CODE",
        **evaluate_graph_against_expected(case, graph, report),
        "dag_exact": dag_exact_match(case, graph),
    }


def _attach_ground_truth(cases: list[dict[str, Any]], master_path: Path | None) -> None:
    """把 master 数据集的完整真值(expected_graph/canonical_task_plan)合入 case。

    仅供评估侧 dag_exact 使用;生成与修复的 prompt payload 来自配置上下文
    (_build_generation_config_context),与 case 字段隔离,真值不会泄露进 prompt。
    数据集本身已含完整真值、或 master 文件缺失/查不到样本时,保持原样(dag_exact=None)。
    """
    if master_path is None or not Path(master_path).exists():
        return
    masters: dict[str, dict[str, Any]] = {}
    with Path(master_path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            masters[record.get("sample_id")] = record
    for case in cases:
        if case.get("expected_graph"):
            continue
        master = masters.get(case.get("sample_id"))
        if master:
            case["expected_graph"] = master.get("expected_graph")
            case["canonical_task_plan"] = master.get("canonical_task_plan")


def _repair_actionable(eval_cur: dict[str, Any], report) -> bool:
    """是否存在修复 Agent 可据以行动的验证信号。

    修复 prompt 只喂验证报告(L1-L4 环境信号),不含真值。若 4 层验证全绿且 z3 结果符合预期
    (overall_passed 且 l3_expected_result),则 first_pass 失败只可能来自与真值的 node/edge/
    constraint 完整度差异 —— 报告里没有任何可修信号,修复必然空转。这类失败不进修复循环,
    既避免无效 LLM 调用,也让 repair_success_rate 只统计“真正有信号可修”的样本。
    """
    if eval_cur["first_pass"]:
        return False
    report_green = bool(report and report.overall_passed)
    if report_green and eval_cur.get("l3_expected_result"):
        return False
    return True


def _export_dag(graph) -> dict[str, Any] | None:
    """最终 BuiltGraph -> 节点 + 有向边,格式对齐 case 的 expected_graph 便于直接 diff。"""
    if graph is None:
        return None
    nodes = [
        {"task_id": n.task_id, "actor": n.actor, "action": n.action, "target": n.target}
        for n in graph.nodes.values()
    ]
    edges = [
        {
            "source": e.source,
            "target": e.target,
            "relation": e.relation,
            "condition": getattr(e, "condition", None),
        }
        for e in graph.edges
    ]
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }


def _error_record(case: dict[str, Any], exc: Exception) -> dict[str, Any]:
    err_eval = {
        "execution_success": False,
        "execution_error_type": type(exc).__name__,
        **dict(_EMPTY_GRAPH_EVAL),
        # 真值在手却没产出图 → False;真值不可得 → None(不参与 dag_exact 分母)
        "dag_exact": dag_exact_match(case, None),
    }
    return {
        "sample_id": case.get("sample_id", "<unknown>"),
        "case": case,
        "generation": None,
        "initial_evaluation": err_eval,
        "attempts": [],
        "final_evaluation": err_eval,
        "initial_code": "",
        "final_code": "",
        "final_dag": None,
        "evaluation": {
            "initial_pass": False,
            "repair_attempted": False,
            "repair_success": False,
            "final_pass": False,
            "repair_rounds": 0,
            "initial_error_type": type(exc).__name__,
            "final_error_type": type(exc).__name__,
            "error": f"{type(exc).__name__}: {exc}",
        },
    }


# --------------------------------------------------------------------------- #
# 聚合 / 输出
# --------------------------------------------------------------------------- #
def _aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)

    def frac(count: int) -> float:
        return count / total if total else 0.0

    def eval_frac(which: str, field: str) -> float:
        return frac(sum(1 for r in records if (r.get(which) or {}).get(field)))

    initial_pass = sum(1 for r in records if r["evaluation"]["initial_pass"])
    final_pass = sum(1 for r in records if r["evaluation"]["final_pass"])
    repair_attempt = sum(1 for r in records if r["evaluation"]["repair_attempted"])
    repair_success = sum(1 for r in records if r["evaluation"]["repair_success"])
    total_rounds = sum(r["evaluation"]["repair_rounds"] for r in records)

    # dag_exact 三值:True/False 参与分母,None(无完整真值)不参与
    def dag_rate(which: str) -> tuple[float | None, int]:
        vals = [(r.get(which) or {}).get("dag_exact") for r in records]
        evaluable = sum(1 for v in vals if v is not None)
        if not evaluable:
            return None, 0
        return sum(1 for v in vals if v is True) / evaluable, evaluable

    initial_dag_rate, dag_evaluable = dag_rate("initial_evaluation")
    final_dag_rate, _ = dag_rate("final_evaluation")

    transitions: dict[str, int] = {}
    recovered: dict[str, int] = {}
    for r in records:
        ev = r["evaluation"]
        key = f"{ev['initial_error_type']}→{ev['final_error_type']}"
        transitions[key] = transitions.get(key, 0) + 1
        if ev["repair_success"]:
            it = ev["initial_error_type"] or "UNKNOWN"
            recovered[it] = recovered.get(it, 0) + 1

    return {
        "experiment": EXPERIMENT_NAME,
        "total_cases": total,
        "rates": {
            # 生成单次 vs 修复后(核心对照)
            "first_pass_rate": frac(initial_pass),
            "final_pass_rate": frac(final_pass),
            # 修复闭环
            "repair_attempt_rate": frac(repair_attempt),
            "repair_success_rate": (repair_success / repair_attempt) if repair_attempt else 0.0,
            "avg_repair_rounds": frac(total_rounds),
            # 还原度(初始)
            "initial_execution_success_rate": eval_frac("initial_evaluation", "execution_success"),
            "initial_node_complete_rate": eval_frac("initial_evaluation", "node_complete"),
            "initial_edge_complete_rate": eval_frac("initial_evaluation", "edge_complete"),
            "initial_constraint_complete_rate": eval_frac("initial_evaluation", "constraint_complete"),
            # 还原度(最终)
            "final_execution_success_rate": eval_frac("final_evaluation", "execution_success"),
            "final_node_complete_rate": eval_frac("final_evaluation", "node_complete"),
            "final_edge_complete_rate": eval_frac("final_evaluation", "edge_complete"),
            "final_constraint_complete_rate": eval_frac("final_evaluation", "constraint_complete"),
            "final_l3_rate": eval_frac("final_evaluation", "l3_expected_result"),
            # DAG 精确匹配(第 8 项,最严口径:节点映射/逐边端点/同步组逐一对照真值;
            # 分母=携带完整真值的样本数,见 dag_exact_evaluable)
            "initial_dag_exact_rate": initial_dag_rate,
            "final_dag_exact_rate": final_dag_rate,
        },
        "dag_exact_evaluable": dag_evaluable,
        "recovered_error_type_distribution": recovered,
        "error_transition_matrix": transitions,
        "records": [
            {"sample_id": r["sample_id"], "evaluation": r["evaluation"]}
            for r in records
        ],
    }


def _ensure_dirs(root: Path) -> dict[str, Path]:
    exp_root = root / EXPERIMENT_NAME
    dirs = {
        "root": exp_root,
        "raw": exp_root / "raw_outputs",
        "initial_code": exp_root / "initial_code",
        "final_code": exp_root / "final_code",
        "attempts": exp_root / "repair_attempts",
        "final_dag": exp_root / "final_dag",
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
    (dirs["initial_code"] / f"{sid}.py").write_text(
        record.get("initial_code") or "", encoding="utf-8"
    )
    (dirs["final_code"] / f"{sid}.py").write_text(
        record.get("final_code") or "", encoding="utf-8"
    )
    (dirs["attempts"] / f"{sid}.json").write_text(
        json.dumps(record.get("attempts") or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    final_dag = record.get("final_dag")
    if final_dag is not None:
        (dirs["final_dag"] / f"{sid}.json").write_text(
            json.dumps(final_dag, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (dirs["reports"] / f"{sid}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _summary_line(record: dict[str, Any]) -> str:
    ev = record["evaluation"]
    mark = "通过" if ev["final_pass"] else "失败"
    line = (
        f"[{mark}] {record['sample_id']} "
        f"initial={ev['initial_pass']} rounds={ev['repair_rounds']} "
        f"repair_success={ev['repair_success']} final={ev['final_pass']}"
    )
    err = ev.get("error")
    if err and not ev["final_pass"]:
        line += f" err={err[:120]}"
    return line


if __name__ == "__main__":
    raise SystemExit(main())
