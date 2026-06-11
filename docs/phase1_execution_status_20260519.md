# 阶段 1 执行状态快照

日期：2026-05-19

本文用于在对话上下文过长或中断时快速恢复阶段 1 的当前状态。它对照原“阶段 1A/1B：多协议 LLM API 接入与 GCJP 生成评测”执行清单，记录已经完成、已验证、尚未完成或需要后续复跑的事项。

## 当前结论

阶段 1A/1B 的主链路已经实现并跑通：

- 1A：结构化任务描述 JSON -> LLM -> GCJP v1 -> VerificationPipeline
- 1B：标准无歧义自然语言 -> LLM -> GCJP v1 -> VerificationPipeline
- 支持 OpenAI-compatible Chat Completions 与 Anthropic Messages 两类协议。
- 支持本地 Codex/Claude provider 配置读取，可复用 CC Switch 或类似工具写入的 provider 参数。
- 支持 Anthropic-style 中转站兼容 preset，例如自动补 `Authorization: Bearer ...` 和 Claude CLI 风格 `User-Agent`。
- 支持最终发送 headers 的脱敏预览，不保存 API key。
- 当前扩充后的 zero-shot live baseline 已由用户手动跑通：
  - 1A：15/15，全指标 1.0
  - 1B：12/12，全指标 1.0
- 修复后的 few-shot live 对照也已由用户手动跑通：
  - 1A：15/15，全指标 1.0
  - 1B：12/12，全指标 1.0
- 阶段 1C 固定坏代码修复闭环已跑通：
  - 5/5 修复成功
  - 平均 1 轮修复

## 已完成部分

### 1. 多协议 LLM Provider 抽象

已完成文件：

- `agents/llm_client.py`
- `configs/llm_providers.example.yaml`
- `tools/get_local_api_config.py`
- `demos/demo_llm_client_smoke.py`

完成内容：

- `LLMProviderConfig`
- `LLMClient.generate(messages)`
- `LLMResponse`
- `protocol=openai_chat`
- `protocol=anthropic_messages`
- OpenAI-compatible `/chat/completions`
- Anthropic Messages `/v1/messages`
- `headers` / `extra_body`
- `auth_header`
- `user_agent`
- `BASE_URL_COMPAT_PRESETS`
- `safe_summary()`
- `effective_headers_preview`
- `model_source=remote|local_config`
- retry 配置：
  - `retry_attempts`
  - `retry_backoff_seconds`
  - `PHASE1_LLM_RETRY_ATTEMPTS`
  - `PHASE1_LLM_RETRY_BACKOFF_SECONDS`

retry 策略：

- 会重试：
  - HTTP 502
  - HTTP 503
  - HTTP 504
  - timeout
  - SSL EOF
  - connection reset / aborted / remote closed 等连接中断
- 不会重试：
  - HTTP 400
  - HTTP 401
  - HTTP 403
  - provider 配置错误

### 2. 配置读取

已完成：

- CLI 参数覆盖
- `PHASE1_LLM_*` 环境变量
- `PHASE1_LLM_CONFIG` + `PHASE1_LLM_PROFILE`
- 原生环境变量兜底：
  - `OPENAI_API_KEY`
  - `OPENAI_BASE_URL`
  - `ANTHROPIC_API_KEY`
  - `ANTHROPIC_BASE_URL`
- 本地 provider：
  - `--local-provider claude`
  - `--local-provider codex`

配置优先级已实现：

```text
CLI 参数 > PHASE1_LLM_CONFIG profile > PHASE1_LLM_* 环境变量 > 协议原生环境变量
```

说明：

- 当前项目不读取或修改 CC Switch GUI 内部配置文件。
- 用户可从 CC Switch 或服务商面板复制 `protocol/base_url/api_key/model` 到环境变量或本地 profile。
- 如果本地 Claude/Codex 配置已经由 CC Switch 写好，可通过 `--local-provider claude|codex` 复用。

### 3. Planner Agent 与代码提取

已完成文件：

- `agents/planner_agent.py`
- `agents/code_extraction.py`

完成内容：

- prompt 渲染
- LLM 调用
- raw response 保存
- extracted code 保存
- provider 摘要保存
- fenced Python code block 提取
- 无 fence 时从 `from gcjp.mission_graph import TaskGraphBuilder` 截取
- 无合法 GCJP 入口时返回提取失败

### 4. Prompt

已完成文件：

- `prompts/gcjp_generation_prompt.md`
- `prompts/standard_nl_to_gcjp_prompt.md`
- `prompts/gcjp_generation_prompt_fewshot.md`
- `prompts/standard_nl_to_gcjp_prompt_fewshot.md`

完成内容：

- zero-shot baseline prompt
- few-shot 对照 prompt
- 明确只输出 GCJP Python 代码
- 禁止解释文字
- 禁止非白名单 API
- 强约束：
  - `TaskGraphBuilder(segment_id=..., assigned_actors=...)`
  - `g.declare_segment_meta(assumed_conditions=...)`
  - `g.add_dependency(..., relation=...)`
  - 无能力要求时必须写 `required_capability=[]`
  - 最终必须 `built = g.build()`

说明：

- 默认实验仍使用 zero-shot prompt。
- few-shot prompt 只有显式传 `--prompt` 才会使用。
- few-shot prompt 已补充 `condition_trigger` 与 `physical_feasibility` 示例，避免模型误用 `add_task(condition=...)` 或 `speed_kmh=...`。

### 5. Dataset

已完成文件：

- `datasets/phase1_structured_cases.jsonl`
- `datasets/phase1_standard_nl_cases.jsonl`

当前规模：

- 1A structured cases：15 条
- 1B standard NL cases：12 条

新增覆盖：

- resource + deadline SAT
- group_sync + deadline SAT
- condition + resource UNSAT
- capability + sequence UNSAT
- physical_feasibility + resource SAT
- no capability omitted in NL

每条样本均包含：

- `expected_result`
- `expected_patterns`

### 6. Experiments

已完成文件：

- `experiments/phase1_common.py`
- `experiments/exp_01a_structured_to_gcjp.py`
- `experiments/exp_01b_standard_nl_to_gcjp.py`

完成内容：

- `--local-provider`
- `--provider-profile`
- `--config`
- `--limit`
- `--output-dir`
- `--prompt`
- `--auth-header`
- `--user-agent`
- `--disable-compat-preset`
- `--retry-attempts`
- `--retry-backoff-seconds`
- provider 脱敏摘要打印
- raw response 输出
- extracted code 输出
- report JSON 输出
- metrics JSON 输出
- 失败摘要增强：
  - `execution_error_type`
  - `error`
  - L1 `gcjp_lineno`
  - L1 `api_error.code`
  - extraction error

输出目录：

```text
out/phase1_generation/
```

该目录是本地产物，不应提交入库。

### 7. Metrics

已实现聚合指标：

- `syntax_extract_rate`
- `safety_pass_rate`
- `execution_success_rate`
- `builtgraph_success_rate`
- `l2_graph_pass_rate`
- `l3_expected_result_rate`
- `first_pass_rate`
- `node_complete_rate`
- `edge_complete_rate`
- `constraint_complete_rate`
- `error_type_distribution`

说明：

- 原计划里的 `node_completeness_rate` / `edge_completeness_rate` / `constraint_completeness_rate` 在实现中命名为：
  - `node_complete_rate`
  - `edge_complete_rate`
  - `constraint_complete_rate`

### 8. UNSAT 样本输出

已由 `VerificationReport.to_dict()` 输出：

- `unsat_core`
- `unsat_core_semantic`
- `unsat_core_framework`
- `attribution`

相关回归：

- `demos/demo_z3_unsat_core_filtering.py`

### 9. 非 live 测试

已完成文件：

- `demos/demo_phase1_nonlive_regression.py`

覆盖内容：

- `effective_headers_preview` 脱敏
- `BASE_URL_COMPAT_PRESETS` 命中
- OpenAI effective headers
- Anthropic effective headers
- `extract_gcjp_code` fenced/import-anchor/失败路径
- prompt 中存在 `TaskGraphBuilder(segment_id=...)`
- prompt 中存在 `required_capability=[]`
- retry 策略

### 10. 文档

已完成文件：

- `README.md`
- `docs/phase1_baseline_report.md`
- `docs/phase1_execution_status_20260519.md`

README 已更新：

- 当前阶段状态
- 数据流图
- 工程结构
- 阶段 1 LLM 接入与实验章节
- provider 配置方式
- local provider / CC Switch 兼容策略
- smoke demo
- 1A/1B 运行命令
- few-shot 对照命令
- 输出目录说明
- 已验证能力
- 当前已知边界
- 路线图

### 11. 阶段 1C 修复闭环

已完成文件：

- `agents/repair_agent.py`
- `prompts/gcjp_repair_prompt.md`
- `experiments/exp_01c_repair_loop.py`
- `datasets/phase1_repair_cases.jsonl`

完成内容：

- 从坏代码生成初始 `VerificationReport.to_dict()`。
- 把坏代码、验证报告、case payload 和 prompt context 发送给 LLM。
- 提取修复后的 GCJP Python 代码。
- 最多执行 `max_repair_rounds=2` 轮修复。
- 每轮修复后重新执行 `VerificationPipeline.verify_gcjp_code()`。
- 输出 initial code、final code、repair attempts、per-case report 和 metrics。
- 支持固定坏代码数据集。
- 支持 `--source-report-dir` 从 1A/1B 已生成 report 目录中读取失败样本进行修复。

首版固定坏代码样本覆盖：

- `TaskGraphBuilder()` 缺少 `segment_id/assigned_actors`
- `add_constraint(...)` 虚构 API
- `add_task(condition=...)`
- `add_physical_feasibility_constraint(speed_kmh=...)`
- 漏写 `built = g.build()`

## 已跑通的测试

### 非 live 测试

已通过：

```powershell
python -m py_compile agents\llm_client.py experiments\phase1_common.py experiments\exp_01a_structured_to_gcjp.py experiments\exp_01b_standard_nl_to_gcjp.py demos\demo_phase1_nonlive_regression.py demos\demo_llm_client_smoke.py
python -m demos.demo_phase1_nonlive_regression
python -m demos.demo_12_gcjp_structured_feedback
python -m demos.demo_z3_unsat_core_filtering
```

### live zero-shot baseline

用户已手动跑通：

```powershell
python -m experiments.exp_01a_structured_to_gcjp --local-provider claude
python -m experiments.exp_01b_standard_nl_to_gcjp --local-provider claude
```

结果：

```text
1A structured JSON -> GCJP: 15/15
1B standard NL -> GCJP: 12/12
所有聚合指标均为 1.0
```

使用配置摘要：

```text
provider_name: local_claude
protocol: anthropic_messages
base_url: https://uuapi.net
model: claude-opus-4-6
auth_header: bearer
user_agent: claude-cli/2.0.76 (external, cli)
compat_preset: uuapi_anthropic_gateway
retry_attempts: 2
retry_backoff_seconds: 1.0
```

### live few-shot baseline

few-shot prompt 初次运行时暴露了两个 prompt 诱导问题：

- `condition` 被错误传给 `add_task(...)`。
- `actor_speed_kmh` 被错误写成 `speed_kmh`。

已修正 prompt 后，用户手动完整复跑通过：

```powershell
python -m experiments.exp_01a_structured_to_gcjp --local-provider claude --prompt prompts/gcjp_generation_prompt_fewshot.md
python -m experiments.exp_01b_standard_nl_to_gcjp --local-provider claude --prompt prompts/standard_nl_to_gcjp_prompt_fewshot.md
```

结果：

```text
1A structured JSON -> GCJP few-shot: 15/15
1B standard NL -> GCJP few-shot: 12/12
所有聚合指标均为 1.0
```

### live repair-loop baseline

阶段 1C 固定坏代码修复闭环已完整复跑通过：

```powershell
python -m experiments.exp_01c_repair_loop --local-provider claude --dataset datasets/phase1_repair_cases.jsonl
```

结果：

```text
repair cases: 5/5
initial_pass_rate: 0.0
repair_attempt_rate: 1.0
repair_success_rate: 1.0
final_pass_rate: 1.0
avg_repair_rounds: 1.0
```

备注：

- 默认沙箱中外部 LLM 请求会触发 WinError 10013 网络权限错误。
- 授权外部 LLM 访问后，`--limit 2` smoke 和完整 5 条样本均通过。

## 对照原执行清单尚未完成或待补充的部分

### 1. OpenAI-compatible live provider 尚未验证

代码支持 `openai_chat`，非 live 测试覆盖了 headers 和配置结构，但当前 live baseline 使用的是 Anthropic Messages provider。

待补充：

- 用一个 OpenAI-compatible provider 跑：

```powershell
python -m demos.demo_llm_client_smoke --protocol openai_chat --base-url <base_url> --api-key <key> --model <model>
python -m experiments.exp_01a_structured_to_gcjp --protocol openai_chat --base-url <base_url> --api-key <key> --model <model> --limit 1
```

注意：

- 不要提交真实 API key。
- 可优先使用 profile 或环境变量方式。

### 2. live smoke 不是本轮自动复跑

用户此前已确认：

```powershell
python -m demos.demo_llm_client_smoke --local-provider claude
```

可以与外部服务正常交流。

本轮实现后没有再次自动跑 smoke，因为用户已经直接跑通 1A/1B live 实验，等价证明阶段 1 调用链路可用。

### 3. Responses API / Gemini 原生协议没有实现

原阶段 1 计划明确说第一版暂不实现：

- OpenAI Responses API
- Gemini 原生 API
- Anthropic tools
- streaming

当前状态符合计划。

### 4. 1A/1B 生成实验的实时联动修复尚未强制接入

当前 1C 已支持固定坏代码数据集，也支持 `--source-report-dir` 修复已有 1A/1B report 目录里的失败样本。

尚未做的是：在 1A/1B 实验运行过程中自动发现失败并立即触发修复。

### 5. metrics 中的失败详情尚未做二级聚合

当前失败详情会打印在单条样本 summary 中，但 metrics 里还没有按以下维度展开统计：

- `api_error.code`
- `gcjp_lineno`
- extraction error
- retry 后最终失败原因

建议后续把这些字段也写入 `metrics.json` 的聚合统计。

## 后续建议清单

1. 用一个 OpenAI-compatible provider 做 live smoke，确认另一条协议链路。
2. 将当前 zero-shot / few-shot 结果作为阶段 1 baseline 固化进提交说明。
3. 把 1C 修复闭环接入 1A/1B 实验运行过程，支持失败后自动修复。
4. 后续把 metrics 失败详情做二级聚合。

## 当前最常用命令

非 live 回归：

```powershell
python -m demos.demo_phase1_nonlive_regression
python -m demos.demo_12_gcjp_structured_feedback
python -m demos.demo_z3_unsat_core_filtering
```

LLM smoke：

```powershell
python -m demos.demo_llm_client_smoke --local-provider claude
```

zero-shot live baseline：

```powershell
python -m experiments.exp_01a_structured_to_gcjp --local-provider claude
python -m experiments.exp_01b_standard_nl_to_gcjp --local-provider claude
```

few-shot live 对照：

```powershell
python -m experiments.exp_01a_structured_to_gcjp --local-provider claude --prompt prompts/gcjp_generation_prompt_fewshot.md
python -m experiments.exp_01b_standard_nl_to_gcjp --local-provider claude --prompt prompts/standard_nl_to_gcjp_prompt_fewshot.md
```

repair-loop live baseline：

```powershell
python -m experiments.exp_01c_repair_loop --local-provider claude --dataset datasets/phase1_repair_cases.jsonl
```
