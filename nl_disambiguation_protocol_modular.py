import os
import re
import json
import difflib
from typing import List, Optional, Literal, Dict, Any, Tuple, Type, TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from langchain_core.prompts import ChatPromptTemplate, FewShotChatMessagePromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage

load_dotenv()


# ============================================================
# 1) Stable schema ontology (keep semantic rules here)
# ============================================================
ONTOLOGY: Dict[str, Any] = {
    "clusters": [
        {"canonical": "cluster_1", "aliases": ["1号集群", "集群1", "第一集群"]},
        {"canonical": "cluster_2", "aliases": ["2号集群", "集群2", "第二集群"]},
        {"canonical": "cluster_3", "aliases": ["3号集群", "集群3", "第三集群"]},
        {"canonical": "cluster_4", "aliases": ["4号集群", "集群4", "第四集群"]},
    ],
    "targets": [
        {"canonical": "hq_2", "aliases": ["hq_2", "hq2", "2号高地", "二号高地"]},
        {"canonical": "hq_mark1", "aliases": ["hq_mark1", "mark1", "1号标记点", "一号标记点"]},
        {"canonical": "hq_mark2", "aliases": ["hq_mark2", "mark2", "2号标记点", "二号标记点"]},
        {"canonical": "hq_mark3", "aliases": ["hq_mark3", "mark3", "3号标记点", "三号标记点"]},
        {"canonical": "hq_mark4", "aliases": ["hq_mark4", "mark4", "4号标记点", "四号标记点"]},
        {"canonical": "hq_mark5", "aliases": ["hq_mark5", "mark5", "5号标记点", "五号标记点"]},
        {"canonical": "hq_mark6", "aliases": ["hq_mark6", "mark6", "6号标记点", "六号标记点"]},
        {"canonical": "hq_mark7", "aliases": ["hq_mark7", "mark7", "7号标记点", "七号标记点"]},
        {"canonical": "hq_mark8", "aliases": ["hq_mark8", "mark8", "8号标记点", "八号标记点"]},
        {"canonical": "hq_mark9", "aliases": ["hq_mark9", "mark9", "9号标记点", "九号标记点"]},
        {"canonical": "hq_mark10", "aliases": ["hq_mark10", "mark10", "10号标记点", "十号标记点"]},
    ],
    "actions": [
        {
            "canonical": "move",
            "aliases": ["前往", "飞往", "移动到", "机动到", "抵达", "到达"],
            "boundary": {
                "trigger": "收到移动指令后开始移动",
                "completion": "进入目标区域并稳定到位",
            },
        },
        {
            "canonical": "meet",
            "aliases": ["会合", "汇合", "集结", "汇聚"],
            "boundary": {
                "trigger": "所有参与集群进入会合阶段",
                "completion": "所有参与集群到达同一会合区域",
            },
        },
        {
            "canonical": "attack",
            "aliases": ["攻击", "进攻", "打击", "突击"],
            "boundary": {
                "trigger": "满足攻击前置条件后开始实施打击",
                "completion": "完成一次有效攻击任务",
            },
        },
        {
            "canonical": "breakthrough",
            "aliases": ["突破", "共同突破", "联合突破", "强行突破"],
            "boundary": {
                "trigger": "满足突破前置条件后开始突破",
                "completion": "完成对指定目标点或区域的突破任务",
            },
        },
    ],
}


# ============================================================
# 2) Latest agreed output protocol
# ============================================================
class SurfaceEdit(BaseModel):
    source: str
    normalized: str
    type: Literal["ocr_repair", "punctuation", "whitespace", "list_join", "normalization"]


class UncertainSpan(BaseModel):
    span_text: str
    reason: str


class SurfaceNormalizationResult(BaseModel):
    clean_text: str
    edit_log: List[SurfaceEdit] = Field(default_factory=list)
    uncertain_spans: List[UncertainSpan] = Field(default_factory=list)


class Mention(BaseModel):
    span_text: str = Field(description="原文片段")
    entity_type: Literal["cluster", "target", "action", "reference", "collective_phrase", "unknown"]
    canonical: Optional[str] = Field(default=None, description="规范名；必须来自 ontology；未知时为 null")
    candidates: List[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguous: bool
    reason: str
    role: Optional[str] = Field(default=None, description="如 cluster_local_target / global_final_target / meeting_point")


class AmbiguityItem(BaseModel):
    type: Literal[
        "ocr_noise",
        "target_name",
        "cluster_name",
        "action_name",
        "action_boundary",
        "coreference",
        "participant_omission",
        "role_ambiguity",
        "missing_argument",
        "other",
    ]
    span_text: str
    candidates: List[str] = Field(default_factory=list)
    reason: str
    need_human: bool


class ReferenceItem(BaseModel):
    span_text: str
    refer_to: str
    resolved_actions: List[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class SemanticParseResult(BaseModel):
    mentions: List[Mention] = Field(default_factory=list)
    ambiguities: List[AmbiguityItem] = Field(default_factory=list)
    references: List[ReferenceItem] = Field(default_factory=list)
    normalized_sentences: List[str] = Field(default_factory=list)
    needs_human_review: bool


class FormalStep(BaseModel):
    step_id: str
    cluster: str
    order: int
    stage: Literal["local", "joint", "global_final"]
    action: Literal["move", "meet", "attack", "breakthrough"]
    target: Optional[str] = None
    participants: List[str] = Field(default_factory=list)
    sync_id: Optional[str] = None
    requires: List[str] = Field(default_factory=list)
    inferred: bool = False
    source_fragment: str = ""


class AtomicEventMapping(BaseModel):
    phi: Optional[str] = None
    event_name: str
    actors: List[str] = Field(default_factory=list)
    action: Literal["move", "meet", "attack", "breakthrough"]
    target: Optional[str] = None
    stage: Literal["local", "joint", "global_final"]
    source_step_ids: List[str] = Field(default_factory=list)
    depends_on: List[str] = Field(default_factory=list)
    inferred: bool = False
    usable_in_ltl: bool = True
    excluded_reason: Optional[str] = None


class FormalRepresentation(BaseModel):
    formal_steps: List[FormalStep] = Field(default_factory=list)
    atomic_event_mappings: List[AtomicEventMapping] = Field(default_factory=list)


class PipelineOutput(BaseModel):
    surface_normalization: SurfaceNormalizationResult
    semantic_parse: SemanticParseResult
    formal_representation: FormalRepresentation
    warnings: List[str] = Field(default_factory=list)


# Internal helper model (not kept in final protocol as top-level field)
class SyncConstraint(BaseModel):
    sync_id: str
    sync_type: Literal["meet", "joint_breakthrough"]
    target: Optional[str] = None
    participants: List[str] = Field(default_factory=list)
    preconditions: List[str] = Field(default_factory=list)
    source: str = ""


# ============================================================
# 3) Rule helpers
# ============================================================
def dumps_pretty(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def rule_preprocess_text(raw_input: Any) -> SurfaceNormalizationResult:
    if isinstance(raw_input, list):
        text = "".join(str(x) for x in raw_input)
        edit_log = [SurfaceEdit(source="list_input", normalized="joined_text", type="list_join")]
    else:
        text = str(raw_input)
        edit_log = []

    replacements = {
        "形+成": "形成",
        "会合形 成": "会合形成",
        "、随后": "，随后",
        "、然后": "，然后",
        "；": "。",
    }
    for src, dst in replacements.items():
        if src in text:
            text = text.replace(src, dst)
            edit_log.append(SurfaceEdit(source=src, normalized=dst, type="ocr_repair" if "+" in src or "形" in src else "punctuation"))

    if re.search(r"\s+", text):
        text = re.sub(r"\s+", "", text)
        edit_log.append(SurfaceEdit(source="whitespace", normalized="removed", type="whitespace"))

    deduped = re.sub(r"([，。])\1+", r"\1", text)
    if deduped != text:
        text = deduped
        edit_log.append(SurfaceEdit(source="duplicated_punctuation", normalized="deduped", type="punctuation"))

    uncertain_spans: List[UncertainSpan] = []
    patterns = [
        r"飞往(?:hq_mark\d+|hq_\d+)(?:、(?:hq_mark\d+|hq_\d+))+完成突破",
        r"飞往(?:hq_mark\d+|hq_\d+)(?:、(?:hq_mark\d+|hq_\d+))+执行突破任务后",
        r"同样的后续行动",
        r"后续共同行动",
        r"其他编队",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            uncertain_spans.append(UncertainSpan(span_text=m.group(0), reason="规则预处理发现该片段可能存在作用域或指代歧义"))

    return SurfaceNormalizationResult(clean_text=text, edit_log=edit_log, uncertain_spans=uncertain_spans)


def build_alias_index(items: List[Dict[str, Any]]) -> Dict[str, str]:
    alias_index: Dict[str, str] = {}
    for item in items:
        canonical = item["canonical"]
        alias_index[canonical.lower()] = canonical
        for alias in item.get("aliases", []):
            alias_index[alias.lower()] = canonical
    return alias_index


def extract_terms(text: str) -> List[str]:
    patterns = [
        r"hq_mark\d+",
        r"hq_\d+",
        r"\d+号集群",
        r"[一二三四五六七八九十]+号集群",
        r"\d+号(?:高地|标记点)",
        r"[一二三四五六七八九十]+号(?:高地|标记点)",
        r"(?:会合|汇合|汇聚|集结|突破|攻击|进攻|飞往|前往|共同突破|后续共同行动|同样的后续行动|所有人|其他编队|所有集群)",
    ]
    hits: List[str] = []
    for pattern in patterns:
        hits.extend(re.findall(pattern, text))

    seen = set()
    ordered: List[str] = []
    for item in hits:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def fuzzy_candidates(term: str, alias_index: Dict[str, str], cutoff: float = 0.65, topn: int = 3) -> List[Dict[str, Any]]:
    keys = list(alias_index.keys())
    matches = difflib.get_close_matches(term.lower(), keys, n=topn * 4, cutoff=cutoff)
    scored = []
    for match in matches:
        score = difflib.SequenceMatcher(None, term.lower(), match).ratio()
        scored.append((alias_index[match], round(score, 4)))

    best: Dict[str, float] = {}
    for canonical, score in scored:
        if canonical not in best or score > best[canonical]:
            best[canonical] = score

    result = [{"canonical": canonical, "score": score} for canonical, score in best.items()]
    result.sort(key=lambda x: x["score"], reverse=True)
    return result[:topn]


def build_candidate_hints(text: str, ontology: Dict[str, Any]) -> List[Dict[str, Any]]:
    entity_maps = {
        "cluster": build_alias_index(ontology["clusters"]),
        "target": build_alias_index(ontology["targets"]),
        "action": build_alias_index(ontology["actions"]),
    }

    hints = []
    for term in extract_terms(text):
        for entity_type, alias_index in entity_maps.items():
            cands = fuzzy_candidates(term, alias_index)
            if cands:
                hints.append({"span_text": term, "entity_type": entity_type, "candidates": cands})
    return hints


# ============================================================
# 4) Generic LLM provider factory (each module can choose provider)
# ============================================================
T = TypeVar("T", bound=BaseModel)


def _get_stage_model_name(stage: str, provider: str) -> str:
    stage = stage.upper()
    provider = provider.lower()
    if provider.startswith("gemini"):
        return os.getenv(f"{stage}_GEMINI_MODEL", os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
    if provider == "qwen":
        return os.getenv(f"{stage}_QWEN_MODEL", os.getenv("QWEN_MODEL", "qwen-max"))
    raise ValueError(f"Unsupported provider for stage model lookup: {provider}")


def get_structured_llm(provider: str, schema_cls: Type[T], stage: str):
    provider = provider.lower()

    if provider == "gemini":
        llm = ChatGoogleGenerativeAI(
            model=_get_stage_model_name(stage, provider),
            google_api_key=os.getenv("GEMINI_API_KEY"),
            temperature=0.1,
        )
        return llm.with_structured_output(schema_cls, method="json_schema")

    elif provider == "gemini_proxy_google":
        llm = ChatGoogleGenerativeAI(
            model=_get_stage_model_name(stage, provider),
            google_api_key=os.getenv("GEMINI_API_KEY"),
            temperature=0.1,
            transport="rest",
            base_url=os.getenv("GEMINI_PROXY_BASE_URL", "https://api2.qiandao.mom"),
        )
        return llm.with_structured_output(schema_cls, method="json_schema")

    elif provider == "gemini_proxy_openai":
        llm = ChatOpenAI(
            model=_get_stage_model_name(stage, provider),
            api_key=os.getenv("GEMINI_API_KEY"),
            base_url=os.getenv("GEMINI_PROXY_BASE_URL", "https://api2.qiandao.mom/v1"),
            temperature=0.1,
            stream_usage=False,
        )
        return llm.with_structured_output(schema_cls, method="function_calling")

    elif provider == "qwen":
        llm = ChatOpenAI(
            model=_get_stage_model_name(stage, provider),
            api_key=os.getenv("QWEN_API_KEY"),
            base_url=os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            temperature=0.1,
            stream_usage=False,
        )
        return llm.with_structured_output(schema_cls, method="function_calling")

    raise ValueError(f"Unsupported provider: {provider}")


def coerce_to_model(result: Any, model_cls: Type[T]) -> T:
    if isinstance(result, model_cls):
        return result

    if isinstance(result, dict):
        return model_cls.model_validate(result)

    if isinstance(result, AIMessage):
        content = result.content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if "text" in block:
                        parts.append(block["text"])
                    elif block.get("type") == "text" and "text" in block:
                        parts.append(block["text"])
                else:
                    parts.append(str(block))
            content = "".join(parts)

        if not isinstance(content, str):
            content = str(content)
        content = content.strip()

        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)

        try:
            return model_cls.model_validate_json(content)
        except Exception:
            return model_cls.model_validate(json.loads(content))

    raise TypeError(f"无法将结果转换为 {model_cls.__name__}，实际类型: {type(result)}")


# ============================================================
# 5) Stage 1: surface normalization (small LLM module)
# ============================================================
SURFACE_FEW_SHOTS = [
    {
        "input_text": "1号集群与2号集群会合形+成大部队，然后飞往hq_mark4完成突破。",
        "output_json": dumps_pretty(
            {
                "clean_text": "1号集群与2号集群会合形成大部队，然后飞往hq_mark4完成突破。",
                "edit_log": [
                    {"source": "形+成", "normalized": "形成", "type": "ocr_repair"}
                ],
                "uncertain_spans": []
            }
        ),
    },
    {
        "input_text": "3号集群独立依次飞往hq_mark9、hq_mark8、hq_mark2、hq_mark4完成突破。",
        "output_json": dumps_pretty(
            {
                "clean_text": "3号集群独立依次飞往hq_mark9、hq_mark8、hq_mark2、hq_mark4完成突破。",
                "edit_log": [],
                "uncertain_spans": [
                    {
                        "span_text": "飞往hq_mark9、hq_mark8、hq_mark2、hq_mark4完成突破",
                        "reason": "多个目标点与突破动作的作用域可能存在歧义，需保留给后续语义解析阶段处理"
                    }
                ]
            }
        ),
    },
]

SURFACE_EXAMPLE_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("human", "示例输入：\n{input_text}"),
        ("ai", "示例输出：\n{output_json}"),
    ]
)
SURFACE_FEW_SHOT_PROMPT = FewShotChatMessagePromptTemplate(
    example_prompt=SURFACE_EXAMPLE_PROMPT,
    examples=SURFACE_FEW_SHOTS,
)

SURFACE_SYSTEM_MESSAGE = """
你是“多集群任务文本表层规范化器”。

你的职责只有：
1. 合并输入文本并修复明显 OCR/脏文本问题；
2. 统一标点与句子边界；
3. 保持原始任务语义不变；
4. 标注可能存在作用域或表达不确定的片段。

严格要求：
- 不得新增目标点、动作、参与方、前置条件；
- 不得替用户补全“后续行动”的具体内容；
- 不得擅自选择原文中存在歧义的某一种解释；
- 只输出 schema 对应的 JSON。
""".strip()


def build_surface_prompt_messages(raw_text: str):
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SURFACE_SYSTEM_MESSAGE),
            SURFACE_FEW_SHOT_PROMPT,
            ("human", "【待规范化文本】\n{input_text}"),
        ]
    )
    return prompt.format_messages(input_text=raw_text)


def run_surface_normalization(raw_input: Any, provider: Optional[str] = None) -> SurfaceNormalizationResult:
    # 文本清洗的部分可以不要单独拿出来调用一次llm api, 在其他环节的system prompt的时候添加一段描述让他去做文本清洗
    provider = (provider or os.getenv("SURFACE_PROVIDER", "rule")).lower()
    raw_text = "".join(str(x) for x in raw_input) if isinstance(raw_input, list) else str(raw_input)

    if provider == "rule":
        return rule_preprocess_text(raw_input)

    llm = get_structured_llm(provider, SurfaceNormalizationResult, stage="SURFACE")
    messages = build_surface_prompt_messages(raw_text)
    raw_result = llm.invoke(messages)
    result = coerce_to_model(raw_result, SurfaceNormalizationResult)

    if not result.clean_text.strip():
        # hard fallback
        return rule_preprocess_text(raw_input)
    return result


# ============================================================
# 6) Stage 2: semantic parse (main LLM module)
# ============================================================
SEMANTIC_FEW_SHOTS = [
    {
        "input_text": "1号集群先进攻hq_mark6，随后飞往hq_mark7与2号集群会合后共同突破。",
        "output_json": dumps_pretty(
            {
                "mentions": [
                    {"span_text": "1号集群", "entity_type": "cluster", "canonical": "cluster_1", "candidates": ["cluster_1"], "confidence": 0.99, "ambiguous": False, "reason": "精确匹配", "role": None},
                    {"span_text": "2号集群", "entity_type": "cluster", "canonical": "cluster_2", "candidates": ["cluster_2"], "confidence": 0.99, "ambiguous": False, "reason": "精确匹配", "role": None},
                    {"span_text": "hq_mark6", "entity_type": "target", "canonical": "hq_mark6", "candidates": ["hq_mark6"], "confidence": 0.99, "ambiguous": False, "reason": "精确匹配", "role": "cluster_local_target"},
                    {"span_text": "hq_mark7", "entity_type": "target", "canonical": "hq_mark7", "candidates": ["hq_mark7"], "confidence": 0.99, "ambiguous": False, "reason": "精确匹配", "role": "meeting_point"},
                    {"span_text": "共同突破", "entity_type": "action", "canonical": "breakthrough", "candidates": ["breakthrough"], "confidence": 0.58, "ambiguous": True, "reason": "突破目标未明确", "role": None}
                ],
                "ambiguities": [
                    {"type": "missing_argument", "span_text": "共同突破", "candidates": ["breakthrough"], "reason": "原文未给出突破目标点，不能补全", "need_human": True}
                ],
                "references": [],
                "normalized_sentences": [
                    "cluster_1: attack(hq_mark6) -> move(hq_mark7) -> meet(cluster_2, hq_mark7) -> breakthrough(UNKNOWN_TARGET)",
                    "cluster_2: meet(cluster_1, hq_mark7) -> breakthrough(UNKNOWN_TARGET)"
                ],
                "needs_human_review": True
            }
        ),
    },
    {
        "input_text": "3号集群飞往hq_mark2，再前往hq_2与其他集群会合。",
        "output_json": dumps_pretty(
            {
                "mentions": [
                    {"span_text": "3号集群", "entity_type": "cluster", "canonical": "cluster_3", "candidates": ["cluster_3"], "confidence": 0.99, "ambiguous": False, "reason": "精确匹配", "role": None},
                    {"span_text": "hq_mark2", "entity_type": "target", "canonical": "hq_mark2", "candidates": ["hq_mark2"], "confidence": 0.99, "ambiguous": False, "reason": "精确匹配", "role": "cluster_local_target"},
                    {"span_text": "hq_2", "entity_type": "target", "canonical": "hq_2", "candidates": ["hq_2", "hq_mark2"], "confidence": 0.88, "ambiguous": True, "reason": "与 hq_mark2 命名相近，需提醒", "role": "meeting_point"},
                    {"span_text": "其他集群", "entity_type": "reference", "canonical": None, "candidates": [], "confidence": 0.52, "ambiguous": True, "reason": "未明确列出具体参与集群", "role": None}
                ],
                "ambiguities": [
                    {"type": "target_name", "span_text": "hq_2", "candidates": ["hq_2", "hq_mark2"], "reason": "名称相似，易混淆", "need_human": False},
                    {"type": "coreference", "span_text": "其他集群", "candidates": [], "reason": "未明确列出具体集群编号", "need_human": True}
                ],
                "references": [
                    {"span_text": "其他集群", "refer_to": "除 cluster_3 外的其他参与方，但原文未明确", "resolved_actions": [], "confidence": 0.45}
                ],
                "normalized_sentences": [
                    "cluster_3: move(hq_mark2) -> move(hq_2) -> meet(OTHER_CLUSTERS, hq_2)"
                ],
                "needs_human_review": True
            }
        ),
    },
]

SEMANTIC_EXAMPLE_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("human", "示例输入：\n{input_text}"),
        ("ai", "示例输出：\n{output_json}"),
    ]
)
SEMANTIC_FEW_SHOT_PROMPT = FewShotChatMessagePromptTemplate(
    example_prompt=SEMANTIC_EXAMPLE_PROMPT,
    examples=SEMANTIC_FEW_SHOTS,
)

SEMANTIC_SYSTEM_MESSAGE = """
你是“多集群任务文本规范化与歧义显式化器”。

你的目标不是补全任务，而是把自然语言任务文本安全地转成结构化中间表示。

必须遵守：
1. 识别 cluster / target / action / reference / collective_phrase；
2. canonical 只能来自给定 ontology，禁止发明新 canonical；
3. 原文未给出的信息不得脑补；必须标为歧义、缺失参数或需人工复核；
4. 对“同样的后续行动”“后续共同行动”“其他编队”“所有人”“所有集群”等表达，必须在 references 中显式处理；
5. 对“会合后共同突破”这类短语，要拆成 meet 和 breakthrough 两层语义；若突破目标未给出，必须标 need_human=true；
6. 若同一目标点在不同阶段扮演不同角色，应在 mentions.role 中标出；
7. normalized_sentences 是给后续形式化映射用的中间文本，应尽量用 canonical 名称与动作链形式表达；
8. 对“依次飞往A、B、C完成突破”这类表达，除非原文明确写出每个点都突破，否则不要给每个点都自动补 breakthrough；
9. 只输出符合 schema 的 JSON。
""".strip()


def build_semantic_prompt_messages(clean_text: str, ontology: Dict[str, Any], candidate_hints: List[Dict[str, Any]], uncertain_spans: List[UncertainSpan]):
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SEMANTIC_SYSTEM_MESSAGE),
            (
                "system",
                "【ontology】\n{ontology_json}\n\n【候选提示】\n{candidate_hints_json}\n\n【上一阶段标注的不确定片段】\n{uncertain_spans_json}",
            ),
            SEMANTIC_FEW_SHOT_PROMPT,
            ("human", "【待处理文本】\n{input_text}"),
        ]
    )
    return prompt.format_messages(
        ontology_json=dumps_pretty(ontology),
        candidate_hints_json=dumps_pretty(candidate_hints),
        uncertain_spans_json=dumps_pretty([x.model_dump() for x in uncertain_spans]),
        input_text=clean_text,
    )


def validate_semantic_result(result: SemanticParseResult, ontology: Dict[str, Any]) -> SemanticParseResult:
    needs_human = bool(result.needs_human_review)
    valid = {
        "cluster": {x["canonical"] for x in ontology["clusters"]},
        "target": {x["canonical"] for x in ontology["targets"]},
        "action": {x["canonical"] for x in ontology["actions"]},
    }

    for m in result.mentions:
        if m.entity_type in valid and m.canonical is not None and m.canonical not in valid[m.entity_type]:
            m.canonical = None
            m.ambiguous = True
            m.reason = "canonical 不在 ontology 中，已回退为人工复核"
            needs_human = True
        if m.confidence < 0.75:
            m.ambiguous = True
            needs_human = True

    for a in result.ambiguities:
        if a.need_human:
            needs_human = True

    result.needs_human_review = needs_human
    return result


def run_semantic_parse(surface_result: SurfaceNormalizationResult, provider: Optional[str] = None) -> SemanticParseResult:
    provider = (provider or os.getenv("SEMANTIC_PROVIDER", os.getenv("PROVIDER", "gemini"))).lower()
    candidate_hints = build_candidate_hints(surface_result.clean_text, ONTOLOGY)
    messages = build_semantic_prompt_messages(surface_result.clean_text, ONTOLOGY, candidate_hints, surface_result.uncertain_spans)
    llm = get_structured_llm(provider, SemanticParseResult, stage="SEMANTIC")
    raw_result = llm.invoke(messages)
    result = coerce_to_model(raw_result, SemanticParseResult)
    return validate_semantic_result(result, ONTOLOGY)


# ============================================================
# 7) Stage 3: deterministic formal mapper
# ============================================================
ACTION_PATTERN = re.compile(r"^(?P<action>[A-Za-z_]+)\((?P<args>.*)\)$")


def split_top_level(text: str, sep: str = ",") -> List[str]:
    items: List[str] = []
    current: List[str] = []
    depth = 0
    for ch in text:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == sep and depth == 0:
            part = "".join(current).strip()
            if part:
                items.append(part)
            current = []
        else:
            current.append(ch)
    part = "".join(current).strip()
    if part:
        items.append(part)
    return items


def parse_participants(expr: str) -> List[str]:
    expr = expr.strip()
    if expr in {"OTHER_CLUSTERS", "UNKNOWN_CLUSTERS", "ALL_CLUSTERS"}:
        return [expr]
    if expr.startswith("[") and expr.endswith("]"):
        expr = expr[1:-1].strip()
        if not expr:
            return []
        return [x.strip() for x in split_top_level(expr)]
    if expr.startswith("cluster_"):
        return [expr]
    return [expr]


def parse_normalized_sentences(normalized_sentences: List[str]) -> Tuple[List[FormalStep], List[str]]:
    steps: List[FormalStep] = []
    warnings: List[str] = []

    for sentence in normalized_sentences:
        if ":" not in sentence:
            warnings.append(f"无法解析 normalized sentence（缺少冒号）: {sentence}")
            continue
        cluster, chain = sentence.split(":", 1)
        cluster = cluster.strip()
        fragments = [frag.strip() for frag in chain.split("->") if frag.strip()]
        prev_step_id: Optional[str] = None
        for idx, frag in enumerate(fragments, start=1):
            match = ACTION_PATTERN.match(frag)
            if not match:
                warnings.append(f"无法解析动作片段: {frag}")
                continue

            action = match.group("action")
            args = match.group("args").strip()
            target: Optional[str] = None
            participants: List[str] = []

            if action == "meet":
                parts = split_top_level(args)
                if len(parts) >= 2:
                    participants = parse_participants(parts[0])
                    target = parts[-1].strip()
                elif len(parts) == 1:
                    target = parts[0].strip()
            else:
                target = args if args else None

            step_id = f"{cluster}_s{idx:02d}"
            requires = [prev_step_id] if prev_step_id else []
            steps.append(
                FormalStep(
                    step_id=step_id,
                    cluster=cluster,
                    order=idx,
                    stage="local",
                    action=action,
                    target=target,
                    participants=participants,
                    requires=requires,
                    inferred=False,
                    source_fragment=frag,
                )
            )
            prev_step_id = step_id

    return steps, warnings 


def extract_cluster_clauses(clean_text: str) -> Dict[str, str]:
    mapping = {
        "1号集群": "cluster_1",
        "2号集群": "cluster_2",
        "3号集群": "cluster_3",
        "4号集群": "cluster_4",
        "第一集群": "cluster_1",
        "第二集群": "cluster_2",
        "第三集群": "cluster_3",
        "第四集群": "cluster_4",
    }
    clauses: Dict[str, str] = {}
    pattern = re.compile(r"((?:[1-4]号集群|第[一二三四]集群).*?)(?=(?:[1-4]号集群|第[一二三四]集群)|$)")
    for block in pattern.findall(clean_text):
        for k, v in mapping.items():
            if block.startswith(k):
                clauses[v] = block
                break
    return clauses


def extract_targets(seq_text: str) -> List[str]:
    return re.findall(r"(hq_mark\d+|hq_\d+)", seq_text)


def infer_explicit_breakthrough_targets(clause: str) -> List[str]:
    explicit = re.findall(r"(?:突破|完成最后的突破|完成突破|执行突破任务(?:后)?)(hq_mark\d+|hq_\d+)", clause)
    explicit += re.findall(r"对(hq_mark\d+|hq_\d+)的突破", clause)
    for m in re.finditer(r"(?:依次)?飞往((?:hq_mark\d+|hq_\d+)(?:、(?:hq_mark\d+|hq_\d+))+)(?:完成突破|执行突破任务后)", clause):
        seq = extract_targets(m.group(1))
        if seq:
            explicit.append(seq[-1])
    return list(dict.fromkeys(explicit))


def infer_explicit_attack_targets(clause: str) -> List[str]:
    explicit = re.findall(r"(?:攻击|进攻|打击|突击)(hq_mark\d+|hq_\d+)", clause)
    return list(dict.fromkeys(explicit))


def detect_global_final_target(clean_text: str) -> Optional[str]:
    patterns = [
        r"所有人飞往(hq_mark\d+|hq_\d+)完成最后的突破",
        r"所有集群一起飞往(hq_mark\d+|hq_\d+)完成突破",
        r"所有人飞往(hq_mark\d+|hq_\d+)完成突破",
    ]
    for pat in patterns:
        m = re.search(pat, clean_text)
        if m:
            return m.group(1)
    return None


def detect_global_meet_target(clean_text: str) -> Optional[str]:
    patterns = [
        r"汇聚到(hq_mark\d+|hq_\d+)后，所有人",
        r"汇聚到(hq_mark\d+|hq_\d+)后，所有集群",
        r"在(hq_mark\d+|hq_\d+)会合后，所有人",
        r"在(hq_mark\d+|hq_\d+)会合后，所有集群",
    ]
    for pat in patterns:
        m = re.search(pat, clean_text)
        if m:
            return m.group(1)
    return None


def apply_conservative_repairs(steps: List[FormalStep], clean_text: str) -> List[str]:
    warnings: List[str] = []
    global_final_target = detect_global_final_target(clean_text)
    clauses = extract_cluster_clauses(clean_text)
    global_meet_target = detect_global_meet_target(clean_text)

    steps_by_cluster: Dict[str, List[FormalStep]] = {}
    for step in steps:
        steps_by_cluster.setdefault(step.cluster, []).append(step)

    for cluster, csteps in steps_by_cluster.items():
        clause = clauses.get(cluster, "")
        explicit_bt = set(infer_explicit_breakthrough_targets(clause))
        explicit_at = set(infer_explicit_attack_targets(clause))

        for step in csteps:
            if step.action == "attack":
                if step.target not in explicit_at and step.target not in explicit_bt:
                    warnings.append(f"{cluster}: 将非显式 attack({step.target}) 标记为可疑。")
                    step.inferred = True
            elif step.action == "breakthrough":
                if step.target == global_final_target:
                    continue
                if step.target not in explicit_bt:
                    step.inferred = True
                    warnings.append(f"{cluster}: breakthrough({step.target}) 不是原文明确突破点，已标记为推断动作。")

    if global_meet_target:
        all_clusters = sorted(steps_by_cluster.keys())
        for cluster, csteps in steps_by_cluster.items():
            for step in csteps:
                if step.action == "meet" and step.target == global_meet_target:
                    step.stage = "joint"
                    expected = [c for c in all_clusters if c != cluster]
                    if step.participants != expected:
                        step.participants = expected
                        step.inferred = True
                        warnings.append(f"{cluster}: 已将 {global_meet_target} 的会合参与方统一修正为全局参与方。")

    if global_final_target:
        all_clusters = sorted(steps_by_cluster.keys())
        for cluster, csteps in steps_by_cluster.items():
            passed_joint_meet = False
            for step in csteps:
                if step.action == "meet" and (step.target == global_meet_target or step.target == "hq_2"):
                    passed_joint_meet = True
                if step.target == global_final_target and step.action in {"move", "breakthrough"} and passed_joint_meet:
                    step.stage = "global_final"
                    if step.action == "breakthrough":
                        step.participants = list(all_clusters)
                        step.inferred = step.inferred or len(step.participants) > 1

    if global_final_target:
        for cluster, csteps in steps_by_cluster.items():
            seen_global = any(s.target == global_final_target and s.stage == "global_final" for s in csteps)
            if seen_global:
                for s in csteps:
                    if s.target == global_final_target and s.stage != "global_final":
                        s.stage = "local"

    return warnings


def build_sync_constraints(steps: List[FormalStep], clean_text: str) -> List[SyncConstraint]:
    syncs: List[SyncConstraint] = []
    seen: set = set()

    for step in steps:
        if step.action == "meet":
            participants = [step.cluster] + [p for p in step.participants if p.startswith("cluster_")]
            participants = sorted(dict.fromkeys(participants))
            sync_id = f"meet_{step.target}_{'_'.join(participants)}"
            step.sync_id = sync_id
            key = ("meet", sync_id)
            if key not in seen:
                seen.add(key)
                syncs.append(
                    SyncConstraint(
                        sync_id=sync_id,
                        sync_type="meet",
                        target=step.target,
                        participants=participants,
                        preconditions=[],
                        source=f"{step.cluster}:{step.source_fragment}",
                    )
                )

    global_final_target = detect_global_final_target(clean_text)
    if global_final_target:
        participants = sorted({s.cluster for s in steps if s.target == global_final_target and s.stage == "global_final"})
        if participants:
            sync_id = f"joint_breakthrough_{global_final_target}"
            for s in steps:
                if s.action == "breakthrough" and s.target == global_final_target and s.stage == "global_final":
                    s.sync_id = sync_id
            syncs.append(
                SyncConstraint(
                    sync_id=sync_id,
                    sync_type="joint_breakthrough",
                    target=global_final_target,
                    participants=participants,
                    preconditions=[],
                    source="由全局最终协同行动规则生成",
                )
            )
    return syncs


def cluster_sort_key(cluster: str) -> Tuple[int, Any]:
    m = re.search(r"(\d+)$", cluster)
    if m:
        return (0, int(m.group(1)))
    return (1, cluster)


def normalize_event_token(name: Optional[str]) -> str:
    if not name:
        return "unknown"
    return name.replace("_", "")


def actors_to_event_token(actors: List[str]) -> str:
    ordered = sorted(dict.fromkeys(actors), key=cluster_sort_key)
    if not ordered:
        return "unknownactors"
    if not all(a.startswith("cluster_") for a in ordered):
        return "_".join(normalize_event_token(a) for a in ordered)

    nums: List[int] = []
    for actor in ordered:
        m = re.search(r"(\d+)$", actor)
        if m:
            nums.append(int(m.group(1)))
        else:
            return "_".join(normalize_event_token(a) for a in ordered)

    if len(nums) == 1:
        return f"cluster{nums[0]}"
    if len(nums) == 2:
        return f"cluster{nums[0]}_cluster{nums[1]}"
    return f"cluster{nums[0]}_" + "_".join(str(x) for x in nums[1:])


def make_event_name(actors: List[str], action: str, target: Optional[str]) -> str:
    return f"{actors_to_event_token(actors)}_{action}_{normalize_event_token(target)}_done"


def is_placeholder_symbol(name: Optional[str]) -> bool:
    if not name:
        return True
    return name in {"UNKNOWN_TARGET", "OTHER_CLUSTERS", "UNKNOWN_CLUSTERS", "ALL_CLUSTERS"}


def infer_step_joint_contexts(steps: List[FormalStep]) -> Dict[str, Dict[str, Any]]:
    contexts: Dict[str, Dict[str, Any]] = {}
    steps_by_cluster: Dict[str, List[FormalStep]] = {}
    for step in steps:
        steps_by_cluster.setdefault(step.cluster, []).append(step)

    for cluster, csteps in steps_by_cluster.items():
        csteps = sorted(csteps, key=lambda s: s.order)
        active_actors = [cluster]
        active_context = f"local::{cluster}"
        for step in csteps:
            if step.stage == "global_final":
                contexts[step.step_id] = {"actors": [cluster], "context_id": f"global_final::{step.target}"}
                continue

            if step.action == "meet":
                actors = sorted(dict.fromkeys([cluster] + [p for p in step.participants if p.startswith("cluster_")]), key=cluster_sort_key)
                context_id = step.sync_id or f"meetctx::{step.target}::{'|'.join(actors)}"
                contexts[step.step_id] = {"actors": actors, "context_id": context_id}
                if len(actors) > 1:
                    active_actors = actors
                    active_context = context_id
                else:
                    active_actors = [cluster]
                    active_context = f"local::{cluster}"
            else:
                if len(active_actors) > 1:
                    contexts[step.step_id] = {"actors": list(active_actors), "context_id": active_context}
                else:
                    contexts[step.step_id] = {"actors": [cluster], "context_id": f"local::{cluster}"}
    return contexts


def build_atomic_event_mappings(steps: List[FormalStep]) -> Tuple[List[AtomicEventMapping], List[str]]:
    warnings: List[str] = []
    step_index = {step.step_id: idx for idx, step in enumerate(steps)}
    step_lookup = {step.step_id: step for step in steps}
    contexts = infer_step_joint_contexts(steps)

    grouped: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    step_to_group: Dict[str, Tuple[Any, ...]] = {}

    for step in steps:
        if step.target is None or is_placeholder_symbol(step.target):
            warnings.append(f"{step.step_id}: target={step.target}，跳过原子事件生成。")
            continue

        if step.action == "meet":
            actors = contexts.get(step.step_id, {}).get("actors") or sorted(
                dict.fromkeys([step.cluster] + [p for p in step.participants if p.startswith("cluster_")]),
                key=cluster_sort_key,
            )
            event_stage = "joint"
            context_id = step.sync_id or contexts.get(step.step_id, {}).get("context_id", f"meet::{step.target}")
        elif step.stage == "global_final":
            if step.action == "move":
                actors = sorted({s.cluster for s in steps if s.action == "move" and s.stage == "global_final" and s.target == step.target}, key=cluster_sort_key)
            elif step.action == "breakthrough":
                actors = sorted(set(step.participants) if step.participants else {s.cluster for s in steps if s.action == "breakthrough" and s.stage == "global_final" and s.target == step.target}, key=cluster_sort_key)
            else:
                actors = [step.cluster]
            event_stage = "global_final"
            context_id = f"global_final::{step.action}::{step.target}"
        else:
            ctx = contexts.get(step.step_id, {"actors": [step.cluster], "context_id": f"local::{step.cluster}"})
            actors = sorted(dict.fromkeys(ctx["actors"]), key=cluster_sort_key)
            event_stage = "joint" if len(actors) > 1 else "local"
            context_id = ctx["context_id"]

        if not actors:
            actors = [step.cluster]

        key = (context_id, tuple(actors), step.action, step.target, event_stage)
        grouped.setdefault(
            key,
            {
                "actors": actors,
                "action": step.action,
                "target": step.target,
                "stage": event_stage,
                "source_step_ids": [],
                "inferred": False,
                "earliest": step_index[step.step_id],
            },
        )
        grouped[key]["source_step_ids"].append(step.step_id)
        grouped[key]["inferred"] = grouped[key]["inferred"] or step.inferred
        grouped[key]["earliest"] = min(grouped[key]["earliest"], step_index[step.step_id])
        step_to_group[step.step_id] = key

    deps_by_group: Dict[Tuple[Any, ...], set] = {k: set() for k in grouped}
    for key, info in grouped.items():
        for step_id in info["source_step_ids"]:
            step = step_lookup[step_id]
            for req in step.requires:
                dep_group = step_to_group.get(req)
                if dep_group and dep_group != key:
                    deps_by_group[key].add(dep_group)

    indegree: Dict[Tuple[Any, ...], int] = {k: len(v) for k, v in deps_by_group.items()}
    outgoing: Dict[Tuple[Any, ...], List[Tuple[Any, ...]]] = {k: [] for k in grouped}
    for node, deps in deps_by_group.items():
        for dep in deps:
            outgoing.setdefault(dep, []).append(node)

    provisional_names: Dict[Tuple[Any, ...], str] = {}
    for key, info in grouped.items():
        provisional_names[key] = make_event_name(info["actors"], info["action"], info["target"])

    available = sorted([k for k, deg in indegree.items() if deg == 0], key=lambda k: (grouped[k]["earliest"], provisional_names[k]))
    topo_order: List[Tuple[Any, ...]] = []
    while available:
        current = available.pop(0)
        topo_order.append(current)
        for nxt in sorted(outgoing.get(current, []), key=lambda k: (grouped[k]["earliest"], provisional_names[k])):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                available.append(nxt)
                available.sort(key=lambda k: (grouped[k]["earliest"], provisional_names[k]))

    if len(topo_order) != len(grouped):
        remaining = [k for k in grouped if k not in topo_order]
        remaining.sort(key=lambda k: (grouped[k]["earliest"], provisional_names[k]))
        topo_order.extend(remaining)
        warnings.append("原子事件分组存在循环依赖或不完整依赖，已按 earliest 规则补排序。")

    mappings: List[AtomicEventMapping] = []
    phi_counter = 1
    for key in topo_order:
        info = grouped[key]
        event_name = provisional_names[key]
        placeholder_actor = any(is_placeholder_symbol(a) for a in info["actors"])
        usable = not info["inferred"] and not placeholder_actor and not is_placeholder_symbol(info["target"])
        excluded_reason = None
        if info["inferred"]:
            excluded_reason = "包含推断动作，默认不直接分配原子命题"
        elif placeholder_actor:
            excluded_reason = "参与方存在占位符"
        elif is_placeholder_symbol(info["target"]):
            excluded_reason = "目标存在占位符"

        phi = None
        if usable:
            phi = f"φ{phi_counter}"
            phi_counter += 1

        depends_on = [provisional_names[dep] for dep in sorted(deps_by_group[key], key=lambda k: (grouped[k]["earliest"], provisional_names[k]))]
        mappings.append(
            AtomicEventMapping(
                phi=phi,
                event_name=event_name,
                actors=list(info["actors"]),
                action=info["action"],
                target=info["target"],
                stage=info["stage"],
                source_step_ids=list(info["source_step_ids"]),
                depends_on=depends_on,
                inferred=info["inferred"],
                usable_in_ltl=usable,
                excluded_reason=excluded_reason,
            )
        )

    return mappings, warnings


def build_formal_representation(surface_result: SurfaceNormalizationResult, semantic_result: SemanticParseResult) -> Tuple[FormalRepresentation, List[str]]:
    steps, parse_warnings = parse_normalized_sentences(semantic_result.normalized_sentences)
    repair_warnings = apply_conservative_repairs(steps, surface_result.clean_text)
    _ = build_sync_constraints(steps, surface_result.clean_text)  # internal only
    mappings, mapping_warnings = build_atomic_event_mappings(steps)
    formal = FormalRepresentation(formal_steps=steps, atomic_event_mappings=mappings)
    return formal, parse_warnings + repair_warnings + mapping_warnings


# ============================================================
# 8) End-to-end pipeline
# ============================================================
def parse_task_pipeline(
    raw_input: Any,
    surface_provider: Optional[str] = None,
    semantic_provider: Optional[str] = None,
) -> PipelineOutput:
    surface_result = run_surface_normalization(raw_input, provider=surface_provider)
    semantic_result = run_semantic_parse(surface_result, provider=semantic_provider)
    formal_representation, formal_warnings = build_formal_representation(surface_result, semantic_result)
    return PipelineOutput(
        surface_normalization=surface_result,
        semantic_parse=semantic_result,
        formal_representation=formal_representation,
        warnings=formal_warnings,
    )


# Backward-compatible convenience alias
parse_to_formal_mapping = parse_task_pipeline


if __name__ == "__main__":
    raw_task_text = [
        "蓝方兵力分为四个集群，",
        "1号集群首先独立进攻hq_mark6、随后飞往hq_mark7与2号集群会合形成大部队，然后一起共同执行突破hq_mark1，然后突破hq_mark5，最后与其他编队汇聚到hq_2后，所有人飞往hq_mark4完成最后的突破。",
        "2号集群独立飞往hq_mark7，与1号集群会合后执行同样的后续行动。",
        "3号集群独立依次飞往hq_mark9、hq_mark8、hq_mark2、hq_mark4完成突破，随后前往hq_2与1、2、4号集群会合，后续共同行动。",
        "4号集群独立飞往hq_mark10、hq_mark3执行突破任务后，汇聚到hq_2与1、3号集群会合后，所有集群一起飞往hq_mark4完成突破。",
    ]

    result = parse_task_pipeline(
        raw_task_text,
        surface_provider=os.getenv("SURFACE_PROVIDER", "rule"),
        semantic_provider=os.getenv("SEMANTIC_PROVIDER", os.getenv("PROVIDER", "gemini")),
    )

    from datetime import datetime
    import os.path as osp

    data_folder_path = osp.join(os.path.dirname(__file__), "data")
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_dir = osp.join(data_folder_path, "disambiguation")
    os.makedirs(output_dir, exist_ok=True)
    out_path = osp.join(output_dir, f"nl_pipeline_result_{timestamp}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result.model_dump(), f, ensure_ascii=False, indent=2)

    print(result.model_dump_json(indent=2))
