"""
experiments/exp_01b_standard_nl_to_gcjp.py
用法：
  # 默认从 configs/llm_providers.local.yaml 读取 profile
  python -m experiments.exp_01b_standard_nl_to_gcjp --provider-profile <profile_name> --limit 1 --workers 8
  # 覆盖dataset路径
  --dataset datasets/generated/phase1_standard_nl_cases.v2.jsonl

  # 覆盖标准自然语言数据集 / prompt / 配置表
  python -m experiments.exp_01b_standard_nl_to_gcjp --provider-profile <profile_name> \
      --dataset datasets/phase1_standard_nl_cases.jsonl \
      --prompt prompts/standard_nl_to_gcjp_prompt.md \
      --action-templates configs/action_templates.yaml \
      --capability-model configs/capability_model.yaml

Phase 1B：1F-complete 标准语义自然语言 → 配置注入 → LLM 生成 GCJP 代码实验。

场景：
  从 datasets/phase1_standard_nl_cases.jsonl 读取**只含作战语义**的标准化指令
  （actor/action/target/relation/condition + 必要的 time window 或 physical context），
  并把 configs/action_templates.yaml 与 configs/capability_model.yaml 作为
  generation_context 一并注入 prompt（复用 1G 的 _build_generation_config_context）。
  LLM 据此生成 GCJP 代码，再执行安全检查 + 受限执行 + 验证管道，统计各阶段通过率。

与旧版 1B 的区别（见 docs/exp_01b_semantic_contract_refactor_rationale.md）：
  旧版 standard_instruction 把 duration / energy / ammo / required_capability /
  资源上限等系统参数写进自然语言，使数据集成为第二套参数真源，Z3 容易形成
  “自定义消耗 + 自定义上限”的自洽验证。改造后这些系统参数一律由配置表注入，
  自然语言只表达作战语义，与 1F 完整性契约的分层保持一致；Z3 验证的是
  capability_model 下的真实可行性，而非样本文本自造参数下的可行性。

预期输出：
  out/phase1_generation/exp_01b_standard_nl_to_gcjp/ 下输出原始回复、
  提取的 GCJP 代码、验证报告、汇总 metrics.json。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from agents.llm_client import LLMConfigError
# 与 1G/1H 共用同一份配置上下文构造逻辑；后续 §4.5 合并时再提升到 phase1_common。
from experiments.exp_01g_raw_nl_to_gcjp_pipeline import (
    _build_generation_config_context,
)
from experiments.phase1_common import (
    add_common_args,
    handle_config_error,
    print_provider_summary_from_args,
    run_generation_experiment,
)


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
        default=Path("prompts") / "standard_nl_to_gcjp_prompt.md",
    )
    parser.add_argument(
        "--action-templates",
        type=Path,
        default=Path("configs") / "action_templates.yaml",
        help="Action defaults injected into the GCJP generation prompt.",
    )
    parser.add_argument(
        "--capability-model",
        type=Path,
        default=Path("configs") / "capability_model.yaml",
        help="Fleet capability/resource model injected into the GCJP generation prompt.",
    )
    args = parser.parse_args()

    try:
        print_provider_summary_from_args(args)
        generation_context = _build_generation_config_context(
            action_templates_path=args.action_templates,
            capability_model_path=args.capability_model,
        )
        run_generation_experiment(
            experiment_name="exp_01b_standard_nl_to_gcjp",
            dataset_path=args.dataset,
            prompt_path=args.prompt,
            args=args,
            # 只注入配置上下文：expected_patterns / tags(含 sat-unsat 标签)是
            # 评分真值,绝不能进 prompt,否则等于让模型"看着答案生成"。评分器
            # _evaluate_expected 直接从原始 case 读这些真值,与 prompt 解耦。
            case_payload_fn=lambda _case: generation_context,
            standard_instruction_fn=lambda case: case["standard_instruction"],
        )
    except LLMConfigError as exc:
        return handle_config_error(exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
