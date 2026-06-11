# exp_01l 端到端生成+闭环修复实验管线

## 一、使用手册

### 1. 概述与定位

`exp_01l_standard_nl_to_gcjp_with_repair` 是 Phase1 实验族中的**端到端管线**，将原先分离的两阶段实验（exp_01b 代码生成 + exp_01c 闭环修复）合并为单次 per-case 流水线。在项目整体数据流中的位置：

```
generate_cases (数据生成)
    ↓ phase1_standard_nl_cases.v2.jsonl
exp_01l (本实验)
    ├─ PlannerAgent: NL → GCJP code (生成)
    ├─ execute_gcjp_code + VerificationPipeline (执行+验证)
    ├─ evaluate_graph_against_expected (评分，7项统一口径)
    ├─ RepairAgent: broken_code + report → fixed code (闭环修复)
    └─ 输出: final_dag/ + metrics.json
```

核心价值：免去"先跑 01b 出 reports/ 再手动把目录喂给 01c `--source-report-dir`"的两段式割裂，一条命令完成生成→评估→修复→最终 DAG 导出→汇总指标。

### 2. 架构概览

```
┌────────────────── per-case worker ──────────────────┐
│                                                      │
│  NL instruction ──→ PlannerAgent.generate_gcjp()     │
│                          │                           │
│                    extracted_code                     │
│                          ↓                           │
│              execute_gcjp_code() ──→ graph           │
│              VerificationPipeline() ──→ report       │
│              evaluate_graph_against_expected()        │
│                          │                           │
│                   first_pass? ──── Yes ──→ done      │
│                          │ No                        │
│                          ↓                           │
│         ┌── repair loop (max N rounds) ──┐           │
│         │  RepairAgent.repair_gcjp()     │           │
│         │  execute + verify + evaluate   │           │
│         │  first_pass? → break           │           │
│         └────────────────────────────────┘           │
│                          ↓                           │
│              final_dag + evaluation record            │
└──────────────────────────────────────────────────────┘
              ↓ (并发 workers)
        _aggregate() → metrics.json
```

关键模块依赖：

| 模块 | 角色 |
|------|------|
| `agents/planner_agent.py` | NL → GCJP 代码生成 |
| `agents/repair_agent.py` | 验证报告 → 修复代码 |
| `gcjp/code_executor.py` | 受限沙箱执行 GCJP 代码 |
| `verifier/pipeline.py` | 4层验证（L1执行/L2图结构/L3 Z3约束/L4语义） |
| `experiments/phase1_common.py` | 共用工具（加载/评分/并发/输出） |

### 3. 使用说明

#### CLI 参数

| 参数 | 类型/默认值 | 说明 |
|------|------------|------|
| `--provider-profile` | str (必填) | LLM provider 配置名 |
| `--dataset` | Path, `datasets/phase1_standard_nl_cases.jsonl` | 输入数据集 |
| `--prompt` | Path, `prompts/standard_nl_to_gcjp_prompt.md` | 生成 prompt 模板 |
| `--repair-prompt` | Path, `prompts/gcjp_repair_prompt.md` | 修复 prompt 模板 |
| `--max-repair-rounds` | int, 2 | 最大修复轮次 |
| `--action-templates` | Path, `configs/action_templates.yaml` | 动作模板配置 |
| `--capability-model` | Path, `configs/capability_model.yaml` | 编队能力模型 |
| `--limit` | int | 限制处理的 case 数量 |
| `--workers` | int, 1 | 并发 worker 数 |
| `--output-dir` | Path | 输出根目录 |
| `--master-dataset` | Path, `datasets/v2/_trial_master.jsonl` | 含完整真值(expected_graph/canonical_task_plan)的 master 数据集,供 dag_exact 指标;仅评估侧使用,绝不进 prompt;缺失时 dag_exact 记 None |

#### 典型调用

```bash
conda run -n llm python -u -m experiments.exp_01l_standard_nl_to_gcjp_with_repair \
  --provider-profile mimo \
  --dataset datasets/generated/_trial/phase1_standard_nl_cases.v2.jsonl \
  --max-repair-rounds 2 \
  --workers 4 \
  --limit 10
```

#### 输出结构

```
out/phase1_generation/<run_dir>/exp_01l_standard_nl_to_gcjp_with_repair/
├── raw_outputs/          # LLM 原始响应
├── initial_code/         # 首次生成代码 (.py)
├── final_code/           # 修复后最终代码 (.py)
├── repair_attempts/      # 各轮修复记录 (.json)
├── final_dag/            # 最终有向图 (.json)
├── reports/              # 完整 per-case 报告 (.json)
└── metrics.json          # 汇总指标
```

#### metrics.json 核心指标

| 指标 | 含义 |
|------|------|
| `first_pass_rate` | 首次生成即通过率 |
| `final_pass_rate` | 修复后最终通过率 |
| `repair_attempt_rate` | 进入修复的比例 |
| `repair_success_rate` | 修复成功率（分母=尝试修复数） |
| `initial/final_node_complete_rate` | 节点还原完整度 |
| `initial/final_edge_complete_rate` | 边关系还原完整度 |
| `initial/final_constraint_complete_rate` | 约束还原完整度 |
| `final_l3_rate` | Z3 约束求解与 ground truth 一致率 |
| `initial/final_dag_exact_rate` | **第 8 项,最严口径**:整图与 master 真值精确匹配率(节点映射/逐边端点方向/同步组及 tolerance 逐一对照;sync 三种编码语义等价互认);分母=`dag_exact_evaluable` |
| `error_transition_matrix` | 错误类型迁移矩阵（初始→最终） |

### 4. 功能清单

- **单次端到端**：生成 + 执行 + 验证 + 修复 + DAG 导出，一条命令完成
- **公平评测**：expected_patterns / expected_result / tags 绝不进 prompt；评分用 `evaluate_graph_against_expected`，与 01A/01B/01H/01I/01J 七项口径完全一致
- **闭环修复**：修复 prompt 仅喂验证报告（环境信号）+ 配置上下文，不泄露 ground truth
- **并发执行**：`--workers N` 支持多 case 并行处理
- **错误兜底**：worker 异常不崩溃聚合，记录 error_type 进 metrics
- **DAG 导出**：最终 BuiltGraph 导出为 `{nodes, edges}` JSON，可直接与 expected_graph diff

### 5. 集成方式

exp_01l 处于实验管线末端，接入方式：

```bash
# 1. 生成数据集（若无现成）
conda run -n llm python -m tools.dataset.generate_cases \
  --batch-config tools/dataset/templates/trial_batch_200.yaml

# 2. 运行 exp_01l
conda run -n llm python -u -m experiments.exp_01l_standard_nl_to_gcjp_with_repair \
  --provider-profile mimo \
  --dataset datasets/generated/_trial/phase1_standard_nl_cases.v2.jsonl \
  --max-repair-rounds 2 --workers 4

# 3. 查看结果
# metrics.json 位于输出目录，final_dag/ 包含可 diff 的 DAG JSON
```

### 6. 已知局限

| 局限 | 原因 | 影响 |
|------|------|------|
| 修复 prompt 无多轮对话上下文 | RepairAgent 每轮独立调用，不累积历史 | 修复轮次间可能重复犯错 |
| Z3 不建模 per-fleet 时序互斥 | Z3 公式化复杂度过高 | 部分 sat/unsat 判定可能存在假阳性 |
| `--workers` 过高时 API 限流 | 外部 LLM provider 速率限制 | deepseek 64 workers 全卡，建议 ≤8 |
| `fly_to` duration 默认值陷阱 | `action_templates.yaml` 中 `min_duration: null` → 不同加载器解析为 1.0 或 0.0 | 可能影响 time_window 约束求解 |

### 7. 相关文件索引

| 文件 | 角色 |
|------|------|
| [experiments/exp_01l_standard_nl_to_gcjp_with_repair.py](experiments/exp_01l_standard_nl_to_gcjp_with_repair.py) | 主实验脚本 |
| [agents/planner_agent.py](agents/planner_agent.py) | 代码生成 Agent |
| [agents/repair_agent.py](agents/repair_agent.py) | 闭环修复 Agent |
| [verifier/pipeline.py](verifier/pipeline.py) | 4层验证管线（含本次 L2 修复） |
| [gcjp/mission_graph.py](gcjp/mission_graph.py) | TaskGraphBuilder DSL/API |
| [experiments/phase1_common.py](experiments/phase1_common.py) | 共用评分/加载/并发工具 |
| [prompts/standard_nl_to_gcjp_prompt.md](prompts/standard_nl_to_gcjp_prompt.md) | 生成 prompt 模板 |
| [prompts/gcjp_repair_prompt.md](prompts/gcjp_repair_prompt.md) | 修复 prompt 模板 |
| [tests/test_layer2_sync_connectivity.py](tests/test_layer2_sync_connectivity.py) | L2 sync 连通性回归测试 |
| [datasets/generated/_trial/phase1_standard_nl_cases.v2.jsonl](datasets/generated/_trial/phase1_standard_nl_cases.v2.jsonl) | 216条 trial 数据集 |

---

## 二、设计决策与讨论上下文

### 1. 触发背景

原有实验流程是分段的：先跑 exp_01b 生成代码并输出 reports/，再手动用 exp_01c 从 `--source-report-dir` 读取失败报告做修复。这种两段式割裂导致：

- 需要人工衔接两个实验步骤
- 无法在单次运行中获得"首次通过率 vs 修复后通过率"的直接对比
- 无法自动产出最终 DAG 用于下游可视化或结构 diff

因此建立 exp_01l 作为统一管线。

在首次全量跑通 216 条 trial 数据集后（qwen provider），发现 215/216 通过、唯一失败样本 `trial_binary_sync_ac0a598d` 暴露了验证器 Layer2 的口径不一致 bug，进而触发了本次 verifier 修复。

### 2. 关键决策

#### 决策 1：exp_01l 合并 01b+01c 而非修改 01b/01c

- **选择**：新建 exp_01l 独立文件，复用 PlannerAgent / RepairAgent / phase1_common
- **理由**：01b 和 01c 各自是有效的单功能实验（分别对标不同 baseline），合并后不应改变它们的独立可用性
- **被拒绝方案**：在 01b 内加 `--repair` flag → 会使 01b 的接口/输出结构变得复杂

#### 决策 2：修复验证器口径，不改 TaskGraphBuilder API

- **选择**：只修 `verifier/pipeline.py` 中 L2 孤立节点检测逻辑
- **理由**：`add_sync_constraint` 是 standalone explicit constraint，语义上就是"不建 edge、只加约束"；让它自动建 edge 会模糊 DSL 中"依赖关系"和"额外约束"的边界
- **被拒绝方案**：让 `add_sync_constraint` 自动创建 edge → 改变 API 语义，破坏已有代码

#### 决策 3：修复范围限定为 sync + group_sync，不扩展到所有约束类型

- **选择**：只把 `sync` 和 `group_sync` 算作连通性依据
- **理由**：`resource`、`capability`、`time_window`、`physical_feasibility` 等是一元/属性约束，不代表任务间的协调关系；若全部算作连通性依据会过宽放行真正孤立的多任务图
- **边界**：同步约束（sync/group_sync）的语义是"这些任务必须协调执行"，天然具有连通性含义

### 3. 关键发现

| 发现 | 影响 |
|------|------|
| `add_sync_constraint` 只注册约束不建 edge，`add_dependency(relation="sync")` 同时建 edge + 注册约束 | 模型常混用两者，验证器必须对 standalone 形式容错 |
| qwen 模型修复策略：`add_sync_constraint` → `add_group_sync_constraint`（换 API 绕过 L2，而非用正确的 `add_dependency`） | 说明验证器口径不一致会引导模型走错修复路径 |
| `generate_cases` 的 `binary_sync` 家族走的是 `add_dependency(relation="sync")` 正确路径 | ground truth 数据本身无问题，问题纯在验证器侧 |
| deepseek provider 开 `thinking: enabled` + `reasoning_effort: max` + `--workers 64` 会完全卡死 | API 限流 + 单请求太慢，建议 workers ≤ 8 |
| Z3 不建模 per-fleet 时序互斥 | sat 家族中有极少数 case 理论上不可行但 Z3 判 sat（已知盲区，不影响 unsat 家族） |

### 4. 已修复问题清单

| 问题 | 分类 | 根因 | 修复方式 |
|------|------|------|----------|
| L2 对 standalone `sync` 判孤立、对 `group_sync` 判连通 | 真 bug | 孤立节点检测只收集 `group_sync` 类型 | 扩展为 `{"sync", "group_sync"}` |
| 错误提示文案只提"组同步约束" | 逻辑过简 | 文案写死了 group_sync | 改为"同步约束" |

### 5. 设计权衡

| 做法 | 理由 |
|------|------|
| 修复后 `add_sync_constraint` 仍不建 edge | prompt 已明确：relation sync 应用 `add_dependency`；standalone constraint 是补充声明，不应自动产生结构副作用 |
| 修复后 `edge_complete` 仍会对 standalone sync 扣分 | 正确行为：expected 中有 sync relation edge，模型没建 edge 就该扣分；L2 通过只是不误判孤立 |
| exp_01l 不自动 retry API 超时 | phase1_common 的 worker 兜底已足够；retry 逻辑在 LLMClient 层处理 |

### 6. 仍然存在的问题与后续改进方向

| 问题 | 优先级 | 说明 |
|------|--------|------|
| 修复 prompt 无多轮上下文累积 | 中 | 当前每轮修复独立，无法利用前轮修复尝试的信息；可考虑把前轮 diff 或失败原因链式传入 |
| `fly_to` duration 默认值不一致 | 中 | `action_templates.yaml` 的 `min_duration: null` 被 `task_plan_loader` 默认为 1.0、被 `ConfigIndex` 默认为 0.0，需统一 |
| Z3 per-fleet 时序互斥盲区 | 低 | 已知局限，影响极少数 sat case，暂不改 |
| ~~deepseek/qwen 的 sync API 选择倾向~~ | ~~中~~ | ✅ 已解决（20260610）：生成 prompt 立 `add_group_sync_constraint` 为首选规范写法并移除 `relation="sync"`；重跑 216 样本同步类全部收敛到 group_sync（68/68），`add_sync_constraint`/sync 边零出现 |
| `generate_cases` 结构去重天花板 ~341 | 低 | 当前 216 条 trial 数据足够实验；若需扩大数据集需引入更多家族或放宽去重粒度 |
| ~~缺少 prompt-API 对齐测试~~ | ~~中~~ | ✅ 已解决：`tests/test_prompt_api_alignment.py`（4 个生成 prompt × 方法存在性/签名参数/relation 字面量）+ `tests/test_sync_equivalence_scoring.py`（sync 等价评分与 prompt 归一）|
| ~~未在修复后全量重跑验证~~ | ~~高~~ | ✅ 已完成（20260610 run `…20260610-120533`）：216/216 first_pass，`initial/final_dag_exact_rate=1.0`，独立审计工具交叉复核 216/216 整图精确匹配，repair_attempt_rate=0 |
| 评分采用 sync/group_sync 语义等价口径 | 记录 | `phase1_common._SYNC_EQUIV`：sync 边 / sync 约束 / group_sync 约束三种编码 Z3 语义相同，评分互认；dag_exact 同口径。若未来要求字面编码一致需另加严格模式 |
| 四层验证报告未单独落盘 | 低 | reports/ 只存派生布尔项；首过样本的 L1-L4 逐层明细需离线复算（`VerificationPipeline.verify_gcjp_code(final_code)`）或看修复轮 prompt 内嵌报告 |

### 7. 关键结论

| 结论 |
|------|
| exp_01l 一条命令完成 NL→代码→验证→修复→DAG→指标，可直接对标 01b 单次通过率 |
| 7 项评分指标（node/edge/constraint_complete, l2_graph_pass, l3_expected_result, first_pass, builtgraph_success）与所有 Phase1 实验统一口径 |
| ground truth 可靠性由 generate_cases 的 Z3 gate 保证：sat/unsat 标签经过真实 Z3 求解确认 |
| L2 验证器修复后，sync 和 group_sync 连通性口径一致，不会再误导修复 Agent 换 API 绕过检测 |
| 修复不影响 `edge_complete` 评分——模型不建 edge 仍会被正确扣分，只是不再触发假阳性孤立节点错误 |
| 20260609 run 唯二失败（binary_sync edge_complete / aggregate_disperse constraint_complete）根因是评分按 API 写法而非语义比较 + prompt 与 generate_cases 对"同步"编码口径互相打架；修复 Agent 因四层全绿无信号必然空转 |
| 修订后（评分语义等价 + prompt 归一 group_sync + 修复触发守卫 + dag_exact 第 8 项指标）20260610 重跑：first_pass 216/216、dag_exact_rate=1.0、无效修复调用归零 |
| dag_exact 为最严口径（节点映射/逐边端点方向/同步组及 tolerance 逐一对照 master 真值），由 `--master-dataset` 提供完整真值，仅评估侧使用；离线审计工具 `tools/dataset/diff_run_vs_groundtruth.py` 与其同源 |

### 8. 相关文件

- 本文档关联的核心实验代码：[experiments/exp_01l_standard_nl_to_gcjp_with_repair.py](experiments/exp_01l_standard_nl_to_gcjp_with_repair.py)
- L2 修复位置：[verifier/pipeline.py:218-232](verifier/pipeline.py#L218-L232)
- 回归测试：[tests/test_layer2_sync_connectivity.py](tests/test_layer2_sync_connectivity.py)
- 设计决策（generate_cases 部分）：[docs/dataset_v2_generate_cases_rationale.md](docs/dataset_v2_generate_cases_rationale.md)
- 使用参考（generate_cases 部分）：[docs/dataset_v2_generate_cases_reference.md](docs/dataset_v2_generate_cases_reference.md)
