"""
作战指令规范化 Agent，将原始模糊 NL 指令分析为结构化规范化结果。

支持多轮澄清：通过 clarification_history 传入已有澄清记录，
agent 本身无状态，每次调用接收完整上下文。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from agents.json_extraction import JsonExtractionResult, extract_json_object
from agents.llm_client import LLMClient, LLMResponse


@dataclass
class NormalizationResult:
    sample_id: str
    prompt: str
    raw_response: str
    parsed_output: dict[str, Any] | None
    extraction: dict[str, Any]
    status: str | None
    standard_instruction: str | None
    resolved_fields: dict[str, Any] | None
    missing_fields: list[str]
    ambiguities: list[dict[str, Any]]
    clarification_round: int
    model: str
    model_source: str
    provider: dict[str, Any]
    usage: dict[str, Any]
    model_reported_status: str | None = None
    status_overridden_by_invariant: bool = False


class InstructionNormalizerAgent:
    def __init__(self, client: LLMClient):
        self.client = client

    def normalize(
        self,
        *,
        sample_id: str,
        prompt_template: str,
        raw_instruction: str,
        clarification_history: list[dict[str, Any]] | None = None,
        clarification_round: int = 0,
    ) -> NormalizationResult:
        prompt = render_normalization_prompt(
            prompt_template,
            raw_instruction=raw_instruction,
            clarification_history=clarification_history,
        )
        response = self.client.generate(
            [
                {
                    "role": "system",
                    "content": "你是一个作战指令规范化助手。仅输出 JSON，不要解释。",
                },
                {"role": "user", "content": prompt},
            ]
        )
        extraction = extract_json_object(response.text)
        return _build_result(
            sample_id, prompt, response, extraction, clarification_round,
        )


def render_normalization_prompt(
    prompt_template: str,
    *,
    raw_instruction: str,
    clarification_history: list[dict[str, Any]] | None = None,
) -> str:
    prompt = prompt_template.replace("{{RAW_INSTRUCTION}}", raw_instruction)
    history_text = _format_clarification_history(clarification_history)
    prompt = prompt.replace("{{CLARIFICATION_HISTORY}}", history_text)
    return prompt


def _format_clarification_history(
    history: list[dict[str, Any]] | None,
) -> str:
    if not history:
        return ""
    lines = ["【澄清记录】"]
    for entry in history:
        round_num = entry.get("round", "?")
        commander_input = entry.get("commander_input", "")
        lines.append(f"第{round_num}轮澄清 — 指挥员补充：\"{commander_input}\"")
    return "\n".join(lines)


def _build_result(
    sample_id: str,
    prompt: str,
    response: LLMResponse,
    extraction: JsonExtractionResult,
    clarification_round: int,
) -> NormalizationResult:
    parsed = extraction.data if extraction.ok else None
    status = None
    standard_instruction = None
    resolved_fields = None
    missing_fields: list[str] = []
    ambiguities: list[dict[str, Any]] = []

    if parsed:
        status = parsed.get("status")
        standard_instruction = parsed.get("standard_instruction")
        resolved_fields = parsed.get("resolved_fields")
        missing_fields = parsed.get("missing_fields") or []
        ambiguities = parsed.get("ambiguities") or []

    model_reported_status = status
    status_overridden_by_invariant = False
    if status == "complete" and (missing_fields or ambiguities):
        status = "incomplete"
        standard_instruction = None
        status_overridden_by_invariant = True

    return NormalizationResult(
        sample_id=sample_id,
        prompt=prompt,
        raw_response=response.text,
        parsed_output=parsed,
        extraction=asdict(extraction),
        status=status,
        standard_instruction=standard_instruction,
        resolved_fields=resolved_fields,
        missing_fields=missing_fields,
        ambiguities=ambiguities,
        clarification_round=clarification_round,
        model=response.model,
        model_source=response.model_source,
        provider=response.provider,
        usage=response.usage,
        model_reported_status=model_reported_status,
        status_overridden_by_invariant=status_overridden_by_invariant,
    )
