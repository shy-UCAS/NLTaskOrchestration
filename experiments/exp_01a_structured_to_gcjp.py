"""
experiments/exp_01a_structured_to_gcjp.py
python -m experiments.exp_01a_structured_to_gcjp --limit 1
# 读取本地 Claude Code 配置
python -m experiments.exp_01a_structured_to_gcjp --local-provider claude --limit 1

Phase 1A：标准化任务描述 JSON → LLM 生成 GCJP 代码实验。

场景：
  从 datasets/phase1_structured_cases.jsonl 读取标准化任务用例，
  经 prompts/gcjp_generation_prompt.md 模板渲染后发送给 LLM（兼容 OpenAI/Anthropic 协议），
  提取回复中的 GCJP 代码，执行安全检查 + 受限执行 + 验证管道，统计各阶段通过率。

预期输出：
  out/phase1_generation/exp_01a_structured_to_gcjp/ 下输出原始回复、
  提取的 GCJP 代码、验证报告、汇总 metrics.json。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from agents.llm_client import LLMConfigError
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
        run_generation_experiment(
            experiment_name="exp_01a_structured_to_gcjp",
            dataset_path=args.dataset,
            prompt_path=args.prompt,
            args=args,
            case_payload_fn=lambda case: case["input_payload"],
        )
    except LLMConfigError as exc:
        return handle_config_error(exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
