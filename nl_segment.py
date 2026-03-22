import os
import json
import re
from pathlib import Path
from openai import OpenAI


ROUND1_SYSTEM_PROMPT = r"""
你是“无人机集群任务文本预处理器（第一轮）”。

你的任务是把输入的中文任务描述转换为“事件级中间表示”，重点完成：
1. 语句切分
2. 复合动作拆分
3. 主语补全
4. 群体状态跟踪
5. 记录可能存在歧义、需要第二轮再解析的引用

你不负责：
1. 最终任务规划
2. LTL公式生成
3. Declare约束抽取
4. 战术分析
5. 最终裁决所有模糊引用

【第一轮核心规则】

1. 必须把输入文本拆成多个原子事件。
- 一个事件只表达一个核心动作。
- 如“飞往X并与Y会合形成大部队”必须拆成多个事件。

2. 必须补全省略主语。
- 若当前片段没有主语，则继承最近的合法 actor。
- 例如“随后飞往hq_mark7”默认继承上一事件主语。

3. 必须维护群体状态。
- 若出现“会合、联合、一起、共同、形成大部队、所有人、全体、其他编队”等表述，更新群体状态。
- 若形成新的联合体，请在 group_registry 中登记。

4. 第一轮允许保留歧义，但必须显式标出来。
- 如果某个表达无法在第一轮唯一确定，例如“3号与1号汇合”中的“1号”到底指个体还是联合体，
  不要强行武断决定。
- 必须把该引用写入 unresolved_references。
- 同时给出当前最可能解释和原因。

5. 每个事件都要生成 explicit_text。
- explicit_text 必须是补全后的显式中文句子。

6. actor 统一写成：
- G1, G2, G3 ...
- 全体用 ["ALL"]
- 未知其他编队可用 ["OTHER_FORMATIONS"]

7. 不要脑补不存在的信息。

【输出格式】
只输出一个 JSON 对象，不要输出解释，不要输出 markdown。

JSON 结构如下：
{
  "input_text": "...",
  "events": [
    {
      "event_id": "E1",
      "source_fragment": "...",
      "explicit_text": "...",
      "actor": ["G1"],
      "action_hint": "move|attack|joint_attack|rendezvous|assemble|patrol|block|occupy|final_attack|command|unknown",
      "target_hint": "",
      "location_hint": "",
      "time_marker": "首先|随后|然后|最后|待…后|无",
      "depends_on": [],
      "trigger_text": "",
      "group_state_before": [],
      "group_state_after": [],
      "new_group_created": {
        "group_id": "",
        "members": []
      },
      "completion_type": [],
      "needs_reference_resolution": false,
      "reference_candidates": []
    }
  ],
  "group_registry": [
    {
      "group_id": "GRP1",
      "members": ["G1"],
      "created_by_event": "E1",
      "reason": "初始单体"
    }
  ],
  "unresolved_references": [
    {
      "event_id": "E3",
      "surface_form": "1号集群",
      "possible_resolutions": [
        {"type": "single", "value": ["G1"]},
        {"type": "group", "value": ["G1","G2"]}
      ],
      "preferred_resolution": {"type": "group", "value": ["G1","G2"]},
      "reason": "G1在前文已与G2会合"
    }
  ]
}
"""

ROUND1_USER_TEMPLATE = r"""
下面给出一个示例，请学习输出格式。

输入：
1号集群首先攻击hq_mark6，随后飞往hq_mark7与2号集群会合，然后一起突破hq_mark1。

输出：
{
  "input_text": "1号集群首先攻击hq_mark6，随后飞往hq_mark7与2号集群会合，然后一起突破hq_mark1。",
  "events": [
    {
      "event_id": "E1",
      "source_fragment": "1号集群首先攻击hq_mark6",
      "explicit_text": "1号集群首先攻击hq_mark6。",
      "actor": ["G1"],
      "action_hint": "attack",
      "target_hint": "hq_mark6",
      "location_hint": "hq_mark6",
      "time_marker": "首先",
      "depends_on": [],
      "trigger_text": "",
      "group_state_before": [],
      "group_state_after": ["G1"],
      "new_group_created": {
        "group_id": "GRP1",
        "members": ["G1"]
      },
      "completion_type": ["explicit_subject"],
      "needs_reference_resolution": false,
      "reference_candidates": []
    },
    {
      "event_id": "E2",
      "source_fragment": "随后飞往hq_mark7",
      "explicit_text": "1号集群随后飞往hq_mark7。",
      "actor": ["G1"],
      "action_hint": "move",
      "target_hint": "hq_mark7",
      "location_hint": "hq_mark7",
      "time_marker": "随后",
      "depends_on": ["E1"],
      "trigger_text": "",
      "group_state_before": ["G1"],
      "group_state_after": ["G1"],
      "new_group_created": {
        "group_id": "",
        "members": []
      },
      "completion_type": ["subject_inherited"],
      "needs_reference_resolution": false,
      "reference_candidates": []
    },
    {
      "event_id": "E3",
      "source_fragment": "与2号集群会合",
      "explicit_text": "1号集群与2号集群在hq_mark7会合。",
      "actor": ["G1","G2"],
      "action_hint": "rendezvous",
      "target_hint": "2号集群",
      "location_hint": "hq_mark7",
      "time_marker": "然后",
      "depends_on": ["E2"],
      "trigger_text": "",
      "group_state_before": ["G1"],
      "group_state_after": ["G1","G2"],
      "new_group_created": {
        "group_id": "GRP2",
        "members": ["G1","G2"]
      },
      "completion_type": ["group_scope_expanded", "compound_split"],
      "needs_reference_resolution": false,
      "reference_candidates": []
    },
    {
      "event_id": "E4",
      "source_fragment": "然后一起突破hq_mark1",
      "explicit_text": "1号集群和2号集群随后共同突破hq_mark1。",
      "actor": ["G1","G2"],
      "action_hint": "joint_attack",
      "target_hint": "hq_mark1",
      "location_hint": "hq_mark1",
      "time_marker": "然后",
      "depends_on": ["E3"],
      "trigger_text": "",
      "group_state_before": ["G1","G2"],
      "group_state_after": ["G1","G2"],
      "new_group_created": {
        "group_id": "",
        "members": []
      },
      "completion_type": ["subject_inherited", "group_scope_expanded"],
      "needs_reference_resolution": false,
      "reference_candidates": []
    }
  ],
  "group_registry": [
    {
      "group_id": "GRP1",
      "members": ["G1"],
      "created_by_event": "E1",
      "reason": "初始单体"
    },
    {
      "group_id": "GRP2",
      "members": ["G1","G2"],
      "created_by_event": "E3",
      "reason": "G1与G2会合形成联合体"
    }
  ],
  "unresolved_references": []
}

现在处理下面输入：

{input_text}

请严格只输出 JSON 对象。
"""

ROUND2_SYSTEM_PROMPT = r"""
你是“无人机集群任务文本预处理器（第二轮）”。

你的任务是基于：
1. 原始文本
2. 第一轮中间结果

完成以下工作：
1. 解析第一轮中所有 unresolved_references
2. 判断某个被提及 actor 指向单体还是其当前所属联合体
3. 修正 explicit_text
4. 生成最终 resolved_events
5. 输出最终的 reference_resolutions 和 final_group_registry

【第二轮核心规则】

1. 若某个 actor 在前文已并入联合体，则后文单独提及该 actor 时：
- 默认优先解析为该 actor 当前所属联合体
- 除非文本明确表示其单独行动、脱离联合体或重新独立执行任务

2. 当出现“与X汇合”时：
- X 不是简单字符串
- 需要先查询 X 在当前上下文中的所属作战单元
- 若 X 当前属于某联合体，则“与X汇合”默认表示“与该联合体汇合”

3. 必须保留 reference_resolution 记录：
- surface_form
- resolved_entity_type
- resolved_entity
- reason

4. 若第一轮已有正确结果，可直接保留，不要无端改写。

5. explicit_text 必须写成最终显式版本。
例如：
原文：“3号与1号汇合”
若解析结果是 G1 当前属于 {G1,G2}
则 explicit_text 应写为：
“3号集群与由1号集群和2号集群组成的联合编队汇合。”

【输出格式】
只输出一个 JSON 对象，不要输出解释，不要输出 markdown。

JSON 结构：
{
  "input_text": "...",
  "resolved_events": [
    {
      "event_id": "E1",
      "source_fragment": "...",
      "explicit_text": "...",
      "actor": [],
      "action_hint": "",
      "target_hint": "",
      "location_hint": "",
      "time_marker": "",
      "depends_on": [],
      "trigger_text": "",
      "group_state_before": [],
      "group_state_after": [],
      "reference_resolution": []
    }
  ],
  "reference_resolutions": [
    {
      "event_id": "E3",
      "surface_form": "1号集群",
      "resolved_entity_type": "single|group",
      "resolved_entity": ["G1","G2"],
      "reason": "..."
    }
  ],
  "final_group_registry": [
    {
      "group_id": "GRP2",
      "members": ["G1","G2"],
      "created_by_event": "E2",
      "reason": "..."
    }
  ]
}
"""

ROUND2_USER_TEMPLATE = r"""
请基于以下两部分输入完成第二轮解析。

【原始文本】
{input_text}

【第一轮结果】
{round1_json}

请重点处理：
1. unresolved_references
2. 某 actor 是否应解析为当前所属联合体
3. 修正 explicit_text
4. 输出 resolved_events、reference_resolutions、final_group_registry

请严格只输出 JSON 对象。
"""


def build_round1_user_prompt(input_text: str) -> str:
    return ROUND1_USER_TEMPLATE.replace("{input_text}", input_text)


def build_round2_user_prompt(input_text: str, round1_obj: dict) -> str:
    return ROUND2_USER_TEMPLATE.replace(
        "{input_text}", input_text
    ).replace(
        "{round1_json}", json.dumps(round1_obj, ensure_ascii=False, indent=2)
    )


def extract_json_from_text(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("模型输出中未找到合法 JSON 对象")
    return json.loads(text[start:end + 1])


def ensure_dir(path: str):
    Path(path).mkdir(parents=True, exist_ok=True)


def save_text(path: str, content: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def save_json(path: str, obj: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def validate_round1(obj: dict):
    if "input_text" not in obj or "events" not in obj:
        raise ValueError("第一轮输出缺少 input_text 或 events")
    if not isinstance(obj["events"], list):
        raise ValueError("第一轮 events 不是列表")
    for i, ev in enumerate(obj["events"], start=1):
        for key in [
            "event_id", "source_fragment", "explicit_text", "actor",
            "action_hint", "group_state_before", "group_state_after"
        ]:
            if key not in ev:
                raise ValueError(f"第一轮事件缺少字段: {key}")
        expected_id = f"E{i}"
        if ev["event_id"] != expected_id:
            raise ValueError(f"第一轮 event_id 不连续，期望 {expected_id}，实际 {ev['event_id']}")


def validate_round2(obj: dict):
    if "input_text" not in obj or "resolved_events" not in obj:
        raise ValueError("第二轮输出缺少 input_text 或 resolved_events")
    if not isinstance(obj["resolved_events"], list):
        raise ValueError("第二轮 resolved_events 不是列表")
    for i, ev in enumerate(obj["resolved_events"], start=1):
        for key in [
            "event_id", "source_fragment", "explicit_text", "actor",
            "action_hint", "group_state_before", "group_state_after"
        ]:
            if key not in ev:
                raise ValueError(f"第二轮事件缺少字段: {key}")
        expected_id = f"E{i}"
        if ev["event_id"] != expected_id:
            raise ValueError(f"第二轮 event_id 不连续，期望 {expected_id}，实际 {ev['event_id']}")


def call_qwen(client: OpenAI, system_prompt: str, user_prompt: str, model: str = "qwen3-max") -> str:
    completion = client.chat.completions.create(
        model=model,
        temperature=0.0,
        top_p=1.0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
    )
    return completion.choices[0].message.content


def run_pipeline(input_text: str, output_dir: str = "./data/SentenceSplitting") -> dict:
    ensure_dir(output_dir)

    client = OpenAI(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key=os.getenv("DASHSCOPE_API_KEY"),
    )

    # Round 1
    round1_user_prompt = build_round1_user_prompt(input_text)
    round1_raw = call_qwen(client, ROUND1_SYSTEM_PROMPT, round1_user_prompt)
    save_text(f"{output_dir}/round1_raw.txt", round1_raw)

    round1_obj = extract_json_from_text(round1_raw)
    validate_round1(round1_obj)
    save_json(f"{output_dir}/round1.json", round1_obj)

    # Round 2
    round2_user_prompt = build_round2_user_prompt(input_text, round1_obj)
    round2_raw = call_qwen(client, ROUND2_SYSTEM_PROMPT, round2_user_prompt)
    save_text(f"{output_dir}/round2_raw.txt", round2_raw)

    round2_obj = extract_json_from_text(round2_raw)
    validate_round2(round2_obj)
    save_json(f"{output_dir}/round2.json", round2_obj)

    # Bundle
    bundle = {
        "input_text": input_text,
        "round1": round1_obj,
        "round2": round2_obj
    }
    save_json(f"{output_dir}/pipeline_bundle.json", bundle)

    return bundle


if __name__ == "__main__":
    input_text = (
        "蓝方兵力分为四个集群，1号集群首先独立进攻hq_mark6，随后飞往hq_mark7与2号集群会合形成大部队，然后一起共同执行突破hq_mark1，然后突破hq_mark5，最后与其他编队汇聚到hq_2后，所有人飞往hq_mark4完成最后的突破。2号集群独立飞往hq_mark7，与1号集群会合后执行同样的后续行动。3号集群独立依次飞往hq_mark9、hq_mark8、hq_mark2、hq_mark4完成突破，随后前往hq_2与1、2、4号集群会合，后续共同行动。4号集群独立飞往hq_mark10、hq_mark3执行突破任务后，汇聚到hq_2与1、3号集群会合后，所有集群一起飞往hq_mark4完成突破。"
    )

    try:
        result = run_pipeline(input_text)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"错误信息：{e}")