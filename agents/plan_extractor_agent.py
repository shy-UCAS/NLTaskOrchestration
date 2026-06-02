"""
Phase 1I 任务计划抽取 Agent。

与 PlannerAgent 不同:本 Agent 让 LLM 仅输出**只含作战语义的 task_plan JSON 骨架**
(task_id/actor/action/target/relations/time_window),不含任何系统参数。
后续由 gcjp.task_plan_loader.build_graph_from_task_plan 从 YAML 确定性填参。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from agents.json_extraction import extract_json_object
from agents.llm_client import LLMClient, LLMResponse


@dataclass
class PlanGeneration:
    sample_id: str
    prompt: str
    raw_response: str
    parsed_plan: dict[str, Any] | None
    extraction: dict[str, Any]
    model: str
    model_source: str
    provider: dict[str, Any]
    usage: dict[str, Any]


class PlanExtractorAgent:
    def __init__(self, client: LLMClient):
        self.client = client

    def extract_plan(
        self,
        *,
        sample_id: str,
        prompt_template: str,
        standard_instruction: str,
        case_payload: dict[str, Any] | None = None,
    ) -> PlanGeneration:
        prompt = render_plan_prompt(
            prompt_template,
            standard_instruction=standard_instruction,
            case_payload=case_payload,
        )
        response = self.client.generate(
            [
                {
                    "role": "system",
                    "content": "你是作战指令到任务计划的结构化抽取器。仅输出 JSON,不要解释。",
                },
                {"role": "user", "content": prompt},
            ]
        )
        extraction = extract_json_object(response.text)
        return PlanGeneration(
            sample_id=sample_id,
            prompt=prompt,
            raw_response=response.text,
            parsed_plan=extraction.data if extraction.ok else None,
            extraction=asdict(extraction),
            model=response.model,
            model_source=response.model_source,
            provider=response.provider,
            usage=response.usage,
        )


def render_plan_prompt(
    prompt_template: str,
    *,
    standard_instruction: str,
    case_payload: dict[str, Any] | None = None,
) -> str:
    import json

    prompt = prompt_template.replace("{{STANDARD_INSTRUCTION}}", standard_instruction or "")
    case_json = json.dumps(case_payload or {}, ensure_ascii=False, indent=2)
    prompt = prompt.replace("{{CASE_JSON}}", case_json)
    return prompt
