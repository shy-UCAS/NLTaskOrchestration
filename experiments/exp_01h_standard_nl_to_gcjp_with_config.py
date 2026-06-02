"""
experiments/exp_01h_standard_nl_to_gcjp_with_config.py
用法：
  # 默认从 configs/llm_providers.local.yaml 读取 profile
  python -m experiments.exp_01h_standard_nl_to_gcjp_with_config --provider-profile <profile_name> --limit 1 --workers 8

  # 覆盖数据集 / prompt / 配置表
  python -m experiments.exp_01h_standard_nl_to_gcjp_with_config --provider-profile <profile_name> \
      --dataset datasets/phase1_standard_nl_cases.jsonl \
      --prompt prompts/standard_nl_to_gcjp_prompt.md \
      --action-templates configs/action_templates.yaml \
      --capability-model configs/capability_model.yaml

Phase 1H：标准化自然语言指令 → LLM 生成 GCJP 代码实验（注入真实配置表）。

场景：
  与 1B 完全相同的输入（标准化自然语言）与评测口径（expected_patterns / 验证管道），
  唯一区别是：在 case_payload 中额外注入 action_templates.yaml 与 capability_model.yaml
  （复用 1G 的 _build_generation_config_context），让 LLM 从真实能力模型查表填充
  duration_lb / energy_cost / ammo_cost / required_capability / 资源上限等系统参数，
  而非自行编造。

设计目的：
  1B 不注入配置表，LLM 自定义全部数值，Z3 可行性检查近乎自指（自定义成本 + 自定义天花板）。
  1H 注入配置表后，生成代码的数值受真实地面真值约束，Z3 验证才是真实物理可行性检查。
  1H vs 1B 的对照可隔离“有无真实约束注入”这一单一变量。

预期输出：
  out/phase1_generation/exp_01h_standard_nl_to_gcjp_with_config/ 下输出原始回复、
  提取的 GCJP 代码、验证报告、汇总 metrics.json。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from agents.llm_client import LLMConfigError
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
            experiment_name="exp_01h_standard_nl_to_gcjp_with_config",
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
