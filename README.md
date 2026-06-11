# NLTaskOrchestration

> Natural-language-driven and verifiable task graph construction for UAV swarm missions.

面向无人机集群任务规划场景，将自然语言作战指令转化为结构化任务图，并通过图结构检查与 SMT/Z3 约束验证提升任务编排的可靠性。

当前已完成 **Layer 1 确定性底座** 与 **阶段 1 LLM 生成评测体系**：标准化任务计划 JSON / 标准无歧义自然语言 → LLM 生成 GCJP v1 代码 → 安全检查 → 受限执行构图 → 图结构与 Z3 约束验证 → 指标与报告输出。阶段 1 已接入 OpenAI-compatible Chat Completions 与 Anthropic Messages 两类 provider，并支持本地 Codex/Claude 配置、CC Switch 风格中转站参数、脱敏 headers 预览和实验输出归档。

在 baseline（1A/1B/1C）之上，阶段 1 实验族已扩展到：修复反馈消融（1D）、真实指令语义规范化与澄清闭环（1F/1G）、确定性系统参数注入家族（1H/1I/1J/1K）、生成+闭环修复单次流水线（1L），并建成 **v2 master 数据集**（程序化生成 + 多道闸门 + 语义漂移审计）与统一评分体系（7+1 指标，含 `dag_exact` 全图精确匹配）。

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
VerificationPipeline  (L1 安全/执行 → L2 图结构 → L3 Z3 → L4 语义反向校验*)
    *L4 引擎已在数据层落地（verifier/semantic_reverse.py），pipeline 内为预留挂点
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
│   ├── action_templates.yaml          # 9 种动作模板（能力要求/耗时/能量/弹药）
│   ├── action_lexicon.yaml            # 动作归一化词表（1F 指令规范化）
│   ├── capability_model.yaml          # 12 个异构集群能力与资源上限
│   ├── environment_config.yaml        # scenario_demo / scenario_simple
│   ├── environment_facilities.yaml    # 设施地图 UTM 坐标场景
│   ├── environment_facilities_v2.yaml # v2 扩展设施场景（UAV/雷达/防御圈，数据生成用）
│   ├── experiment_presets.yaml        # run_preset 实验预设
│   └── llm_providers.example.yaml     # LLM provider profile 示例
│
├── agents/
│   ├── llm_client.py                  # OpenAI/Anthropic 多协议 LLM client（思考预算与输出额度分离计费）
│   ├── planner_agent.py               # prompt 渲染 + LLM 调用 + 代码提取
│   ├── plan_extractor_agent.py        # 1I：LLM 仅输出作战语义 task_plan JSON 骨架
│   ├── repair_agent.py                # 1C/1L：基于验证报告修复 GCJP 代码
│   ├── instruction_normalizer_agent.py # 1F：原始 NL 指令 → 结构化规范化结果
│   ├── instruction_validators.py      # 1F：指令完整性确定性契约校验
│   ├── clarification_loop.py          # 1F/1G：多轮澄清闭环控制器
│   ├── code_extraction.py             # 从模型响应提取 GCJP Python 代码
│   └── json_extraction.py             # 从模型响应提取 JSON 对象
│
├── prompts/                           # 生成/修复/规范化/骨架/API-fill 等 10 个 prompt
│   ├── gcjp_generation_prompt.md / standard_nl_to_gcjp_prompt.md (+ *_fewshot.md)
│   ├── gcjp_repair_prompt.md / gcjp_simulated_natural_failure_prompt.md
│   ├── instruction_normalization_prompt.md
│   └── standard_nl_to_gcjp_{apifill,skeleton}_prompt.md / standard_nl_to_task_plan_json_prompt.md
│
├── datasets/
│   ├── v2/                            # master 数据集（唯一真源）+ 试产批次（_trial_master.jsonl）
│   ├── generated/                     # 由 master 导出的实验视图（*.v2.jsonl）
│   ├── seed/                          # GCJP seed 样本
│   └── *.jsonl                        # v1 各实验数据集（1A/1B/1C/1F/failure seed 等）
│
├── experiments/
│   ├── phase1_common.py               # CLI/并发执行/评分（7+1 指标）/汇总与基线归档
│   ├── run_preset.py                  # 按 configs/experiment_presets.yaml 批量跑实验
│   ├── exp_01a_structured_to_gcjp.py  # 1A：结构化 JSON → GCJP
│   ├── exp_01b_standard_nl_to_gcjp.py # 1B：标准语义 NL → GCJP（语义契约 + 配置注入）
│   ├── exp_01c_repair_loop.py         # 1C：坏代码/失败 report → LLM 修复 → 再验证
│   ├── exp_01d_repair_feedback_ablation.py             # 1D：修复反馈消融
│   ├── exp_01e_simulated_natural_failure_generation.py # 1E：模拟自然失败样本生成
│   ├── exp_01f_instruction_normalization.py            # 1F：真实指令语义规范化评测
│   ├── exp_01g_raw_nl_to_gcjp_pipeline.py              # 1G：原始 NL→规范化→GCJP 端到端
│   ├── exp_01h_standard_nl_to_gcjp_with_config.py      # 1H：1B + 运行时配置注入对照
│   ├── exp_01i_nl_to_taskplan_json_deterministic.py    # 1I：LLM 出 JSON 骨架 + 确定性构图
│   ├── exp_01j_nl_to_skeleton_code_deterministic.py    # 1J：LLM 出骨架代码 + AST 确定性填参
│   ├── exp_01k_nl_to_gcjp_apifill_deterministic.py     # 1K：API-fill + 运行时参数注入
│   ├── exp_01l_standard_nl_to_gcjp_with_repair.py      # 1L：生成 + 闭环修复单次流水线
│   ├── exp_02_json_to_gcjp_comparison.py               # 2：LLM 生成 vs 确定性 reference F1 对比
│   └── exp_03_single_agent_nl_pipeline.py              # 3：raw NL 全串联单 Agent 原型
│
├── schemas/
│   ├── task_plan_schema.json          # 标准化任务计划 JSON Schema
│   ├── phase1_master_case_schema.json # v2 master case Schema
│   └── gcjp_seed_schema.json          # GCJP seed 样本 Schema
│
├── gcjp/
│   ├── api_spec.py                    # GCJP v1 受限 API 规范（白名单 single source of truth）
│   ├── mission_graph.py               # TaskGraphBuilder / BuiltGraph（API 实现）
│   ├── errors.py                      # GCJPAPIError：API 层结构化错误
│   ├── safety_checker.py              # AST 白名单校验 + 结构化 SafetyViolation
│   ├── code_executor.py               # GCJP 代码字符串 → BuiltGraph 受限执行
│   ├── constraint_templates.py        # Z3 约束构建与求解
│   ├── task_plan_loader.py            # 标准化 JSON → BuiltGraph（含显式约束构建）
│   ├── runtime_context.py             # 1K：执行期系统参数注入上下文
│   ├── skeleton_filler.py             # 1J：AST 骨架代码确定性填参
│   ├── graph_visualizer.py            # BuiltGraph 静态图渲染（PNG/SVG/PDF）
│   ├── environment_model.py           # 场景引用校验与坐标距离
│   └── debug_logger.py                # 全局可控调试日志
│
├── verifier/
│   ├── pipeline.py                    # 四层递进验证管道
│   └── semantic_reverse.py            # L4 反向复述语义一致性引擎（advisory）
│
├── demos/                             # 21 个端到端 demo（见 §1 关键 demo 表）
│
├── tools/
│   ├── dataset/
│   │   ├── generate_cases.py            # Phase 1 v2 程序化样本生成器 (spec 前端)
│   │   ├── make_case.py                 # spec → master case 组装 + Z3 自检闸门
│   │   ├── common.py                    # 共享工具 (canonical plan 转换、difficulty 推导等)
│   │   ├── validate_cases.py            # 数据集全量校验 (schema + 约束可实现性 + Z3)
│   │   ├── check_semantics.py           # NL↔plan 语义漂移检测 (Tier-1 确定性 / Tier-2 LLM)
│   │   ├── summarize_coverage.py        # 家族/难度/sat:unsat 覆盖分布统计
│   │   ├── export_phase1_views.py       # master → generated views 导出
│   │   ├── diff_run_vs_groundtruth.py   # run 产物 ↔ master 真值 DAG 精确审计
│   │   ├── migrate_legacy_cases.py      # v1 → v2 数据迁移
│   │   ├── review_sheet.py              # 人工审查辅助
│   │   └── templates/                   # 生成器模板 / make_case 模板
│   ├── jsonl_viewer.html              # JSONL/YAML 数据与报告浏览器（多文件/目录拖放）
│   ├── visualize_saved_graph.py       # 保存的图 JSON → 可视化
│   ├── validate_task_plan.py / validate_configs.py 等校验脚本
│   └── get_local_api_config.py        # 读取本地 Codex/Claude provider 配置
│
├── docs/                              # reference / rationale / call_flow / results / progress 文档
│   ├── phase1_scoring_metrics_reference.md     # 评分体系（7+1 指标）参考
│   ├── verifier_semantic_reverse_reference.md  # L4 反向复述引擎参考
│   ├── dataset_v2_generate_cases_reference.md  # 数据生成器使用手册
│   ├── exp_01l_repair_pipeline_reference.md    # 1L 闭环修复管线手册
│   ├── phase1_baseline_report.md               # 阶段 1A/1B/1C 脱敏 baseline 摘要
│   └── ...                                     # 其余各实验 rationale / results / 进展快照
│
├── development_plan/                  # 研究计划、执行清单与契约设计记录
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

`capability_model.yaml`（12 个异构集群，由 `load_capability_model_from_yaml()` 读取；下表节选前 4 个）：

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
Layer 2: 图结构验证（DAG / 连通性 / 节点覆盖 / 关键路径；sync/group_sync 约束计入连通性证据）
Layer 3: Z3 约束验证（SAT → schedule；UNSAT → unsat_core + 归因）
Layer 4: 语义反向校验（pipeline 内预留挂点；引擎已在数据层落地，见下）
```

入口：

| 方法 | 输入 | 行为 |
| --- | --- | --- |
| `verify_gcjp_code(code)` | GCJP 代码字符串 | L1–L4 完整闭环（**结构化反馈主入口**） |
| `verify_graph(graph)` | `BuiltGraph` | 跳过 L1，跑 L2–L4 |
| `verify_code(code, graph)` | 旧接口 | 走 subprocess 沙箱的 `Layer1CodeVerifier`，**不产出结构化反馈** |

`verify_gcjp_code()` 的 `LayerResult.details` 透传 executor 端全部结构化字段（`structured_violations` / `gcjp_lineno` / `source_context` / `traceback_text` / `api_error`），因此 `VerificationReport.to_dict()` 可直接作为 LLM 修复 prompt 的载荷。

**`verifier/semantic_reverse.py`** —— Layer 4 对应能力的数据层先行实现：`verbalize()` 把 `canonical_task_plan` 确定性渲染回可读描述，`tier1_check()` 做高 precision 的词项/实体/关系族交叉核对（plan→NL 方向，结构性避开资源/能力假阳性），`tier2_check()` 为可选 LLM 裁判。整体 advisory-only，由 `tools/dataset/check_semantics.py` 驱动，用于数据集 NL↔plan 语义漂移审计。详见 [`docs/verifier_semantic_reverse_reference.md`](docs/verifier_semantic_reverse_reference.md)。

---

### 3.11 `gcjp/debug_logger.py`

全局可控调试日志。`VERBOSE=False`（默认）时日志只写入内部缓存；`VERBOSE=True` 时同步打印。所有模块通过 `from gcjp.debug_logger import debug` 共享同一实例。

---

### 3.12 `agents/` 与 `experiments/` — 阶段 1 LLM 生成评测

阶段 1 把 LLM 调用、GCJP 代码提取和验证评测拆成独立边界。Agent 层：

| 模块 | 作用 |
| --- | --- |
| `agents/llm_client.py` | 统一 OpenAI-compatible Chat Completions 与 Anthropic Messages 调用；支持 profile/env/local provider、headers 脱敏预览、base_url 兼容 preset、502/503/504 与网络中断 retry；token 用量按思考预算与输出额度分离计费 |
| `agents/planner_agent.py` | 渲染 prompt，调用 LLM，并返回 raw response、extracted code、provider 摘要 |
| `agents/plan_extractor_agent.py` | 1I：让 LLM 仅输出作战语义 task_plan JSON 骨架（不含系统参数），后续由 `task_plan_loader` 确定性填参 |
| `agents/repair_agent.py` | 1C/1L：根据坏代码、case payload 与 `VerificationReport.to_dict()` 调用 LLM 生成修复版 GCJP |
| `agents/instruction_normalizer_agent.py` | 1F：原始模糊 NL 指令 → 结构化规范化结果，支持多轮澄清历史输入 |
| `agents/instruction_validators.py` | 1F：指令完整性确定性契约校验（语义与系统参数解耦） |
| `agents/clarification_loop.py` | 1F/1G：多轮 LLM 分析 + 指挥员澄清交互闭环控制器 |
| `agents/code_extraction.py` / `json_extraction.py` | 从模型响应提取 GCJP 代码 / JSON 对象 |

实验族（公共底座 `experiments/phase1_common.py`：统一 CLI、provider 加载、并发执行、7+1 指标评分、汇总与基线归档；`run_preset.py` 按 `configs/experiment_presets.yaml` 批量复跑）：

| 实验 | 内容 |
| --- | --- |
| `exp_01a` | 1A：结构化任务描述 JSON → GCJP |
| `exp_01b` | 1B：标准语义自然语言 → GCJP（作战语义契约 + 配置注入） |
| `exp_01c` | 1C：固定坏代码或已有失败 report → LLM 修复 → 再验证 |
| `exp_01d` | 1D：修复反馈消融（full_report / layer1_only / error_summary_only 等模式对比） |
| `exp_01e` | 1E：按失败 spec 让 LLM 生成"自然犯错"的失败样本 |
| `exp_01f` | 1F：真实指挥指令语义规范化评测（single-shot / clarification-loop 两种模式） |
| `exp_01g` | 1G：原始 NL → 指令规范化（含澄清闭环）→ GCJP 生成 → 验证 端到端 |
| `exp_01h` | 1H：1B + 运行时配置注入对照 |
| `exp_01i` | 1I：LLM 出 task_plan JSON 骨架 → Python 确定性构图（绕过 exec） |
| `exp_01j` | 1J：LLM 出带 sentinel 的 GCJP 骨架代码 → AST 确定性填参 → 受限执行 |
| `exp_01k` | 1K：API-fill——从 LLM 可见 API 中移除系统参数槽位，执行期由 runtime config 注入 |
| `exp_01l` | 1L：生成 + 闭环修复合并为单次 per-case 流水线，记录 initial/final 双份指标与 `dag_exact`，详见 [`docs/exp_01l_repair_pipeline_reference.md`](docs/exp_01l_repair_pipeline_reference.md) |
| `exp_02` | LLM 生成 vs `task_plan_loader` 确定性 reference 的 Node/Edge/Constraint-F1 对比 |
| `exp_03` | raw NL 全串联单 Agent 原型（M-2 决策点实验） |

当前 zero-shot prompt 是 baseline；`*_fewshot.md` 用作对照实验，不替代 baseline。

---

### 3.13 `tools/dataset/` — 数据集生成与校验工具链

Phase 1 v2 数据集由 **master JSONL** (`datasets/v2/phase1_master_cases.jsonl`) 作为唯一真源，通过程序化生成 + 多道闸门保证质量。完整工具链如下：

| 工具 | 作用 |
|------|------|
| `generate_cases.py` | **程序化样本生成器**。按 11 种结构家族 (motif) 批量产出 compact spec YAML，支持 synthetic / environment 两种 target 来源、启发式与 Z3 标定两种难度模式。**零 LLM**，全部由确定性规则 + Z3 完成。 |
| `make_case.py` | **spec → master case 的组装 + 校验闸门**。补全派生字段 (canonical_task_plan / expected_graph / expected_verification / difficulty / language 等)，过 schema / 系统参数泄漏 / 引用 / Z3 标签确认四道闸后才写入 master。 |
| `validate_cases.py` | **全量校验**。对已有 master 数据集逐条检查 schema、引用、约束类型合法性与可实现性 (`expected ⊆ actual`)、Z3 标签一致性。 |
| `check_semantics.py` | **NL↔plan 语义漂移检测**。Tier-1 做确定性词项/关系交叉核对，Tier-2 可选 LLM 裁判。输出 advisory 报告，永不阻塞写入。 |
| `export_phase1_views.py` | **视图导出**。从 master JSONL 按 case_type/split 过滤并导出 `datasets/generated/*.jsonl`，供实验直接使用。 |
| `summarize_coverage.py` | **覆盖分布统计**。按家族/难度/sat:unsat/约束类型输出分布矩阵。 |

**数据管线:**

```
generate_cases  ──产出 spec YAML──▶  make_case  ──组装+校验──▶  master JSONL
                                                         │
                              ┌──────────────────────────┘
                              ▼
                       validate_cases  ←── 全量校验
                       check_semantics ←── NL↔plan 漂移审计
                              │
                              ▼
                      export_phase1_views  ──▶  generated/*.jsonl  ──▶  exp_01b / 01i / 01j / 01k
```

详细文档见 [`docs/dataset_v2_generate_cases_reference.md`](docs/dataset_v2_generate_cases_reference.md)。

---

### 3.14 评分体系 — 7+1 指标统一口径

全部生成类实验共用 `evaluate_graph_against_expected()`（`experiments/phase1_common.py`）评分，只依赖最终 `BuiltGraph` + 验证报告 + case 真值，与生成方式无关，可横向对照；真值字段绝不进入 prompt。

| 指标 | 口径 |
|------|------|
| `builtgraph_success` / `l2_graph_pass` / `l3_expected_result` | 构图成功 / L2 图结构通过 / Z3 结果与 `expected_result` 一致 |
| `node_complete` / `edge_complete` / `constraint_complete` | 节点 / 边关系类型 / 约束类型满足 `expected_patterns` |
| `first_pass` | 以上 l3+node+edge+constraint 全部满足（一次通过） |
| `dag_exact`（第 8 项，最严） | 整图与 master 真值精确比对：逐节点映射、逐边端点、同步对集合（含 tolerance）；仅 v2 完整真值样本可评 |

同步语义按等价类评分：`sync` 边、`add_sync_constraint`、`add_group_sync_constraint` 三种可证等价写法互认。详见 [`docs/phase1_scoring_metrics_reference.md`](docs/phase1_scoring_metrics_reference.md)。

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

LLM 配置入口优先级为 CLI 参数 > 显式 `--config/--provider-profile` 或 `--local-provider` > `PHASE1_LLM_*` 普通环境变量 > 协议原生环境变量。

环境变量方式：

```powershell
$env:PHASE1_LLM_PROTOCOL="anthropic_messages"
$env:PHASE1_LLM_BASE_URL="https://your-provider.example"
$env:PHASE1_LLM_API_KEY="sk-..."
$env:PHASE1_LLM_MODEL="your-model"
$env:PHASE1_LLM_TEMPERATURE="0.1"
$env:PHASE1_LLM_MAX_TOKENS="4096"
# 可选：显式启用官方 SDK 后端。
# $env:PHASE1_LLM_TRANSPORT="official_sdk"
# 可选覆盖：默认已开启 max thinking。
# $env:PHASE1_LLM_THINKING="disabled"
# $env:PHASE1_LLM_REASONING_EFFORT="high"  # openai_chat / openai_responses
# $env:PHASE1_LLM_OUTPUT_EFFORT="high"     # anthropic_messages
```

profile 方式：

```powershell
Copy-Item configs\llm_providers.example.yaml configs\llm_providers.local.yaml
python -m demos.demo_llm_client_smoke --config configs/llm_providers.local.yaml --provider-profile your_profile
```

本地 provider / CC Switch 兼容方式：

```powershell
conda run -n llm --no-capture-output python -m demos.demo_llm_client_smoke --local-provider claude
conda run -n llm --no-capture-output python -m demos.demo_llm_client_smoke --local-provider codex
```

本项目不读取或修改 CC Switch GUI 内部配置；只复用用户已经写入本地 Codex/Claude 配置或环境变量中的 `protocol/base_url/api_key/model`。对于需要特殊 header 的 Anthropic-style 中转站，可通过 `BASE_URL_COMPAT_PRESETS` 自动补 `auth_header` 和 `User-Agent`，也可以用 `--auth-header`、`--user-agent` 显式覆盖。

推理/思考参数会按协议分流，默认开启最大模式：`thinking` 默认 `enabled`，`openai_chat` 协议默认使用 `reasoning_effort=max`，`anthropic_messages` 协议默认使用 `output_effort=max`，分别对应 `{"thinking": {"type": "enabled"}}`、`{"reasoning_effort": "max"}`、`{"output_config": {"effort": "max"}}`。如需关闭，可传 `--thinking disabled` 或在 profile/env 中设为 `disabled`；更特殊的 provider 字段仍可放进 profile 的 `extra_body`，并会覆盖上述显式配置生成的同名字段。

官方 SDK 后端是显式 opt-in，不影响默认 HTTP 请求路径。使用前按需安装：

```powershell
pip install openai anthropic
```

OpenAI Responses API 示例：

```powershell
python -m demos.demo_llm_client_smoke --protocol openai_responses --transport official_sdk --model gpt-5.5 --api-key-env OPENAI_API_KEY
```

Anthropic Messages SDK 示例：

```powershell
python -m demos.demo_llm_client_smoke --protocol anthropic_messages --transport official_sdk --model claude-sonnet-4-6 --thinking adaptive --output-effort high --api-key-env ANTHROPIC_API_KEY
```

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

阶段 1C 修复闭环：

```powershell
conda run -n llm --no-capture-output python -m experiments.exp_01c_repair_loop --local-provider claude --dataset datasets/phase1_repair_cases.jsonl
```

阶段 1L 端到端闭环（生成 + 修复单次流水线，v2 数据集）：

```powershell
conda run -n llm --no-capture-output python -m experiments.exp_01l_standard_nl_to_gcjp_with_repair `
    --provider-profile <profile> --workers 4 --max-repair-rounds 2 `
    --dataset datasets/generated/_trial/phase1_standard_nl_cases.v2.jsonl
```

按预设批量复跑（预设定义见 `configs/experiment_presets.yaml`）：

```powershell
conda run -n llm --no-capture-output python -m experiments.run_preset --list
conda run -n llm --no-capture-output python -m experiments.run_preset <preset_name> --dry-run
```

实验输出写入 `out/phase1_generation/`，包括 raw response、extracted code、report JSON 和 metrics；该目录是本地产物，不入库。run 产物可整目录拖入 `tools/jsonl_viewer.html` 浏览（自动聚合逐样本报告并切换报告视图，见 [`docs/tools_jsonl_viewer_guide.md`](docs/tools_jsonl_viewer_guide.md)）。

非 live 回归：

```powershell
conda run -n llm --no-capture-output python -m demos.demo_phase1_nonlive_regression
```

### 4.5 工具脚本

```powershell
conda run -n llm --no-capture-output python tools/validate_task_plan.py schemas/task_plan_schema.json demos/demo_03_facilities_task_plan.json
conda run -n llm --no-capture-output python tools/validate_configs.py
```

### 4.6 v2 数据集生成流水线

```powershell
# 1. 程序化生成候选 spec（零 LLM，Z3 标定难度）
conda run -n llm --no-capture-output python -m tools.dataset.generate_cases `
    --n 30 --seed 7 --prefix genv2 --out tools/dataset/templates/generated_batch.yaml `
    --self-check --calibrate-difficulty

# 2. 组装 + 四道闸门后写入 master（先 --dry-run 验证）
conda run -n llm --no-capture-output python -m tools.dataset.make_case `
    --template tools/dataset/templates/generated_batch.yaml `
    --out datasets/v2/phase1_master_cases.jsonl

# 3. 重导出实验视图 + 全量校验 + 语义漂移审计
conda run -n llm --no-capture-output python -m tools.dataset.export_phase1_views --master datasets/v2/phase1_master_cases.jsonl --out-dir datasets/generated
conda run -n llm --no-capture-output python -m tools.dataset.validate_cases --dataset datasets/v2/phase1_master_cases.jsonl
conda run -n llm --no-capture-output python -m tools.dataset.check_semantics --dataset datasets/v2/phase1_master_cases.jsonl --out out/semantic/phase1_drift.md
```

完整六步流程与参数说明见 [`docs/dataset_v2_generate_cases_reference.md`](docs/dataset_v2_generate_cases_reference.md)。

---

## 5. 已验证能力

1. 标准化任务计划 JSON 通过 schema 表达任务节点与依赖；`task_plan_loader.py` 转换为 `BuiltGraph` 并自动接入外部 YAML 配置。
2. `action_templates.yaml` / `capability_model.yaml` 替换硬编码默认值，提供动作参数与异构集群资源上限。
3. `VerificationPipeline` 完成 SAT/UNSAT 检测：资源超限（demo_02：fleet_1 弹药 5 > 4）与能力不匹配（demo_11）均能被 Z3 检测并归因。
4. `environment_model.py` 校验 scenario_id / actor / target 与环境配置的一致性；`environment_facilities.yaml` 提供 UTM 坐标转换后的真实设施地图。
5. **结构化错误反馈链路**：safety 违规、GCJP API 错误、运行时异常均带行号、源码上下文与修复建议，可直接以 JSON 形式作为 LLM 修复 prompt 输入（`demo_12` 5/5 通过）。
6. **阶段 1A/1B LLM baseline**：2026-05-19 使用 Anthropic Messages 兼容 provider 复跑通过，扩充版 zero-shot 与 few-shot 均达到 1A 15/15、1B 12/12；脱敏摘要见 `docs/phase1_baseline_report.md`。
7. **多协议 provider 接入**：支持 OpenAI-compatible Chat Completions、Anthropic Messages、本地 Codex/Claude 配置读取、CC Switch 风格中转站参数复用、headers 脱敏预览和 retry。
8. **阶段 1C 修复闭环**：固定坏代码样本 5/5 修复成功，平均 1 轮修复；覆盖缺构造参数、虚构 API、错误 condition 参数、错误 physical 参数和漏 `built`。
9. `DebugLogger` 实现全局可控调试输出，`VERBOSE=False` 时静默运行。
10. **阶段 1D 修复反馈消融**：量化不同反馈裁剪模式（full_report / layer1_only / error_summary_only 等）对修复成功率的影响（`docs/exp_01d_feedback_ablation_results.md`）。
11. **阶段 1F/1G 指令规范化**：真实指挥指令 → 结构化规范化 + 完整性契约判定（语义与系统参数解耦），1G 把澄清闭环接入端到端 GCJP 生成。
12. **确定性参数注入家族（1H–1K）**：系统参数（duration / energy / ammo / 能力 / 资源上限）从 LLM 手里收回，由 YAML 配置经确定性构图（1I）、AST 骨架填参（1J）、runtime config 注入（1K）三种路径补全。
13. **v2 master 数据集**：程序化生成 + schema / 系统参数泄漏 / 引用 / Z3 四道闸门 + L4 语义漂移审计；200 例试产批次已过全部闸门写入 `_trial` master 并导出试产视图。
14. **阶段 1L 端到端闭环**：生成 + 修复合并为单次流水线，修复仅在验证报告携带可行动信号时触发；评分新增 `dag_exact` 全图精确匹配与 sync 语义等价互认。

---

## 6. 当前已知边界

| # | 范围 | 说明 |
| --- | --- | --- |
| 6.1 | 环境模型 | 仅做引用校验，不做禁飞区绕行 / 威胁区惩罚 / 动态避让 |
| 6.2 | 物理距离 | JSON 路径尚未根据 actor 初始位置自动计算飞行距离并注入 `physical_feasibility` 约束 |
| 6.3 | UNSAT 归因 | 已拆分 semantic/framework core 与 attribution，但还没有形成面向自然语言修复的完整解释模板 |
| 6.4 | Layer 4 | 反向复述引擎已在数据层落地（NL↔plan 漂移审计，advisory）；pipeline 内对 BuiltGraph 的在线反向校验仍为预留挂点 |
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
- ✅ 阶段 1A/1B LLM 生成评测 baseline：多协议 provider（Anthropic Messages 与 OpenAI-compatible 均已实跑）、prompt/dataset/experiments、脱敏输出与本地 provider 读取
- ✅ 阶段 1 数据集扩充、retry、非 live 回归与 few-shot 对照 prompt
- ✅ 阶段 1C LLM 修复闭环：固定坏代码样本 5/5 修复成功
- ✅ 阶段 1D 修复反馈消融与 1E 模拟失败样本生成
- ✅ 阶段 1F/1G 指令规范化契约、澄清闭环与 raw NL 端到端管线
- ✅ 确定性参数注入家族 1H/1I/1J/1K（配置注入 / JSON 骨架 / AST 填参 / API-fill + runtime config）
- ✅ Phase 1 v2 master 数据集与工具链（程序化生成、四道闸门、视图导出、覆盖统计、人工审查指南）
- ✅ L4 反向复述语义一致性引擎（数据层 advisory，Tier-1 确定性 + Tier-2 LLM 裁判）
- ✅ 阶段 1L 生成+修复闭环单次流水线（替代离线两段式 1B→1C 联动）
- ✅ 统一评分体系：7+1 指标、`dag_exact` 全图精确匹配、sync 语义等价互认与离线审计 CLI

### 下一阶段

1. **v2 数据集全量生产与基线复跑**：试产 200 例 → 全量 master，1B/1H–1L 在 v2 数据集上复跑形成新基线。
2. **L4 接入 pipeline**：把反向复述引擎接到 `Layer4SemanticVerifier`，对实验产出的 `BuiltGraph` 做在线语义校验闭环。
3. **失败诊断数据化**：把 `_summary_line()` 的失败摘要进一步汇总到 metrics，形成按 `api_error.code` / `gcjp_lineno` / error type 的报告视图。
4. **物理可行性自动注入**：JSON 路径根据 actor 初始位置与目标坐标自动计算飞行距离，调用 `add_physical_feasibility_constraint()`。
5. **UNSAT 归因解释模板**：基于 semantic/framework core 和 attribution 生成可读解释，供人工审查或修复 Agent 使用。
