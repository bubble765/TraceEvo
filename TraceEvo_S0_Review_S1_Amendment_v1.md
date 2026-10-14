# TraceEvo：S0评审结论与S1修订指令 v1.0

## 0. 评审结论

**结论：GO，允许进入 S1。**

S0 已满足原计划门槛：10个病例可稳定运行、GoS Algorithm 1/2 已映射到源码、评测脚本可重复、上游代码和工作分支隔离、关键配置和原始输出均已保存。

S0 的两个最重要发现——GoS Evidence 节点缺乏执行来源约束、`central_agent.graphs` 不能提供真实历史快照——直接强化了 TraceEvo 的研究动机。S1 不需要重新设计研究方向，但必须按本修订指令调整。

**首次执行本文件时，仅执行 S1。S1 完成后停止，不得自动进入 S2。**

---

# 1. 对 S0 三个待决问题的正式决定

## 决定 D1：当前不做 δ/η 全量敏感性扫描

接受“10例、当前作者配置下 backtracking 仅触发3次”为**基线观察**，不把它解释成 GoS 的普遍结论。

理由：

1. S1 的事件采集和溯源图构建不依赖 backtracking 的自然触发频率；
2. 此时调 δ/η 会改变冻结的基线行为，并可能在 Dev-Small 上产生调参偏差；
3. TraceEvo 的核心是执行溯源与可信进化，而不是优化 GoS 的状态转换超参数。

S1 只增加一个**确定性的 Backtracking instrumentation 单元测试**，验证 BACKTRACK 事件可以被正确记录。可直接构造一个最小 BeliefGraph/FSM 状态触发回溯，不要求额外运行大量 LLM 病例。

δ/η 敏感性分析延期到 S5；若 S2 需要展示真实病例中的回溯传播，可再设计受控异常场景。

---

## 决定 D2：不修改 GoS 原始 Evidence 行为；在 Provenance 层做来源分类

S1 **不得清洗、删除或重写 GoS Causal Graph 中的 Evidence 节点**，否则后续与 GoS baseline 的性能差异会混入“图清洗”因素。

S1 应新增独立 provenance 元数据，对 GoS 节点的**来源**做确定性标记。

优先采用来源分类，而不是一开始让 LLM 判断语义：

- `INPUT_OBSERVATION`：来自初始主诉/体检；
- `RETRIEVED_RECORD`：确实由 `evidence_retriever` 返回的外部检查记录；
- `AGENT_GENERATED`：由专家/中心智能体生成，但没有真实 retrieval 来源；
- `SYSTEM_GENERATED`：框架内部生成的控制性对象；
- `UNKNOWN`：暂时无法确定。

可再增加可选 `semantic_kind`：

- `clinical_evidence`
- `domain_knowledge`
- `agent_inference`
- `absence_claim`
- `recommendation`
- `hypothesis`
- `conclusion`

但 `semantic_kind` 在 S1 不作为强制审计判据，避免把一个未验证的语义分类器引入核心结果。

S0 的“27.7% 无来源 Evidence”目前视为**探索性发现**。由于交付包中尚无自动化、可重复的 Evidence 分类程序，该数字暂时不能作为论文正式实验结果。S1 必须通过 event binding 产生可重复的 source-grounding 指标。

后续消融增加一项：

`w/o Source-aware Evidence Typing`

若未来尝试在线过滤/修复 GoS Evidence，则必须作为独立实验变体，不得并入主方法而不做消融。

---

## 决定 D3：S3/S4 不采用“提升 > 2σ”作为主判据

`>2σ` 基于10例聚合 judge 波动，统计意义不稳健，不作为后续正式成功判据。

从 S1 起冻结数据划分：

- `Dev-Small`: case 0–9（已使用，仅调试）
- `Dev-Evolve`: case 10–49（40例，用于 S3/S4 方法开发）
- `Validation`: case 50–69（20例，用于阈值/超参数选择）
- `Held-out Test`: case 70–137（68例，S5 方法冻结后才允许运行）

S1/S2 的主要判据使用**确定性审计指标**，不依赖诊断 Judge。

S3/S4 的诊断效果采用：

1. 同一 prediction 使用低温度 Judge（优先 `temperature=0`；若接口不支持则取可用最低值）；
2. 每例至少重复3次，取 case-level median/majority score；
3. 方法比较采用**同病例配对比较**；
4. 报告 paired bootstrap 95% CI；
5. 同时报告 Judge disagreement rate；
6. Match/Relevant 只作为结果之一，不作为“可信进化成立”的唯一证据。

S3/S4 的核心成功指标优先使用：
- Poisoned Experience Admission Rate；
- Repeated Error Rate；
- 审计异常检测 F1；
- Root-cause localization Accuracy；
- Evidence trace success rate；
- 后续规划/选Agent行为是否发生可解释变化。

S5 最终投稿实验应尽量加入盲法人工评价。若无法获得临床专家，至少对 Judge 不一致病例进行独立人工复核，并明确作为局限性报告。

---

# 2. S1 修订后的研究目标

S1 的唯一目标：

> **在不改变 GoS 推理决策的条件下，构建一个可重放、可验证、可与 Causal Graph 交叉关联的执行 Provenance Layer。**

S1 完成时必须能够回答：

1. 某个 GoS Evidence/Hypothesis 节点由哪一次操作产生？
2. 该 Evidence 是输入事实、真实检索记录，还是 agent-generated assertion？
3. 某次 `UpdateGraph` 前后图发生了什么 delta？
4. 最终 prediction/report 可以回溯到哪些 GoS 节点、哪些 agent/tool 调用、哪些外部记录？
5. 如果关键事件被删除/篡改，基础完整性检查是否能发现？

S1 **不实现正式异常检测算法，不实现 Experience Memory，不实现自进化。**

---

# 3. S1 必须新增的数据模型

## 3.1 ProvenanceEvent

最低字段：

```json
{
  "event_id": "evt-...",
  "task_id": "case-...",
  "step_id": 1,
  "timestamp": "...",
  "event_type": "PLAN|AGENT_CALL|TOOL_CALL|EVIDENCE_READ|ANALYSIS|GRAPH_UPDATE|BACKTRACK|DRILL_DOWN|FINAL_REPORT",
  "actor_id": "...",
  "actor_role": "...",
  "tool_or_model": "...",
  "input_refs": [],
  "output_refs": [],
  "causal_node_refs": [],
  "parent_event_ids": [],
  "source_class": "INPUT_OBSERVATION|RETRIEVED_RECORD|AGENT_GENERATED|SYSTEM_GENERATED|UNKNOWN",
  "model_version": "...",
  "status": "success|failed",
  "prev_hash": "...",
  "event_hash": "..."
}
```

要求：
- canonical JSON 后计算 `event_hash`；
- 同任务事件形成 hash chain；
- 所有事件必须可 JSONL 顺序重放；
- 不记录 API key；
- 医疗数据保持原 benchmark 范围，不新增身份字段。

---

## 3.2 GraphSnapshot / GraphDelta

**禁止使用 `central_agent.graphs` 作为历史真值。**

在每次关键图更新处执行 `copy.deepcopy(graph.to_dict())`，或优先记录 GraphDelta：

```json
{
  "delta_id": "...",
  "event_id": "...",
  "added_nodes": [],
  "updated_nodes": [],
  "deleted_nodes": [],
  "added_edges": [],
  "deleted_edges": []
}
```

必须保证：

`Replay(G_t, delta_t) == G_(t+1)`

至少对10个 Dev-Small 病例自动测试。

---

## 3.3 Cross-Graph Binding

维护：

```text
provenance_event_id
    ↕
GoS causal node_id / edge_id
    ↕
retrieval record_id / input span_id
```

关键要求：

- 每个真正来自 `evidence_retriever` 的 Evidence 必须绑定 `retrieval_event_id`；
- `AGENT_GENERATED` 内容不得伪装成 `RETRIEVED_RECORD`；
- 新 Hypothesis 必须绑定生成它的 GRAPH_UPDATE / ANALYSIS 事件；
- Final Report 必须绑定其直接依赖的 reasoning state。

---

# 4. S1 推荐 hook 位置

根据 S0 的 `gos_code_mapping.md`，优先在以下语义位置增加非侵入式 hook：

1. `Central_Agent.ingest()`：记录 INPUT_OBSERVATION 和初始图；
2. `extract_frontier()`：记录当前 focus；
3. `plan()`：记录 PLAN；
4. `act()` / `Expert_Agent.run()`：记录 AGENT_CALL、ANALYSIS；
5. `evidence_retriever()`：记录真正的 EVIDENCE_READ，并给返回记录分配稳定 `record_id`；
6. `UpdateGraph` 执行前后：记录 GRAPH_UPDATE + GraphDelta + cross binding；
7. `backtrack_frontier_chain()`：记录 BACKTRACK；
8. `fsm.maybe_transit()` / refine 分支：记录 DRILL_DOWN；
9. `Report`：记录 FINAL_REPORT。

原则：hook 失败不应悄悄改变 GoS 推理结果；开发阶段应 fail-fast 并记录错误。

---

# 5. S1 工程要求

新增独立目录，优先：

```text
traceevo/
├── audit/
│   ├── event_schema.py
│   ├── event_logger.py
│   ├── provenance_graph.py
│   ├── graph_delta.py
│   ├── cross_graph_binding.py
│   └── integrity.py
└── tests/
    ├── test_event_hash_chain.py
    ├── test_graph_replay.py
    ├── test_retrieval_binding.py
    └── test_backtrack_event.py
```

增加配置开关：

```yaml
traceevo:
  enabled: true
  provenance_only: true
```

当 `enabled: false` 时，行为必须退化为当前 S0 GoS baseline。

---

# 6. S1 验收实验

仍只使用 Dev-Small case 0–9，不得触碰 case 10–137。

至少完成：

### T1 非侵入性
同一冻结配置下比较 provenance off/on。

检查：
- prediction；
- report；
- GoS final graph；
- LLM 调用数；
- prompt 内容。

原则上 instrumentation 不应改变传给 LLM 的内容。

由于 temperature=0.8 导致运行本身随机，最优做法是加入**record/replay LLM 响应模式**：先保存一次 S0 LLM 响应，再在 provenance-on 流程中重放同一响应。这样才能确定代码 instrumentation 本身是否改变行为。

若 S1 能实现 record/replay，这将成为后续消融和调试的重要基础。

### T2 Event Coverage
预先定义 required operation manifest，自动计算：

`coverage = observed_required_events / expected_required_events`

Dev-Small 上开发目标：100%。

### T3 Cross-Graph Grounding
报告：
- GoS Evidence 总数；
- `INPUT_OBSERVATION` 数；
- `RETRIEVED_RECORD` 数；
- `AGENT_GENERATED` 数；
- `UNKNOWN` 数；
- 可绑定来源的 Evidence 比例。

该结果替代 S0 手工/探索性 27.7% 数字。

### T4 Graph Replay
从初始图 + event/delta 重建最终图。

10/10 病例必须与真实最终图完全一致（canonical hash 相同）。

### T5 Integrity
人工删除/修改一个 event：
- hash chain 检查必须发现异常；
- 不要求此阶段完成复杂异常分类。

### T6 Backtracking Event
不做 δ/η 全量扫描。

通过一个确定性最小图/FSM 单元测试强制触发 backtracking，并验证：
- BACKTRACK event 被记录；
- 删除节点/层级信息正确；
- GraphDelta 可重放。

---

# 7. S1 强制性交付

输出：

```text
S1_provenance/
├── S1_report.md
├── REVIEW_REQUEST.md
├── schema.json
├── event_manifest.md
├── source_taxonomy.md
├── sample_trace.jsonl
├── sample_provenance_graph.json
├── sample_provenance_graph.png
├── sample_graph_deltas.jsonl
├── cross_graph_mapping.json
├── coverage_results.csv
├── grounding_results.csv
├── replay_results.csv
├── integrity_test_results.csv
├── before_after_outputs.csv
├── tests/
├── git_diff.patch
└── git_commit.txt
```

`S1_report.md` 必须回答：

1. Provenance 开关是否改变 GoS 输入/输出？
2. 10例的 event coverage 是否 100%？
3. 每类 source_class 各多少？
4. GoS 中有多少 Evidence 无法绑定到真实 retrieval/input？
5. 10例是否都能从 delta 完整重放最终图？
6. hash chain 能否发现 event 删除/篡改？
7. 哪些 provenance 字段在实际源码中无法可靠获得？
8. S2 异常注入应该从哪些真实缺陷开始？

---

# 8. S1 GO/STOP 门槛

只有同时满足以下条件才能建议进入 S2：

- Dev-Small 10/10 正常运行；
- instrumentation 可关闭并恢复 S0 行为；
- required event coverage = 100%；
- graph replay = 10/10；
- retrieved evidence 具备稳定来源绑定；
- hash integrity 测试通过；
- 不存在尚未解释的 provenance 丢失。

如果 `UNKNOWN` source 仍很多，不强制 STOP，但必须分析来源，并在 S2 前明确它们是否属于正常 agent-generated assertion。

---

# 9. 明确禁止事项

S1 阶段不得：

- 修改 GoS Evidence 节点内容以“提高质量”；
- 根据审计结果改变 GoS 当前推理；
- 建立长期 Experience Memory；
- 更新 Capability Profile；
- 实现自进化；
- 在 case 10–137 上试验；
- 为追求 backtracking 数量调整 δ/η。

这些工作分别留给 S2/S3/S4/S5。

---

---

# 11. S1 开始前的两个小型清理项

这两项不改变算法，可与 S1 第一笔 commit 一起完成：

1. **收窄 API 重试条件**：当前 S0 为覆盖网络故障将通用 `API call error` 纳入重试，可能把模型名非法、参数错误等永久故障也重试5次。改为按可识别的传输异常、连接重置、超时、HTTP 429 和 HTTP 5xx 重试；认证失败、模型不存在、请求参数错误应 fail-fast。
2. **修正 S0 报告中的 Judge 个数笔误**：6次评分的实际逐例结果为 **7/10 病例评分完全一致，3/10 病例不稳定（case 2、4、8）**。S0 文档中出现“4例完全一致”或“4例不一致”的表述均应更正，但不改变“Judge 存在明显随机性”的结论。

S1 期间继续冻结 S0 的 backbone、temperature、δ、η、`max_steps`、retrieval budget 和 session budget；除上述基础设施修复外，不调整推理超参数。

# 10. 给本地科研智能体的启动指令

> 阅读原 `TraceEvo_Research_Plan_v1.md`、GoS 论文、`S0_baseline/S0_report.md`、`S0_baseline/gos_code_mapping.md` 与本文件。以本文件覆盖原计划中与 S1 冲突的内容。仅执行 S1，不得进入 S2。保持 GoS 推理逻辑不变，优先完成非侵入式 event instrumentation、graph delta、cross-graph binding、source-aware grounding 与完整性测试。完成全部 S1 交付物后生成 `REVIEW_REQUEST.md` 并停止。
