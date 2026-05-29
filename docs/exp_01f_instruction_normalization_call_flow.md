# exp_01f_instruction_normalization 指令规范化调用流程分析

本文按照 `experiments/exp_01f_instruction_normalization.py` 的内部调用顺序，梳理 Phase 1F 真实指挥指令语义规范化实验的完整函数调用流程、两种运行模式、关键状态变量、评估规则与输出结构。

Phase 1F 的目标不是直接生成 GCJP 代码，而是评估 normalizer 是否能把一条真实自然语言作战指令解析成结构化 JSON，并判断该指令的作战语义是否足够完整。持续时间、能耗、弹药消耗、资源上限等系统参数由后续配置注入，不要求指挥官在原文中提供。

## 1. 总体入口

调用入口位于 `experiments/exp_01f_instruction_normalization.py`：

```text
main()
  -> add_common_args(parser)
  -> 解析 --dataset / --prompt / --mode / --max-clarification-rounds
  -> print_provider_summary_from_args(args)
  -> run_normalization_experiment(args)
  -> 返回退出码
```

真正的实验主流程在 `run_normalization_experiment(args)`：

```text
run_normalization_experiment(args)
  -> load_jsonl(args.dataset, limit=args.limit)
  -> load_config_from_args(args)
  -> InstructionNormalizerAgent(LLMClient(config))
  -> read_prompt_template(args.prompt)
  -> resolve_phase1_run_output(...)
  -> 创建 exp_dir / raw_outputs / parsed_outputs
  -> 定义 _worker(case)
  -> 定义 _on_complete(record)
  -> run_cases_concurrent(cases, _worker, ...)
  -> _aggregate_metrics(records, args.mode, cases)
  -> 写 metrics.json
  -> write_latest_run_index(...)
  -> 可选 save_baseline_json / append_baseline_markdown
```

这里完成了六件事：

1. 读取待规范化的自然语言指令样本。
2. 读取 LLM provider 配置和 prompt 模板。
3. 构造 `InstructionNormalizerAgent`。
4. 按模式对每个 case 执行 single-shot 或 clarification-loop。
5. 保存原始 LLM 输出、解析后的 JSON 和汇总指标。
6. 可选写入 baseline，用于后续对比。

## 2. 参数与样本加载流程

`main()` 在公共参数之外增加了 4 个 Phase 1F 专用参数：

```text
--dataset
  默认 datasets/phase1_instruction_normalization_eval.jsonl

--prompt
  默认 prompts/instruction_normalization_prompt.md

--mode
  可选 single-shot / clarification-loop
  默认 single-shot

--max-clarification-rounds
  clarification-loop 最大轮数
  默认 5
```

样本加载只走一条路径：

```text
cases = load_jsonl(args.dataset, limit=args.limit)
```

每个 case 主要依赖以下字段：

```text
sample_id
raw_instruction
expected_status
expected_missing_fields
expected_ambiguity_spans
scripted_clarifications
expected_status_after_clarification
tags
```

其中 `raw_instruction` 是待分析的指挥官原始自然语言指令；`expected_status` 是 single-shot 阶段期望的完整性标签；`scripted_clarifications` 用于 clarification-loop 模式模拟指挥官补充说明。

如果数据集为空，主流程会直接抛出：

```text
ValueError("No cases loaded from ...")
```

## 3. 输出目录初始化

实验输出目录由公共工具 `resolve_phase1_run_output()` 决定：

```text
run_output = resolve_phase1_run_output(
  output_dir=args.output_dir,
  provider_summary=provider_summary,
  run_label=args.run_label,
  no_run_timestamp=args.no_run_timestamp
)
```

随后 Phase 1F 在该 run 目录下创建实验子目录：

```text
exp_dir = run_output["run_dir"] / "exp_01f_instruction_normalization"
raw_dir = exp_dir / "raw_outputs"
parsed_dir = exp_dir / "parsed_outputs"
```

最终结构大致为：

```text
out/phase1_generation/{run_label_or_timestamp}/exp_01f_instruction_normalization/
  raw_outputs/{sample_id}.txt
  parsed_outputs/{sample_id}.json
  metrics.json
```

`raw_outputs` 保存模型原始文本回复；`parsed_outputs` 保存抽取出的 JSON 对象，如果抽取失败则写入 `null`。

## 4. 并发执行与单样本分发

Phase 1F 使用公共函数 `run_cases_concurrent()` 并发处理样本：

```text
records = run_cases_concurrent(
  cases,
  _worker,
  workers=args.workers,
  on_complete=_on_complete,
  show_usage=args.show_usage
)
```

单个样本由内部函数 `_worker(case)` 分发：

```text
_worker(case)
  -> sample_id = case["sample_id"]
  -> 如果 args.mode == "single-shot":
       _run_single_shot(case, agent, prompt_template, sample_id)
     否则:
       _run_clarification_loop(
         case, agent, prompt_template, sample_id,
         max_rounds=args.max_clarification_rounds
       )
  -> 如果异常:
       _error_record(case, exc)
```

因此，`run_normalization_experiment()` 自身不关心具体模式细节，只负责为每个 case 选择执行路径，并收集统一格式的 `record`。

每个 case 完成后 `_on_complete(record)` 会执行：

```text
_on_complete(record)
  -> 写 raw_outputs/{sample_id}.txt
  -> 写 parsed_outputs/{sample_id}.json
  -> print(_summary_line(record))
```

## 5. single-shot 核心流程

single-shot 模式用于评估模型首轮识别能力，核心函数是 `_run_single_shot()`：

```text
_run_single_shot(case, agent, prompt_template, sample_id)
  -> agent.normalize(
       sample_id=sample_id,
       prompt_template=prompt_template,
       raw_instruction=case["raw_instruction"]
     )
  -> _evaluate_normalization(case, result)
  -> 返回 record
```

返回的 record 结构包括：

```text
sample_id
mode = "single-shot"
expected_status
raw_response
parsed_output
extraction
predicted_status
model_reported_status
status_overridden_by_invariant
usage
evaluation
```

这里的关键点是：

```text
predicted_status
  是经过 invariant 修正后的最终状态

model_reported_status
  是模型 JSON 中原始声明的 status

status_overridden_by_invariant
  表示模型是否被代码侧一致性规则纠正
```

也就是说，模型如果一边输出 `status="complete"`，一边又给出 `missing_fields` 或 `ambiguities`，最终会被强制改为 `incomplete`。

## 6. clarification-loop 核心流程

clarification-loop 模式用于评估多轮澄清是否能把不完整指令补全，核心函数是 `_run_clarification_loop()`：

```text
_run_clarification_loop(case, agent, prompt_template, sample_id, max_rounds)
  -> answers = case.get("scripted_clarifications", [])
  -> loop = ClarificationLoop(
       agent,
       max_rounds=max_rounds,
       input_fn=scripted_input_fn(answers)
     )
  -> loop.run(
       sample_id=sample_id,
       prompt_template=prompt_template,
       raw_instruction=case["raw_instruction"]
     )
  -> 根据 expected_status_after_clarification 构造 eval_case
  -> _evaluate_normalization(eval_case, final)
  -> 补充 loop_final_status / total_rounds / clarification_success
  -> 返回 record
```

该模式下的期望状态来自：

```text
expected_after = case.get("expected_status_after_clarification", "complete")
```

如果澄清后期望为 `complete`，代码会把：

```text
eval_case["expected_missing_fields"] = []
```

这表示澄清闭环结束后不应该再缺少语义字段。

clarification-loop record 比 single-shot 多出：

```text
clarification_history
total_rounds
loop_final_status
usage = 每轮 LLM 调用 usage 列表
```

其中 `clarification_success` 的判定是：

```text
loop_result.final_status == "complete"
并且 evaluation["semantic_structure"] is not False
```

注意这里不是只看模型自报 complete，还要通过 `_complete_output_is_semantically_ready()` 的结构完整性检查。

## 7. ClarificationLoop 内部闭环

澄清闭环控制器位于 `agents/clarification_loop.py`：

```text
ClarificationLoop.run(...)
  初始化:
    history = []
    all_results = []

  for round_idx in range(max_rounds):
    result = agent.normalize(
      raw_instruction=raw_instruction,
      clarification_history=history if history else None,
      clarification_round=round_idx
    )
    all_results.append(result)

    如果 result.status == "complete":
      返回 final_status="complete"

    commander_input = input_fn(result.ambiguities, result.missing_fields)

    如果 commander_input is None:
      返回 final_status="user_abort"

    history.append({
      round,
      ambiguities_shown,
      missing_fields_shown,
      commander_input
    })

  达到最大轮数:
    返回 final_status="max_rounds_exceeded"
```

这个闭环里的关键状态变量是：

```text
history
all_results
result.status
commander_input
final_status
```

`scripted_input_fn(answers)` 会把数据集里的 `scripted_clarifications` 包装成自动输入源：

```text
scripted_input_fn(answers)
  -> 每次被调用时返回 answers 中的下一个字符串
  -> 用完后返回 None
```

因此，实验可以在不需要人工终端输入的情况下模拟多轮指挥官澄清。

## 8. InstructionNormalizerAgent 调用流程

LLM 规范化逻辑位于 `agents/instruction_normalizer_agent.py` 的 `InstructionNormalizerAgent.normalize()`：

```text
InstructionNormalizerAgent.normalize(...)
  -> render_normalization_prompt(
       prompt_template,
       raw_instruction=raw_instruction,
       clarification_history=clarification_history
     )
  -> self.client.generate([
       {"role": "system", "content": "...仅输出 JSON..."},
       {"role": "user", "content": prompt}
     ])
  -> extract_json_object(response.text)
  -> _build_result(...)
```

prompt 渲染只替换两个占位符：

```text
{{RAW_INSTRUCTION}}
  原始作战指令

{{CLARIFICATION_HISTORY}}
  已有澄清记录，没有则为空字符串
```

澄清记录由 `_format_clarification_history()` 转成文本，格式上会把每轮指挥官补充说明拼回 prompt，使下一轮模型能基于完整上下文重新判断。

`_build_result()` 把 LLM 回复封装为 `NormalizationResult`：

```text
NormalizationResult
  sample_id
  prompt
  raw_response
  parsed_output
  extraction
  status
  standard_instruction
  resolved_fields
  missing_fields
  ambiguities
  clarification_round
  model
  model_source
  provider
  usage
  model_reported_status
  status_overridden_by_invariant
```

其中最重要的代码侧 invariant 是：

```text
如果 status == "complete" 且 missing_fields 或 ambiguities 非空:
  status = "incomplete"
  standard_instruction = None
  status_overridden_by_invariant = True
```

这个 invariant 保证模型输出不会出现“声明 complete 但又列出缺失或歧义”的自相矛盾状态。

## 9. JSON 抽取流程

JSON 抽取逻辑位于 `agents/json_extraction.py`：

```text
extract_json_object(raw_response)
  1. 优先寻找第一个 json fenced code block
     如果 json.loads 成功且结果是 dict:
       ok=True, method="fenced"

  2. 如果没有有效 fenced JSON:
       _try_bare_json(raw_response)

  3. 如果仍然失败:
       ok=False, error="未找到有效的 JSON 对象。"
```

`_try_bare_json()` 的策略是：

```text
从文本中找到第一个 "{"
用括号深度计数寻找匹配的 "}"
期间跳过字符串内部的括号
对候选片段 json.loads
成功且为 dict 则返回
否则继续寻找下一个 "{"
```

因此，模型即使额外输出了解释性文字，只要回复里有一个合法 JSON 对象，仍然可以被解析；但如果 JSON 语法错误或不是对象类型，则进入抽取失败分支。

## 10. 规范化结果评估流程

每个 `NormalizationResult` 都会经过 `_evaluate_normalization(case, result)`：

```text
_evaluate_normalization(case, result)
  如果 result is None:
    返回全失败评估

  expected_status = case["expected_status"]
  predicted_status = result.status
  json_ok = result.extraction["ok"]

  如果 predicted_status == "complete":
    -> _complete_output_is_semantically_ready(result.parsed_output)

  status_correct = predicted_status == expected_status
  如果 predicted_status == "complete" 但 semantic_structure 为 False:
    status_correct = False

  expected_missing = _canonical_missing_fields(case.expected_missing_fields)
  predicted_missing = _canonical_missing_fields(result.missing_fields + ambiguity_fields)
  missing_detected = expected_missing 是否为 predicted_missing 子集

  expected_ambiguities = case.expected_ambiguity_spans
  ambiguity_detected = 预测歧义数量是否覆盖期望歧义数量

  false_complete = expected_status == "incomplete" 且 predicted_status == "complete"

  返回 evaluation
```

返回的 evaluation 字段包括：

```text
json_parse_success
status_correct
missing_field_detected
ambiguity_detected
false_complete
semantic_structure
missing_semantic_task_fields
```

这里的成功不是单纯看 JSON 能否解析，而是同时考察：

```text
状态判断是否正确
缺失字段是否被识别
歧义是否被识别
complete 输出是否真的具备可交给后续 GCJP 生成阶段的结构
```

## 11. 缺失字段归一化

缺失字段判断不直接比较模型输出字符串，而是先走 canonical 归一化：

```text
_canonical_missing_fields(fields)
  -> 对每个 field 调用 _canonical_missing_field(field)
  -> 返回 set[str]
```

`_canonical_missing_field()` 会处理两类情况。

第一类是字典表直接映射：

```text
actor / actors / assigned_actor / assigned_actors -> assigned_actors
target -> target
action -> action
relation / relations / order -> relation
condition / trigger -> condition
split / split_assignment / assignment -> split_assignment
```

第二类是模糊关键词匹配：

```text
包含 actor / 编队 / 主体        -> assigned_actors
包含 target / 目标 / 区域 / 点位 -> target
包含 action / 动作              -> action
包含 relation / 关系 / 顺序      -> relation
包含 condition / trigger / 条件 -> condition
包含 split / 分配 / 拆分         -> split_assignment
```

这样做的目的，是允许模型使用不同表述，但评估仍能落到统一维度上。例如模型输出 `actors`、`assigned_actor` 或中文“编队”，都算作 `assigned_actors` 维度。

## 12. complete 输出的语义结构检查

如果模型最终预测 `complete`，还必须通过 `_complete_output_is_semantically_ready(parsed_output)`：

```text
_complete_output_is_semantically_ready(parsed_output)
  -> parsed_output 必须是 dict
  -> parsed_output["resolved_fields"] 必须是 dict
  -> resolved_fields["assigned_actors"] 必须是非空 list
  -> resolved_fields["tasks"] 必须是非空 list
  -> 如果 tasks 数量 > 1:
       resolved_fields["relations"] 必须是非空 list
  -> 每个 task 必须是 dict
  -> 每个 task 必须具备 actor/action/target 三个语义值
```

必需字段由常量定义：

```text
SEMANTIC_TASK_REQUIRED_FIELDS = ("actor", "action", "target")
```

单个 task 字段是否有语义值由 `_task_field_has_semantic_value()` 判断：

```text
actor/action/target:
  必须是非空字符串

其他字段:
  只要求不是 None
```

这意味着一个输出即使 JSON 格式正确、状态也写了 `complete`，只要缺少：

```text
resolved_fields
assigned_actors
tasks
relations
tasks[i].actor
tasks[i].action
tasks[i].target
```

就会被判为结构未准备好，并导致 `status_correct=False`。

## 13. 指标聚合流程

所有 case 完成后进入 `_aggregate_metrics(records, mode, cases)`：

```text
_aggregate_metrics(records, mode, cases)
  -> total = len(records)
  -> evals = [r["evaluation"] for r in records]
  -> 计算基础 rates
  -> 对 incomplete case 计算缺失/歧义/误判 complete 指标
  -> 计算 invariant override 和模型自一致性
  -> 如果 mode == "clarification-loop":
       计算澄清成功率、平均轮数、澄清效率
  -> 对 predicted_status == "complete" 的 record:
       计算 semantic_structure_success_rate
  -> 返回 metrics
```

基础指标包括：

```text
json_parse_success_rate
status_accuracy_rate
```

针对 incomplete case 额外计算：

```text
missing_field_detection_rate
ambiguity_detection_rate
false_complete_rate
false_complete_by_dimension
```

其中 `false_complete_by_dimension` 会把误判为 complete 的 case 按缺失维度统计，例如：

```text
assigned_actors
target
action
relation
condition
split_assignment
unspecified
```

模型一致性指标包括：

```text
invariant_override_rate
model_self_consistency_rate
```

clarification-loop 模式额外计算：

```text
clarification_success_rate
avg_clarification_rounds
clarification_efficiency
```

最后，对所有预测为 complete 的输出计算结构成功率：

```text
semantic_structure_success_rate
complete_structure_success_rate
```

这两个字段当前使用同一个值，都是 complete 输出里通过语义结构检查的比例。

## 14. 异常与失败分支

Phase 1F 的异常兜底由 `_error_record(case, exc)` 统一处理：

```text
_error_record(case, exc)
  -> sample_id = case["sample_id"]
  -> mode = "error"
  -> raw_response = ""
  -> parsed_output = None
  -> extraction = {}
  -> predicted_status = None
  -> evaluation:
       json_parse_success = False
       status_correct = False
       missing_field_detected = False
       ambiguity_detected = False
       false_complete = False
       semantic_structure = None
       missing_semantic_task_fields = []
       error = "{ExceptionType}: {message}"
```

因此，单个 case 的 LLM 调用、解析、评估异常不会中断整个实验；它会被记录成失败样本，并继续参与汇总指标计算。

另一类失败是 JSON 抽取失败：

```text
extract_json_object(...)
  -> ok=False
  -> parsed_output=None
  -> result.status=None
  -> _evaluate_normalization(...)
       json_parse_success=False
       status_correct=False
```

这类失败不会进入 `_error_record()`，因为流程本身没有抛异常，只是模型输出不满足 JSON 解析要求。

## 15. 输出文件结构

每个 case 完成时 `_on_complete(record)` 写出两个样本级文件：

```text
raw_outputs/{sample_id}.txt
  LLM 原始回复文本

parsed_outputs/{sample_id}.json
  抽取出的 JSON 对象
  抽取失败则为 null
```

实验结束后写出：

```text
metrics.json
```

`metrics.json` 顶层结构包括：

```text
experiment
total_cases
rates
records
base_output_dir
run_dir
run_label
run_label_source
run_dir_name
run_timestamp
run_timestamp_enabled
provider
mode
output_dir
```

其中 `records` 不是完整原始响应，而是精简后的逐样本评估摘要：

```text
sample_id
expected_status
predicted_status
model_reported_status
status_overridden_by_invariant
evaluation
```

完整原始响应和完整解析 JSON 分别在 `raw_outputs` 与 `parsed_outputs` 中。

## 16. 总结调用链

single-shot 模式可以压缩为：

```text
raw_instruction
  -> render_normalization_prompt()
  -> LLMClient.generate()
  -> extract_json_object()
  -> _build_result()
       如 complete 但存在 missing_fields/ambiguities:
         强制改为 incomplete
  -> _evaluate_normalization()
       JSON 是否可解析
       status 是否正确
       缺失字段是否覆盖
       歧义数量是否覆盖
       complete 结构是否语义就绪
  -> 写 raw_outputs / parsed_outputs
  -> _aggregate_metrics()
```

clarification-loop 模式可以压缩为：

```text
raw_instruction
  -> 第 0 轮 normalize()
  -> 如果 complete:
       结束
     否则:
       scripted_input_fn() 提供指挥官补充说明
       history.append(...)
  -> 下一轮 normalize(raw_instruction + clarification_history)
  -> 重复直到:
       complete
       user_abort
       max_rounds_exceeded
  -> 用 final_result 做 _evaluate_normalization()
  -> 聚合 clarification_success_rate / avg_clarification_rounds
```

一句话概括：

```text
exp_01f_instruction_normalization.py 实现的是“真实自然语言作战指令到结构化语义 JSON 的 LLM 规范化评估”：
single-shot 评估首轮完整性识别能力，clarification-loop 评估多轮补充说明能否把 incomplete 指令推进到可供后续 GCJP 生成阶段使用的 complete 结构。
```
