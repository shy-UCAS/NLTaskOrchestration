# Phase 1B 语义契约化改造说明

本文解释为什么 Phase 1B 需要从旧的“标准自然语言显式携带 GCJP 参数”转向“1F-complete 作战语义 + 配置注入生成 GCJP”。本文是 rationale + execution plan，不是 baseline 报告，也不替代 [1F 指令完整性契约 v1](../development_plan/1F_指令完整性契约_v1.md)。

## 1. 背景

Phase 1F 已经完成职责重构：它不再检查指挥官原文是否提供 `duration_lb`、`energy_cost`、`ammo_cost`、`resource_constraints` 等 GCJP 参数，而是按 1F 完整性契约判断作战语义是否完整。

新的 1F 边界是：

- `complete`：actor、action、target、relation、condition、split/merge 等作战语义已经唯一明确，可以交给后续 GCJP 生成阶段。
- `incomplete`：存在缺失、多义、推迟到运行时决策或需要人工解释，触发 human-in-the-loop。
- `duration_lb`、`energy_cost`、`ammo_cost`、能力约束和资源上限属于后续配置注入，不属于 1F 向指挥官索要的语义字段。

但当前 [exp_01b_standard_nl_to_gcjp.py](../experiments/exp_01b_standard_nl_to_gcjp.py) 仍默认读取旧式 [phase1_standard_nl_cases.jsonl](../datasets/phase1_standard_nl_cases.jsonl)。这些 `standard_instruction` 往往显式写入 duration、energy、ammo、required capability、resource 上限等系统参数。这与 1F 新分层并不一致。

## 2. 为什么 01B 也必须调整

### 2.1 系统参数不应来自自然语言

指挥官或标准语义层应表达作战意图与任务结构，例如谁执行、做什么、对哪个目标、任务之间是顺序还是并行。持续时间、能耗、弹药消耗、能力要求和资源上限应来自：

- `configs/action_templates.yaml`
- `configs/capability_model.yaml`

如果 01B 仍要求 standard instruction 写满这些参数，就会让自然语言数据集成为第二套参数真源，和配置文件发生职责重叠。

### 2.2 旧 01B 容易形成自洽验证

旧 01B 样本同时告诉模型“任务消耗多少”和“资源上限多少”。这种设计容易形成“自定义消耗 + 自定义资源上限”的自洽闭环：

- 模型按样本中的消耗生成 GCJP。
- 模型按样本中的上限生成 resource constraint。
- Z3 在这套人为参数下通过。

这只能说明生成结果与样本文本自洽，不能说明它在真实 `capability_model.yaml` 下可行。

### 2.3 阶段边界会不一致

如果 1F 已经明确“语义完整性”和“系统参数”分层，而 01B 仍要求标准自然语言携带 GCJP 参数，那么整个 Phase 1 的职责链会出现断裂：

```text
01F: raw command -> semantic completeness only
01B: standard NL -> explicit GCJP parameter completion
01G: raw command -> normalization -> config-injected GCJP
```

这会让 01B 成为一个历史遗留口径，而不是 01F/01G 之间可复用的生成评测。

### 2.4 01B 应测试配置落地能力

01B 的新职责应是验证：

- LLM 能否忠实读取已经明确的作战语义。
- LLM 能否将 actor/action/target/relation/condition 正确落成 GCJP 图。
- LLM 能否从配置上下文中查表补齐 GCJP 必需参数。
- LLM 能否正确添加资源约束和能力约束，而不是自行编造参数。

换句话说，01B 应该评估“标准语义到 GCJP 的配置落地能力”，而不是评估“自然语言中是否已经写满 GCJP 参数”。

## 3. 01B 新定位

改造后，各阶段职责建议如下：

| 阶段 | 输入 | 职责 | 是否 human-in-the-loop |
|---|---|---|---|
| 01F | 原始指挥自然语言 | 判断作战语义是否 complete；不补 GCJP 系统参数 | 是 |
| 01B | 1F-complete 标准语义 NL | 生成 GCJP，并从配置表补齐任务参数、资源上限和能力约束 | 否 |
| 01G | 原始指挥自然语言 | raw NL -> 01F normalization/clarification -> 01B-style GCJP generation | 是 |
| 01H | 标准 NL + 配置注入 | 可保留为历史对照或兼容 wrapper，不作为主路径 | 否 |

01B 不应重新做 complete/incomplete 判断；它默认输入已经经过 1F 契约筛选。若输入不满足 1F complete，应由 01F 或 01G 前半段拦截，而不是由 01B 兜底。

## 4. 执行计划

### 4.1 更新 01B 实验入口

在 `experiments/exp_01b_standard_nl_to_gcjp.py` 中新增：

- `--action-templates`，默认 `configs/action_templates.yaml`
- `--capability-model`，默认 `configs/capability_model.yaml`

运行时构造 `generation_context`，通过 `CASE_JSON` 注入 prompt，至少包含：

- action defaults：`duration_lb`、`energy_cost`、`ammo_cost`、`required_capability`
- fleet capability/resource model：`max_ammo`、`max_energy_kwh`、能力布尔字段
- parameter source policy：语义来自 standard instruction，系统参数来自配置表

### 4.2 重写 01B 默认数据集

默认 `datasets/phase1_standard_nl_cases.jsonl` 应从 1F complete 样本派生，保留标准语义，不再写人工参数。

`standard_instruction` 应包含：

- segment id
- assigned actors
- task ids
- actor/action/target
- relations：sequence、parallel、sync、conditional、fork、join
- 可机器判定的 condition
- 必要的 time window 或 physical context，如果该样本专门测试这类约束

`standard_instruction` 不应包含：

- duration / duration_lb
- energy / energy_cost
- ammo / ammo_cost
- required capability
- resource upper bound / max_value

### 4.3 更新 prompt 责任边界

`prompts/standard_nl_to_gcjp_prompt.md` 应明确：

- actor、action、target、relation、condition 来自 `STANDARD_INSTRUCTION`
- `duration_lb`、`energy_cost`、`ammo_cost`、`required_capability` 来自 `action_defaults`
- resource constraints 和 actor capabilities 来自 `capability_model`
- 不允许模型为了让 Z3 通过而自行调整消耗、能力或资源上限

### 4.4 增加配置一致性评估

现有 `expected_patterns` 仍用于检查图结构和约束类型，但还需要新增配置一致性检查：

- task 参数是否与 `action_templates.yaml` 对齐
- resource constraints 是否与 `capability_model.yaml` 对齐
- capability constraints 是否使用 action default 的 required capability 和 actor 的 capabilities

建议新增指标：

- `config_parameter_conformance_rate`
- `resource_constraint_conformance_rate`
- `capability_constraint_conformance_rate`

`first_pass` 仍可保留为端到端通过指标，但配置一致性失败应单独暴露，避免“Z3 通过但参数来源错误”被误认为成功。

### 4.5 处理 01H

仓库中已有 `experiments/exp_01h_standard_nl_to_gcjp_with_config.py`，其定位接近“01B + 配置注入”。后续有两种可选处理方式：

- 将 01H 的配置注入逻辑并入 01B，01H 保留为兼容 wrapper。
- 保留 01H 作为 legacy 对照实验，但文档中说明 01B 是新主线。

推荐第一种：减少两个实验长期分叉带来的维护成本。

### 4.6 移除注入 prompt 的答案泄露

旧 01B/01H 在构造 `case_payload` 时，把 `expected_patterns` 和 `tags` 一并塞进 `CASE_JSON` 暴露给模型。这两者都是评分真值：

- `expected_patterns`（`node_count` / `edge_relations` / `constraint_types`）正是 `node_complete` / `edge_complete` / `constraint_complete` 的判分目标；其中 `constraint_types` 含 `resource`，恰是本次改造想测的“配置落地”行为。
- `tags` 含 `sat` / `unsat` 字符串，等于从侧门泄露了 `expected_result`，直接剧透 Layer 3 答案。

这会让 01B 从“给语义+配置盲评生成能力”退化为“照着结构与 SAT 答案抄”，并直接架空本次改造让 Z3 验证更真实的目标。

修正：`case_payload_fn` 只注入 `generation_context`（配置上下文），不再注入 `expected_patterns` / `tags`。评分器 `_evaluate_expected` 始终从原始 `case` 读取真值，与 prompt 解耦，因此移除注入不影响判分，只是不再喂给模型。`prompts/standard_nl_to_gcjp_prompt.md` 末尾的 `CASE_JSON` 说明也相应从 “optional expected pattern hints” 改为仅“Configuration context”。

## 5. 预期收益

- 01B 与 01F 完整性契约保持一致。
- GCJP 参数来源唯一化，避免数据集、prompt 和配置文件互相冲突。
- baseline 更接近真实流程，不再依赖人为写满参数的自然语言。
- Z3 验证含义更真实：验证的是配置模型下的可行性，而不是样本文本自造参数下的可行性。
- 失败归因更清楚，可区分语义遗漏、配置注入错误、GCJP API 错误和 SMT 不可满足。

## 6. 后续检查项

文档级检查：

- 本文只作为设计迁移说明，不写入 baseline 结论。
- 本文引用 1F 契约、01B 实验入口和当前 01B 数据集，便于追踪迁移原因。

实现级检查：

- 新 01B 数据集中不得把 energy、ammo、duration、resource upper bound 作为 standard instruction 的语义字段。
- 所有 actor 必须存在于 `capability_model.yaml`。
- 所有 action 必须存在于 `action_templates.yaml`。
- 01B 跑通后，应与旧 01B/01H 做一次对照说明，解释指标变化来自职责边界变化，而不是模型能力突然退化。

## 7. 结论

01B 的改造不是简单复用 01F 的 complete/incomplete 判断逻辑，而是同步 01F 已经确立的职责分层：自然语言层只表达作战语义，GCJP 参数由配置表注入。

因此，01B 的新主线应是：

```text
1F-complete 标准语义 NL
  -> 配置上下文注入
  -> GCJP 代码生成
  -> 安全执行 + 图结构检查 + SMT/Z3 验证 + 配置一致性检查
```

这能让 01B 成为 01F 与 01G 之间稳定、可复用、可解释的生成评测环节。
