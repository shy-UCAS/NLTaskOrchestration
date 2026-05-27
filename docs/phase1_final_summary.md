# Phase 1 最终状态总结

> 日期：2026-05-27
>
> 对应论文：v4.5 研究任务2 / v4.6 研究计划1
>
> 核对来源：各实验 metrics.json + phase1_baseline_report + phase1_plan_progress_review

---

## 1. 各子阶段完成状态

| 子阶段 | 名称 | 状态 | 一句话总结 |
|--------|------|------|-----------|
| Phase 1A | 结构化 JSON → GCJP | **已完成** | 15 条结构化样本全部一次通过，zero-shot 和 few-shot 均 first_pass_rate = 1.0 |
| Phase 1B | 标准自然语言 → GCJP | **已完成** | 12 条标准 NL 样本全部一次通过，first_pass_rate = 1.0 |
| Phase 1C | 修复闭环 | **已完成** | 固定坏例 5/5、failure seed 6/6、模拟失败 11/11 全部修复，repair_success_rate = 1.0 |
| Phase 1D | 修复反馈消融 | **已完成** | 6 种 feedback mode 产生明确区分度，task oracle 被证明为修复关键因素 |
| Phase 1E | 模拟自然失败生成 | **已完成** | 11 条模拟失败全部有效，simulated_valid_failure_rate = 1.0 |

---

## 2. 核心指标表

### 2.1 生成实验（Phase 1A / 1B）

| 指标 | 1A 结构化 (n=15) | 1B 标准 NL (n=12) |
|------|---:|---:|
| syntax_extract_rate | 1.0 | 1.0 |
| safety_pass_rate | 1.0 | 1.0 |
| execution_success_rate | 1.0 | 1.0 |
| builtgraph_success_rate | 1.0 | 1.0 |
| l2_graph_pass_rate | 1.0 | 1.0 |
| l3_expected_result_rate | 1.0 | 1.0 |
| **first_pass_rate** | **1.0** | **1.0** |
| node_complete_rate | 1.0 | 1.0 |
| edge_complete_rate | 1.0 | 1.0 |
| constraint_complete_rate | 1.0 | 1.0 |

模型：Claude Opus 4.6，zero-shot prompt，temperature = 0.1

### 2.2 修复实验（Phase 1C）

| 数据来源 | 样本数 | initial_pass_rate | repair_success_rate | final_pass_rate | avg_repair_rounds |
|---------|---:|---:|---:|---:|---:|
| 固定坏例 | 5 | 0.0 | 1.0 | 1.0 | 1.0 |
| 1B failure seed | 3 | 0.0 | 1.0 | 1.0 | 1.333 |
| 1A failure seed | 3 | 0.0 | 1.0 | 1.0 | 1.333 |
| 1E 模拟失败 | 11 | 0.0 | 1.0 | 1.0 | 1.091 |

### 2.3 消融实验（Phase 1D strict）

| feedback_mode | repair_success_rate | final_pass_rate |
|---|---:|---:|
| full_report | 1.0 | 1.0 |
| no_report | 1.0 | 1.0 |
| no_report_no_bug_spec | 1.0 | 1.0 |
| task_only_no_report | 0.8182 | 0.8182 |
| report_only_no_oracle | 0.4545 | 0.4545 |
| broken_only | 0.3636 | 0.3636 |

### 2.4 模拟失败生成（Phase 1E）

| 指标 | 值 |
|------|---:|
| simulated_valid_failure_rate | 1.0 |
| expected_failure_layer_match_rate | 1.0 |
| no_code_rate | 0.0 |
| 样本数 | 11 |

---

## 3. 里程碑状态

| 里程碑 | 通过条件 | 状态 |
|--------|---------|------|
| M-0 | 确定性底座：GCJP API + 验证管道 + UNSAT 归因降噪 | **已通过** |
| M-1 | 强模型生成验证：first_pass_rate ≥ 0.8 | **已通过**（1.0） |
| M-2 | 修复闭环验证：repair_success_rate ≥ 0.8 + 消融有区分度 | **已通过**（1.0 + 63% 方差） |

---

## 4. 已验证的核心假设

1. **LLM 可以从结构化/自然语言描述稳定生成合法 GCJP 代码**。在 15+12 条样本上实现 100% 一次通过率，覆盖 sequence、parallel、sync、condition_trigger、barrier、group_sync 等关系类型，以及 resource、capability、physical_feasibility、time_window 等约束类型。

2. **4 层验证管道能有效检测生成代码中的错误**。Layer 1（执行安全）、Layer 2（图结构）、Layer 3（Z3 约束求解）形成递进式质量门禁，UNSAT 归因可区分 semantic core 和 framework core。

3. **结构化验证反馈可以支撑 LLM 自动修复**。在 22 条不同来源的失败样本上，平均 1.1 轮修复即可恢复全部样本，修复不会通过删除约束强行将 UNSAT 转为 SAT。

4. **不同反馈信息通道对修复效果有可测量的影响**。task oracle（任务语义 + expected patterns）是修复成功率的关键因素，其贡献大于验证报告本身。压缩错误摘要可替代完整报告。

---

## 5. 已知边界和局限

1. **数据规模有限**。当前生成实验仅覆盖 15+12 条样本，修复实验 22 条。结论的统计置信度依赖后续种子数据集扩展（目标 30→100 条）。

2. **模型依赖**。所有 baseline 基于 Claude Opus 4.6。不同模型/provider 的表现可能有差异，尚未做跨模型对比。

3. **样本复杂度集中在 L1-L3**。当前样本以 1-2 个集群、2-6 个节点为主，缺少 4+ 集群联合段和复杂 UNSAT 场景的系统性覆盖。

4. **环境模型仅做引用校验**。物理可行性约束需要手动指定距离/速度参数，尚未实现从环境坐标自动注入。

5. **Layer 4 语义反向校验未实现**。当前通过 node/edge/constraint completeness 指标部分替代，但不等于完整的语义检查。

6. **NL 前端缺失**。当前实验输入为"标准无歧义 NL"或结构化 JSON，尚未处理原始模糊自然语言的歧义消解和指令规范化。

---

## 6. 下一步衔接

Phase 1 实验体系已完成，下一步进入工程清单 Layer 2-3：

| 任务 | 内容 | 对应计划 |
|------|------|---------|
| B | NL → 标准化指令（指令规范化 + 交互式澄清闭环） | Layer 2.2 |
| C | JSON → GCJP reference 对比实验 | Layer 2.1 深化 |
| D | 种子数据集工程化（30→100 条） | Layer 4 |
| E | NL 全串联单 Agent 原型（端到端 Z3 通过率 ≥ 60%） | Layer 3 |

详见 `development_plan/研究任务1_下一步执行清单.md`。

---

## 7. 关键文件索引

### 实验代码
- `experiments/exp_01a_structured_to_gcjp.py`
- `experiments/exp_01b_standard_nl_to_gcjp.py`
- `experiments/exp_01c_repair_loop.py`
- `experiments/exp_01d_repair_feedback_ablation.py`
- `experiments/exp_01e_simulated_natural_failure_generation.py`
- `experiments/phase1_common.py`

### Agent 与 Prompt
- `agents/planner_agent.py` / `agents/repair_agent.py` / `agents/llm_client.py`
- `prompts/gcjp_generation_prompt.md` / `prompts/standard_nl_to_gcjp_prompt.md`
- `prompts/gcjp_repair_prompt.md` / `prompts/gcjp_simulated_natural_failure_prompt.md`

### 数据集
- `datasets/phase1_structured_cases.jsonl` (15 条)
- `datasets/phase1_standard_nl_cases.jsonl` (12 条)
- `datasets/phase1_repair_cases.jsonl` (5 条)
- `datasets/phase1_simulated_failure_specs.jsonl` (11 条)

### 文档
- `docs/phase1_baseline_report.md`
- `docs/phase1c_repair_loop_results.md`
- `docs/phase1d_feedback_ablation_results.md`
- `docs/phase1_plan_progress_review_20260521.md`
