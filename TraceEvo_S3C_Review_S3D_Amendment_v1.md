# TraceEvo：S3C评审结论与S3D实施指令 v1.0
## ——扩充独立 Verified-Success 语料并校准可迁移检索

## 0. 评审结论

**结论：S3C 方法学目标通过，但仍不进入 S4。维持 `NEED_MORE_VERIFIED_EXPERIENCE`，授权执行一次有限的 S3D。**

S3C 已经把 Gate 论证补完整：

- 真实 `verified_success` 已首次走通 `eligible → success partition → retrieval index → injectable payload`；
- 4 条 headline gold-safe success × 4 类污染形成 16 个 correct-but-poisoned twins；
- OutcomeOnly 接收 16/16，而 TraceEvo 接收 0/16；
- AuditOnly 仍大量接收 incorrect-clean；
- 所有单信号 Gate 均存在结构性盲点；
- success / failure memory 通道已经在代码层分离；
- gold leakage = 0；
- failure memory 已退出 initial Plan retrieval。

因此，**“结果反馈 + 过程审计联合门控”这一论文核心机制已经成立。**

当前不能进入 S4 的原因只剩两个：

1. case 10–39 仅有 **4 条 headline gold-safe verified-success**，低于预注册的 5 条最低门槛；
2. 现有 Success Retriever 的阈值 28.6789 来自“source case 自匹配 vs 其他 case”的分离，它证明了 abstention 机制能工作，却**不能证明跨病例迁移相关性**；case 40–49 的 0/10 命中正说明这一点。

因此 S3D 只解决“语料覆盖”和“检索可迁移性描述”，不做 S4 自进化。

**S3D 完成后必须停止。**

---

# 1. 对 S3C 两个评审判断点的正式决定

## D1. 不降低 5 条 headline gold-safe success 的门槛

case-22 不计入 gold-safe success 的处理完全正确。

它满足：

```text
outcome = verified_success
```

但同时存在：

```text
unresolved decision-path P3
A1 anomaly
```

所以它是最重要的反例之一：

> **结果正确不代表过程可信。**

不得因为“只差 1 条”而把它重新归入 success。

legacy case 0–9 的 2 条 success 继续只作为：

```text
legacy_dev
headline_eligible = false
```

不改变 headline 计数。

---

## D2. 授权扩展 build corpus，但只使用一个预先冻结的新块：case 50–59

这是一次**语料决策**，不是调参。

在读取任何 case 50–59 内容之前冻结新的长期数据划分：

```text
0–9     Legacy Dev
10–29   Migration / Headline Historical
30–39   Original Memory-Build
40–49   Historical Behavior Probe / retrieval dry-run only
50–59   S3D Expansion-Build
60–69   Reserve for S4 development probe
70–79   Reserve for S4/parameter validation
80–137  Final Held-out Test
```

从现在起：

- S3D 只允许读取/运行 case 50–59；
- 不得读取 case 60–137；
- 最终 held-out test 从 case 80 开始，仍保留 58 个从未接触病例。

---

# 2. S3D 的三个目标

### G1：在全新 build block 上获得并封存独立 outcome-verified 经验

### G2：判断 headline gold-safe success 是否达到至少 5 条

### G3：把 Success Retriever 的 28.6789 从“可迁移阈值”降级为“self-vs-other diagnostic”，并生成跨病例相关性审查材料

S3D **不要求**诊断准确率提升，不运行 Memory 行为实验。

---

# 3. S3D-A：case 50–59 的运行协议

## 3.1 必须使用 No-Memory GoS

case 50–59 运行：

```text
GoS + provenance + audit + invocation ledger
Memory = OFF
Capability Profile = OFF
```

禁止：
- Success Memory 注入；
- Failure Memory 注入；
- agent selection 自适应；
- Capability EMA；
- 修改 Planner。

目的是生成干净的、可用于经验构建的独立轨迹。

---

## 3.2 运行前冻结

在首次读取 case 50 之前生成：

```text
S3D_freeze_manifest.json
```

至少记录：

```text
git_commit
backbone
temperature
max_steps
retrieval_budget
delta
eta
audit_rule_hash
absence_regex_hash
P0-P3 rule hash
OutcomeVerifier prompt hash
OutcomeVerifier model/config
Experience Gate hash
```

S3D 中这些内容不得修改。

---

## 3.3 先封存，后看 gold

每个 case 严格执行：

```text
1. GoS 完成
2. events / ledger / deltas / graph / prediction / report 落盘
3. 生成 per-case artifact manifest
4. 写入 sealed hash
5. seal 完成后 OutcomeVerifier 才可读取 reference
6. Gate 根据 outcome + audit 判定
```

这次必须补上 S3A/S3C 缺失的真正 seal 机制。

每个 case 输出：

```text
case-XX_seal.json
```

OutcomeVerifier 必须验证：

```text
input_artifact_hash == sealed_hash
```

否则该 case 不得进入经验语料。

---

# 4. S3D-B：Outcome Verification

完全沿用 S3B/S3C：

```text
3/3 score=2 -> verified_success
3/3 score=0 -> verified_incorrect
其他 -> uncertain
```

不得修改 judge prompt、judge 模型、温度或判定标准。

仍保存 3 次原始 judge 输出。

特别统计：

```text
score_1_unanimous
score_1_mixed
```

因为 S3C 已发现大量 `1|1|1`，S3D 要判断这一现象是否继续出现。

---

# 5. S3D-C：Gold-safe Gate

沿用六条件，不改：

```text
verified_success
AND integrity_ok
AND invocation_complete
AND semantic_trace_complete
AND no critical anomaly
AND no unresolved decision-path P3
```

统计：

```text
N_verified_success_50_59
N_gold_safe_50_59
N_verified_success_but_unsafe
```

如果存在类似 case-22：

```text
verified_success + unsafe process
```

必须单独列为 case study，不允许计入 success。

---

# 6. S3D headline 计数

S3D 完成后：

```text
headline_gold_safe =
    gold-safe(case 10–29)
  + gold-safe(case 30–39)
  + gold-safe(case 50–59)
```

明确不包括：

```text
case 0–9 legacy
case 40–49 behavior probe
```

### 判定

若：

```text
headline_gold_safe >= 5
```

则 S3D 在“经验数量”上通过。

若：

```text
headline_gold_safe < 5
```

则停止并返回：

```text
VERIFIED_SUCCESS_SCARCITY_PERSISTS
```

不得继续自动消耗 case 60+。

---

# 7. S3D-D：扩展 Success Memory

只有新的 gold-safe success 可以加入 headline Success Store。

每条仍需满足：

```text
gold diagnosis absent
prediction absent
reference answer absent
source trace hash present
audit summary hash present
outcome verdict present
extractor version present
```

重新运行 leakage guard：

```text
gold_leakage_count = 0
```

legacy success 可继续保存在单独 corpus，但不得混入 headline store 的统计。

---

# 8. S3D-E：Correct-but-poisoned 反例扩展

若 case 50–59 产生新 gold-safe success：

对每条新 success 构造：

```text
PZ1 unsupported Evidence
PZ2 retrieval misbinding/tampering
PZ3 degraded/failed decision-path execution
PZ4 trace integrity tampering
```

用于验证 Gate 的结果不因新 corpus 改变：

```text
OutcomeOnly correct-but-poisoned admission > 0
TraceEvo correct-but-poisoned admission = 0
```

S3D 不需要重新扩大所有 baseline 实验，只需要把新 success 纳入累计表。

---

# 9. S3D-F：修正 Success Retriever 的科学解释

## 9.1 28.6789 不再称为可迁移 retrieval threshold

将现有阈值重新命名：

```text
self_identity_separation_threshold
```

它只证明：

> retriever 能区分“source case 本身”和完全不同 case。

它不能证明：

> 一个新的相似病例应该检索哪条历史经验。

因此 **S4 不得直接继承 28.6789 作为线上相关性阈值**。

---

## 9.2 S3D 只做 Cross-case Retrieval Audit，不自动定新阈值

利用扩展后的 headline Success Store，对以下 query 做离线 top-k：

```text
case 40–49 pre-task query
case 50–59 pre-task query
```

其中：
- 50–59 的 self memory 必须排除；
- query 只用任务开始前可见文本；
- 不读取 60+。

每个 query 输出 top-3：

```text
query_case
memory_case
BM25/TFIDF score
rank
task_retrieval_key
memory strategy summary
```

注意：**不使用 source diagnosis / gold diagnosis 作为 runtime feature。**

---

# 10. 跨病例相关性 Review Queue

因为当前没有可靠的“两个不同病例是否应该共享经验”的 gold label，S3D 不伪造 relevance accuracy。

输出：

```text
cross_case_retrieval_review.csv
```

每个 query 取 top-3，字段：

```text
query_case
memory_case
score
query_pre_task_summary
memory_pre_task_summary
memory_strategy
review_label = UNREVIEWED
review_question =
  "从任务开始时可见信息与策略层面看，这条历史经验是否适合作为当前病例的流程指导？"
```

S3D 本地智能体**不得自己用最终诊断标签填 relevance**。

该文件留给外部评审/人工抽样，之后再决定 S4 的 retrieval policy。

---

# 11. S3D-G：Failure Memory 保持禁用

Failure Store：

```text
stored = true
auditable = true
initial_plan_retrievable = false
activation_status = inactive_until_S4
```

S3D 不实现 risk-triggered failure retrieval。

不得因为 success retrieval 仍稀疏重新打开 failure global retrieval。

---

# 12. S3D 进入下一阶段的门槛

## 必须项

1. case 50–59 全部按 seal-before-outcome 协议执行；
2. frozen config / audit / judge / Gate 全程未改；
3. `headline_gold_safe >= 5`；
4. 新 success 的真实 eligible path 可复核；
5. correct-but-poisoned 的 TraceEvo admission 仍为 0；
6. gold leakage = 0；
7. failure memory 仍不进入 initial Plan；
8. 28.6789 已从“线上阈值”降级为 diagnostic；
9. cross-case retrieval review queue 已生成；
10. 未读取/运行 case 60–137。

### 不再要求

S3D 不要求：
- cross-case retrieval 一定命中；
- Memory 提高 Match；
- Capability Profile 生效。

---

# 13. 如果达到 5 条 success，下一步也不是立刻大规模实验

若 S3D 通过，下一阶段建议为：

```text
S4A：Capability Profile + 条件化策略更新
```

S4A 才使用 case 60–69。

S4A 的重点将是：

- audit-verified experience → agent capability profile；
- profile 是否可预测某角色/工具的可信执行表现；
- profile 是否能够改变 agent selection；
- Failure Memory 只在匹配风险条件下激活；
- Success Memory 是否检索，由 S3D cross-case review 决定。

而不是先声称“Memory 提升诊断”。

---

# 14. S3D 交付物

```text
S3D_verified_expansion/
├── S3D_report.md
├── REVIEW_REQUEST.md
├── S3D_freeze_manifest.json
├── seals/
├── runs_50_59/
├── outcome_results_50_59.csv
├── judge_raw/
├── gold_safe_triage_50_59.csv
├── headline_success_summary.csv
├── expanded_success_store/
├── expanded_memory_audit_links.jsonl
├── new_success_poisoned_candidates.jsonl
├── cumulative_gate_metrics.csv
├── leakage_scan_results.csv
├── self_identity_threshold_note.md
├── cross_case_retrieval_topk.csv
├── cross_case_retrieval_review.csv
├── failure_cases.md
├── tests/
├── git_diff.patch
└── git_commit.txt
```

---

# 15. S3D_report.md 必须回答

1. case 50–59 中 verified_success / incorrect / uncertain 各多少？
2. 其中 gold-safe success 多少？
3. 是否存在 verified-success-but-unsafe，原因是什么？
4. case 10–39 + 50–59 headline gold-safe 总数是多少？
5. 是否达到 >=5？
6. score 1 的数量和形态是否继续大量出现？
7. 新 success 是否全部完成 seal → outcome → gate → store？
8. 新 correct-but-poisoned twins 的 Gate 结果？
9. gold leakage 是否仍为 0？
10. failure memory 是否仍完全退出 initial Plan？
11. 扩展 Success Store 有多少 headline / legacy entry？
12. 原 28.6789 为什么不能视为跨病例相关性阈值？
13. cross-case retrieval top-k 的 score 分布如何？
14. 是否存在明显可迁移的 strategy pair，或全部需要人工判断？
15. 建议：GO S4A / VERIFIED_SUCCESS_SCARCITY_PERSISTS / NEED_RETRIEVAL_REDESIGN？

---

# 16. S3D 停止条件

S3D 完成后必须停止。

不得自动：

- 运行 case 60–137；
- 实现 Capability Profile；
- 做 EMA；
- 修改 agent selection；
- 激活 Failure Memory；
- 用未人工审查的 cross-case retrieval 进入 Planner；
- 修改 judge / Gate / audit 规则；
- 更换 backbone。

---

# 17. 给本地科研智能体的启动指令

> 阅读 `TraceEvo_S3B_Review_S3C_Amendment_v1.md`、`S3C_experience_expansion/S3C_report.md`、`S3C_experience_expansion/REVIEW_REQUEST.md`、`S3C_experience_expansion/failure_cases.md` 与本文件。S3C 的 Gate 机制判定通过，但 headline gold-safe success=4，仍不得进入 S4。仅执行 S3D。首次读取 case 50 前冻结全部配置；case 50–59 运行 No-Memory GoS，并严格执行 seal-before-outcome；OutcomeVerifier、audit、Gate 一字不改。若累计 headline gold-safe success 达到至少5条，则扩展 success store、补新 success 的 poisoned twins，并生成 cross-case retrieval review queue；若仍低于5条，立即停止并返回 `VERIFIED_SUCCESS_SCARCITY_PERSISTS`。不得读取或运行 case 60–137，不实现 Capability Profile，不把 28.6789 当作线上跨病例检索阈值。完成交付物和 `REVIEW_REQUEST.md` 后停止。
