# NLTaskOrchestration

> Natural-language-driven and verifiable task graph construction for UAV swarm missions.

面向无人机集群任务规划场景，将自然语言作战指令转化为结构化任务图，并通过图结构检查与 SMT/Z3 约束验证提升任务编排的可靠性。

当前已完成 **Layer 1 确定性底座** 与 **阶段 1 LLM 生成评测 baseline**：标准化任务计划 JSON / 标准无歧义自然语言 → LLM 生成 GCJP v1 代码 → 安全检查 → 受限执行构图 → 图结构与 Z3 约束验证 → 指标与报告输出。阶段 1 已接入 OpenAI-compatible Chat Completions 与 Anthropic Messages 两类 provider，并支持本地 Codex/Claude 配置、CC Switch 风格中转站参数、脱敏 headers 预览和实验输出归档。

---

## 1. 数据流

```text
自然语言指令 / 结构化任务描述 JSON         标准化任务计划 JSON
    │ LLMClient + PlannerAgent              │ schema 校验
    │ code_extraction                       ▼
    ▼                                  task_plan_loader.py
GCJP v1 代码字符串                         │
    │ safety_checker                        │
    │ code_executor (沙箱)                  │
    ▼                                      ▼
TaskGraphBuilder ◄─────────────────────────┘
    ▼
BuiltGraph
    ▼
VerificationPipeline  (L1 安全/执行 → L2 图结构 → L3 Z3 → L4 语义)
    ▼
VerificationReport
    schedule / unsat_core / attribution
    structured_violations / api_error / source_context / traceback_text
```

关键 demo：

| Demo | 角色 |
| --- | --- |
| `demo_01` / `demo_02` / `demo_03` | JSON → 任务图 → 验证（SAT / 资源 UNSAT / 设施 UTM 场景） |
| `demo_06` / `demo_08`-`11` | 手写 GCJP v1 代码字符串端到端验证（含并行、同步/屏障、条件触发 UNSAT、能力不匹配 UNSAT） |
| `demo_07` | L1 失败路径诊断回归（15 个失败 case） |
| `demo_12` | 结构化反馈契约 gate（5 个 case，含建议文案反向断言） |
| `demo_llm_client_smoke` | LLM provider 配置读取与远程连通性 smoke test |
| `demo_phase1_nonlive_regression` | 阶段 1 非 live 回归，不调用外部 LLM |

---

## 2. 工程结构

```text
NLTaskOrchestration/
├── README.md
│
├── configs/
│   ├── action_templates.yaml          # 9 种动作模板
│   ├── capability_model.yaml          # 4 个异构集群能力与资源上限
│   ├── environment_config.yaml        # scenario_demo / scenario_simple
│   ├── environment_facilities.yaml    # 设施地图 UTM 坐标场景
│   └── llm_providers.example.yaml     # 阶段 1 LLM provider profile 示例
│
├── agents/
│   ├── llm_client.py                  # OpenAI/Anthropic 多协议 LLM client
│   ├── planner_agent.py               # prompt 渲染 + LLM 调用 + 代码提取
│   └── code_extraction.py             # 从模型响应提取 GCJP Python 代码
│
├── prompts/
│   ├── gcjp_generation_prompt.md
│   ├── standard_nl_to_gcjp_prompt.md
│   ├── gcjp_generation_prompt_fewshot.md
│   └── standard_nl_to_gcjp_prompt_fewshot.md
│
├── datasets/
│   ├── phase1_structured_cases.jsonl  # 1A 结构化输入评测集
│   └── phase1_standard_nl_cases.jsonl # 1B 标准自然语言评测集
│
├── experiments/
│   ├── phase1_common.py
│   ├── exp_01a_structured_to_gcjp.py
│   └── exp_01b_standard_nl_to_gcjp.py
│
├── schemas/
│   └── task_plan_schema.json          # 标准化任务计划 JSON Schema
│
├── gcjp/
│   ├── api_spec.py                    # GCJP v1 受限 API 规范（白名单 single source of truth）
│   ├── mission_graph.py               # TaskGraphBuilder / BuiltGraph（API 实现）
│   ├── errors.py                      # GCJPAPIError：API 层结构化错误
│   ├── safety_checker.py              # AST 白名单校验 + 结构化 SafetyViolation
│   ├── code_executor.py               # GCJP 代码字符串 → BuiltGraph 受限执行
│   ├── constraint_templates.py        # Z3 约束构建与求解
│   ├── task_plan_loader.py            # 标准化 JSON → BuiltGraph
│   ├── environment_model.py           # 场景引用校验与坐标距离
│   └── debug_logger.py                # 全局可控调试日志
│
├── verifier/
│   └── pipeline.py                    # 四层递进验证管道
│
├── demos/
│   ├── demo_01_simple_task_plan.json + demo_01_build_graph_from_json.py
│   ├── demo_02_resource_unsat_task_plan.json + demo_02_build_resource_unsat_from_json.py
│   ├── demo_03_facilities_task_plan.json + demo_03_build_facilities_from_json.py
│   ├── demo_01_simple_solo.py / demo_05_unsat_example.py
│   ├── demo_06_fixed_gcjp_api.py
│   ├── demo_07_gcjp_code_executor_failures.py
│   ├── demo_08_parallel_tasks_gcjp.py / demo_09_sync_barrier_gcjp.py
│   ├── demo_10_condition_resource_conflict_gcjp.py
│   ├── demo_11_capability_mismatch_gcjp.py
│   ├── demo_12_gcjp_structured_feedback.py
│   ├── demo_llm_client_smoke.py
│   └── demo_phase1_nonlive_regression.py
│
├── tools/                             # JSON / 配置校验 + UTM 坐标转换脚本
│   └── get_local_api_config.py        # 读取本地 Codex/Claude provider 配置
│
├── docs/
│   └── phase1_baseline_report.md      # 阶段 1A/1B 脱敏 baseline 摘要
└── research/                          # 研究计划文档
```

---

## 3. 核心模块

### 3.1 `configs/` — 配置层

`action_templates.yaml`（动作模板库，由 `load_action_defaults_from_yaml()` 读取）：

| 动作 | 能力要求 | 最短耗时 | 能量 (kWh) | 弹药 |
| --- | --- | --- | --- | --- |
| `reconnaissance` | recon_capable | 2.0 | 3.0 | 0 |
| `strike` | strike_capable | 1.5 | 5.0 | 1 |
| `breakthrough` | strike_capable | 2.0 | 8.0 | 1 |
| `fly_to` | — | 动态 | 动态 | 0 |
| `rendezvous` | — | 0.5 | 1.0 | 0 |
| `standby` | — | 0.5 | 0.5 | 0 |
| `jam` | jamming_capable | 3.0 | 10.0 | 0 |
| `intercept` | strike_capable | 1.0 | 6.0 | 1 |
| `track` | recon_capable | 2.0 | 4.0 | 0 |

`capability_model.yaml`（4 个异构集群，由 `load_capability_model_from_yaml()` 读取）：

| 集群 | 能力 | 弹药 | 能量 (kWh) | 速度 (km/h) |
| --- | --- | --- | --- | --- |
| fleet_1 | recon, strike | 4 | 50 | 80 |
| fleet_2 | strike | 6 | 60 | 100 |
| fleet_3 | jam, recon | 1 | 80 | 70 |
| fleet_4 | recon, strike | 5 | 45 | 130 |

`environment_config.yaml` 与 `environment_facilities.yaml` 提供初始位置、目标点、威胁区、禁飞区等空间信息；后者从 UTM 坐标转换的真实设施地图（以 hq_1 为原点）。

---

### 3.2 `schemas/task_plan_schema.json`

标准化任务计划 JSON 的格式契约。字段：`scenario_id`、`participants`、`tasks`（含 task_id / actor / action / target / condition / time_window / expected_output）、`relations`（支持 sequence、sync、condition_trigger、barrier、handoff 等 8 种）、`ambiguities`、`missing_fields`。

---

### 3.3 `gcjp/task_plan_loader.py`

JSON 路径入口。核心函数：

| 函数 | 功能 |
| --- | --- |
| `load_task_plan()` | 加载 JSON + 可选 Schema 校验 |
| `load_action_defaults_from_yaml()` | 读取动作参数 |
| `load_capability_model_from_yaml()` | 读取集群能力与资源上限 |
| `build_graph_from_task_plan()` | dict → BuiltGraph |
| `build_graph_from_task_plan_file()` | 一站式（文件路径 → BuiltGraph，含环境引用校验） |

当 `build_graph_from_task_plan_file()` 传入 `environment_config_path` 时，自动调用 `validate_task_plan_environment()` 校验 scenario_id / actor / target 引用一致性。

---

### 3.4 `gcjp/environment_model.py`

轻量级环境引用层：

| 函数 | 功能 |
| --- | --- |
| `load_environment_config()` | 加载环境 YAML |
| `get_scenario()` / `resolve_location()` | 场景查找与位置解析 |
| `euclidean_distance_km()` / `estimate_straight_line_metrics()` | 距离与耗时/能耗估算 |
| `validate_environment_refs()` / `validate_task_plan_environment()` | 引用校验 |

当前只做引用校验，不做禁飞区绕行 / 威胁区惩罚等复杂可行性分析。

---

### 3.5 `gcjp/mission_graph.py` — GCJP v1 受限 API

核心类：

- `TaskNode` —— duration_lb/ub、energy_cost、ammo_cost、time_window 等字段；
- `Constraint` —— constraint_type 与 params；
- `TaskGraphBuilder` —— LLM 可调用的白名单方法（见 `api_spec.ALLOWED_BUILDER_METHODS`）；
- `BuiltGraph` —— 只读任务图，含 NetworkX DAG 与约束列表。

GCJP v1 白名单（共 13 个，集中在 `gcjp/api_spec.py`）：

```text
declare_segment_meta              add_time_order_constraint
add_task                          add_time_window_constraint
add_dependency                    add_sync_constraint
declare_resource_state            add_group_sync_constraint
declare_interface_fulfillment     add_resource_constraint
build                             add_capability_constraint
add_physical_feasibility_constraint
```

GCJP v1 关系语义边界：

| relation | 当前语义 |
|---|---|
| `sequence` | 自动生成 `time_order`：`end(source) <= start(target)` |
| `condition_trigger` | 自动生成 `time_order`，条件文本作为边属性 |
| `barrier` / `join` / `handoff` | 自动生成 `time_order`，用于汇聚或交接前置约束 |
| `sync` | 自动生成 `sync`：`abs(start_i - start_j) <= tolerance` |
| `parallel` | 语义边，不生成 Z3 时间约束；参与图结构分析 |

---

### 3.6 `gcjp/constraint_templates.py`

`BuiltGraph` 约束 → Z3 表达式 → 求解：

| 约束类型 | 作用 |
|---|---|
| `time_order` | `end(before) <= start(after)` |
| `duration` | `lb <= dur <= ub` |
| `time_window` | 时间窗与截止时间 |
| `sync` | `\|start_i - start_j\| <= tolerance` |
| `group_sync` | 组内任意两个任务 start/end 时间差不超过 `tolerance` |
| `resource` | `sum(cost) <= max` |
| `capability` | `required ⊆ actor_caps` |
| `physical_feasibility` | `dur >= dist / speed × 60` |

UNSAT 时通过 `assert_and_track` 提取冲突约束标签，供归因分析使用。

---

### 3.7 `gcjp/errors.py` — GCJP API 层结构化错误

`GCJPAPIError(ValueError)` 携带七字段：`code` / `message` / `api` / `actual` / `expected` / `hint` / `details`，并提供 `to_dict()` 序列化为 LLM 反馈 JSON。继承 `ValueError` 保证所有 `except ValueError` 调用零改动。

`mission_graph.py` 的 25 处错误（24 `ValueError` + 1 `RuntimeError`）已全部升级为 `GCJPAPIError`，分布于 `add_task` / `add_dependency` / `add_*_constraint` / `declare_interface_fulfillment` / `build` 等 API。典型错误码示例：`DUPLICATE_TASK_ID` / `ACTOR_NOT_ASSIGNED` / `ILLEGAL_METADATA_KEY` / `INVALID_RELATION` / `MISSING_SOURCE_TASK` / `INVALID_RESOURCE_TYPE` / `EMPTY_GRAPH`。

---

### 3.8 `gcjp/safety_checker.py` — AST 白名单 + 结构化违规

静态分析 GCJP 代码字符串，输出 `SafetyCheckResult`，含：

- `violations: list[str]` —— 兼容旧调用方的字符串形式；
- `structured_violations: list[SafetyViolation]` —— 每项含 `code` / `message` / `lineno` / `col_offset` / `source_line` / `suggestion`，可直接送入 LLM 反馈。

违规码：`FORBIDDEN_PATTERN` / `SYNTAX_ERROR` / `DISALLOWED_IMPORT` / `DISALLOWED_IMPORT_FROM` / `DISALLOWED_BUILDER_METHOD` / `INVALID_METHOD_CALLER` / `DISALLOWED_BUILTIN_CALL` / `FORBIDDEN_DUNDER_ATTR` / `FORBIDDEN_SYNTAX`。其中 `DISALLOWED_BUILDER_METHOD` 的修复建议在运行时从 `ALLOWED_BUILDER_METHODS` 动态拼接，杜绝文案漂移。

---

### 3.9 `gcjp/code_executor.py`

受限沙箱：仅允许导入 `gcjp.mission_graph.TaskGraphBuilder`，要求最终变量 `built` 是 `BuiltGraph`。

```text
GCJP 代码字符串 → safety_checker → 受限执行 → BuiltGraph
```

`GCJPExecutionResult` 字段：

| 字段 | 说明 |
| --- | --- |
| `passed` / `error_type` / `error_msg` | 顶层结果 |
| `graph` | 成功时返回的 `BuiltGraph` |
| `safety` | `SafetyCheckResult`，含 `structured_violations` |
| `traceback_text` | 异常时完整 traceback |
| `gcjp_lineno` | `<gcjp_code>` 中出错行号 |
| `source_context` | 出错行 ±2 行带 `>` 标记的源码片段 |
| `api_error` | 若异常为 `GCJPAPIError` 则填其 `to_dict()` 结果 |
| `locals_snapshot` | 沙箱内已成功赋值的局部变量 |

稳定的 `error_type` 常量：`SUCCESS` / `SAFETY_CHECK_FAILED` / `COMPILE_FAILED` / `EXECUTION_FAILED` / `MISSING_BUILT` / `INVALID_BUILT_TYPE`。`INVALID_BUILT_TYPE` 通过 `_find_built_assignment_line()` 定位到 `built = ...` 那一行，给出源码上下文。

---

### 3.10 `verifier/pipeline.py`

四层递进验证：

```text
Layer 1: GCJP代码执行验证（safety + 受限执行 + BuiltGraph 提取）
Layer 2: 图结构验证（DAG / 连通性 / 节点覆盖 / 关键路径）
Layer 3: Z3 约束验证（SAT → schedule；UNSAT → unsat_core + 归因）
Layer 4: 语义反向校验（预留接口）
```

入口：

| 方法 | 输入 | 行为 |
| --- | --- | --- |
| `verify_gcjp_code(code)` | GCJP 代码字符串 | L1–L4 完整闭环（**结构化反馈主入口**） |
| `verify_graph(graph)` | `BuiltGraph` | 跳过 L1，跑 L2–L4 |
| `verify_code(code, graph)` | 旧接口 | 走 subprocess 沙箱的 `Layer1CodeVerifier`，**不产出结构化反馈** |

`verify_gcjp_code()` 的 `LayerResult.details` 透传 executor 端全部结构化字段（`structured_violations` / `gcjp_lineno` / `source_context` / `traceback_text` / `api_error`），因此 `VerificationReport.to_dict()` 可直接作为 LLM 修复 prompt 的载荷。

---

### 3.11 `gcjp/debug_logger.py`

全局可控调试日志。`VERBOSE=False`（默认）时日志只写入内部缓存；`VERBOSE=True` 时同步打印。所有模块通过 `from gcjp.debug_logger import debug` 共享同一实例。

---

### 3.12 `agents/` 与 `experiments/` — 阶段 1 LLM 生成评测

阶段 1 把 LLM 调用、GCJP 代码提取和验证评测拆成三个边界：

| 模块 | 作用 |
| --- | --- |
| `agents/llm_client.py` | 统一 OpenAI-compatible Chat Completions 与 Anthropic Messages 调用；支持 profile/env/local provider、headers 脱敏预览、base_url 兼容 preset、502/503/504 与网络中断 retry |
| `agents/planner_agent.py` | 渲染 prompt，调用 LLM，并返回 raw response、extracted code、provider 摘要 |
| `agents/code_extraction.py` | 优先提取 fenced Python code block；无 fence 时从 `from gcjp.mission_graph import TaskGraphBuilder` 入口截取 |
| `experiments/phase1_common.py` | 统一 CLI 参数、provider 加载、样本执行、指标聚合、失败摘要打印 |
| `experiments/exp_01a_structured_to_gcjp.py` | 1A：结构化任务描述 JSON → GCJP |
| `experiments/exp_01b_standard_nl_to_gcjp.py` | 1B：标准无歧义自然语言 → GCJP |

当前 zero-shot prompt 是 baseline；`*_fewshot.md` 用作对照实验，不替代 baseline。

---

## 4. 快速开始

### 4.1 安装

```powershell
conda run -n llm --no-capture-output python -m pip install networkx z3-solver pyyaml jsonschema
```

建议 Python 3.10+，推荐使用 conda 环境 `llm`。

### 4.2 端到端验证

```powershell
conda run -n llm --no-capture-output python -m demos.demo_01_build_graph_from_json          # JSON → SAT
conda run -n llm --no-capture-output python -m demos.demo_02_build_resource_unsat_from_json # JSON → UNSAT（弹药超限）
conda run -n llm --no-capture-output python -m demos.demo_03_build_facilities_from_json     # JSON → SAT（设施 UTM 场景）

conda run -n llm --no-capture-output python -m demos.demo_06_fixed_gcjp_api                 # 手写 GCJP 代码字符串
conda run -n llm --no-capture-output python -m demos.demo_08_parallel_tasks_gcjp            # 并行任务
conda run -n llm --no-capture-output python -m demos.demo_09_sync_barrier_gcjp              # 同步 / 屏障
conda run -n llm --no-capture-output python -m demos.demo_10_condition_resource_conflict_gcjp  # 条件触发资源冲突 UNSAT
conda run -n llm --no-capture-output python -m demos.demo_11_capability_mismatch_gcjp       # 能力不匹配 UNSAT
```

### 4.3 失败路径与结构化反馈回归

```powershell
conda run -n llm --no-capture-output python -m demos.demo_07_gcjp_code_executor_failures    # L1 失败路径诊断（15 case）
conda run -n llm --no-capture-output python -m demos.demo_12_gcjp_structured_feedback       # 结构化反馈契约（5 case）
```

`demo_12` 是"契约 gate"：任何后续回归导致 `structured_violations` / `api_error` / `source_context` 字段丢失，或 `DISALLOWED_BUILDER_METHOD` 建议文案重新出现虚构 API，都会立刻失败。

### 4.4 阶段 1 LLM 接入与实验

LLM 配置有三类入口，优先级为 CLI 参数 > `PHASE1_LLM_CONFIG` profile > `PHASE1_LLM_*` 环境变量 > 协议原生环境变量。

环境变量方式：

```powershell
$env:PHASE1_LLM_PROTOCOL="anthropic_messages"
$env:PHASE1_LLM_BASE_URL="https://your-provider.example"
$env:PHASE1_LLM_API_KEY="sk-..."
$env:PHASE1_LLM_MODEL="your-model"
$env:PHASE1_LLM_TEMPERATURE="0.1"
$env:PHASE1_LLM_MAX_TOKENS="4096"
```

profile 方式：

```powershell
Copy-Item configs\llm_providers.example.yaml configs\llm_providers.local.yaml
$env:PHASE1_LLM_CONFIG="configs/llm_providers.local.yaml"
$env:PHASE1_LLM_PROFILE="your_profile"
```

本地 provider / CC Switch 兼容方式：

```powershell
conda run -n llm --no-capture-output python -m demos.demo_llm_client_smoke --local-provider claude
conda run -n llm --no-capture-output python -m demos.demo_llm_client_smoke --local-provider codex
```

本项目不读取或修改 CC Switch GUI 内部配置；只复用用户已经写入本地 Codex/Claude 配置或环境变量中的 `protocol/base_url/api_key/model`。对于需要特殊 header 的 Anthropic-style 中转站，可通过 `BASE_URL_COMPAT_PRESETS` 自动补 `auth_header` 和 `User-Agent`，也可以用 `--auth-header`、`--user-agent` 显式覆盖。

阶段 1 实验：

```powershell
conda run -n llm --no-capture-output python -m experiments.exp_01a_structured_to_gcjp --local-provider claude
conda run -n llm --no-capture-output python -m experiments.exp_01b_standard_nl_to_gcjp --local-provider claude
```

few-shot 对照：

```powershell
conda run -n llm --no-capture-output python -m experiments.exp_01a_structured_to_gcjp --local-provider claude --prompt prompts/gcjp_generation_prompt_fewshot.md
conda run -n llm --no-capture-output python -m experiments.exp_01b_standard_nl_to_gcjp --local-provider claude --prompt prompts/standard_nl_to_gcjp_prompt_fewshot.md
```

实验输出写入 `out/phase1_generation/`，包括 raw response、extracted code、report JSON 和 metrics；该目录是本地产物，不入库。

非 live 回归：

```powershell
conda run -n llm --no-capture-output python -m demos.demo_phase1_nonlive_regression
```

### 4.5 工具脚本

```powershell
conda run -n llm --no-capture-output python tools/validate_task_plan.py schemas/task_plan_schema.json demos/demo_03_facilities_task_plan.json
conda run -n llm --no-capture-output python tools/validate_configs.py
```

---

## 5. 已验证能力

1. 标准化任务计划 JSON 通过 schema 表达任务节点与依赖；`task_plan_loader.py` 转换为 `BuiltGraph` 并自动接入外部 YAML 配置。
2. `action_templates.yaml` / `capability_model.yaml` 替换硬编码默认值，提供动作参数与异构集群资源上限。
3. `VerificationPipeline` 完成 SAT/UNSAT 检测：资源超限（demo_02：fleet_1 弹药 5 > 4）与能力不匹配（demo_11）均能被 Z3 检测并归因。
4. `environment_model.py` 校验 scenario_id / actor / target 与环境配置的一致性；`environment_facilities.yaml` 提供 UTM 坐标转换后的真实设施地图。
5. **结构化错误反馈链路**：safety 违规、GCJP API 错误、运行时异常均带行号、源码上下文与修复建议，可直接以 JSON 形式作为 LLM 修复 prompt 输入（`demo_12` 5/5 通过）。
6. **阶段 1A/1B LLM baseline**：2026-05-19 使用 Anthropic Messages 兼容 provider 复跑通过，1A structured JSON → GCJP 为 9/9，1B standard NL → GCJP 为 7/7，聚合指标均为 1.0；脱敏摘要见 `docs/phase1_baseline_report.md`。
7. **多协议 provider 接入**：支持 OpenAI-compatible Chat Completions、Anthropic Messages、本地 Codex/Claude 配置读取、CC Switch 风格中转站参数复用、headers 脱敏预览和 retry。
8. `DebugLogger` 实现全局可控调试输出，`VERBOSE=False` 时静默运行。

---

## 6. 当前已知边界

| # | 范围 | 说明 |
| --- | --- | --- |
| 6.1 | 环境模型 | 仅做引用校验，不做禁飞区绕行 / 威胁区惩罚 / 动态避让 |
| 6.2 | 物理距离 | JSON 路径尚未根据 actor 初始位置自动计算飞行距离并注入 `physical_feasibility` 约束 |
| 6.3 | UNSAT 归因 | 已拆分 semantic/framework core 与 attribution，但还没有形成面向自然语言修复的完整解释模板 |
| 6.4 | Layer 4 | 语义反向校验为预留接口，尚未将任务图反向还原为结构化摘要与原始计划比对 |
| 6.5 | LLM 实验 | 当前 baseline 依赖外部 provider，结果会受模型版本、网关稳定性、采样和中转站兼容性影响 |
| 6.6 | 协议覆盖 | 阶段 1 先覆盖 OpenAI Chat Completions 与 Anthropic Messages；OpenAI Responses、Anthropic tools/streaming、Gemini 原生协议尚未纳入默认协议 |

---

## 7. 路线图

### 已完成里程碑

- ✅ Layer 1 确定性底座（JSON → BuiltGraph → Z3 → 报告）
- ✅ GCJP v1 受限 API 冻结（`api_spec.py` 集中白名单）
- ✅ GCJP 代码字符串端到端闭环（`code_executor` + `verify_gcjp_code()`）
- ✅ 结构化错误反馈链路（`SafetyViolation` + `GCJPAPIError` + `source_context` + pipeline 透传）
- ✅ 失败路径与反馈契约 demo（`demo_07` 15 case + `demo_12` 5 case）
- ✅ 阶段 0 UNSAT core 语义/框架拆分与归因输出
- ✅ 阶段 1A/1B LLM 生成评测 baseline：多协议 provider、prompt/dataset/experiments、脱敏输出与本地 provider 读取
- ✅ 阶段 1 数据集扩充、retry、非 live 回归与 few-shot 对照 prompt

### 下一阶段

1. **扩展 baseline 复跑**：用扩充后的 1A 15 条、1B 12 条数据集分别跑 zero-shot 与 few-shot，对比不同 provider/model 的稳定性。
2. **LLM 修复闭环原型**：基于 `VerificationReport.to_dict()` 的结构化反馈构建 prompt，让 LLM 在 N 轮以内自我修复 GCJP 代码；统计收敛轮数、错误类型分布。
3. **失败诊断数据化**：把 `_summary_line()` 的失败摘要进一步汇总到 metrics，形成按 `api_error.code` / `gcjp_lineno` / error type 的报告视图。
4. **物理可行性自动注入**：JSON 路径根据 actor 初始位置与目标坐标自动计算飞行距离，调用 `add_physical_feasibility_constraint()`。
5. **UNSAT 归因解释模板**：基于 semantic/framework core 和 attribution 生成可读解释，供人工审查或修复 Agent 使用。
6. **Layer 4 语义反向校验**：将 `BuiltGraph` 反向还原为结构化摘要，对比原始任务计划，捕获语义漂移。
