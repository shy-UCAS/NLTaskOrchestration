"""
Phase 1 GCJP 生成实验的公共工具函数：CLI 参数、LLM 调用、评估与输出。
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agents.llm_client import (
    LLMClient,
    LLMConfigError,
    load_provider_config,
    provider_summary_items,
)
from agents.planner_agent import PlannerAgent, PlannerGeneration
from gcjp.code_executor import execute_gcjp_code
from gcjp.mission_graph import BuiltGraph
from verifier.pipeline import VerificationPipeline, VerificationReport


DEFAULT_OUTPUT_DIR = Path("out") / "phase1_generation"


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="LLM provider YAML config path")
    parser.add_argument("--provider-profile", help="LLM provider profile name")
    parser.add_argument(
        "--local-provider",
        choices=["codex", "claude"],
        help="Read local API config written by CC Switch/Codex/Claude tools",
    )
    parser.add_argument(
        "--protocol",
        choices=["openai_chat", "openai_responses", "anthropic_messages"],
    )
    parser.add_argument(
        "--transport",
        choices=["http", "official_sdk"],
        help="Request backend: current raw HTTP path or official provider SDK",
    )
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--model")
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument(
        "--thinking",
        choices=["enabled", "disabled", "adaptive"],
        help="Provider reasoning switch, sent as {'thinking': {'type': value}}",
    )
    parser.add_argument(
        "--thinking-budget-tokens",
        type=int,
        help="Anthropic Messages thinking.budget_tokens value; must be less than max_tokens",
    )
    parser.add_argument(
        "--reasoning-effort",
        help="OpenAI Chat reasoning_effort value, e.g. high/max",
    )
    parser.add_argument(
        "--output-effort",
        help="Anthropic Messages output_config.effort value, e.g. high/max",
    )
    parser.add_argument("--retry-attempts", type=int)
    parser.add_argument("--retry-backoff-seconds", type=float)
    parser.add_argument(
        "--auth-header",
        choices=["default", "x_api_key", "x-api-key", "bearer", "both"],
        help="Anthropic Messages auth header strategy for proxies/gateways",
    )
    parser.add_argument(
        "--user-agent",
        help="Request User-Agent for providers that require a CLI-style UA",
    )
    parser.add_argument(
        "--disable-compat-preset",
        action="store_true",
        help="Disable automatic base_url compatibility presets",
    )
    parser.add_argument("--limit", type=int, help="Limit number of cases")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for raw outputs, extracted code, reports and metrics",
    )
    parser.add_argument(
        "--run-label",
        type=str,
        default=None,
        help=(
            "Optional run name under --output-dir. If omitted, the label is "
            "derived from provider/model/base_url. By default, a timestamp is "
            "appended to prevent rerun overwrites."
        ),
    )
    parser.add_argument(
        "--no-run-timestamp",
        action="store_true",
        help="Use the exact run label directory without appending a timestamp.",
    )
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="Save sanitized metrics + per-case results to experiments/baselines/ for git tracking.",
    )


def build_agent_from_args(args: argparse.Namespace) -> PlannerAgent:
    config = load_config_from_args(args)
    return PlannerAgent(LLMClient(config))


def load_config_from_args(args: argparse.Namespace):
    overrides = {
        "protocol": args.protocol,
        "transport": args.transport,
        "base_url": args.base_url,
        "api_key": args.api_key,
        "model": args.model,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "thinking": args.thinking,
        "thinking_budget_tokens": args.thinking_budget_tokens,
        "reasoning_effort": args.reasoning_effort,
        "output_effort": args.output_effort,
        "retry_attempts": args.retry_attempts,
        "retry_backoff_seconds": args.retry_backoff_seconds,
        "auth_header": args.auth_header,
        "user_agent": args.user_agent,
        "disable_compat_preset": args.disable_compat_preset or None,
    }
    return load_provider_config(
        config_path=args.config,
        profile=args.provider_profile,
        local_provider=args.local_provider,
        overrides=overrides,
    )


def print_provider_summary_from_args(args: argparse.Namespace) -> None:
    """打印脱敏后的 provider 配置摘要和最终请求 headers 预览。"""
    config = load_config_from_args(args)
    print("[配置读取成功]")
    for key, value in provider_summary_items(config):
        print(f"{key}: {value}")
    print("-" * 40)


def resolve_phase1_run_output(
    *,
    output_dir: Path,
    provider_summary: dict[str, Any],
    run_label: str | None = None,
    no_run_timestamp: bool = False,
) -> dict[str, Any]:
    """Resolve the concrete run directory and metadata for a Phase 1 experiment."""
    label_source = "cli" if run_label else "auto_config"
    label = run_label or auto_run_label_from_config(provider_summary)
    run_dir, run_dir_name, run_timestamp = _resolve_run_dir(
        output_dir,
        label,
        timestamp_enabled=not no_run_timestamp,
    )
    return {
        "base_output_dir": str(output_dir),
        "run_dir": run_dir,
        "run_label": label,
        "run_label_source": label_source,
        "run_dir_name": run_dir_name,
        "run_timestamp": run_timestamp,
        "run_timestamp_enabled": bool(label and not no_run_timestamp),
        "provider": provider_summary,
    }


def phase1_run_metadata_json(run_output: dict[str, Any]) -> dict[str, Any]:
    return {
        "base_output_dir": run_output["base_output_dir"],
        "run_dir": str(run_output["run_dir"]),
        "run_label": run_output["run_label"],
        "run_label_source": run_output["run_label_source"],
        "run_dir_name": run_output["run_dir_name"],
        "run_timestamp": run_output["run_timestamp"],
        "run_timestamp_enabled": run_output["run_timestamp_enabled"],
        "provider": run_output["provider"],
    }


def write_latest_run_index(
    *,
    run_output: dict[str, Any],
    experiment_name: str,
    experiment_dir: Path,
    reports_dir: Path | None,
    metrics_path: Path | None = None,
    summary_path: Path | None = None,
) -> Path:
    index = {
        "experiment": experiment_name,
        **phase1_run_metadata_json(run_output),
        "experiment_dir": str(experiment_dir),
        "reports_dir": str(reports_dir) if reports_dir else None,
        "metrics_path": str(metrics_path) if metrics_path else None,
        "summary_path": str(summary_path) if summary_path else None,
    }
    path = Path(run_output["base_output_dir"]) / "latest_run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def auto_run_label_from_config(config_summary: dict[str, Any]) -> str:
    host = _base_url_hostname(config_summary.get("base_url"))
    parts = [
        _slugify_run_label_part(config_summary.get("provider_name")),
        _slugify_run_label_part(config_summary.get("model")),
        _slugify_run_label_part(host or config_summary.get("protocol")),
    ]
    parts = [part for part in parts if part]
    return "__".join(parts) or "llm_run"


def _resolve_run_dir(
    output_dir: Path,
    run_label: str,
    *,
    timestamp_enabled: bool,
) -> tuple[Path, str, str | None]:
    if not timestamp_enabled:
        return output_dir / run_label, run_label, None

    run_timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir_stem = f"{run_label}__{run_timestamp}"
    run_dir = output_dir / run_dir_stem
    suffix = 2
    while run_dir.exists():
        run_dir = output_dir / f"{run_dir_stem}_{suffix}"
        suffix += 1
    return run_dir, run_dir.name, run_timestamp


def _base_url_hostname(base_url: Any) -> str:
    text = str(base_url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if not parsed.hostname and "://" not in text:
        parsed = urlparse("//" + text)
    return (parsed.hostname or "").lower()


def _slugify_run_label_part(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def load_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    cases = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            cases.append(json.loads(line))
            if limit is not None and len(cases) >= limit:
                break
    return cases


def read_prompt_template(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def run_generation_experiment(
    *,
    experiment_name: str,
    dataset_path: Path,
    prompt_path: Path,
    args: argparse.Namespace,
    case_payload_fn,
    standard_instruction_fn=None,
) -> dict[str, Any]:
    cases = load_jsonl(dataset_path, limit=args.limit)
    if not cases:
        raise ValueError(f"No cases loaded from {dataset_path}")

    config = load_config_from_args(args)
    provider_summary = config.safe_summary()
    run_output = resolve_phase1_run_output(
        output_dir=args.output_dir,
        provider_summary=provider_summary,
        run_label=args.run_label,
        no_run_timestamp=args.no_run_timestamp,
    )

    agent = PlannerAgent(LLMClient(config))
    prompt_template = read_prompt_template(prompt_path)
    output_dirs = _ensure_output_dirs(run_output["run_dir"], experiment_name)

    records = []
    for case in cases:
        sample_id = case["sample_id"]
        try:
            generation = agent.generate_gcjp(
                sample_id=sample_id,
                prompt_template=prompt_template,
                case_payload=case_payload_fn(case),
                standard_instruction=(
                    standard_instruction_fn(case)
                    if standard_instruction_fn else None
                ),
            )
            record = _evaluate_generation(case, generation)
        except Exception as exc:
            record = _error_record(case, exc)

        _write_case_outputs(output_dirs, sample_id, record)
        records.append(record)
        print(_summary_line(record))

    metrics = _aggregate_metrics(experiment_name, records)
    metrics.update(phase1_run_metadata_json(run_output))
    metrics["output_dir"] = str(output_dirs["root"])
    metrics_path = output_dirs["root"] / "metrics.json"
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_latest_run_index(
        run_output=run_output,
        experiment_name=experiment_name,
        experiment_dir=output_dirs["root"],
        reports_dir=output_dirs["reports"],
        metrics_path=metrics_path,
    )
    print(f"\n[{experiment_name}] 汇总指标 -> {metrics_path}")
    print(json.dumps(metrics["rates"], ensure_ascii=False, indent=2))
    return metrics


def _evaluate_generation(
    case: dict[str, Any],
    generation: PlannerGeneration,
) -> dict[str, Any]:
    sample_id = case["sample_id"]
    extraction_ok = bool(generation.extraction.get("ok"))
    code = generation.extracted_code

    exec_result = execute_gcjp_code(code) if extraction_ok else None
    graph = exec_result.graph if exec_result and exec_result.graph else None
    report = (
        VerificationPipeline(z3_timeout_ms=15_000).verify_gcjp_code(code)
        if extraction_ok else None
    )
    eval_result = _evaluate_expected(case, graph, report, exec_result, extraction_ok)

    return {
        "sample_id": sample_id,
        "case": case,
        "generation": asdict(generation),
        "execution_error_type": exec_result.error_type if exec_result else "NO_CODE",
        "report": report.to_dict() if report else None,
        "evaluation": eval_result,
    }


def _evaluate_expected(
    case: dict[str, Any],
    graph: BuiltGraph | None,
    report: VerificationReport | None,
    exec_result,
    extraction_ok: bool,
) -> dict[str, Any]:
    expected_patterns = case.get("expected_patterns", {}) or {}
    expected_result = case.get("expected_result")
    layer2 = _layer(report, 2)
    layer3 = _layer(report, 3)
    z3_result = (layer3.details or {}).get("z3_result") if layer3 else None

    if expected_result == "sat":
        l3_expected = bool(report and report.overall_passed and z3_result == "sat")
    elif expected_result == "unsat":
        l3_expected = z3_result == "unsat"
    else:
        l3_expected = False

    node_ok = _node_complete(graph, expected_patterns)
    edge_ok = _edge_complete(graph, expected_patterns)
    constraint_ok = _constraint_complete(graph, expected_patterns)
    safety_passed = bool(exec_result and exec_result.safety and exec_result.safety.passed)
    execution_success = bool(exec_result and exec_result.passed)

    return {
        "syntax_extract": extraction_ok,
        "safety_pass": safety_passed,
        "execution_success": execution_success,
        "builtgraph_success": bool(graph),
        "l2_graph_pass": bool(layer2 and layer2.passed),
        "l3_expected_result": l3_expected,
        "first_pass": l3_expected and node_ok and edge_ok and constraint_ok,
        "node_complete": node_ok,
        "edge_complete": edge_ok,
        "constraint_complete": constraint_ok,
    }


def _layer(report: VerificationReport | None, layer_no: int):
    if not report:
        return None
    for layer in report.layers:
        if layer.layer == layer_no:
            return layer
    return None


def _node_complete(graph: BuiltGraph | None, expected: dict[str, Any]) -> bool:
    if not graph:
        return False
    node_count = expected.get("node_count")
    if node_count is not None and len(graph.nodes) != int(node_count):
        return False
    expected_nodes = expected.get("nodes") or []
    actual = {
        (node.actor, node.action, node.target)
        for node in graph.nodes.values()
    }
    for item in expected_nodes:
        triple = (item.get("actor"), item.get("action"), item.get("target"))
        if triple not in actual:
            return False
    return True


def _edge_complete(graph: BuiltGraph | None, expected: dict[str, Any]) -> bool:
    if not graph:
        return False
    expected_relations = expected.get("edge_relations") or []
    actual_relations = [edge.relation for edge in graph.edges]
    return all(rel in actual_relations for rel in expected_relations)


def _constraint_complete(graph: BuiltGraph | None, expected: dict[str, Any]) -> bool:
    if not graph:
        return False
    expected_types = expected.get("constraint_types") or []
    actual_types = [constraint.constraint_type for constraint in graph.constraints]
    return all(ctype in actual_types for ctype in expected_types)


def _aggregate_metrics(experiment_name: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    keys = [
        "syntax_extract",
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
    total = len(records)
    rates = {}
    for key in keys:
        count = sum(1 for r in records if r["evaluation"].get(key))
        rates[f"{key}_rate"] = count / total if total else 0.0

    error_dist: dict[str, int] = {}
    for record in records:
        err = record.get("execution_error_type") or "UNKNOWN"
        error_dist[err] = error_dist.get(err, 0) + 1

    return {
        "experiment": experiment_name,
        "total_cases": total,
        "rates": rates,
        "error_type_distribution": error_dist,
        "records": [
            {
                "sample_id": r["sample_id"],
                "evaluation": r["evaluation"],
                "execution_error_type": r.get("execution_error_type"),
            }
            for r in records
        ],
    }


def _error_record(case: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "sample_id": case["sample_id"],
        "case": case,
        "generation": None,
        "execution_error_type": type(exc).__name__,
        "report": None,
        "evaluation": {
            "syntax_extract": False,
            "safety_pass": False,
            "execution_success": False,
            "builtgraph_success": False,
            "l2_graph_pass": False,
            "l3_expected_result": False,
            "first_pass": False,
            "node_complete": False,
            "edge_complete": False,
            "constraint_complete": False,
        },
        "error": f"{type(exc).__name__}: {exc}",
    }


def _ensure_output_dirs(root: Path, experiment_name: str) -> dict[str, Path]:
    exp_root = root / experiment_name
    dirs = {
        "root": exp_root,
        "raw": exp_root / "raw_outputs",
        "code": exp_root / "extracted_code",
        "reports": exp_root / "reports",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def _write_case_outputs(
    output_dirs: dict[str, Path],
    sample_id: str,
    record: dict[str, Any],
) -> None:
    raw_response = ""
    extracted_code = ""
    generation = record.get("generation")
    if generation:
        raw_response = generation.get("raw_response") or ""
        extracted_code = generation.get("extracted_code") or ""

    (output_dirs["raw"] / f"{sample_id}.txt").write_text(
        raw_response,
        encoding="utf-8",
    )
    (output_dirs["code"] / f"{sample_id}.py").write_text(
        extracted_code,
        encoding="utf-8",
    )
    (output_dirs["reports"] / f"{sample_id}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _summary_line(record: dict[str, Any]) -> str:
    ev = record["evaluation"]
    mark = "通过" if ev.get("first_pass") else "失败"
    line = (
        f"[{mark}] {record['sample_id']} "
        f"extract={ev.get('syntax_extract')} "
        f"safety={ev.get('safety_pass')} "
        f"exec={ev.get('execution_success')} "
        f"l3={ev.get('l3_expected_result')}"
    )
    if ev.get("first_pass"):
        return line
    diagnostics = _failure_diagnostics(record)
    return f"{line} {diagnostics}" if diagnostics else line


def _failure_diagnostics(record: dict[str, Any]) -> str:
    parts = []
    err_type = record.get("execution_error_type")
    if err_type:
        parts.append(f"error_type={err_type}")
    error = record.get("error")
    if error:
        parts.append(f"error={_compact_text(error, 120)}")

    layer1 = _first_report_layer(record.get("report"), 1)
    details = layer1.get("details", {}) if layer1 else {}
    lineno = details.get("gcjp_lineno")
    if lineno is not None:
        parts.append(f"line={lineno}")
    api_error = details.get("api_error") or {}
    api_code = api_error.get("code")
    if api_code:
        parts.append(f"api_code={api_code}")
    extraction = ((record.get("generation") or {}).get("extraction") or {})
    extraction_error = extraction.get("error")
    if extraction_error:
        parts.append(f"extract_error={_compact_text(extraction_error, 80)}")
    return " ".join(parts)


def _first_report_layer(report: dict[str, Any] | None, layer_no: int) -> dict[str, Any] | None:
    if not report:
        return None
    for layer in report.get("layers", []):
        if layer.get("layer") == layer_no:
            return layer
    return None


def _compact_text(text: str, max_len: int) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3] + "..."


BASELINES_DIR = Path("experiments") / "baselines"
BASELINE_DOC_PATH = Path("docs") / "phase1_baseline_report.md"

_BASELINE_SECTION_TITLES = {
    "exp_01f_instruction_normalization": "Baseline E：阶段 1F 指令规范化",
    "exp_01g_raw_nl_to_gcjp_pipeline": "Baseline F：阶段 1G 原始 NL → GCJP 端到端管道",
}


def save_baseline_json(
    experiment_name: str,
    metrics: dict[str, Any],
    baselines_dir: Path | None = None,
) -> Path:
    """导出脱敏指标 + 逐 case 结果到 experiments/baselines/{experiment}.json。"""
    out_dir = baselines_dir or BASELINES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    provider = metrics.get("provider", {})
    sanitized_provider = {
        k: v for k, v in provider.items()
        if k not in ("api_key", "pre_headers", "effective_headers_preview")
    }

    baseline = {
        "experiment": experiment_name,
        "timestamp": datetime.now().isoformat(),
        "provider": sanitized_provider,
        "total_cases": metrics.get("total_cases", 0),
        "rates": metrics.get("rates", {}),
        "records": metrics.get("records", []),
    }

    # 保留失败归因分布（exp_01g 特有）
    if "failure_attribution_distribution" in metrics:
        baseline["failure_attribution_distribution"] = metrics[
            "failure_attribution_distribution"
        ]

    path = out_dir / f"{experiment_name}.json"
    path.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"[save-baseline] JSON -> {path}")
    return path


def append_baseline_markdown(
    experiment_name: str,
    metrics: dict[str, Any],
    doc_path: Path | None = None,
) -> bool:
    """往 docs/phase1_baseline_report.md 追加该实验的 section。已存在则跳过。"""
    path = doc_path or BASELINE_DOC_PATH
    if not path.exists():
        print(f"[save-baseline] 文档不存在，跳过 markdown 追加: {path}")
        return False

    title = _BASELINE_SECTION_TITLES.get(experiment_name)
    if not title:
        print(f"[save-baseline] 未知实验名，跳过: {experiment_name}")
        return False

    existing = path.read_text(encoding="utf-8")
    if title in existing:
        print(f"[save-baseline] section 已存在，跳过: {title}")
        return False

    rates = metrics.get("rates", {})
    total = metrics.get("total_cases", 0)
    mode = metrics.get("mode", "")

    lines = [
        "",
        f"### {title}",
        "",
        "日期：" + datetime.now().strftime("%Y-%m-%d"),
        "",
        "命令：",
        "",
        "```powershell",
    ]

    if experiment_name == "exp_01f_instruction_normalization":
        cmd = f"python -m experiments.exp_01f_instruction_normalization --local-provider claude"
        if mode:
            cmd += f" --mode {mode}"
        lines.append(cmd)
    elif experiment_name == "exp_01g_raw_nl_to_gcjp_pipeline":
        lines.append(
            "python -m experiments.exp_01g_raw_nl_to_gcjp_pipeline --local-provider claude"
        )

    lines += [
        "```",
        "",
        "结果：",
        "",
        "```text",
        f"total_cases: {total}",
    ]
    for key, value in rates.items():
        lines.append(f"{key}: {value}")
    lines += ["```", ""]

    path.write_text(existing.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"[save-baseline] Markdown -> {path}")
    return True


def handle_config_error(exc: LLMConfigError) -> int:
    print(f"LLM 配置错误: {exc}")
    print(
        "请设置 PHASE1_LLM_PROTOCOL、PHASE1_LLM_API_KEY 和 PHASE1_LLM_MODEL 环境变量，"
        "或传 --config/--provider-profile，或传 --local-provider codex|claude。"
    )
    return 2
