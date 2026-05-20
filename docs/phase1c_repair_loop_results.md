# Phase 1C 闭环修复实验结果

本文记录 Phase 1A/1B failure seed 生成失败样本，并由 Phase 1C 自动修复闭环进行回放修复的实验结果。主证据目录采用隔离重跑产物，避免旧运行或中断运行覆盖 `metrics.json`。

## 实验目标

1. 验证 1A/1B 真实 LLM 生成失败报告能被 Phase 1C 读取。
2. 验证 Phase 1C 能利用 `VerificationReport` 中的结构化诊断修复坏 GCJP 代码。
3. 验证修复后代码满足 `expected_result` 与 `expected_patterns`，包括 `unsat` 目标。

## 数据集与命令

Failure seed 数据集：

```text
datasets/phase1_failure_seed_standard_nl_cases.jsonl
datasets/phase1_failure_seed_structured_cases.jsonl
```

1B standard NL 失败样本生成：

```powershell
python -m experiments.exp_01b_standard_nl_to_gcjp --local-provider claude --dataset datasets\phase1_failure_seed_standard_nl_cases.jsonl --output-dir out\phase1_failure_seed_runs --max-tokens 280 --retry-attempts 0
```

1B -> 1C 修复主证据目录：

```powershell
python -m experiments.exp_01c_repair_loop --local-provider claude --source-report-dir out\phase1_failure_seed_runs\exp_01b_standard_nl_to_gcjp\reports --output-dir out\phase1_failure_seed_repairs_verify --max-repair-rounds 2 --max-tokens 1800 --retry-attempts 0
```

1A structured 失败样本生成：

```powershell
python -m experiments.exp_01a_structured_to_gcjp --local-provider claude --dataset datasets\phase1_failure_seed_structured_cases.jsonl --output-dir out\phase1_failure_seed_structured_runs --max-tokens 280 --retry-attempts 0
```

1A -> 1C 修复主证据目录：

```powershell
python -m experiments.exp_01c_repair_loop --local-provider claude --source-report-dir out\phase1_failure_seed_structured_runs\exp_01a_structured_to_gcjp\reports --output-dir out\phase1_failure_seed_structured_repairs --max-repair-rounds 2 --max-tokens 1800 --retry-attempts 0
```

## 失败样本生成结果

1B standard NL failure seed：

```json
{
  "syntax_extract_rate": 1.0,
  "safety_pass_rate": 0.0,
  "execution_success_rate": 0.0,
  "first_pass_rate": 0.0
}
```

1A structured failure seed：

```json
{
  "syntax_extract_rate": 1.0,
  "safety_pass_rate": 0.0,
  "execution_success_rate": 0.0,
  "first_pass_rate": 0.0
}
```

两组 seed 均产生了真实 LLM 输出的可抽取坏代码：

```text
extract=True
execution_error_type=SAFETY_CHECK_FAILED
first_pass=False
```

这些失败主要由低 `--max-tokens 280` 触发，常见表现为代码截断导致的 `SYNTAX_ERROR`，但仍保留 `from gcjp.mission_graph import TaskGraphBuilder`，因此可被 1C 的 `--source-report-dir` 路径读取和修复。

## Phase 1C 修复结果

### 1B Standard NL -> 1C

主证据目录：

```text
out\phase1_failure_seed_repairs_verify\exp_01c_repair_loop
```

汇总指标：

```json
{
  "initial_pass_rate": 0.0,
  "repair_attempt_rate": 1.0,
  "repair_success_rate": 1.0,
  "final_pass_rate": 1.0,
  "avg_repair_rounds": 1.3333333333333333
}
```

Case 结果：

| repair sample | 初始错误 | 修复轮数 | 最终结果 |
| --- | --- | ---: | --- |
| `repair_1b_failseed_001_mixed_constraints_sat` | `SAFETY_CHECK_FAILED` | 2 | pass |
| `repair_1b_failseed_002_capability_physical_unsat` | `SAFETY_CHECK_FAILED` | 1 | pass |
| `repair_1b_failseed_003_barrier_group_sync_resource_sat` | `SAFETY_CHECK_FAILED` | 1 | pass |

### 1A Structured -> 1C

主证据目录：

```text
out\phase1_failure_seed_structured_repairs\exp_01c_repair_loop
```

汇总指标：

```json
{
  "initial_pass_rate": 0.0,
  "repair_attempt_rate": 1.0,
  "repair_success_rate": 1.0,
  "final_pass_rate": 1.0,
  "avg_repair_rounds": 1.3333333333333333
}
```

Case 结果：

| repair sample | 初始错误 | 修复轮数 | 最终结果 |
| --- | --- | ---: | --- |
| `repair_1a_failseed_001_mixed_constraints_sat` | `SAFETY_CHECK_FAILED` | 2 | pass |
| `repair_1a_failseed_002_capability_physical_unsat` | `SAFETY_CHECK_FAILED` | 1 | pass |
| `repair_1a_failseed_003_barrier_group_sync_resource_sat` | `SAFETY_CHECK_FAILED` | 1 | pass |

## 一致性校验工具

新增工具：

```text
tools/recompute_phase1c_metrics.py
```

用法：

```powershell
python tools\recompute_phase1c_metrics.py out\phase1_failure_seed_repairs_verify\exp_01c_repair_loop\reports --write out\phase1_failure_seed_repairs_verify\exp_01c_repair_loop\metrics.recomputed.json
python tools\recompute_phase1c_metrics.py out\phase1_failure_seed_structured_repairs\exp_01c_repair_loop\reports --write out\phase1_failure_seed_structured_repairs\exp_01c_repair_loop\metrics.recomputed.json
```

校验结果：

```text
out\phase1_failure_seed_repairs_verify\exp_01c_repair_loop: metrics == recomputed
out\phase1_failure_seed_structured_repairs\exp_01c_repair_loop: metrics == recomputed
```

因此新的主证据目录不存在旧目录中 `metrics.json` 与逐 case report 不一致的问题。

## 关键观察

1. 1A/1B failure seed 都成功制造了真实可修复坏代码：`syntax_extract_rate=1.0`，但 `safety_pass_rate=0.0`。
2. Phase 1C 能从 Layer 1 结构化诊断恢复截断代码，并补全任务、依赖和约束。
3. 两组实验中各有一个 case 需要 2 轮修复，体现了“修复 -> 验证 -> 新报告 -> 再修复”的闭环价值。
4. `capability_physical_unsat` case 的最终 Layer 3 保持 `z3_result=unsat`，且 unsat core 包含 `capability_t2_fleet4_jam_site_q`，说明修复没有通过删除约束强行转成 SAT。

## 与固定坏例 Baseline 的关系

固定坏例 baseline：

```text
datasets/phase1_repair_cases.jsonl
5/5 repaired
repair_success_rate = 1.0
avg_repair_rounds = 1.0
```

真实 failure seed 回放：

```text
1B standard NL failure seed: 3/3 repaired, avg_repair_rounds = 1.3333
1A structured failure seed: 3/3 repaired, avg_repair_rounds = 1.3333
```

结论：固定坏例验证了修复器对典型 API/语法错误的可控修复能力；真实 failure seed 验证了 Phase 1C 可以接续 1A/1B 实际 LLM 生成失败报告，并通过闭环验证恢复可验证 GCJP 代码。

## 历史核查备注

旧目录曾出现 `metrics.json` 与逐 case report 不一致：

```text
out\phase1_failure_seed_repairs\exp_01c_repair_loop
```

该目录中逐 case report 显示 3/3 通过，但 `metrics.json` 曾保留 2/3 的旧状态。已通过新增反算工具确认逐 case report 为 3/3，并使用新的隔离目录 `out\phase1_failure_seed_repairs_verify` 作为主证据。

## 结论

Phase 1C 已在固定坏例、1B standard NL failure seed、1A structured failure seed 三组实验上验证闭环修复能力。当前最强证据链为：

```text
Phase 1A/1B 真实失败报告
  -> 提取 broken_code 与 VerificationReport
  -> RepairAgent 构造修复 prompt
  -> LLM 生成 repaired_code
  -> execute_gcjp_code + VerificationPipeline 重新验证
  -> 未达标则继续下一轮
  -> 最终满足 expected_result 与 expected_patterns
```

两组真实 failure seed 共 6 条样本全部修复成功，平均修复轮数均为 1.33，说明该闭环机制具备从真实 LLM 生成失败中恢复可验证 GCJP 代码的能力。
