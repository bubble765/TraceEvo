# TraceEvo：S2.1评审结论与S3A实施指令 v1.0
## ——审计泛化与经验门控前置验证

## 0. 评审结论

**结论：S2.1 通过。允许进入 S3，但只先执行 S3A；S3A 完成后必须停止评审，不得直接实现 Experience Memory / Gate。**

S2.1 已完成原补全任务：

- A3 真实缺陷复现成功，`PLAN → invalid dispatch → failed/INVALID_EXPERT_DISPATCH → 原 ValueError` 链路完整；
- A4 两个端到端 response-substitution 实验均真实进入 pipeline，后续 prompt 发生变化；
- 两个 A4 均测得为 **L0**：注入文本未进入事件、因果图或最终报告；
- 没有为追求传播结果修改 GoS 推理逻辑、审计阈值或 Evidence 分类规则；
- clean/injected 对照、失败日志与传播级别均已保留。

### 关于 S2.1 §8 第4条的裁决

原门槛“auditor 对 A3/A4 root 均能定位到真实异常起点”在 A4 场景下判定为：

**N/A，视为通过，不判失败。**

原因不是“检测器漏检”，而是注入响应在 GoS 的 decision/content seam 被解析前吸收，未生成任何 semantic event，因此 trace-only auditor 没有可定位的 event root。把该结果记成 `no` 会把“不可观测”错误统计成“定位失败”。

但是，这一结果暴露了一个必须在 Experience Gate 前修复的审计边界：

> **TraceEvo 当前能审计已经进入事件流/状态的操作，但无法完整观察“发生过、随后被 parser 丢弃”的模型调用。**

因此，S3A 首先增加一个轻量的 **Model Invocation Ledger** 作为第二观测通道，并在未使用病例上验证 S2 规则的迁移性。完成这两项后，再决定是否进入 S3B 的可信经验门控。

---

# 1. S3A 的两个目标

S3A 只做两件事：

### Goal A：补齐“事件之前”的模型调用可观测性

新增第二观测通道，记录每次 LLM 调用及其解析/提交结果，使：

- 被 parser 丢弃的响应仍然可审计；
- 可以区分“发生过的模型调用”与“真正提交到 GoS 状态的事件”；
- Experience Gate 后续只消费“已提交且审计通过”的轨迹，不把未提交响应误当经验。

### Goal B：冻结 S2 规则，在未使用病例上进行 blind migration audit

不允许先看病例再改规则。

目的不是追求更高分，而是回答：

> **S2 在 case 0–9 上形成的 Evidence 边界、absence 规则与异常规则，到了新病例上是否仍然合理？**

---

# 2. 数据划分从现在起冻结

将此前 `Dev-Evolve 10–49` 再拆分：

```text
Dev-Small          case 0–9
    已用于 S0–S2，今后仅回归测试

Migration-Holdout case 10–29
    S3A blind migration audit
    在首次报告生成前禁止针对这些病例修改规则

Dev-Evolve         case 30–49
    未来 S3B/S4 方法开发与经验积累

Validation         case 50–69
    未来选阈值/超参数

Held-out Test      case 70–137
    S5 方法冻结后才能运行
```

**S3A 只能读取/运行 case 10–29。不得读取 case 30–137。**

---

# 3. Model Invocation Ledger

## 3.1 设计原则

Provenance Event 继续表示：

> “已经进入 TraceEvo/GoS 可解释执行状态的语义事件”。

Model Invocation Ledger 表示：

> “实际发生过的 LLM 请求/响应尝试，无论最终是否被 parser 接受、是否提交为事件”。

两者不可混为一个概念。

---

## 3.2 最小记录结构

新增例如：

```json
{
  "call_id": "call-...",
  "task_id": "case-...",
  "step_id": 1,
  "actor_id": "...",
  "actor_role": "...",
  "stage": "CENTRAL_PLAN|EXPERT_DECISION|EXPERT_CONTENT|GRAPH_UPDATE|REPORT|OTHER",
  "model_id": "...",
  "prompt_hash": "...",
  "response_hash": "...",
  "parent_event_id": "...",
  "parse_status": "accepted|discarded|failed",
  "discard_reason": "NONE|DECISION_CONTENT_MISMATCH|PARSER_ERROR|INVALID_SCHEMA|OTHER",
  "committed_to_state": true,
  "committed_event_ids": [],
  "timestamp": "..."
}
```

原始 prompt/response 可以继续保存在独立 raw recording 中，主 ledger 只保存 hash 与引用，避免让主事件文件膨胀。

要求：

- 每次真实或 replay 的 LLM 调用都必须有 `call_id`；
- ledger 在 parser 之前创建；
- parser 完成后更新 `parse_status`；
- 若该调用最终形成 ANALYSIS / GRAPH_UPDATE / FINAL_REPORT 等事件，则填 `committed_event_ids`；
- 被丢弃的调用必须保留，并标记 `committed_to_state=false`；
- 不改变任何 prompt、返回文本或 GoS 控制流。

---

# 4. 用 Model Invocation Ledger 重测 S2.1 的 A4

只重测：

```text
A4-case5
A4-case8
```

不修改注入内容。

期望验证的不是 L1/L2，而是：

```text
注入模型响应
→ ledger 中存在对应 call
→ parse_status = discarded
→ discard_reason 可解释
→ committed_to_state = false
→ 无 semantic event / causal node
→ semantic propagation = L0
```

这样 A4 的 root 应从：

```text
semantic event root = n/a
```

升级为：

```text
model-call root = 可定位
semantic event root = n/a
```

这不是“让 A4 传播”，而是补齐审计可观测边界。

S3A 必须明确区分：

- **Invocation root localization**
- **Semantic-event root localization**

---

# 5. Experience Eligibility Contract（只定义，不实现 Memory）

S3A 需要定义未来 S3B 的经验准入契约，但**不得实现长期 Memory 或修改 Planner**。

每条候选经验至少具有：

```json
{
  "trace_id": "...",
  "task_id": "...",
  "outcome": "...",
  "integrity_ok": true,
  "invocation_complete": true,
  "semantic_trace_complete": true,
  "has_critical_anomaly": false,
  "evidence_profile": {
    "p0": 0,
    "p1": 0,
    "p2": 0,
    "p3": 0
  },
  "uncommitted_calls": 0,
  "eligibility": "eligible|failure_experience|reject|manual_review",
  "reasons": []
}
```

### 初步准入原则

未来只有满足以下条件的成功轨迹才能成为 `Validated Success`：

```text
integrity_ok
AND invocation_complete
AND semantic_trace_complete
AND no critical anomaly
AND no unresolved P3 on decision path
AND outcome judged correct
```

失败轨迹若异常根因明确，可成为 `Validated Failure`。

### 重要约束

- P0 `DIRECT_GROUNDED`：可作为外部/输入事实支撑；
- P1 `SOFT_GROUNDED`：进入经验前需更谨慎；
- P2 `DERIVED`：允许保留，但不得等价成外部事实；
- P3 `UNSUPPORTED_ASSERTION`：若位于决策路径上，不允许成为成功经验事实；
- `committed_to_state=false` 的模型响应不得进入经验内容。

S3A 只输出契约与候选判定，不建立长期经验库。

---

# 6. Blind Migration Audit：执行规则

## 6.1 第一遍必须完全冻结 S2 规则

在运行 case 10–29 前记录：

```text
audit_rule_commit
evidence_thresholds
absence_regex_hash
capability_policy_hash
```

然后运行全部 20 个病例。

**在生成第一份 migration 报告前禁止修改：**

- absence regex；
- P0–P3 规则；
- similarity threshold；
- A1/A2/A4 detector；
- severity/confidence；
- capability policy。

---

## 6.2 第一遍必须报告的指标

注意：新病例中的 alert 不一定真是“false positive”，因此不要直接把它叫 FPR。

报告：

```text
clean_trace_alert_rate
evidence_alert_rate
P0 / P1 / P2 / P3 分布
externally_grounded rate
lineage_complete rate
uncommitted_call rate
degraded / failed call rate
decision-content discard rate
```

还需要输出：

```text
migration_review_queue.csv
```

其中包含所有：

- P3 Evidence；
- P1 soft-grounded；
- degraded/failed 调用；
- decision/content mismatch；
- 其他 S2 detector 警报。

字段至少：

```text
case_id
event_or_call_id
alert_type
text
source_candidate
rule
score
context
suggested_review_question
```

---

# 7. Migration Pass/Stop 规则

S3A 不允许为了过关自动调规则。

## 可直接建议 GO 的条件

满足：

1. 20/20 病例正常完成或失败原因有明确可审计解释；
2. Model Invocation Ledger coverage = 100%；
3. ledger 与 semantic event 的 committed binding 可重放；
4. A4 case5/8 的 invocation root 可定位；
5. case 10–29 上没有发现 schema/DAG 系统性断裂；
6. Evidence alert rate 未出现数量级爆炸；
7. absence regex 未出现明显大规模漏检/误类迹象。

由于没有人工 gold label，**不设置“migration F1 ≥ X”这类伪精确门槛。**

若新病例 alert 数量明显高于 Dev-Small，例如：

```text
clean_trace_alert_rate > 25%
或
P3 rate > 10%
```

不得自动 STOP，也不得自动调参；标记 `NEED_REVIEW`，把 review queue 返回外部评审。

---

# 8. S3A 必须做的非侵入性回归

完成 ledger 后，在 case 0–9 的现有 recording 上做 replay regression：

比较 ledger off/on：

```text
prompt sequence hash
response sequence hash
prediction
report
final causal graph
semantic provenance events
```

要求：

```text
逐字节/哈希一致
```

即 Model Invocation Ledger 只能观察，不能改变执行。

---

# 9. S3A 交付物

输出：

```text
S3A_migration/
├── S3A_report.md
├── REVIEW_REQUEST.md
├── model_invocation_schema.json
├── experience_eligibility_contract.md
├── migration_config.yaml
├── rule_freeze_manifest.json
├── migration_results.csv
├── migration_evidence_analysis.csv
├── migration_call_analysis.csv
├── migration_review_queue.csv
├── a4_invocation_recheck/
│   ├── case5.json
│   └── case8.json
├── regression_results.csv
├── failure_cases.md
├── tests/
├── git_diff.patch
└── git_commit.txt
```

---

# 10. S3A_report.md 必须回答

1. ledger 是否覆盖所有实际 LLM 调用？
2. ledger on/off 是否保持 GoS 行为完全一致？
3. A4 case5/8 是否能定位到 model-call root？
4. 多少调用发生后被 parser 丢弃？
5. 这些 discarded calls 是否进入 semantic state？
6. case 10–29 的 P0/P1/P2/P3 分布与 case 0–9 相比如何？
7. S2 的 absence/derivation 规则在新病例上是否出现明显失效？
8. 新病例的 trace alert rate / Evidence alert rate 如何？
9. 哪些警报需要人工/外部评审，而不能靠现有规则决定？
10. 当前 audit 是否足以安全地作为 Experience Gate 的输入？
11. Experience Eligibility Contract 是否存在无法计算的字段？
12. 是否建议进入 S3B：GO / NEED_REVISION / STOP？

---

# 11. S3A 通过后的下一步（现在不得执行）

S3A 通过后才进入：

> **S3B：审计证据门控的可信经验记忆**

S3B 才会实现：

```text
Validated Success
Validated Failure
Rejected / Manual Review
Trusted Memory Retrieval
```

并首次使用 case 30–49 做经验积累实验。

S3A 阶段不得：

- 建 Memory Store；
- 把历史经验注入 Planner；
- 更新 Capability Profile；
- 修改智能体选择；
- 做“自进化效果”实验；
- 读取 case 30–137。

---

# 12. 给本地科研智能体的启动指令

> 阅读 `TraceEvo_S2_Review_S2.1_Completion_v1.md`、`S2_audit/S2_report.md`、`S2_audit/REVIEW_REQUEST.md`、`S2_audit/case_studies/case_study_summary.md` 与本文件。S2.1 判定通过，A4 §5.4 的 semantic-event root 为 N/A，不按漏检处理。仅执行 S3A：首先实现非侵入式 Model Invocation Ledger，重测 A4 case5/8 以定位 model-call root；随后冻结全部 S2 Evidence/审计规则，在从未使用的 case 10–29 上完成 blind migration audit，并定义 Experience Eligibility Contract。不得建立长期 Memory、不得修改 Planner、不得实现自进化。完成全部 S3A 交付物与 `REVIEW_REQUEST.md` 后停止。
