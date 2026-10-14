# TraceEvo：S1评审结论与S2实施修订 v1.0

## 0. 评审结论

**结论：GO，允许进入 S2。**

S1 已满足全部进入 S2 的门槛：非侵入性验证通过、required event coverage=100%、graph replay=10/10、来源绑定可重复计算、hash integrity 测试通过、backtracking instrumentation 通过，且单元测试 40 passed。

S1 的主要科研价值不只是“完成埋点”，而是已经确认了三个后续研究问题：

1. GoS 的 `Evidence` 类型混合了真实检索证据、缺失陈述、专家推断/建议等不同语义对象；
2. 上游缺少逐操作成功/降级/失败状态，导致静默降级不可观测；
3. 当前可以精确回答“节点由哪个事件产生”，但不能把“某个 LLM 判断实际受哪些证据影响”解释为严格因果关系。

S2 的目标因此修订为：

> **在不改变 GoS 正常推理逻辑的前提下，建立可观测的执行状态、可解释的异常审计规则和可验证的异常传播路径回溯机制，并通过受控异常注入获得确定性 ground truth。**

**本文件仅授权执行 S2。S2 完成后停止，不得自动进入 S3。**

---

# 1. 对 S1 三个待决问题的正式决定

## D1. `status`：采用“补可观测信号 + 将静默降级作为异常类型”的组合方案

不接受继续把所有事件写成 `success`，也不修改 GoS 的决策结果。

S2 将 provenance 状态扩展为：

```text
success
degraded
failed
```

并新增：

```text
reason_code
```

建议最小 reason code：

```text
NONE
BUDGET_EXHAUSTED
INSUFFICIENT_INFORMATION_FALLBACK
INVALID_EXPERT_DISPATCH
TOOL_ERROR
LLM_ERROR
PARSER_ERROR
TRACE_INTEGRITY_ERROR
```

要求：

- 专家预算耗尽、返回固定降级文案：记录 `degraded`，**仍返回原上游文本，不改变控制流**；
- 专家名无法匹配等原本会抛异常的路径：先记录 `failed + reason_code`，然后**继续抛出原异常**；
- hook 只能增加观测，不得吞异常、改答案或自动修复。

因此，S1 发现的“静默降级”正式成为 S2 的异常类型 **A0**。

### `model_version` 的处理

不再假装存在无法观测的 provider model version。

S2 schema 改为：

```text
model_id           # 例如配置中的模型名，可靠
model_version      # Optional；无法获得则 null
version_observable # bool
```

S2 不做“模型版本失配”实验。

对于检索证据，新增可验证的：

```text
record_hash = sha256(canonical evidence record)
```

后续“版本/篡改”相关实验改成**证据记录完整性/错绑定**，不虚构模型版本能力。

---

## D2. 消费类 `causal_node_refs`：接受当前近似，不做 prompt 级因果消融

第一篇论文范围内，当前能力已经足够：

- **创建来源**：精确，节点可追溯到产生它的事件；
- **执行依赖**：S2 进一步完善 `parent_event_ids`；
- **推理结构依赖**：利用 GoS Causal Graph 的 support/refute/refine 关系；
- **真实 LLM 反事实影响**：不声称、不做 prompt-level counterfactual attribution。

论文中必须明确区分：

> “structural provenance / execution dependency” ≠ “counterfactual causal influence”。

S2 不追加 prompt 级消融。

---

## D3. T1 A组无需重新完整真实运行

接受 S1 的非侵入性结论。

理由：

- 主证据是 B vs C：相同记录、相同 prompt 序列、唯一变量是 instrumentation 开/关；
- 已有单例全真实运行验证 record→replay 忠实性；
- S2 不应再消耗约2小时重跑 A。

此问题关闭。

---

# 2. S2 的核心研究问题

S2 只回答三个问题：

### Q1：什么才算“异常 Evidence”？

不能把“未绑定检索记录”直接等同于伪造证据。

S1 已表明未绑定项包含：

- 合法的缺失陈述；
- 检索证据的近似改写；
- 专家推断/建议；
- 真正没有来源支撑的事实性断言。

因此 S2 首先建立**来源可信度 + 语义角色**的双维审计，不使用单一 token-overlap 阈值作为 ground truth。

### Q2：如何发现执行异常并定位起点？

检测对象至少包括：

- 静默降级；
- 无来源事实性 Evidence；
- 证据错绑定/篡改；
- 非法专家/能力调用；
- 中间分析污染；
- 事件链篡改。

### Q3：异常如何传播到后续推理与最终结论？

S2 需要建立 execution dependency DAG，并从异常根节点向后追踪：

```text
异常事件
→ Expert Analysis
→ Graph Update
→ Hypothesis / Evidence
→ 后续 Plan / Agent Call
→ Final Report
```

---

# 3. S2A：先补齐“可审计执行语义”

这是 S2 的第一步，必须先完成，再实现 detector。

## 3.1 扩展事件状态

Schema：

```json
{
  "status": "success|degraded|failed",
  "reason_code": "NONE|BUDGET_EXHAUSTED|INSUFFICIENT_INFORMATION_FALLBACK|INVALID_EXPERT_DISPATCH|TOOL_ERROR|LLM_ERROR|PARSER_ERROR|TRACE_INTEGRITY_ERROR"
}
```

要求：

- 不能改变 GoS 正常输出；
- 失败路径先落事件再 re-raise；
- 对原本静默 fallback 的路径只加标记，不改返回内容。

增加测试：

```text
test_budget_exhaustion_emits_degraded
test_invalid_dispatch_emits_failed_then_reraises
test_normal_analysis_emits_success
```

---

## 3.2 完善 execution dependency DAG

S1 的 `parent_event_ids` 太稀疏。S2 至少建立：

```text
PLAN
  ↓
AGENT_CALL
  ↓
TOOL_CALL
  ↓
EVIDENCE_READ
  ↓
ANALYSIS
  ↓
GRAPH_UPDATE
  ↓
BACKTRACK / DRILL_DOWN
  ↓
下一轮 PLAN
  ↓
FINAL_REPORT
```

具体规则：

- `AGENT_CALL.parent = PLAN`
- `TOOL_CALL.parent = AGENT_CALL`
- `EVIDENCE_READ.parent = TOOL_CALL`
- `ANALYSIS.parent = AGENT_CALL + 本次该 expert 的 EVIDENCE_READ`
- `GRAPH_UPDATE.parent = 本 step 的 ANALYSIS`
- `BACKTRACK/DRILL_DOWN.parent = preceding GRAPH_UPDATE`
- 下一轮 `PLAN.parent = preceding GRAPH_UPDATE/BACKTRACK/DRILL_DOWN`
- `FINAL_REPORT.parent = 当前 reasoning state 最近一次 GRAPH_UPDATE/DRILL_DOWN`

这里表示**执行依赖**，不是声称 LLM 的反事实因果关系。

必须新增 DAG consistency test：
- 无悬空 parent；
- 无 task 内环；
- 所有 FINAL_REPORT 可向前遍历到初始事件。

---

## 3.3 检索记录完整性

每个 `RetrievalRecord` 新增：

```text
record_hash
```

计算：

```text
sha256(canonicalize(query, text, expert, task_id))
```

用于 S2 的错绑定/篡改检测。

---

# 4. S2B：Evidence 审计边界

## 4.1 不再用“0.6阈值 = 真来源”作为二元真值

将 Evidence provenance 分为四档：

### P0 `DIRECT_GROUNDED`
满足任一：
- 来自初始输入；
- 与某条真实 `EVIDENCE_READ` 返回存在规范化直接包含/明确 record_id 绑定。

### P1 `SOFT_GROUNDED`
存在候选 retrieval record，文本相似度较高，但不是直接匹配。

S2 可同时计算：
- token coverage；
- SequenceMatcher/字符相似度；
- 可选 embedding similarity（若本地已有，不作为必需依赖）。

### P2 `DERIVED`
明确属于：
- absence claim；
- recommendation；
- agent inference；
- domain knowledge。

这类节点**不能因为无 retrieval source 就判为异常**。

### P3 `UNSUPPORTED_ASSERTION`
Evidence 类型节点：
- 不是输入；
- 没有可靠/候选 retrieval source；
- 也不属于合法 derived 类；
- 并被用于 support/refute 或进入最终结论路径。

P3 才是“伪造/无来源证据”审计的主要高风险对象。

---

## 4.2 阈值选择原则

S2 允许在 `Dev-Small case 0–9` 上调来源匹配阈值，但必须满足：

- 同时报告敏感性；
- 不把阈值调到让所有自然节点都“有来源”；
- 优先控制 clean trace 的 false positive；
- 最终阈值只作为开发配置，S5 重新验证。

建议目标：

```text
clean trace Evidence anomaly FPR <= 10%
```

理想：

```text
<= 5%
```

输出 `evidence_boundary_analysis.csv`，每个 Evidence 至少包含：

```text
case_id
node_id
label
source_class
semantic_kind
best_record_id
token_coverage
char_similarity
provenance_tier
audit_label
```

---

# 5. S2 异常类型与注入设计

S2 至少实现 A0–A5。

## A0：Silent Degradation

场景：
- 专家预算耗尽；
- 上游返回 `Insufficient information...` 等 fallback。

Ground truth：
- 注入/触发的 expert call；
- `status=degraded`；
- reason code。

目标：
- Detector 应识别为 degradation；
- 不等同于安全攻击，但属于可信协同中的运行异常。

---

## A1：Fabricated / Unsupported Evidence

注入：
- 在某次 GRAPH_UPDATE 中加入一个不存在于 input/retrieval 的事实性 Evidence；
- 将其通过 support/refute 边连接到当前 Hypothesis。

Ground truth：
- injected node；
- 创建事件；
- 受影响 hypothesis；
- 下游依赖路径。

要求：
- 不使用 absence claim / recommendation 作为 A1；
- 伪造内容必须是“可被当作临床事实”的 assertion。

---

## A2：Evidence Misbinding / Tampering

不再做无法验证的“model version mismatch”。

至少两种：

1. `wrong_record_binding`：Evidence 绑定到错误 retrieval record；
2. `record_tampering`：修改 retrieval text，但保留原 record_id，导致 `record_hash` 不一致。

Ground truth 明确记录被改的 record/event。

---

## A3：Invalid / Unauthorized Capability Invocation

优先复用 S1 真实发现：

```text
Laboratory Physician
vs
Laboratory_Physician
```

注入：
- 请求不存在/无法解析的 expert；
- 或根据 capability policy 请求该角色无权使用的 tool。

要求：
- `AGENT_CALL`/dispatch attempt 记录 `failed`；
- reason=`INVALID_EXPERT_DISPATCH` 或权限原因；
- 保持上游原异常行为，不自动修复。

---

## A4：Corrupted Intermediate Analysis

注入：
- 修改专家 ANALYSIS 内容；
- 保留其来源事件；
- 标记 ground-truth corruption point；
- 根据 execution DAG 记录理论受影响的 downstream events/nodes。

第一版定量检测可以在 artifact/replay 层完成，不强制每次都重新调用 LLM。

至少选择 2–3 个病例做端到端 case study：
- 注入后继续运行；
- 允许 downstream prompt 改变并产生新调用；
- 展示错误如何进入 Graph Update / Hypothesis / Final Report，或被审计阻断。

---

## A5：Trace Tampering

继续复用 S1 完整性能力：

- modify
- delete
- reorder
- truncate

S2 中作为单独的 integrity 类结果，不要与 A1–A4 的语义异常 F1 混在一起。

---

# 6. S2 审计器

建议目录：

```text
traceevo/audit/
├── policy.py
├── evidence_auditor.py
├── execution_auditor.py
├── integrity_auditor.py
├── anomaly_detector.py
├── dependency_graph.py
├── path_tracer.py
└── audit_report.py
```

每个异常结果统一为：

```json
{
  "anomaly_id": "...",
  "task_id": "...",
  "type": "A0|A1|A2|A3|A4|A5",
  "root_event_id": "...",
  "root_node_id": "...",
  "severity": "low|medium|high|critical",
  "evidence": [],
  "affected_events": [],
  "affected_nodes": [],
  "path_to_conclusion": [],
  "detector_rule": "...",
  "confidence": 0.0
}
```

---

# 7. 路径回溯与传播分析

从 detected root 出发，在两张图之间联合遍历：

```text
Execution Dependency DAG
        ↕ cross binding
GoS Causal Graph
```

输出：

1. 最早异常事件；
2. 对应异常 Causal node；
3. 下游受影响事件；
4. 下游受影响 hypothesis/evidence；
5. 是否到达 FINAL_REPORT；
6. root → conclusion 的最短/主要传播路径。

不得把“execution dependency”表述成严格心理/模型因果影响。

---

# 8. S2 实验设计

## 8.1 数据范围

**仍只使用 Dev-Small case 0–9。**

禁止触碰：
- case 10–49
- case 50–69
- case 70–137

S2 的 ground truth 来自受控注入，因此不需要扩大病例数。

---

## 8.2 定量注入矩阵

目标：

```text
10 clean traces
+
10 cases × A0
+
10 cases × A1
+
10 cases × A2
+
10 cases × A3
+
10 cases × A4
+
A5 integrity variants
```

若某类异常不适用于全部病例，应明确适用数和原因，不伪造样本。

优先通过 record/replay 或 artifact mutation 完成，减少 API 成本。

---

## 8.3 指标

### clean false positive
- Clean Trace FPR
- Clean Evidence FPR

### anomaly detection
- Precision
- Recall
- F1
- per-class Recall

### root cause
- Root-cause Top-1 Accuracy
- Root-cause Top-k（可选）

### propagation
- Affected-event Recall
- Affected-node Recall
- Path Recall / Path F1

### provenance
- Evidence trace success rate
- Event dependency completeness

### integrity
- A5 Detection Rate

---

# 9. S2 工程验收门槛

这些只是“允许进入 S3”的工程门槛，不是最终论文结论：

```text
Clean Trace FPR <= 10%
Macro anomaly F1 >= 0.80
Root-cause Top-1 Accuracy >= 0.80
Propagation-path Recall >= 0.80
A5 tamper detection = 100%
Dependency DAG consistency = 100%
```

如果 A1 的 F1 明显受自然 Evidence 语义混用影响：

**不得通过继续调低阈值掩盖问题。**

必须提交 false-positive taxonomy，判断是否需要修改 Evidence audit policy。

---

# 10. S2 还必须做一个“自然缺陷”案例研究

除人工注入外，至少选择 S1 已自然发生的一个真实问题：

```text
"Laboratory Physician" vs "Laboratory_Physician"
```

形成完整 case study：

```text
PLAN
→ invalid expert dispatch
→ failed event
→ error reason
→ task interruption
→ provenance report
```

该案例用于证明 TraceEvo 不只识别人造篡改，也能解释真实运行故障。

如果能自然捕获预算耗尽的静默降级，再增加第二个 case study。

---

# 11. S2 强制性交付

输出目录：

```text
S2_audit/
├── S2_report.md
├── REVIEW_REQUEST.md
├── schema_v2.json
├── status_semantics.md
├── capability_policy.md
├── evidence_boundary_analysis.csv
├── injection_spec.md
├── injection_ground_truth.jsonl
├── clean_audit_results.csv
├── anomaly_results.csv
├── per_class_metrics.csv
├── root_cause_results.csv
├── propagation_results.csv
├── integrity_results.csv
├── dependency_dag_results.csv
├── natural_failure_case.md
├── example_audit_report.json
├── example_path.png
├── failure_cases.md
├── tests/
├── git_diff.patch
└── git_commit.txt
```

---

# 12. S2_report.md 必须回答

1. `success/degraded/failed` 是否能在不改变 GoS 行为的情况下可靠记录？
2. clean trace 的异常 FPR 是多少？
3. S1 的 41.6% AGENT_GENERATED Evidence 最终分别落入哪些语义/来源层级？
4. A0–A4 每类异常的 Precision/Recall/F1 如何？
5. 哪类异常最难检测？为什么？
6. root-cause localization 是否能找到注入起点？
7. propagation path 是否能覆盖真实注入标注路径？
8. 哪些异常能传播至最终报告，哪些在中途终止？
9. dependency DAG 是否存在断链/错误 parent？
10. S2 的审计输出是否足以支持 S3 的 Experience Gate？
11. 哪些规则看起来过拟合 Dev-Small，需要在 S3/Validation 阶段重新检查？
12. 至少给出 2 个成功案例和 2 个失败案例。

---

# 13. S2 的停止条件

S2 完成后**必须停止**。

不得自动实现：

- Experience Memory；
- Experience Gate；
- Capability Profile；
- Planner 的历史经验注入；
- 自进化；
- case 10–137 上的实验。

这些属于 S3/S4。

---

# 14. 给本地科研智能体的启动指令

> 阅读 `TraceEvo_Research_Plan_v1.md`、GoS 论文、`S1_provenance/S1_report.md`、`S1_provenance/REVIEW_REQUEST.md` 与本文件。以本文件覆盖原计划中与 S2 冲突的内容。仅执行 S2。先完成可观测状态语义、execution dependency DAG 和 evidence 审计边界，再实现 A0–A5 注入、异常检测与路径回溯。保持 GoS 正常推理逻辑不变，不自动修复异常，不建立任何长期经验记忆或自进化模块。完成全部 S2 交付物和 `REVIEW_REQUEST.md` 后停止，等待评审。
