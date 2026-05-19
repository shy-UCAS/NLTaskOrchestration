# 阶段 1 baseline 报告

日期：2026-05-19

## 目标

记录阶段 1A/1B 的可复现实验基线：

- 1A：结构化任务描述 JSON -> GCJP v1 Python 代码
- 1B：标准无歧义自然语言 -> GCJP v1 Python 代码

实验输出目录为 `out/phase1_generation/`，该目录是本地生成产物，不纳入版本库。

## Provider 摘要

本次 baseline 使用本地 Claude provider 配置，并通过 Anthropic Messages 协议访问外部兼容服务。

脱敏摘要：

```text
provider_name: local_claude
protocol: anthropic_messages
base_url: https://uuapi.net
model: claude-opus-4-6
temperature: 0.1
max_tokens: 4096
pre_headers: {}
extra_body: {}
auth_header: bearer
user_agent: claude-cli/2.0.76 (external, cli)
compat_preset: uuapi_anthropic_gateway
retry_attempts: 2
retry_backoff_seconds: 1.0
effective_headers_preview: {
  "Content-Type": "application/json",
  "Authorization": "***",
  "anthropic-version": "2023-06-01",
  "User-Agent": "claude-cli/2.0.76 (external, cli)"
}
api_key_present: True
```

## 结果

### Baseline A：早期 zero-shot

这是阶段 1A/1B 初次全量跑通时的基线，数据集规模尚未扩充。

#### 1A structured JSON -> GCJP

命令：

```powershell
python -m experiments.exp_01a_structured_to_gcjp --local-provider claude
```

结果：

```text
total_cases: 9
syntax_extract_rate: 1.0
safety_pass_rate: 1.0
execution_success_rate: 1.0
builtgraph_success_rate: 1.0
l2_graph_pass_rate: 1.0
l3_expected_result_rate: 1.0
first_pass_rate: 1.0
node_complete_rate: 1.0
edge_complete_rate: 1.0
constraint_complete_rate: 1.0
```

#### 1B standard NL -> GCJP

命令：

```powershell
python -m experiments.exp_01b_standard_nl_to_gcjp --local-provider claude
```

结果：

```text
total_cases: 7
syntax_extract_rate: 1.0
safety_pass_rate: 1.0
execution_success_rate: 1.0
builtgraph_success_rate: 1.0
l2_graph_pass_rate: 1.0
l3_expected_result_rate: 1.0
first_pass_rate: 1.0
node_complete_rate: 1.0
edge_complete_rate: 1.0
constraint_complete_rate: 1.0
```

### Baseline B：扩充版 zero-shot

这是数据集扩充后的默认 prompt 基线。默认 prompt 仍为 zero-shot：

- 1A：`prompts/gcjp_generation_prompt.md`
- 1B：`prompts/standard_nl_to_gcjp_prompt.md`

#### 1A structured JSON -> GCJP

命令：

```powershell
python -m experiments.exp_01a_structured_to_gcjp --local-provider claude
```

结果：

```text
total_cases: 15
syntax_extract_rate: 1.0
safety_pass_rate: 1.0
execution_success_rate: 1.0
builtgraph_success_rate: 1.0
l2_graph_pass_rate: 1.0
l3_expected_result_rate: 1.0
first_pass_rate: 1.0
node_complete_rate: 1.0
edge_complete_rate: 1.0
constraint_complete_rate: 1.0
```

#### 1B standard NL -> GCJP

命令：

```powershell
python -m experiments.exp_01b_standard_nl_to_gcjp --local-provider claude
```

结果：

```text
total_cases: 12
syntax_extract_rate: 1.0
safety_pass_rate: 1.0
execution_success_rate: 1.0
builtgraph_success_rate: 1.0
l2_graph_pass_rate: 1.0
l3_expected_result_rate: 1.0
first_pass_rate: 1.0
node_complete_rate: 1.0
edge_complete_rate: 1.0
constraint_complete_rate: 1.0
```

### Baseline C：扩充版 few-shot 对照

few-shot prompt 初次运行时在 `condition_trigger` 和 `physical_feasibility` 样本上暴露了 API 签名诱导问题：

- 模型把 `condition` 传给了 `add_task(...)`，但真实 API 要求 condition 放在 `add_dependency(..., relation="condition_trigger", condition=...)`。
- 模型把 `actor_speed_kmh` 误写为 `speed_kmh`。

随后已修正 few-shot prompt，增加 condition/physical 示例和参数名约束。修正后完整复跑通过。

#### 1A structured JSON -> GCJP few-shot

命令：

```powershell
python -m experiments.exp_01a_structured_to_gcjp --local-provider claude --prompt prompts/gcjp_generation_prompt_fewshot.md
```

结果：

```text
total_cases: 15
syntax_extract_rate: 1.0
safety_pass_rate: 1.0
execution_success_rate: 1.0
builtgraph_success_rate: 1.0
l2_graph_pass_rate: 1.0
l3_expected_result_rate: 1.0
first_pass_rate: 1.0
node_complete_rate: 1.0
edge_complete_rate: 1.0
constraint_complete_rate: 1.0
```

#### 1B standard NL -> GCJP few-shot

命令：

```powershell
python -m experiments.exp_01b_standard_nl_to_gcjp --local-provider claude --prompt prompts/standard_nl_to_gcjp_prompt_fewshot.md
```

结果：

```text
total_cases: 12
syntax_extract_rate: 1.0
safety_pass_rate: 1.0
execution_success_rate: 1.0
builtgraph_success_rate: 1.0
l2_graph_pass_rate: 1.0
l3_expected_result_rate: 1.0
first_pass_rate: 1.0
node_complete_rate: 1.0
edge_complete_rate: 1.0
constraint_complete_rate: 1.0
```

## 说明

- 本报告只保存脱敏配置和聚合指标，不保存 raw response、API key 或完整请求体。
- 当前 baseline 依赖外部 LLM provider，结果可能受模型版本、网关稳定性和采样策略影响。
- 当前推荐默认使用 zero-shot prompt；few-shot prompt 保留为弱模型、替代 provider 和后续修复闭环的对照工具。
