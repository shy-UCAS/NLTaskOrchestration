# Phase 1 计划清单与当前代码进展核对

> 核对日期：2026-05-21  
> 对照计划：`NLTaskOrchestration_下一步开发计划_修订版.md`  
> 当前仓库：`NLTaskOrchestration`

---

## 1. 总体结论

截至目前，项目已经明显超过原计划中“阶段 1：强模型 GCJP 生成能力验证”的最低要求，并且已经向“阶段 2：LLM 修复闭环原型”和“反馈消融评估”推进。

当前已经完成的核心闭环是：

```text
LLM 生成 GCJP
→ 安全/执行/图结构/Z3 验证
→ 失败样本进入修复闭环
→ LLM 根据反馈修复
→ 再验证
→ 对反馈信息做消融实验
```

从实验结果看：

- 阶段 1A / 1B 生成实验已经跑通，并在现有 baseline 中达到 `first_pass_rate = 1.0`
- 阶段 1C 修复闭环已经实现，并在固定坏代码与模拟失败样本上达到 `repair_success_rate = 1.0`
- 阶段 1D 反馈消融已经实现，并完成 strict 消融，证明不同信息通道对修复成功率有明显影响
- 阶段 1E 模拟自然失败生成已经实现，生成的失败样本满足 `simulated_valid_failure_rate = 1.0`

因此，当前不只是完成了原计划阶段 1 的“强模型生成能力验证”，还补充了阶段 1 内部的完整实验体系：生成、修复、模拟失败、消融分析、preset 自动化运行。

---

## 2. 原计划阶段 1 清单核对

原计划阶段 1 名称：

```text
阶段 1：强模型 GCJP 生成能力验证
```

目标：

```text
验证强模型能否从结构化/自然语言任务描述稳定生成合法 GCJP，并通过安全、执行、图结构、Z3 等验证。
```

### 2.1 计划新增文件 vs 当前实现

| 计划项 | 当前实现 | 状态 |
|---|---|---|
| `agents/planner_agent.py` | 已存在 `agents/planner_agent.py` | 已完成 |
| `prompts/gcjp_generation_prompt.md` | 已存在 `prompts/gcjp_generation_prompt.md` | 已完成 |
| few-shot 生成 prompt | 已有 `prompts/gcjp_generation_prompt_fewshot.md`、`prompts/standard_nl_to_gcjp_prompt_fewshot.md` | 超出计划 |
| `experiments/exp_01_strong_model_gcjp_generation.py` | 实际拆分为 `exp_01a_structured_to_gcjp.py` 和 `exp_01b_standard_nl_to_gcjp.py` | 已完成，命名不同 |
| `demos/demo_14_strong_model_generate_gcjp.py` | 未按该名称实现；已有 `demos/demo_phase1_nonlive_regression.py` 与 smoke demo | 部分替代 |
| 结构化输入数据集 | 已有 `datasets/phase1_structured_cases.jsonl` | 已完成 |
| 标准自然语言数据集 | 已有 `datasets/phase1_standard_nl_cases.jsonl` | 超出计划 |
| 指标聚合 | 已在 `experiments/phase1_common.py` 中统一实现 | 已完成 |
| LLM provider 封装 | 已有 `agents/llm_client.py` | 已完成并扩展 |

### 2.2 输入格式实现情况

原计划第一步使用结构化子任务描述，当前实际实现为两条主线：

```text
1A：结构化 JSON / case payload → GCJP
1B：标准自然语言 instruction → GCJP
```

对应文件：

```text
experiments/exp_01a_structured_to_gcjp.py
experiments/exp_01b_standard_nl_to_gcjp.py
datasets/phase1_structured_cases.jsonl
datasets/phase1_standard_nl_cases.jsonl
```

当前实现比原计划更细：

- 原计划只强调结构化任务描述
- 当前已经同时覆盖结构化输入和标准自然语言输入
- 后续还扩展了 failure seed v2 数据集

### 2.3 Prompt 设计实现情况

原计划要求：

```text
API 规范 + 3-4 个 few-shot 示例 + 当前任务
```

当前实现：

```text
prompts/gcjp_generation_prompt.md
prompts/gcjp_generation_prompt_fewshot.md
prompts/standard_nl_to_gcjp_prompt.md
prompts/standard_nl_to_gcjp_prompt_fewshot.md
```

完成情况：

- zero-shot prompt 已实现
- few-shot prompt 已实现
- few-shot prompt 后续针对 `condition_trigger`、`physical_feasibility` API 签名问题做过修正
- README / baseline report 中已有对应运行记录

结论：阶段 1 prompt 设计已完成，并且已经经过至少一轮问题修正。

### 2.4 评价指标实现情况

| 指标 | 当前实现情况 |
|---|---|
| `safety_pass_rate` | 已实现 |
| `execution_success_rate` | 已实现 |
| `builtgraph_success_rate` | 已实现 |
| `z3_sat_rate` | 已由 L3 / expected result 类指标覆盖 |
| `first_pass_rate` | 已实现 |
| `constraint_completeness_rate` | 已实现为 `constraint_complete_rate` |
| `error_type_distribution` | 已实现部分错误类型统计 |

当前实际输出还包括：

```text
node_complete_rate
edge_complete_rate
constraint_complete_rate
l2_graph_pass_rate
l3_expected_result_rate
no_code_rate
simulated_valid_failure_rate
expected_failure_layer_match_rate
repair_success_rate
final_pass_rate
avg_repair_rounds
```

结论：阶段 1 指标体系已完成，并且扩展到了修复和模拟失败场景。

---

## 3. 阶段 1 当前实际完成的子模块

当前 Phase 1 实际已经形成了 1A 到 1E 的实验体系。

### 3.1 Phase 1A：结构化任务 → GCJP

核心文件：

```text
experiments/exp_01a_structured_to_gcjp.py
datasets/phase1_structured_cases.jsonl
prompts/gcjp_generation_prompt.md
prompts/gcjp_generation_prompt_fewshot.md
```

完成情况：

- 支持结构化 case payload 输入
- 支持 LLM 生成 GCJP
- 支持代码提取、安全检查、执行、验证、指标输出
- 已有 baseline 记录，扩充版 zero-shot 和 few-shot 均达到 `first_pass_rate = 1.0`

状态：已完成。

### 3.2 Phase 1B：标准自然语言 → GCJP

核心文件：

```text
experiments/exp_01b_standard_nl_to_gcjp.py
datasets/phase1_standard_nl_cases.jsonl
prompts/standard_nl_to_gcjp_prompt.md
prompts/standard_nl_to_gcjp_prompt_fewshot.md
```

完成情况：

- 支持标准自然语言任务描述输入
- 支持 zero-shot / few-shot prompt
- 已有 baseline 记录，扩充版 zero-shot 和 few-shot 均达到 `first_pass_rate = 1.0`

状态：已完成。

### 3.3 Phase 1C：修复闭环

核心文件：

```text
agents/repair_agent.py
prompts/gcjp_repair_prompt.md
experiments/exp_01c_repair_loop.py
datasets/phase1_repair_cases.jsonl
```

完成情况：

- 支持从固定坏代码样本修复
- 支持从已有 generation report 中读取失败样本修复
- 支持多轮修复，当前常用 `max_repair_rounds=2`
- 输出 initial code、final code、attempts、report、metrics
- 支持 latest run 自动读取

已验证结果：

```text
phase1c_repair_simulated_latest
repair_success_rate = 1.0
final_pass_rate = 1.0
avg_repair_rounds = 1.0909
```

状态：已完成，并已用于 simulated failure 闭环。

### 3.4 Phase 1D：修复反馈消融实验

核心文件：

```text
experiments/exp_01d_repair_feedback_ablation.py
configs/experiment_presets.yaml
```

当前支持 feedback modes：

```text
full_report
no_report
no_report_no_bug_spec
task_only_no_report
report_only_no_oracle
broken_only
layer1_only
error_summary_only
```

已新增 strict preset：

```text
phase1d_simulated_ablation_strict_latest
```

strict 消融结果：

| feedback_mode | repair_success_rate | final_pass_rate | avg_repair_rounds |
|---|---:|---:|---:|
| `full_report` | 1.0000 | 1.0000 | 1.0000 |
| `no_report` | 1.0000 | 1.0000 | 1.0909 |
| `no_report_no_bug_spec` | 1.0000 | 1.0000 | 1.0909 |
| `task_only_no_report` | 0.8182 | 0.8182 | 1.1818 |
| `report_only_no_oracle` | 0.4545 | 0.4545 | 1.6364 |
| `broken_only` | 0.3636 | 0.3636 | 1.6364 |

主要结论：

```text
1. 完整反馈可以稳定修复全部 simulated failures。
2. 仅移除 verification report 影响较小，因为 task oracle 仍然很强。
3. 移除 task oracle 后，语义类修复成功率明显下降。
4. broken-only 主要只能修 API / 执行 / safety 类显式错误。
```

状态：已完成，并产生有区分度的实验结果。

### 3.5 Phase 1E：模拟自然失败生成

核心文件：

```text
experiments/exp_01e_simulated_natural_failure_generation.py
datasets/phase1_simulated_failure_specs.jsonl
prompts/gcjp_simulated_natural_failure_prompt.md
```

完成情况：

- 支持从 bug spec 生成自然失败 GCJP 代码
- 生成结果写入 reports，供 Phase 1C / 1D 复用
- 已修复 `simfail_010_wrong_physical_speed` 的 deadline 规格问题
- 已强化 prompt，避免模型在非目标 bug 上破坏 API 签名

最新有效结果：

```text
phase1e_simulated_failure_v1
syntax_extract_rate = 1.0
simulated_valid_failure_rate = 1.0
expected_failure_layer_match_rate = 1.0
no_code_rate = 0.0
```

状态：已完成，并已成为 Phase 1C / 1D 的输入来源。

---

## 4. 阶段 0 完成情况

虽然当前核对聚焦阶段 1，但阶段 0 是阶段 2 修复闭环的前置依赖，也需要记录。

原计划阶段 0：

```text
UNSAT 归因降噪
```

当前实现证据：

```text
gcjp/constraint_templates.py
verifier/pipeline.py
demos/demo_z3_unsat_core_filtering.py
```

已实现能力：

- `unsat_core_raw`
- `unsat_core_semantic`
- `unsat_core_framework`
- `attribution`
- legacy `unsat_core` 指向 semantic core
- demo 验证 semantic core 不包含框架约束

状态：已完成。

---

## 5. 已完成但原阶段 1 计划未明确列出的增强项

### 5.1 LLM Client 能力增强

核心文件：

```text
agents/llm_client.py
demos/demo_llm_client_smoke.py
configs/llm_providers.example.yaml
```

已实现：

- HTTP transport
- official SDK transport
- OpenAI Chat 协议
- OpenAI Responses SDK 协议
- Anthropic Messages HTTP / SDK 协议
- `thinking` 开关
- `thinking_budget_tokens`
- `reasoning_effort`
- `output_effort`
- `extra_body` 透传
- `--local-provider claude` 读取本地 cc switch 配置
- 修复 Anthropic HTTP thinking 与 temperature 冲突

状态：已完成，属于基础设施增强。

### 5.2 Preset 运行系统

核心文件：

```text
experiments/run_preset.py
configs/experiment_presets.yaml
```

已实现：

- 统一 preset 配置
- 支持 `latest_run` 自动解析
- 支持 `--set args.xxx=yyy`
- 支持 `--dry-run`
- 支持 Phase 1A/1B/1C/1D/1E 预设

状态：已完成。

### 5.3 最新运行索引

核心文件：

```text
experiments/phase1_common.py
```

已实现：

- 自动 run dir
- provider-based run label
- `latest_run.json`
- downstream 实验自动读取 upstream reports

状态：已完成。

---

## 6. 部分完成 / 仍需补齐的内容

### 6.1 Demo 命名未完全对齐原计划

原计划：

```text
demos/demo_14_strong_model_generate_gcjp.py
demos/demo_15_gcjp_repair_loop.py
```

当前：

```text
demos/demo_phase1_nonlive_regression.py
demos/demo_llm_client_smoke.py
```

现状：

- 功能上已有 smoke / regression demo
- 但没有按计划命名的 demo_14 / demo_15

建议：

- 如果要严格对齐计划文档，可新增轻量 wrapper demo
- 如果不追求编号一致，可在计划更新文档中说明命名调整

状态：部分完成。

### 6.2 error transition matrix 尚未完整实现

原计划阶段 2 指标包含：

```text
error_transition_matrix
```

当前已有：

```text
initial_error_type
final_error_type
recovered_error_type_distribution
unrecovered_error_type_distribution
```

但尚未形成完整矩阵，例如：

```text
EXECUTION_FAILED → SUCCESS
SUCCESS/L3_FAIL → EXECUTION_FAILED
SAFETY_CHECK_FAILED → SUCCESS
```

建议：

- 在 `exp_01c_repair_loop.py` 聚合指标中新增 `error_transition_matrix`
- 在 `exp_01d_repair_feedback_ablation.py` summary 中同步显示各模式 transition matrix

状态：部分完成。

### 6.3 种子数据集构建尚未完整完成

原计划阶段 3 要求：

```text
datasets/seed/gcjp_seed.jsonl
datasets/seed/gcjp_seed_schema.json
tools/build_seed_dataset.py
tools/validate_seed_dataset.py
tools/mutate_seed_dataset.py
```

当前已有：

```text
datasets/phase1_failure_seed_structured_cases.jsonl
datasets/phase1_failure_seed_standard_nl_cases.jsonl
datasets/phase1_failure_seed_structured_cases_v2.jsonl
datasets/phase1_failure_seed_standard_nl_cases_v2.jsonl
tools/validate_phase1_failure_seed_pairs.py
```

现状：

- 已有 failure seed 数据集
- 已有 structured / standard-NL 成对校验工具
- 但还没有通用 seed schema、demo 提取工具、结构化 mutation 工具
- 还未达到“50-100 条自动扩展并验证”的阶段 3 目标

状态：部分完成。

### 6.4 JSON → LLM → GCJP 对比实验尚未单独实现

原计划阶段 4：

```text
experiments/exp_03_json_to_gcjp.py
prompts/json_to_gcjp_prompt.md
demos/demo_16_json_to_gcjp_generation.py
```

当前没有看到这些专用文件。

不过 Phase 1A 已经承担了一部分结构化输入 → GCJP 的验证功能。

缺口在于：

- 尚未明确用 `task_plan_loader.py` 生成 reference BuiltGraph
- 尚未做 ref/pred 节点、边、约束逐项对比实验
- 尚未形成 JSON→GCJP 的专门 baseline report

状态：未完成 / 被 Phase 1A 部分覆盖。

### 6.5 Integrator 尚未实现

原计划阶段 5：

```text
integrator/
schemas/cross_dependency_schema.json
demos/demo_17_integrate_two_segments.py
```

当前仓库中未看到 `integrator/` 模块。

状态：未完成。

### 6.6 物理可行性自动注入尚未完成

原计划阶段 6：

```text
gcjp/task_plan_loader.py
gcjp/environment_model.py
```

当前已有 `gcjp/environment_model.py`，也已有手动 physical feasibility 约束；但尚未确认已经实现“根据环境坐标自动注入 physical_feasibility constraint”的完整流程。

状态：未完成 / 基础能力已有。

### 6.7 Layer 4 语义反向校验尚未完成

原计划阶段 7：

```text
verifier/semantic_checker.py
demos/demo_18_semantic_reverse_check.py
```

当前 pipeline 中 L4 仍是语义预留，没有独立 `semantic_checker.py`。

不过 Phase 1A/1B/1E 里已经用：

```text
node_complete_rate
edge_complete_rate
constraint_complete_rate
expected_patterns
```

覆盖了一部分确定性语义/结构对比。

状态：未完成 / 部分指标替代。

---

## 7. 当前代码进展清单

### 已完成核心文件

```text
agents/planner_agent.py
agents/repair_agent.py
agents/llm_client.py
agents/code_extraction.py

prompts/gcjp_generation_prompt.md
prompts/gcjp_generation_prompt_fewshot.md
prompts/standard_nl_to_gcjp_prompt.md
prompts/standard_nl_to_gcjp_prompt_fewshot.md
prompts/gcjp_repair_prompt.md
prompts/gcjp_simulated_natural_failure_prompt.md

experiments/phase1_common.py
experiments/exp_01a_structured_to_gcjp.py
experiments/exp_01b_standard_nl_to_gcjp.py
experiments/exp_01c_repair_loop.py
experiments/exp_01d_repair_feedback_ablation.py
experiments/exp_01e_simulated_natural_failure_generation.py
experiments/run_preset.py

datasets/phase1_structured_cases.jsonl
datasets/phase1_standard_nl_cases.jsonl
datasets/phase1_repair_cases.jsonl
datasets/phase1_failure_seed_structured_cases.jsonl
datasets/phase1_failure_seed_standard_nl_cases.jsonl
datasets/phase1_failure_seed_structured_cases_v2.jsonl
datasets/phase1_failure_seed_standard_nl_cases_v2.jsonl
datasets/phase1_simulated_failure_specs.jsonl

tools/validate_phase1_failure_seed_pairs.py
tools/recompute_phase1c_metrics.py

configs/experiment_presets.yaml
configs/llm_providers.example.yaml
```

---

## 8. 当前已验证实验结果

### 8.1 Phase 1A / 1B baseline

根据 `docs/phase1_baseline_report.md`：

```text
1A structured JSON -> GCJP:
first_pass_rate = 1.0

1B standard NL -> GCJP:
first_pass_rate = 1.0

few-shot 修正后：
1A first_pass_rate = 1.0
1B first_pass_rate = 1.0
```

### 8.2 Phase 1E simulated failure generation

最新有效 run：

```text
out/phase1_simulated_failure_runs/local_claude__claude_opus_4_6__uuapi_net__20260521-201510
```

指标：

```text
syntax_extract_rate = 1.0
simulated_valid_failure_rate = 1.0
expected_failure_layer_match_rate = 1.0
no_code_rate = 0.0
```

### 8.3 Phase 1C repair on simulated failures

最新有效 run：

```text
out/phase1_simulated_failure_repairs/local_claude__claude_opus_4_6__uuapi_net__20260521-201751
```

指标：

```text
repair_success_rate = 1.0
final_pass_rate = 1.0
avg_repair_rounds = 1.0909
```

### 8.4 Phase 1D strict ablation

最新有效 run：

```text
out/phase1_feedback_ablation_manual/simulated_strict/local_claude__claude_opus_4_6__uuapi_net__20260521-223327
```

关键结果：

```text
full_report              repair_success_rate = 1.0
no_report                repair_success_rate = 1.0
no_report_no_bug_spec    repair_success_rate = 1.0
task_only_no_report      repair_success_rate = 0.8182
report_only_no_oracle    repair_success_rate = 0.4545
broken_only              repair_success_rate = 0.3636
```

实验解释：

```text
完整上下文可以稳定修复所有 simulated failures。
只移除 verification report 时，任务 oracle 仍然足以支撑修复。
去掉任务 oracle 后，语义类修复明显下降。
只看 broken code 时，只能稳定修 API / 执行 / safety 类显式错误。
```

---

## 9. 当前仍需完成的任务清单

### 高优先级

1. 补齐 Phase 1C / 1D 的 `error_transition_matrix`
   - 当前已有 initial/final error type
   - 需要聚合成矩阵
   - 用于分析修复是否引入新错误

2. 把 Phase 1D strict ablation 结果写入正式文档
   - 建议更新 `docs/exp_01c_repair_loop_results.md`
   - 或新增 `docs/exp_01d_feedback_ablation_results.md`

3. 明确 Phase 1 当前里程碑状态
   - M-0：已通过
   - M-1：已通过
   - M-2：已通过
   - M-3 及之后：尚未完成

### 中优先级

4. 完成阶段 3 种子数据集工程化
   - `datasets/seed/gcjp_seed.jsonl`
   - `datasets/seed/gcjp_seed_schema.json`
   - `tools/build_seed_dataset.py`
   - `tools/validate_seed_dataset.py`
   - `tools/mutate_seed_dataset.py`

5. 实现 JSON → LLM → GCJP 专门对比实验
   - `experiments/exp_03_json_to_gcjp.py`
   - `prompts/json_to_gcjp_prompt.md`
   - reference vs prediction 对比

6. 增加 demo wrapper 或更新计划说明
   - 是否补 `demo_14` / `demo_15`
   - 或说明当前已由 `demo_phase1_nonlive_regression.py` 与实验脚本替代

### 低优先级 / 后续阶段

7. 最小 integrator
8. 物理可行性自动注入
9. Layer 4 语义反向校验
10. 大规模训练 / GRPO / UI / 多 Agent 上层系统

---

## 10. 建议下一步

建议下一步不是继续新增 LLM 调用能力，而是把当前 Phase 1 的实验体系固化成可复现报告：

```text
1. 写 Phase 1D strict ablation result 文档
2. 给 Phase 1C/1D 补 error_transition_matrix
3. 汇总 Phase 1A-1E 的最终状态文档
4. 再进入阶段 3：种子数据集工程化
```

原因：

```text
当前核心研究假设已经被初步验证：
LLM 可以生成合法 GCJP；
结构化验证反馈可以支持修复；
不同反馈信息通道对修复效果有可测影响。
```

因此，下一步更适合做“结果固化 + 数据集扩展”，而不是马上跳到 integrator 或训练。
