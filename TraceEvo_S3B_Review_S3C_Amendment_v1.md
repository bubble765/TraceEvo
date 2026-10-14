# TraceEvo：S3B评审结论与S3C实施指令 v1.0
## ——已验证经验扩充与记忆通道校准

## 0. 评审结论

**结论：S3B 的 Gate / 防污染机制通过，但不允许进入 S4。先执行 S3C。**

S3B 已经证明的内容：

- 38/38 人工污染候选均未进入 TraceEvo `eligible`；
- Naive 对 38/38 全部接收；
- 忽略 hash integrity 后，异常检测层仍可阻止污染候选被当作成功经验；
- AuditOnly 会接收“过程干净但结果错误”的真实轨迹；
- `manual_review` 与 retrieval 隔离；
- runtime prompt 上 gold leakage = 0；
- 每条 Memory 均可回溯至 source trace / audit / outcome；
- 三臂行为实验真实执行，且运行时注入内容由独立记录核验。

但 S3B 没有满足进入 S4 的两个必要条件：

1. case 30–39 中 **0 个 `verified_success`**，因此无法验证“正确但过程污染 → OutcomeOnly 接收、TraceEvo 拒绝”；
2. Trusted Store 只有 **1 条 Validated Failure**，因此 M2 不是“可信成功经验记忆”，而是“一个失败防护提示反复注入”的实验。

同时，行为结果给出明确警告：

```text
M0 NoMemory:
  Match 0.20
  Relevant 0.60
  Unsupported Evidence 0.030
  P3-used 0.20

M2 TrustedMemory:
  Match 0.10
  Relevant 0.40
  Unsupported Evidence 0.053
  P3-used 0.30
```

n=10 不足以形成统计结论，但方向不支持继续直接堆叠 Capability Profile。

因此：

> **S3C 的任务不是继续“做自进化”，而是先获得真实的 verified-success 经验，并把 success/failure 两类记忆通道分开。**

本文件仅授权执行 S3C。完成后必须停止评审。

---

# 1. 对 S3B 两个核心问题的正式裁决

## D1. 接受 `NEED_MORE_VERIFIED_EXPERIENCE`

不允许通过以下方式人为制造 success：

- 将 judge score=1 提升为 `verified_success`；
- 使用 majority vote 把 2|0|2 等冲突结果判为成功；
- 放松 `3/3 score=2` 的成功定义；
- 降低 audit Gate 标准；
- 把 `uncertain` 经验偷偷放进 trusted retrieval。

严格的 outcome rule 保持冻结：

```text
3/3 score = 2 -> verified_success
3/3 score = 0 -> verified_incorrect
其他 -> uncertain
```

S3C 通过扩充**已存在且已封存的无记忆轨迹**获得更多成功样本，而不是改变判定标准。

---

## D2. Validated Failure 不再与 Validated Success 共用默认 Planner 检索通道

S3B 暴露了一个设计问题：

> case-32 的一条 Validated Failure 被 10 个 probe case 反复作为 `[Failure Prevention]` 注入。

它不是污染，但也没有证据表明应该基于病例文本相似度对所有任务注入。

因此从 S3C 起，Memory 分成两个逻辑通道：

```text
Success Memory
    -> 可按任务相似度在初始 Plan 阶段检索

Failure Memory
    -> 继续存储、可追溯
    -> S3C 默认不参与 initial Plan retrieval
    -> 未来 S4 只有在当前任务出现匹配 risk/anomaly condition 时才允许条件触发
```

S3C 不实现 S4 的在线风险触发，只完成通道分离。

这意味着未来不能再用：

```text
not verified_success == unsafe guidance
```

作为统一暴露定义。

从现在起至少分开报告：

```text
poison_exposure
unverified_success_guidance_exposure
validated_failure_guidance_exposure
```

Validated Failure 本身不是“unsafe”；它是否有益，需要单独实验。

---

# 2. S3C 的数据策略：不消耗 case 50–137

S3C **不读取、不运行 case 50–137**。

优先利用已经执行完成并封存的历史 no-memory traces：

```text
Tier A: case 10–29
来源：S3A migration
特点：S2 规则冻结后 blind 运行
用途：优先扩充 verified experience

Tier B: case 30–39
来源：S3B build
特点：已有 outcome
用途：继续保留

Tier C: case 0–9
来源：S0/S1/S2 Dev-Small
特点：曾用于 audit 规则开发
用途：只有 Tier A+B 成功经验不足时才作为 legacy supplement
```

case 40–49：
- 不再作为独立评测集；
- 已被 S3B 行为实验使用；
- S3C 只允许做**离线 retrieval dry-run**，不得重新运行 GoS 来制造新结果。

---

# 3. S3C-A：对 case 10–29 做 post-hoc Outcome Verification

## 3.1 不重新运行 GoS

直接读取 S3A 已封存的 baseline/no-memory：

```text
prediction
report
events
ledger
graph
audit summary
artifact hash
```

然后才读取 benchmark reference。

必须证明：

```text
S3A sealed artifact hash
==
S3C OutcomeVerifier 输入 artifact hash
```

禁止为了 outcome 重跑或修改 case 10–29 的推理。

---

## 3.2 OutcomeVerifier 完全复用 S3B

保持：

- 相同 judge 模型；
- 相同 judge prompt；
- 相同 temperature；
- 每例 3 次；
- 相同 0/1/2 rubric；
- 相同 success / incorrect / uncertain 规则。

不得在看到 10–29 的结果后修改 verifier。

输出：

```text
archive_outcome_results_10_29.csv
archive_judge_raw/
```

---

# 4. S3C-B：经验扩充的分阶段决策

完成 case 10–29 outcome 后，统计：

```text
N_success_A  = gold-safe verified success
N_failure_A  = validated failure
N_uncertain_A
```

其中 `gold-safe verified success` 仍必须同时满足：

```text
verified_success
AND integrity_ok
AND invocation_complete
AND semantic_trace_complete
AND no critical anomaly
AND no unresolved decision-path P3
```

## 4.1 如果 Tier A + B 已有 >= 5 条 gold-safe success

即：

```text
case 10–39 中 gold-safe success >= 5
```

则：

- **不要使用 case 0–9 扩充 success memory**；
- 直接进入 S3C-C。

---

## 4.2 如果 Tier A + B 只有 3–4 条 gold-safe success

允许对 case 0–9 的**历史封存轨迹**做 post-hoc outcome verification。

但所有来自 0–9 的经验必须标：

```text
corpus_source = legacy_dev
headline_eligible = false
```

它们可用于后续开发 Memory Store 的规模补充，但：

- 不计入 S3C 的 headline Gate retention；
- 不计入“独立成功经验数量”；
- 论文中不能和 10–39 无区别混报。

---

## 4.3 如果 Tier A + B 只有 0–2 条 gold-safe success

**S3C STOP。**

不自动读取 case 0–9，也不自动消耗 case 50–137。

返回：

```text
VERIFIED_SUCCESS_SCARCITY
```

此时需要外部评审重新决定：

- 是否更换/升级 backbone；
- 是否增加新训练数据；
- 是否改变论文主线为“审计 + failure-driven adaptation”；
- 是否重新分配尚未使用的病例。

不要用继续扩大病例数掩盖一个可能来自 backbone 的问题。

---

# 5. S3C-C：成功路径必须真正被执行

S3B 中 Gate 的 `eligible` 路径从未在真实数据上执行。

S3C 必须验证：

```text
verified_success
-> audit clean
-> eligible
-> success partition
-> retrieval index
-> injectable payload
```

对每条 success 检查：

- `memory_id`
- `source_trace_id`
- source hashes
- audit summary hash
- outcome verdict
- gate reasons
- extractor version

并确保：

```text
gold diagnosis 不存在于 payload
prediction 不存在于 payload
reference answer 不存在于 payload
```

---

# 6. S3C-D：补齐 S3B 缺失的第一个反例

一旦有真实 `verified_success`，必须从这些成功轨迹构造污染孪生。

优先：

```text
PZ1 unsupported Evidence
PZ2 record misbinding/tampering
PZ3 degraded/failed decision-path execution
PZ4 integrity tampering
```

每类至少应作用于一条真实 verified-success trace；若某 poison 类型技术上不适用，明确记录。

核心反例：

```text
原 outcome = verified_success
过程被污染
```

比较：

```text
OutcomeOnly
AuditOnly
TraceEvo
```

要求验证：

### OutcomeOnly
由于只看 outcome：

```text
至少会接收部分 correct-but-poisoned candidate
```

### TraceEvo
由于联合 outcome + audit：

```text
correct-but-poisoned -> 不得进入 eligible success
```

这才真正完成 S3B §7.2 的第一类反例。

---

# 7. S3C-E：重新计算 Gate 指标

必须分三种候选：

```text
Clean Verified Success
Clean Incorrect / Uncertain
Poisoned Verified Success
```

重新报告：

```text
Poison Admission Rate
Correct-but-Poisoned Admission Rate
Incorrect-Clean Admission Rate
Validated Success Retention
Admission Precision
Admission Recall
Manual Review Rate
Failure Capture Rate
```

### S3C headline denominator

headline Gate 指标只使用：

```text
case 10–39
```

若 case 0–9 被作为 legacy supplement，只单独报告，不进入 headline。

---

# 8. S3C-F：Memory 通道重构

Memory 目录保持：

```text
memory/
├── success/
├── failure/
├── manual_review/
└── rejected/
```

但 retrieval policy 改成：

```text
initial_plan_retrievable = success only
```

Failure Memory：

```text
stored = true
auditable = true
initial_plan_retrievable = false
```

为每条 failure memory 增加：

```json
{
  "failure_pattern": "...",
  "activation_condition": "...",
  "activation_status": "inactive_until_S4"
}
```

S3C 不实现 activation logic。

---

# 9. S3C-G：Success Memory Retrieval Abstention

S3B 的“一条 failure memory 到处命中”说明 top-k 本身不够。

Success Memory Retriever 必须支持：

```text
top_k
+ minimum relevance / abstention
```

但**不得根据 case 50+ 的最终性能调 threshold**。

## 9.1 离线校准数据

允许使用：

```text
case 10–39 的 memory/query pair
case 40–49 的 pre-task query，仅做 retrieval dry-run
```

不得使用 40–49 的最终诊断结果优化 threshold。

---

## 9.2 必须保留 abstain

若没有足够相关的 success memory：

```text
return []
```

禁止为了提高 hit rate 强行注入。

输出：

```text
success_retrieval_calibration.csv
retrieval_dryrun_40_49.csv
```

至少记录：

```text
query_case
top memory
score
threshold
retrieved / abstained
source case
memory type
```

---

# 10. S3C-H：不重新跑行为三臂

S3C **不重新运行 case 40–49 的 GoS 三臂**。

原因：

- 40–49 已经用于 S3B behavior probe；
- 再跑会是 development-after-observation；
- 单次病例成本很高；
- 当前首先需要证明“真实 success memory 存在且 Gate 可正确保留”。

S3C 只做离线 retrieval dry-run。

新的行为实验留给 S4，并使用届时重新冻结的、此前未运行过的病例。

---

# 11. S3C 成功门槛

建议进入 S4 必须同时满足：

### 必须项

1. `case 10–39` 中至少有 **5 条 gold-safe verified-success**；
2. 真实 success 经 TraceEvo Gate 至少保留一部分：
   ```text
   Validated Success Retention > 0
   ```
3. OutcomeOnly 对 correct-but-poisoned success 的 admission > 0；
4. TraceEvo correct-but-poisoned admission = 0；
5. TraceEvo Incorrect-Clean Admission < AuditOnly；
6. gold leakage = 0；
7. success/failure memory channel 已物理/逻辑分离；
8. failure memory 不再默认进入 initial Plan；
9. success retriever 支持 abstention；
10. 所有新 success memory 可回溯到 sealed historical trace。

### 推荐项

希望：

```text
gold-safe success >= 5
且至少来自 >= 3 个不同病例类型/证据模式
```

但“病例类型多样性”只报告，不作为硬性医学分类要求。

---

# 12. 如果 success 仍不足，必须停止

若 case 10–39：

```text
gold-safe success < 5
```

则：

```text
NEED_MORE_VERIFIED_EXPERIENCE
```

仍然成立，不进入 S4。

特别是若：

```text
gold-safe success <= 2
```

应优先怀疑：

- 当前 backbone 与 GoS 的诊断质量不足；
- exact-match outcome 对当前任务过难；
- 当前病例分布与模型能力不匹配。

这时应由外部评审决定是否更换模型/数据策略，而不是让本地智能体继续堆模块。

---

# 13. S3C 交付物

输出：

```text
S3C_experience_expansion/
├── S3C_report.md
├── REVIEW_REQUEST.md
├── corpus_manifest.json
├── sealed_artifact_check.csv
├── archive_outcome_results_10_29.csv
├── archive_judge_raw/
├── expanded_candidates.jsonl
├── expanded_gate_results.csv
├── success_poisoned_candidates.jsonl
├── expanded_gate_metrics.csv
├── success_memory_store/
├── failure_memory_store/
├── memory_channel_policy.md
├── success_retrieval_calibration.csv
├── retrieval_dryrun_40_49.csv
├── leakage_scan_results.csv
├── memory_audit_links.jsonl
├── failure_cases.md
├── tests/
├── git_diff.patch
└── git_commit.txt
```

如果触发 legacy supplement，再增加：

```text
legacy_outcome_results_0_9.csv
legacy_judge_raw/
```

---

# 14. S3C_report.md 必须回答

1. case 10–29 的 `verified_success / incorrect / uncertain` 分布？
2. case 10–39 一共多少 `gold-safe verified-success`？
3. success Gate 的真实 `eligible` 路径是否被执行？
4. Validated Success Retention 是多少？
5. correct-but-poisoned success 有多少？
6. OutcomeOnly 接收了多少 correct-but-poisoned success？
7. TraceEvo 接收了多少？
8. AuditOnly 对 incorrect-clean 的问题是否仍存在？
9. 是否使用了 case 0–9 legacy supplement？为什么？
10. success / failure memory 是否已分通道？
11. failure memory 是否完全退出 initial Plan retrieval？
12. success retrieval 在 case 40–49 dry-run 上 hit / abstain 分别多少？
13. retrieval threshold 是如何在不使用未来 outcome 的情况下确定的？
14. gold leakage 是否仍为 0？
15. 是否存在 ≥5 条可用于 S4 的真实、可追溯 success memory？
16. 建议：GO S4 / NEED_MORE_VERIFIED_EXPERIENCE / RECONSIDER_BACKBONE？

---

# 15. S3C 停止条件

S3C 完成后必须停止。

不得自动：

- 读取/运行 case 50–137；
- 实现 Capability Profile；
- 更新 agent capability EMA；
- 根据 Memory 改 agent selection；
- 做 S4 多轮演化；
- 更换 backbone；
- 修改 OutcomeVerifier 成功定义；
- 修改 frozen audit rules。

---

# 16. 给本地科研智能体的启动指令

> 阅读 `TraceEvo_S3A_Review_S3B_Amendment_v1.md`、`S3B_memory_gate/S3B_report.md`、`S3B_memory_gate/REVIEW_REQUEST.md`、`S3B_memory_gate/failure_cases.md` 与本文件。S3B 的防污染 Gate 判定通过，但由于 case 30–39 没有 verified success，不得进入 S4。仅执行 S3C。优先对已经封存的 S3A case 10–29 no-memory traces 做严格 post-hoc Outcome Verification；不得重跑 GoS，不得修改 judge / Gate / audit 规则。若 case 10–39 达到至少 5 条 gold-safe verified success，则补齐 success Gate、correct-but-poisoned 反例、success/failure memory 通道分离和 success retrieval abstention；若仅 3–4 条，才允许把 case 0–9 作为 legacy supplement，且不得纳入 headline Gate 指标；若 case 10–39 仅 0–2 条，则立即停止并返回 VERIFIED_SUCCESS_SCARCITY。S3C 不运行 case 50–137，不实现 Capability Profile，不进入 S4。完成交付物和 `REVIEW_REQUEST.md` 后停止。
