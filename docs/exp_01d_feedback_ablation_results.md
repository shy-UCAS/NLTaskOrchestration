# Phase 1D 修复反馈消融实验结果

> 日期：2026-05-27
>
> 实验入口：`experiments/exp_01d_repair_feedback_ablation.py`
>
> 数据来源：Phase 1E 模拟自然失败 → Phase 1C 修复闭环

---

## 1. 实验目标

验证 `VerificationReport` 中不同信息通道对 LLM 修复成功率的贡献，量化以下问题：

- 完整验证报告 vs 空报告，修复率差异多大？
- 任务语义上下文（task oracle）vs 验证诊断报告，哪个更关键？
- 压缩版错误摘要能否替代完整报告？
- 仅给 broken code 时，LLM 自身纠错能力边界在哪？

## 2. 实验设置

### 2.1 输入数据

Phase 1E 生成的 11 条模拟自然失败样本（`simulated_valid_failure_rate = 1.0`），覆盖 Layer 1 执行失败、Layer 2 图结构违规、Layer 3 Z3 约束不满足等多种错误类型。

### 2.2 Feedback Mode 定义

实验共定义 8 种 feedback mode，strict 消融使用其中 6 种：

| Mode | 传入修复 prompt 的内容 | 信息维度 |
|------|----------------------|---------|
| `full_report` | 完整 VerificationReport + broken code + task payload + bug spec | 全信息基线 |
| `no_report` | 空 `{}` + broken code + task payload + bug spec | 去报告，保留 oracle |
| `no_report_no_bug_spec` | 空 `{}` + broken code + task payload（无 bug spec） | 去报告去 bug 标注 |
| `task_only_no_report` | 空 `{}` + task payload + expected result/patterns（无 broken code 上下文） | 仅任务语义 |
| `report_only_no_oracle` | VerificationReport + broken code（无 task/bug oracle） | 仅诊断报告 |
| `broken_only` | 仅 broken code | 最小信息 |
| `layer1_only` | 仅 Layer 1 诊断（语法/安全/执行错误） | 浅层诊断 |
| `error_summary_only` | 压缩错误摘要（首个失败层、错误类型、行号、Z3 结果、unsat core、归因） | 压缩诊断 |

### 2.3 实验参数

- 模型：Claude Opus 4.6（via uuapi.net Anthropic Messages 协议）
- `max_repair_rounds = 2`
- `max_tokens = 1800`
- 每个 mode 独立跑完全部 11 条样本

## 3. Strict 消融结果

### 3.1 主结果表

主证据目录：`out/phase1_feedback_ablation_manual/simulated_strict/`

| feedback_mode | repair_success_rate | final_pass_rate | avg_repair_rounds |
|---|---:|---:|---:|
| `full_report` | **1.0000** | **1.0000** | 1.0000 |
| `no_report` | **1.0000** | **1.0000** | 1.0909 |
| `no_report_no_bug_spec` | **1.0000** | **1.0000** | 1.0909 |
| `task_only_no_report` | 0.8182 | 0.8182 | 1.1818 |
| `report_only_no_oracle` | 0.4545 | 0.4545 | 1.6364 |
| `broken_only` | 0.3636 | 0.3636 | 1.6364 |

### 3.2 早期 failure seed 消融结果（对照）

数据来源：`out/phase1_feedback_ablation/standard_nl/` 和 `out/phase1_feedback_ablation/structured/`

| feedback_mode | 1B standard NL repair_success | 1A structured repair_success |
|---|---:|---:|
| `full_report` | 1.0000 | 1.0000 |
| `no_report` | 0.6667 | 0.6667 |
| `layer1_only` | 0.6667 | 0.6667 |
| `error_summary_only` | 1.0000 | 1.0000 |

## 4. 关键发现

### 4.1 信息通道排序

从 strict 消融结果可以得到如下排序：

```
full_report = no_report = no_report_no_bug_spec > task_only_no_report > report_only_no_oracle > broken_only
      1.0          1.0           1.0                    0.8182              0.4545            0.3636
```

### 4.2 核心结论

1. **任务语义上下文（task oracle）是修复成功的关键因素**。移除 task oracle 后（`report_only_no_oracle`），修复率从 1.0 骤降至 0.4545，下降幅度远大于移除验证报告。

2. **验证报告本身不是必需的**。`no_report`（空报告 + task oracle）仍然达到 1.0，说明当 LLM 拥有完整任务语义和 expected patterns 时，即使没有具体错误诊断，也能定位并修复大多数问题。

3. **bug spec 标注无额外贡献**。`no_report_no_bug_spec` 与 `no_report` 结果完全一致（均为 1.0），说明 LLM 不依赖显式 bug 类型标注。

4. **broken code 自身修复能力有限**。`broken_only` 仅 0.3636，LLM 在无任何上下文时只能修复语法/API 签名等表面错误，无法处理语义类错误（如约束遗漏、关系缺失）。

5. **压缩错误摘要可替代完整报告**。早期 failure seed 实验中，`error_summary_only` 达到与 `full_report` 相同的 1.0，而 `layer1_only` 仅 0.6667。这说明跨层诊断摘要是有效的，但仅靠浅层诊断不够。

### 4.3 对系统设计的启示

- 修复 prompt 中**必须包含任务语义上下文**（expected result、expected patterns、task payload）。
- 验证报告可以用压缩摘要替代，无需传递完整 JSON 报告，有助于节省 token。
- 后续多 Agent 架构中，如果修复 Agent 无法获取原始任务描述，修复能力将显著下降。

## 5. 数据一致性说明

所有 mode 的 `metrics.json` 已通过 `tools/recompute_phase1c_metrics.py` 与逐 case report 交叉校验，确认指标一致。
