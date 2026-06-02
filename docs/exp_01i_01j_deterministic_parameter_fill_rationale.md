# 确定性参数填充机制设计文档(exp_01i / exp_01j)

> 主题:为什么要把"系统参数填写"从大模型手里收回,改由 Python 从配置文件确定性注入;
> 以及由此设计的两个并列实验 exp_01i(JSON 骨架)与 exp_01j(骨架代码)的具体方案。
>
> 关联文档:[exp_01b_semantic_contract_refactor_rationale.md](exp_01b_semantic_contract_refactor_rationale.md)

---

## 1. 动机:为什么需要一个"更保险"的机制

### 1.1 现状的问题——参数靠大模型预测

在 1B / 1H([exp_01b_standard_nl_to_gcjp.py](../experiments/exp_01b_standard_nl_to_gcjp.py)、
[exp_01h_standard_nl_to_gcjp_with_config.py](../experiments/exp_01h_standard_nl_to_gcjp_with_config.py))中,
大模型被要求**同时完成两件事**:

1. **生成任务结构**:有哪些任务(actor/action/target)、任务之间的关系、时间窗;
2. **填写系统参数**:每个动作的 `duration_lb` / `energy_cost` / `ammo_cost` /
   `required_capability`,以及每个集群的资源上限(`max_ammo` / `max_energy_kwh`)。

第二件事是问题根源。这些系统参数是**确定性的工程常量**——侦察动作消耗多少能量、
fleet_1 弹药上限是多少,都明明白白写在
[configs/action_templates.yaml](../configs/action_templates.yaml) 和
[configs/capability_model.yaml](../configs/capability_model.yaml) 里。让大模型去"预测"
一个本可查表得到的确定值,带来三重风险:

- **随机性**:大模型是概率生成,同一条指令多次生成可能填出不同数字。实测多轮 run 的
  首轮通过率在 **0% ~ 60%** 之间剧烈波动(见 `out/.../exp_01b` 历史 run 的 metrics)。
- **参数幻觉**:大模型可能编造一个配置表里根本不存在的数值,或张冠李戴
  (把 strike 的弹药消耗填到 reconnaissance 上)。
- **污染 Z3 验证**:Layer 3 的可行性判定依赖这些数字。一旦参数是大模型自造的,
  Z3 验证的就不是"在真实能力模型下是否可行",而是"在大模型自造的数字下是否自洽"——
  sat/unsat 结论失去物理意义。

### 1.2 核心诉求

把大模型的职责**收缩到它真正擅长、且无法被替代的部分**——理解自然语言、抽取作战意图
(谁、对谁、做什么、先后顺序、指挥官规定的时限);而把"查表填参"这件**确定性、
可程序化**的事交给 Python。这样:

- 参数恒等于配置表真值,**随机性彻底消除**;
- 大模型的失败面收窄到**结构层**(动作名错、关系漏、actor 不存在),便于归因;
- Z3 验证回到"真实能力模型下的可行性"这一本来含义。

---

## 2. 关键洞察:确定性引擎其实已经存在

设计前的代码调研发现:**不需要从零造轮子**。

- [gcjp/task_plan_loader.py](../gcjp/task_plan_loader.py) 的
  `build_graph_from_task_plan(plan, action_defaults=, capability_model=)`
  (第 303 行起)已经实现了"给定只含作战语义的任务结构 → 从 YAML 查表填全部系统参数 →
  构出 BuiltGraph"的完整逻辑,而且**绕过代码执行**(直接调 builder API,天然安全)。
  exp_02 早已用它生成 reference graph。
- [schemas/task_plan_schema.json](../schemas/task_plan_schema.json) 中 `tasks` 字段
  **本就不含任何数值参数**(只要 `task_id/actor/action/target`),说明这套数据结构
  从设计之初就是为"结构与参数解耦"准备的。
- [gcjp/skeleton_filler.py] 以外的所有依赖(YAML 加载、代码执行、三层验证、JSON/代码
  抽取、并发与输出骨架)都有现成实现可复用。

因此本次工作的本质是:**把已有的确定性填参引擎接到大模型生成流程上**,而不是发明新机制。

---

## 3. 设计原则

1. **职责分离**:大模型只输出"作战语义骨架",Python 确定性填系统参数。
2. **保留指挥官时间语义**:NL 中明确出现的 `time_window` / `deadline` 是作战意图本身
   (是触发 unsat 的关键),由大模型保留为真实数值;只有"指挥官通常不会说"的工程参数
   才交给 Python。
3. **两条实现并列、可对照**:用户既要"JSON 骨架"也要"骨架代码"两种形态,因此做成两个
   并列实验,共享同一套配置引擎与评分口径。
4. **不动基线**:1B / 1H 原样保留,作为"大模型填参"的对照基线。
5. **防答案泄露**:沿用 1B 纪律,`expected_patterns / expected_result / tags` 这些评分
   真值**绝不进 prompt**,只在评分器里从原始 case 读取。

---

## 4. 两个实验的具体设计

两者输入相同(`datasets/phase1_standard_nl_cases.jsonl`,与 1B/1H 同源),
区别只在"大模型输出什么形态"以及"是否经过代码执行层"。

### 4.1 实验 A —— exp_01i:JSON 骨架 + 确定性构图

文件:[experiments/exp_01i_nl_to_taskplan_json_deterministic.py](../experiments/exp_01i_nl_to_taskplan_json_deterministic.py)

```
NL 标准指令
  │  (LLM: PlanExtractorAgent)
  ▼
task_plan JSON 骨架   ← 只含 task_id/actor/action/target/relations/time_window
  │  json 解析 + 最小结构校验
  ▼
build_graph_from_task_plan(plan, action_defaults, capability_model)
  │  ← Python 从 YAML 确定性填 duration/energy/ammo/capability/资源上限
  ▼
BuiltGraph
  │  VerificationPipeline.verify_graph  (跳过 Layer 1,无代码)
  ▼
L2 图结构 + L3 Z3  →  evaluate_graph_against_expected  →  metrics
```

组成:

- **提示词** [prompts/standard_nl_to_task_plan_json_prompt.md](../prompts/standard_nl_to_task_plan_json_prompt.md):
  要求大模型**只输出 JSON**,字段限定为作战语义,**明令禁止**输出
  duration/energy/ammo/required_capability/资源上限。给出动作白名单、关系白名单和最小示例
  (示例特意不含任何数字)。
- **Agent** [agents/plan_extractor_agent.py](../agents/plan_extractor_agent.py):
  封装 LLM 调用 + 复用现成的 `extract_json_object`
  ([agents/json_extraction.py](../agents/json_extraction.py))抽取并解析 JSON,
  返回 `PlanGeneration`(raw_response + parsed_plan + extraction 状态)。
- **构图与验证**:`build_graph_from_task_plan` + `VerificationPipeline.verify_graph`,
  全部现成。动作名不在 YAML / actor 不在 capability_model → build 抛错 → 记为构图失败。

### 4.2 实验 B —— exp_01j:骨架代码 + AST 占位符填充

文件:[experiments/exp_01j_nl_to_skeleton_code_deterministic.py](../experiments/exp_01j_nl_to_skeleton_code_deterministic.py)

```
NL 标准指令
  │  (LLM: PlannerAgent)
  ▼
带占位符的 GCJP 骨架代码   ← 系统参数处写裸名 sentinel(FILL_DURATION 等)
  │  skeleton_filler:ast 解析 → 按 action/actor 上下文从 YAML 解析为字面量 → unparse
  ▼
填好的可执行 GCJP 代码
  │  execute_gcjp_code + VerificationPipeline.verify_gcjp_code  (完整 L1+L2+L3)
  ▼
L1 受限执行 + L2 图结构 + L3 Z3  →  evaluate_graph_against_expected  →  metrics
```

组成:

- **提示词** [prompts/standard_nl_to_gcjp_skeleton_prompt.md](../prompts/standard_nl_to_gcjp_skeleton_prompt.md):
  要求大模型输出**完整 GCJP 代码结构**,但系统参数处一律写固定的**裸名占位符**:

  | 位置 | 参数 | 占位符 |
  |---|---|---|
  | `add_task` | duration_lb / energy_cost / ammo_cost / required_capability | `FILL_DURATION` / `FILL_ENERGY` / `FILL_AMMO` / `FILL_CAPABILITY` |
  | `add_resource_constraint` | max_value(ammo / energy) | `FILL_MAX_AMMO` / `FILL_MAX_ENERGY` |
  | `add_capability_constraint` | required / actor_capabilities | `FILL_CAPABILITY` / `FILL_ACTOR_CAPS` |

  指挥官时间语义(deadline 等)仍写**真实数值**。

- **填充器** [gcjp/skeleton_filler.py](../gcjp/skeleton_filler.py):用标准库 `ast`,两遍处理——
  1. 第一遍扫描所有 `g.add_task` 调用,建立 `task_id → (actor, action)` 映射;
  2. 第二遍遍历 `add_task` / `add_resource_constraint` / `add_capability_constraint` 调用,
     把其中的 sentinel `Name` 节点按"所在调用的 action/actor/task 上下文"从 YAML 解析为
     字面量节点(`ast.Constant` / 列表),再 `ast.unparse` 回字符串。
  - 若有未知 action/actor、缺上下文、残留占位符,返回 `ok=False` 并带错误信息。
  - 容错:占位符被误写成 `[FILL_CAPABILITY]`(单元素列表)也能识别。
- **执行与验证**:填好的代码走 `execute_gcjp_code` + 完整 `verify_gcjp_code`,与 1B 同口径。

### 4.3 A / B 与基线的验证入口对照

| 实验 | 最终产物 | 验证入口 | Layer 1(代码执行) | L2 | L3 |
|---|---|---|---|---|---|
| A exp_01i | BuiltGraph(Python 直接构) | `verify_graph(graph)` | 跳过(无代码) | ✅ | ✅ |
| B exp_01j | 填好的 GCJP 代码字符串 | `verify_gcjp_code(code)` | ✅ 子进程执行 | ✅ | ✅ |
| 1B(基线) | 大模型全量代码字符串 | `verify_gcjp_code(code)` | ✅ | ✅ | ✅ |

A 与 B 的 L2/L3 完全一致,差别只在"是否经过代码执行层"——这正是两条形态的对照价值:
A 验证"纯结构注入"的上限,B 验证"代码路径仍可行"。

---

## 5. 共享评分:保证四方口径一致

为了让 1B / 1H / exp_01i / exp_01j 能直接横比,从
[experiments/phase1_common.py](../experiments/phase1_common.py) 抽出了与生成方式无关的
图层评分函数 `evaluate_graph_against_expected(case, graph, report)`,产出七项一致指标:
`builtgraph_success / l2_graph_pass / l3_expected_result / node_complete /
edge_complete / constraint_complete / first_pass`。原 `_evaluate_expected` 改为调用它再补
exec 相关字段,旧实验输出保持不变。

各实验**独有**的前段"阶段指标"用于定位失败发生在哪一环:

- exp_01i:`json_parse_ok → schema_valid → build_success`
- exp_01j:`skeleton_extract → fill_success → safety_pass → execution_success`
- 1B:`syntax_extract → safety_pass → execution_success`

`_aggregate_metrics` 增加了 `rate_keys` 参数以容纳不同阶段指标。

---

## 6. 文件清单

**新增**

| 文件 | 作用 |
|---|---|
| [agents/plan_extractor_agent.py](../agents/plan_extractor_agent.py) | exp_01i 的 NL→JSON 骨架抽取 Agent |
| [gcjp/skeleton_filler.py](../gcjp/skeleton_filler.py) | exp_01j 的 AST 占位符确定性填充器 |
| [experiments/exp_01i_nl_to_taskplan_json_deterministic.py](../experiments/exp_01i_nl_to_taskplan_json_deterministic.py) | 实验 A 主体 |
| [experiments/exp_01j_nl_to_skeleton_code_deterministic.py](../experiments/exp_01j_nl_to_skeleton_code_deterministic.py) | 实验 B 主体 |
| [prompts/standard_nl_to_task_plan_json_prompt.md](../prompts/standard_nl_to_task_plan_json_prompt.md) | 实验 A 提示词 |
| [prompts/standard_nl_to_gcjp_skeleton_prompt.md](../prompts/standard_nl_to_gcjp_skeleton_prompt.md) | 实验 B 提示词 |
| [tests/test_taskplan_deterministic_build.py](../tests/test_taskplan_deterministic_build.py) | A 路径离线自测 |
| [tests/test_skeleton_filler.py](../tests/test_skeleton_filler.py) | B 路径离线自测 |

**改动**

- [experiments/phase1_common.py](../experiments/phase1_common.py):抽出 `evaluate_graph_against_expected`,
  `_aggregate_metrics` 支持自定义 `rate_keys`;旧实验输出不变。

---

## 7. 验证

**离线确定性自测(不依赖 API)**已全部通过(8/8),证明两条路径都能从 YAML 正确填入
参数(逐字段等于真值)、构图、Z3=sat,且未知 action 时优雅失败:

```powershell
python -m unittest tests.test_taskplan_deterministic_build tests.test_skeleton_filler -v
```

既有测试无回归(`tests.test_instruction_validators` 5/5),全量模块 import 正常。

---

## 8. 如何运行与预期对照

```powershell
# 三方对照,同数据集(12 条),<P> 替换为 configs/llm_providers.local.yaml 中的 profile
python -m experiments.exp_01b_standard_nl_to_gcjp               --provider-profile <P> --workers 4
python -m experiments.exp_01i_nl_to_taskplan_json_deterministic --provider-profile <P> --workers 4
python -m experiments.exp_01j_nl_to_skeleton_code_deterministic --provider-profile <P> --workers 4
```

产物落在 `out/phase1_generation/<provider__model__时间戳>/<实验名>/`,含原始回复、
骨架/填充产物、逐条 reports、`metrics.json`。

**预期趋势**(本次改造要验证的假设):

- 1B(大模型填参):`first_pass_rate` / `l3_expected_result_rate` 受参数随机性拖累,波动大。
- exp_01i / exp_01j(Python 填参):参数恒为真值,失败只来自结构层,通过率应**更高更稳**。
- exp_01i vs exp_01j:若两者接近,说明代码执行层不是瓶颈;若 B 偏低,差距来自骨架代码本身
  的语法/安全问题。

横比时看四方口径一致的 `first_pass_rate / l3_expected_result_rate /
node_complete_rate / edge_complete_rate / constraint_complete_rate`。
