"""
指令规范化交互式澄清闭环控制器。

管理多轮 LLM 分析 + 指挥员澄清交互，直到指令被判定为 complete
或达到最大轮次。支持通过 input_fn 注入输入源，用于自动化测试。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agents.instruction_normalizer_agent import (
    InstructionNormalizerAgent,
    NormalizationResult,
)


@dataclass
class ClarificationLoopResult:
    sample_id: str
    final_status: str
    final_result: NormalizationResult | None
    total_rounds: int
    clarification_history: list[dict[str, Any]]
    all_results: list[NormalizationResult] = field(default_factory=list)


class ClarificationLoop:
    """
    闭环控制器：调 agent → 检查 status → incomplete 时请求澄清 → 循环。

    Parameters
    ----------
    agent : InstructionNormalizerAgent
    max_rounds : int
        最大总轮次（含首次分析）。
    input_fn : Callable | None
        自定义输入函数。签名: (ambiguities, missing_fields) -> str | None。
        返回 None 表示指挥员中止。
        默认为 None，使用终端 input() 交互。
    """

    def __init__(
        self,
        agent: InstructionNormalizerAgent,
        *,
        max_rounds: int = 5,
        input_fn: Callable[[list[dict], list[str]], str | None] | None = None,
    ):
        self.agent = agent
        self.max_rounds = max_rounds
        self._input_fn = input_fn or self._terminal_input

    def run(
        self,
        *,
        sample_id: str,
        prompt_template: str,
        raw_instruction: str,
    ) -> ClarificationLoopResult:
        history: list[dict[str, Any]] = []
        all_results: list[NormalizationResult] = []

        for round_idx in range(self.max_rounds):
            result = self.agent.normalize(
                sample_id=sample_id,
                prompt_template=prompt_template,
                raw_instruction=raw_instruction,
                clarification_history=history if history else None,
                clarification_round=round_idx,
            )
            all_results.append(result)

            if result.status == "complete":
                return ClarificationLoopResult(
                    sample_id=sample_id,
                    final_status="complete",
                    final_result=result,
                    total_rounds=round_idx + 1,
                    clarification_history=list(history),
                    all_results=all_results,
                )

            commander_input = self._input_fn(
                result.ambiguities, result.missing_fields,
            )

            if commander_input is None:
                return ClarificationLoopResult(
                    sample_id=sample_id,
                    final_status="user_abort",
                    final_result=result,
                    total_rounds=round_idx + 1,
                    clarification_history=list(history),
                    all_results=all_results,
                )

            history.append({
                "round": round_idx + 1,
                "ambiguities_shown": result.ambiguities,
                "missing_fields_shown": result.missing_fields,
                "commander_input": commander_input,
            })

        return ClarificationLoopResult(
            sample_id=sample_id,
            final_status="max_rounds_exceeded",
            final_result=all_results[-1] if all_results else None,
            total_rounds=self.max_rounds,
            clarification_history=list(history),
            all_results=all_results,
        )

    @staticmethod
    def _terminal_input(
        ambiguities: list[dict[str, Any]],
        missing_fields: list[str],
    ) -> str | None:
        print("\n┌─ 指令规范化 · 澄清请求 " + "─" * 40)
        print("│")
        print("│  ⚠ 检测到以下歧义/缺失：")
        print("│")
        for field_name in missing_fields:
            print(f"│  [缺失] {field_name}")
        for amb in ambiguities:
            span = amb.get("span", "?")
            reason = amb.get("reason", "")
            field_name = amb.get("field", "")
            print(f"│  [歧义] \"{span}\" — {reason} (field: {field_name})")
        print("│")
        print("│  请输入补充说明（输入 q 中止）：")
        print("└" + "─" * 55)
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if user_input.lower() == "q":
            return None
        return user_input or None


def scripted_input_fn(
    answers: list[str],
) -> Callable[[list[dict], list[str]], str | None]:
    """
    创建一个从预设答案列表中按顺序返回的 input_fn，用于自动化测试。
    答案用完后返回 None（模拟指挥员中止）。
    """
    iterator = iter(answers)

    def _fn(ambiguities: list[dict], missing_fields: list[str]) -> str | None:
        return next(iterator, None)

    return _fn
