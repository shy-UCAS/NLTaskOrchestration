# Phase 1 评分体系参考（七项指标 + dag_exact）

> 模块：`experiments/phase1_common.py`（`evaluate_graph_against_expected` 及 dag_exact 系列函数）
> 更新时间：2026-06-11
> 适用范围：Phase 1 全部生成类实验（1A / 1B / 1H / 1I / 1J / 1K / 1L）的统一评分口径

---

## 1. 概述与定位

Phase 1 所有实验共用 `evaluate_graph_against_expected(case, graph, report)` 做图层评分。该函数**与生成方式无关**：无论 BuiltGraph 来自执行 LLM 代码（1A/1B/1H/1L）、确定性构图（1I）还是骨架填参后执行（1J/1K），评分只依赖三样东西——最终 `BuiltGraph`、验证报告 `VerificationReport`、case 真值。因此各实验的指标可以横向对照。

**公平性硬约束**：`expected_patterns` / `expected_result` / `expected_graph` 等真值字段始终只在评分侧从原始 case 读取，**绝不进入任何 prompt**。

## 2. 图层七项指标

| 指标 | 口径 |
|------|------|
| `builtgraph_success` | 最终拿到了 `BuiltGraph`（执行/构图成功） |
| `l2_graph_pass` | Layer 2 图结构验证通过（DAG / 连通性 / 节点覆盖） |
| `l3_expected_result` | Z3 结果与 `expected_result` 一致：`sat` 要求 `overall_passed` 且 `z3_result == "sat"`；`unsat` 只要求 `z3_result == "unsat"` |
| `node_complete` | 节点满足 `expected_patterns.node_count` 等节点级期望 |
| `edge_complete` | `expected_patterns.edge_relations` 中的每种关系类型在图中出现（sync 走语义等价，见第 3 节） |
| `constraint_complete` | `expected_patterns.constraint_types` 中的每种约束类型在图中出现（sync/group_sync 归一互认，见第 3 节） |
| `first_pass` | `l3_expected_result ∧ node_complete ∧ edge_complete ∧ constraint_complete`，即"一次通过" |

对执行 LLM 代码的实验，`_evaluate_expected` 在七项之外再补三项执行侧指标：`syntax_extract`（代码提取成功）、`safety_pass`（AST 白名单通过）、`execution_success`（受限执行成功）。汇总报表按 `DEFAULT_RATE_KEYS` 对以上各项求通过率。

## 3. sync 语义等价规则

同一"任务集起始同步"语义在 GCJP 中有三种**可证等价**的写法，产出的 Z3 约束相同：

| 写法 | 产物 |
|------|------|
| `add_dependency(..., relation="sync")` | sync 边 + sync 约束 |
| `add_sync_constraint(task_i, task_j, tolerance=...)` | sync 约束（无边） |
| `add_group_sync_constraint([...], tolerance=..., mode=...)` | group_sync 约束（两两同步，2 任务时与 sync 完全等价） |

评分因此**按语义而非 API 写法**比较（`_SYNC_EQUIV = {"sync", "group_sync"}` 与 `_sync_realized()`）：

- `edge_complete`：期望含 `sync` 边时，图中存在 sync 边**或**任一 sync/group_sync 约束均视为满足——同步是对称定时关系，不是有向前驱边；
- `constraint_complete`：`sync` 与 `group_sync` 归一为同一 SYNC 等价类后再比较，非同步类型仍严格匹配。

**动机**：标准 NL prompt 已把同步规范为 `add_group_sync_constraint`（见 `prompts/standard_nl_to_gcjp_prompt.md`），而部分真值仍以 `sync` 边表达。qwen3-max 试跑中两条实际正确的样本（`trial_binary_sync_ac0a598d` 的 edge_complete、`trial_aggregate_disperse_d6a87b1d` 的 constraint_complete）曾因写法不同被误判失败，现已收录为回归用例。

## 4. dag_exact：第 8 项、最严口径

七项指标对结构只做**类型/计数级**检查：节点数、关系类型出现性、约束类型出现性，从不比较边的端点与方向。一条 `first_pass` 样本仍可能接错边端点。`dag_exact` 弥补这一盲区：

- **前置条件**：case 必须携带完整真值（`expected_graph` + `canonical_task_plan`），即 v2 master 数据集样本；缺真值时返回 `None`（不可评），有真值但图缺失时返回 `False`；
- **三级精确比较**（`diff_dag_structures`）：
  1. 节点——逐 `task_id → (actor, action, target)` 映射相等；
  2. 非同步边——`(source, target, relation)` 集合相等；
  3. 同步——三种 sync 写法展开为**无序任务对集合**后比较，tolerance 数值逐对核对；
- **实现**：`gt_dag_structures(case)` 抽取真值结构，`built_dag_structures(graph)` 抽取产出结构，`dag_exact_match(case, graph)` 给出最终 True/False/None。

`exp_01l` 把它作为第 8 项指标记录（`initial_dag_exact` / `final_dag_exact`）。同一比较核心也驱动离线审计 CLI：

```powershell
# 重放某次 run 的 final_code/，逐样本与 master 真值精确 diff
conda run -n llm python -m tools.dataset.diff_run_vs_groundtruth `
    --run-dir out/phase1_generation/<run>/exp_01l_standard_nl_to_gcjp_with_repair `
    --dataset datasets/v2/phase1_master_cases.jsonl
```

适用于审计 dag_exact 指标上线之前产出的历史 run，或离线复核任意一次 run。

## 5. 相关测试

| 测试 | 覆盖 |
|------|------|
| `tests/test_sync_equivalence_scoring.py` | 三种 sync 写法互认、非同步类型仍严格、prompt 规范化、修复触发门控、qwen3-max 两例回归 |
| `tests/test_dag_exact_match.py` | 精确匹配识别（含跨 sync 写法）、各类结构缺陷（错端点/漏边/多边/错属性/tolerance 不符）必判 False、缺真值返回 None |
