import json
import os
import re
from pathlib import Path
from typing import Any, Dict

from openai import OpenAI


MODEL_NAME = "qwen3-max"
DEFAULT_OUTPUT_DIR = "./data/SentenceSplitting"


ROUND1_SYSTEM_PROMPT = r"""
你是“无人机集群任务文本预处理器（第一轮，split/merge一体化版）”。

你的任务是把输入的中文任务描述转换为“事件级中间表示”，重点完成：
1. 语句切分
2. 复合动作拆分
3. 主语补全
4. 群体状态跟踪
5. split / merge / rendezvous 一体化建模
6. 记录需要第二轮再解析的模糊引用

你不负责：
1. 最终任务规划
2. LTL公式生成
3. Declare约束抽取
4. 战术优劣分析
5. 最终裁决所有模糊引用

【第一轮核心规则】

1. 必须把输入文本拆成多个原子事件。
- 一个事件只表达一个核心动作。
- 如“飞往X并与Y会合形成大部队”必须拆成多个事件。

2. 必须补全省略主语。
- 若当前片段没有主语，则继承最近的合法 actor。
- 例如“随后飞往hq_mark7”默认继承上一事件主语。

3. 必须维护群体状态。
- 若出现“会合、联合、一起、共同、形成大部队、汇聚、所有人、全体、其他编队”等表述，更新群体状态。
- 若形成新的联合体，请在 group_registry 与 lineage_registry 中登记。

4. 必须识别拆分事件。
- 若文本出现“拆分、分出、分成、拆成、拆出、分兵、分路、兵力分为”等表达，必须识别为 split 事件。
- 若某个集群 Gk 被拆分为 x 个子集群，则子集群统一命名为：Gk_1, Gk_2, ..., Gk_x。
- 不得自由命名。
- 若文本明确“其余兵力继续行动”，则剩余主队命名为 Gk_MAIN。
- 若文本表达为完全拆分，则原父实体默认 status=inactive。

5. 必须识别合并事件。
- merge / rendezvous / 汇聚 / 会合 / 联合 都属于会改变实体结构或群体作用域的事件。
- 若多个已有实体形成新联合体，使用 GRP 序号命名，例如 GRP1、GRP2。

6. 第一轮允许保留歧义，但必须显式标出来。
- 如果某个表达无法在第一轮唯一确定，例如“3号与1号汇合”中的“1号”到底指单体、拆分后的主队，还是当前所属联合体，
  不要强行武断决定。
- 必须把该引用写入 unresolved_references。
- 同时给出当前最可能解释和原因。

7. 每个事件都要生成 explicit_text。
- explicit_text 必须是补全后的显式中文句子。

8. actor 统一写成：
- G1, G2, G3 ...
- 拆分子集群使用 G1_1, G1_2 ...
- 剩余主队使用 G1_MAIN
- 全体用 ["ALL"]
- 未知其他编队可用 ["OTHER_FORMATIONS"]

9. 不要脑补不存在的信息。

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
      "action_hint": "move|attack|joint_attack|rendezvous|merge|split|assemble|patrol|block|occupy|final_attack|command|unknown",
      "target_hint": "",
      "location_hint": "",
      "time_marker": "首先|随后|然后|最后|待…后|无",
      "depends_on": [],
      "trigger_text": "",
      "group_state_before": [],
      "group_state_after": [],
      "entity_status_before": [],
      "entity_status_after": [],
      "new_group_created": {
        "group_id": "",
        "members": []
      },
      "split_registry_update": {
        "parent": "",
        "children": [],
        "retained_main_entity": "",
        "parent_status_after_split": ""
      },
      "lineage_update": {
        "operation": "none|split|merge",
        "input_entities": [],
        "output_entities": []
      },
      "completion_type": [],
      "needs_reference_resolution": false,
      "reference_candidates": []
    }
  ],
  "entity_registry": [
    {
      "entity_id": "G1",
      "entity_type": "base_group|derived_group|main_remainder|merged_group|virtual_group",
      "parent_entities": [],
      "status": "active|inactive|ambiguous",
      "created_by_event": "E1",
      "reason": "..."
    }
  ],
  "group_registry": [
    {
      "group_id": "GRP1",
      "members": ["G1", "G2"],
      "created_by_event": "E3",
      "reason": "..."
    }
  ],
  "lineage_registry": [
    {
      "event_id": "E3",
      "operation": "split|merge",
      "input_entities": ["G1"],
      "output_entities": ["G1_1", "G1_2"]
    }
  ],
  "unresolved_references": [
    {
      "event_id": "E5",
      "surface_form": "1号集群",
      "possible_resolutions": [
        {"type": "single", "value": ["G1"]},
        {"type": "group", "value": ["G1_1","G1_2"]}
      ],
      "preferred_resolution": {"type": "group", "value": ["G1_1","G1_2"]},
      "reason": "..."
    }
  ]
}
"""


ROUND1_USER_TEMPLATE = r"""
下面给出两个示例，请学习输出格式。

【示例1：merge / rendezvous】
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
      "entity_status_before": [],
      "entity_status_after": [{"entity_id": "G1", "status": "active"}],
      "new_group_created": {"group_id": "", "members": []},
      "split_registry_update": {"parent": "", "children": [], "retained_main_entity": "", "parent_status_after_split": ""},
      "lineage_update": {"operation": "none", "input_entities": [], "output_entities": []},
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
      "entity_status_before": [{"entity_id": "G1", "status": "active"}],
      "entity_status_after": [{"entity_id": "G1", "status": "active"}],
      "new_group_created": {"group_id": "", "members": []},
      "split_registry_update": {"parent": "", "children": [], "retained_main_entity": "", "parent_status_after_split": ""},
      "lineage_update": {"operation": "none", "input_entities": [], "output_entities": []},
      "completion_type": ["subject_inherited"],
      "needs_reference_resolution": false,
      "reference_candidates": []
    },
    {
      "event_id": "E3",
      "source_fragment": "与2号集群会合",
      "explicit_text": "1号集群与2号集群在hq_mark7会合，形成联合体GRP1。",
      "actor": ["G1", "G2"],
      "action_hint": "merge",
      "target_hint": "2号集群",
      "location_hint": "hq_mark7",
      "time_marker": "然后",
      "depends_on": ["E2"],
      "trigger_text": "",
      "group_state_before": ["G1"],
      "group_state_after": ["GRP1"],
      "entity_status_before": [{"entity_id": "G1", "status": "active"}, {"entity_id": "G2", "status": "active"}],
      "entity_status_after": [{"entity_id": "GRP1", "status": "active"}],
      "new_group_created": {"group_id": "GRP1", "members": ["G1", "G2"]},
      "split_registry_update": {"parent": "", "children": [], "retained_main_entity": "", "parent_status_after_split": ""},
      "lineage_update": {"operation": "merge", "input_entities": ["G1", "G2"], "output_entities": ["GRP1"]},
      "completion_type": ["group_scope_expanded", "compound_split"],
      "needs_reference_resolution": false,
      "reference_candidates": []
    },
    {
      "event_id": "E4",
      "source_fragment": "然后一起突破hq_mark1",
      "explicit_text": "联合体GRP1随后共同突破hq_mark1。",
      "actor": ["GRP1"],
      "action_hint": "joint_attack",
      "target_hint": "hq_mark1",
      "location_hint": "hq_mark1",
      "time_marker": "然后",
      "depends_on": ["E3"],
      "trigger_text": "",
      "group_state_before": ["GRP1"],
      "group_state_after": ["GRP1"],
      "entity_status_before": [{"entity_id": "GRP1", "status": "active"}],
      "entity_status_after": [{"entity_id": "GRP1", "status": "active"}],
      "new_group_created": {"group_id": "", "members": []},
      "split_registry_update": {"parent": "", "children": [], "retained_main_entity": "", "parent_status_after_split": ""},
      "lineage_update": {"operation": "none", "input_entities": [], "output_entities": []},
      "completion_type": ["subject_inherited", "group_scope_expanded"],
      "needs_reference_resolution": false,
      "reference_candidates": []
    }
  ],
  "entity_registry": [
    {"entity_id": "G1", "entity_type": "base_group", "parent_entities": [], "status": "active", "created_by_event": "E1", "reason": "初始单体"},
    {"entity_id": "G2", "entity_type": "base_group", "parent_entities": [], "status": "active", "created_by_event": "E3", "reason": "从文本中显式出现"},
    {"entity_id": "GRP1", "entity_type": "merged_group", "parent_entities": ["G1", "G2"], "status": "active", "created_by_event": "E3", "reason": "G1与G2会合形成联合体"}
  ],
  "group_registry": [
    {"group_id": "GRP1", "members": ["G1", "G2"], "created_by_event": "E3", "reason": "G1与G2会合形成联合体"}
  ],
  "lineage_registry": [
    {"event_id": "E3", "operation": "merge", "input_entities": ["G1", "G2"], "output_entities": ["GRP1"]}
  ],
  "unresolved_references": []
}

【示例2：split】
输入：
1号集群拆分成2个子集群，分别进攻hq_mark1和hq_mark2，随后两路兵力在hq_mark3会合。

输出：
{
  "input_text": "1号集群拆分成2个子集群，分别进攻hq_mark1和hq_mark2，随后两路兵力在hq_mark3会合。",
  "events": [
    {
      "event_id": "E1",
      "source_fragment": "1号集群拆分成2个子集群",
      "explicit_text": "1号集群拆分成2个子集群，分别命名为G1_1和G1_2。",
      "actor": ["G1"],
      "action_hint": "split",
      "target_hint": "2个子集群",
      "location_hint": "",
      "time_marker": "无",
      "depends_on": [],
      "trigger_text": "",
      "group_state_before": ["G1"],
      "group_state_after": ["G1_1", "G1_2"],
      "entity_status_before": [{"entity_id": "G1", "status": "active"}],
      "entity_status_after": [{"entity_id": "G1", "status": "inactive"}, {"entity_id": "G1_1", "status": "active"}, {"entity_id": "G1_2", "status": "active"}],
      "new_group_created": {"group_id": "", "members": []},
      "split_registry_update": {"parent": "G1", "children": ["G1_1", "G1_2"], "retained_main_entity": "", "parent_status_after_split": "inactive"},
      "lineage_update": {"operation": "split", "input_entities": ["G1"], "output_entities": ["G1_1", "G1_2"]},
      "completion_type": ["explicit_subject"],
      "needs_reference_resolution": false,
      "reference_candidates": []
    },
    {
      "event_id": "E2",
      "source_fragment": "分别进攻hq_mark1",
      "explicit_text": "G1_1进攻hq_mark1。",
      "actor": ["G1_1"],
      "action_hint": "attack",
      "target_hint": "hq_mark1",
      "location_hint": "hq_mark1",
      "time_marker": "然后",
      "depends_on": ["E1"],
      "trigger_text": "",
      "group_state_before": ["G1_1", "G1_2"],
      "group_state_after": ["G1_1", "G1_2"],
      "entity_status_before": [{"entity_id": "G1_1", "status": "active"}, {"entity_id": "G1_2", "status": "active"}],
      "entity_status_after": [{"entity_id": "G1_1", "status": "active"}, {"entity_id": "G1_2", "status": "active"}],
      "new_group_created": {"group_id": "", "members": []},
      "split_registry_update": {"parent": "", "children": [], "retained_main_entity": "", "parent_status_after_split": ""},
      "lineage_update": {"operation": "none", "input_entities": [], "output_entities": []},
      "completion_type": ["compound_split"],
      "needs_reference_resolution": false,
      "reference_candidates": []
    },
    {
      "event_id": "E3",
      "source_fragment": "和hq_mark2",
      "explicit_text": "G1_2进攻hq_mark2。",
      "actor": ["G1_2"],
      "action_hint": "attack",
      "target_hint": "hq_mark2",
      "location_hint": "hq_mark2",
      "time_marker": "然后",
      "depends_on": ["E1"],
      "trigger_text": "",
      "group_state_before": ["G1_1", "G1_2"],
      "group_state_after": ["G1_1", "G1_2"],
      "entity_status_before": [{"entity_id": "G1_1", "status": "active"}, {"entity_id": "G1_2", "status": "active"}],
      "entity_status_after": [{"entity_id": "G1_1", "status": "active"}, {"entity_id": "G1_2", "status": "active"}],
      "new_group_created": {"group_id": "", "members": []},
      "split_registry_update": {"parent": "", "children": [], "retained_main_entity": "", "parent_status_after_split": ""},
      "lineage_update": {"operation": "none", "input_entities": [], "output_entities": []},
      "completion_type": ["compound_split"],
      "needs_reference_resolution": false,
      "reference_candidates": []
    },
    {
      "event_id": "E4",
      "source_fragment": "随后两路兵力在hq_mark3会合",
      "explicit_text": "G1_1和G1_2随后在hq_mark3会合，形成联合体GRP1。",
      "actor": ["G1_1", "G1_2"],
      "action_hint": "merge",
      "target_hint": "两路兵力",
      "location_hint": "hq_mark3",
      "time_marker": "随后",
      "depends_on": ["E2", "E3"],
      "trigger_text": "",
      "group_state_before": ["G1_1", "G1_2"],
      "group_state_after": ["GRP1"],
      "entity_status_before": [{"entity_id": "G1_1", "status": "active"}, {"entity_id": "G1_2", "status": "active"}],
      "entity_status_after": [{"entity_id": "GRP1", "status": "active"}],
      "new_group_created": {"group_id": "GRP1", "members": ["G1_1", "G1_2"]},
      "split_registry_update": {"parent": "", "children": [], "retained_main_entity": "", "parent_status_after_split": ""},
      "lineage_update": {"operation": "merge", "input_entities": ["G1_1", "G1_2"], "output_entities": ["GRP1"]},
      "completion_type": ["subject_inherited", "group_scope_expanded"],
      "needs_reference_resolution": false,
      "reference_candidates": []
    }
  ],
  "entity_registry": [
    {"entity_id": "G1", "entity_type": "base_group", "parent_entities": [], "status": "inactive", "created_by_event": "E1", "reason": "拆分后失活"},
    {"entity_id": "G1_1", "entity_type": "derived_group", "parent_entities": ["G1"], "status": "active", "created_by_event": "E1", "reason": "由G1拆分得到"},
    {"entity_id": "G1_2", "entity_type": "derived_group", "parent_entities": ["G1"], "status": "active", "created_by_event": "E1", "reason": "由G1拆分得到"},
    {"entity_id": "GRP1", "entity_type": "merged_group", "parent_entities": ["G1_1", "G1_2"], "status": "active", "created_by_event": "E4", "reason": "G1_1与G1_2会合形成联合体"}
  ],
  "group_registry": [
    {"group_id": "GRP1", "members": ["G1_1", "G1_2"], "created_by_event": "E4", "reason": "G1_1与G1_2会合形成联合体"}
  ],
  "lineage_registry": [
    {"event_id": "E1", "operation": "split", "input_entities": ["G1"], "output_entities": ["G1_1", "G1_2"]},
    {"event_id": "E4", "operation": "merge", "input_entities": ["G1_1", "G1_2"], "output_entities": ["GRP1"]}
  ],
  "unresolved_references": []
}

现在处理下面输入：

{input_text}

请严格只输出 JSON 对象。
"""


ROUND2_SYSTEM_PROMPT = r"""
你是“无人机集群任务文本预处理器（第二轮，split/merge一体化版）”。

你的任务是基于：
1. 原始文本
2. 第一轮中间结果

完成以下工作：
1. 解析第一轮中的 unresolved_references
2. 判断某个被提及 actor 指向单体、拆分子集群、主队，还是当前所属联合体
3. 修正 explicit_text
4. 生成最终 resolved_events
5. 输出最终的 reference_resolutions、final_entity_registry、final_group_registry、final_lineage_registry

【第二轮核心规则】

1. 若某个 actor 在前文已并入联合体，则后文单独提及该 actor 时：
- 默认优先解析为该 actor 当前所属联合体
- 除非文本明确表示其单独行动、脱离联合体或重新独立执行任务

2. 若某父集群已在前文被完全拆分并设为 inactive，则后文再次出现该父集群名称时：
- 不得默认解析为原父集群实体
- 必须优先检查它是否表示：
  a. 全部子集群的统称
  b. 剩余主队 Gk_MAIN
  c. 一个未解决歧义引用

3. 当出现“与X汇合”时：
- X 不是简单字符串
- 需要先查询 X 在当前上下文中的所属作战单元
- 若 X 当前属于某联合体，则“与X汇合”默认表示“与该联合体汇合”

4. 必须保留 reference_resolution 记录：
- surface_form
- resolved_entity_type
- resolved_entity
- reason

5. resolved_entity_type 只能取：
- single
- derived_group
- main_remainder
- group
- ambiguous_after_split

6. 若第一轮已有正确结果，可直接保留，不要无端改写。

7. explicit_text 必须写成最终显式版本。
例如：
- 原文：“3号与1号汇合”
  若解析结果是 G1 当前属于 {G1,G2}
  则 explicit_text 应写为：
  “3号集群与由1号集群和2号集群组成的联合编队汇合。”
- 原文：“随后1号集群与2号会合”
  若 G1 已拆分为 G1_1 和 G1_2 且原 G1 已失活
  则不能直接写“1号集群会合”，
  应改写为“由1号集群拆分出的子集群组成的联合体与2号集群会合”或标记为歧义。

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
      "entity_status_before": [],
      "entity_status_after": [],
      "split_registry_update": {
        "parent": "",
        "children": [],
        "retained_main_entity": "",
        "parent_status_after_split": ""
      },
      "lineage_update": {
        "operation": "none|split|merge",
        "input_entities": [],
        "output_entities": []
      },
      "reference_resolution": []
    }
  ],
  "reference_resolutions": [
    {
      "event_id": "E3",
      "surface_form": "1号集群",
      "resolved_entity_type": "single|derived_group|main_remainder|group|ambiguous_after_split",
      "resolved_entity": ["G1","G2"],
      "reason": "..."
    }
  ],
  "final_entity_registry": [
    {
      "entity_id": "G1",
      "entity_type": "base_group|derived_group|main_remainder|merged_group|virtual_group",
      "parent_entities": [],
      "status": "active|inactive|ambiguous",
      "created_by_event": "E1",
      "reason": "..."
    }
  ],
  "final_group_registry": [
    {
      "group_id": "GRP1",
      "members": ["G1","G2"],
      "created_by_event": "E3",
      "reason": "..."
    }
  ],
  "final_lineage_registry": [
    {
      "event_id": "E3",
      "operation": "split|merge",
      "input_entities": ["G1"],
      "output_entities": ["G1_1","G1_2"]
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
2. 某 actor 是否应解析为当前所属联合体、拆分子集群或剩余主队
3. 拆分后原名是否失效
4. 修正 explicit_text
5. 输出 resolved_events、reference_resolutions、final_entity_registry、final_group_registry、final_lineage_registry

请严格只输出 JSON 对象。
"""


def build_round1_user_prompt(input_text: str) -> str:
    return ROUND1_USER_TEMPLATE.replace("{input_text}", input_text)


def build_round2_user_prompt(input_text: str, round1_obj: Dict[str, Any]) -> str:
    return ROUND2_USER_TEMPLATE.replace(
        "{input_text}", input_text
    ).replace(
        "{round1_json}", json.dumps(round1_obj, ensure_ascii=False, indent=2)
    )


def extract_json_from_text(text: str) -> Dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("模型输出中未找到合法 JSON 对象")
    return json.loads(text[start:end + 1])


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def save_text(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def save_json(path: str, obj: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _validate_common_events(events: Any, label: str) -> None:
    if not isinstance(events, list):
        raise ValueError(f"{label} 不是列表")
    required_keys = [
        "event_id", "source_fragment", "explicit_text", "actor",
        "action_hint", "group_state_before", "group_state_after"
    ]
    for i, ev in enumerate(events, start=1):
        for key in required_keys:
            if key not in ev:
                raise ValueError(f"{label}中的事件缺少字段: {key}")
        expected_id = f"E{i}"
        if ev["event_id"] != expected_id:
            raise ValueError(
                f"{label}中的 event_id 不连续，期望 {expected_id}，实际 {ev['event_id']}"
            )


def validate_round1(obj: Dict[str, Any]) -> None:
    for key in ["input_text", "events", "entity_registry", "group_registry", "lineage_registry", "unresolved_references"]:
        if key not in obj:
            raise ValueError(f"第一轮输出缺少字段: {key}")
    _validate_common_events(obj["events"], "第一轮 events")


def validate_round2(obj: Dict[str, Any]) -> None:
    for key in [
        "input_text", "resolved_events", "reference_resolutions",
        "final_entity_registry", "final_group_registry", "final_lineage_registry"
    ]:
        if key not in obj:
            raise ValueError(f"第二轮输出缺少字段: {key}")
    _validate_common_events(obj["resolved_events"], "第二轮 resolved_events")


def call_qwen(
    client: OpenAI,
    system_prompt: str,
    user_prompt: str,
    model: str = MODEL_NAME,
) -> str:
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


def run_pipeline(
    input_text: str,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    model: str = MODEL_NAME,
) -> Dict[str, Any]:
    ensure_dir(output_dir)

    client = OpenAI(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key=os.getenv("DASHSCOPE_API_KEY"),
    )

    round1_user_prompt = build_round1_user_prompt(input_text)
    round1_raw = call_qwen(client, ROUND1_SYSTEM_PROMPT, round1_user_prompt, model=model)
    save_text(f"{output_dir}/round1_raw.txt", round1_raw)

    round1_obj = extract_json_from_text(round1_raw)
    validate_round1(round1_obj)
    save_json(f"{output_dir}/round1.json", round1_obj)

    round2_user_prompt = build_round2_user_prompt(input_text, round1_obj)
    round2_raw = call_qwen(client, ROUND2_SYSTEM_PROMPT, round2_user_prompt, model=model)
    save_text(f"{output_dir}/round2_raw.txt", round2_raw)

    round2_obj = extract_json_from_text(round2_raw)
    validate_round2(round2_obj)
    save_json(f"{output_dir}/round2.json", round2_obj)

    bundle = {
        "input_text": input_text,
        "model": model,
        "round1": round1_obj,
        "round2": round2_obj,
    }
    save_json(f"{output_dir}/pipeline_bundle.json", bundle)

    return bundle


if __name__ == "__main__":
    input_text = (
        "1号集群拆分成2个子集群，分别进攻hq_mark1和hq_mark2，随后两路兵力在hq_mark3会合。"
    )

    try:
        result = run_pipeline(input_text)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"错误信息：{e}")
