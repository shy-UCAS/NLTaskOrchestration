"""
experiments/exp_01b_standard_nl_to_gcjp.py
python -m experiments.exp_01b_standard_nl_to_gcjp --limit 1

Phase 1B：标准化自然语言指令 → LLM 生成 GCJP 代码实验。

场景：
  从 datasets/phase1_standard_nl_cases.jsonl 读取自然语言任务描述，
  经 prompts/standard_nl_to_gcjp_prompt.md 模板渲染后发送给 LLM，
  提取回复中的 GCJP 代码，执行安全检查 + 受限执行 + 验证管道，统计各阶段通过率。

说明：
  与 1A 的区别在于输入是自然语言（而非已结构化的 JSON），更接近端到端 Code-as-Plan 场景。

预期输出：
  out/phase1_generation/exp_01b_standard_nl_to_gcjp/ 下输出原始回复、
  提取的 GCJP 代码、验证报告、汇总 metrics.json。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from agents.llm_client import LLMConfigError
from experiments.phase1_common import (
    add_common_args,
    handle_config_error,
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
    args = parser.parse_args()

    try:
        run_generation_experiment(
            experiment_name="exp_01b_standard_nl_to_gcjp",
            dataset_path=args.dataset,
            prompt_path=args.prompt,
            args=args,
            case_payload_fn=lambda case: {
                "expected_patterns": case.get("expected_patterns", {}),
                "tags": case.get("tags", []),
            },
            standard_instruction_fn=lambda case: case["standard_instruction"],
        )
    except LLMConfigError as exc:
        return handle_config_error(exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

