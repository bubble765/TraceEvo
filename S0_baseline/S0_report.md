# S0 报告：GoS 基线复现与代码定位

- 阶段：S0（计划 §12）
- 日期：2026-09-16
- 上游代码：https://github.com/gaorch85/Graph-of-States @ `0f28c2e`
- 工作副本：`gos/`（分支 `traceevo-s0`），原始检出保留在 `gos_official/`（未修改）
- 运行配置：见 `config.yaml`；环境：见 `environment.txt`

---

## 1. 本阶段目标与结论摘要

**结论：GoS 在替代 backbone 上可以成功运行，10 个医学病例全部跑通，但上游代码存在 5 处必须先修才能跑的问题，且绝对性能不可与论文数值直接比较。**

| 计划问题 | 回答 |
|---|---|
| GoS 是否可成功运行？ | **可以**，但需要 5 处补丁（§3）。上游代码**开箱即跑不通**：CSV 写入即崩溃、API key 为占位符、两处硬编码了第二个 backbone。 |
| 使用什么模型？ | `deepseek-v4-flash`（本地 opencode 网关）。**这是替代模型**，论文用 `GPT-5.1-2025-11-13`，二者不可比。 |
| 与论文配置有哪些不同？ | 见 §5。核心差异：backbone、无 Human-as-a-Judge、10 例而非 138 例。 |
| 10 个病例 Match/Relevant 如何？ | **Match 25.0 ± 8.4%、Relevant 61.7 ± 7.5%**（10 例 × 6 次 judge 重复），见 §4。论文为 31.88% / 74.64%（138 例，未报方差）。**模型不同、样本仅 10 例、judge 噪声 ±8pp，不可作等价性结论。** |
| Algorithm 1 每个函数对应哪个源码位置？ | 见 `gos_code_mapping.md`（完整映射表 + Algorithm 2 + 数据结构）。 |
| 当前最明显的复现问题是什么？ | 见 §6。最严重的是：**`UpdateGraph` 新增的 47 个 Evidence 节点中有 27.7%（13 个）无法回溯到任何来源**，且 Evidence 节点被当作万能桶，混装了「专家结论」「教科书知识」「建议」等非证据内容（§4.4、§6.1）。这正是 TraceEvo 要解决的问题本身，说明 S1 的溯源埋点是必要而非装饰。 |

---

## 2. 运行环境与数据

- 数据集：`datasets/DiagnosisArena/test_set.csv`，**138 行**，与论文「150 例剔除 12 例」一致。
- 本次使用 **case 0–9 共 10 例**（计划 §10 的 Dev-Small 划分），case 10–137 完全未触碰。
- 每例输入严格为 `##Case information`（主诉）+ `## Physical Examination`（体检）；`Diagnostic Tests` 列仅作为外部信息库 `evidence_texts`，**只有专家智能体显式申请检索才会返回**——与论文 Appendix C.1 的 interactive reformulation 一致。
- 智能体配置与论文 §4.1 一致：中心 `Primary_Physician`，专家 `Laboratory_Physician` / `Pathologist` / `Radiologist`。

---

## 3. 为跑通所做的补丁（5 处，全部为基础设施，不改算法）

补丁集中在 `gos/`，相对上游导入共 **+653 / −42 行**：修改上游文件 5 个（`.gitignore`、`belief/graph.py`、`configs/Agent_Configs.yaml`、`main.py`、`utils/public_function.py`，共 +244），新增独立文件 4 个（`configs/LLM_Configs.yaml`、`evaluate_Medical_scoring.py`、`collect_s0_artifacts.py`、`build_s0_results.py`，共 +451）。

**GoS 主循环（Algorithm 1/2）的语义一行未改**——对算法路径的全部改动只有「模型名从硬编码改为读配置」和「输出路径参数化」两类。完整补丁见 `git_diff.patch`。

| # | 问题 | 上游位置 | 处理 |
|---|---|---|---|
| P1 | `escapechar='"'` 与 `quotechar='"'` 同时使用，pandas 2.x 直接抛 `ValueError: bad escapechar or quotechar value`。**上游 `requirements.txt` 自己钉的就是 pandas 2.3.3**，即按官方依赖安装也跑不通。 | `main.py` 4 处 CSV 写入 | 删除 `escapechar`（`QUOTE_ALL`+`quotechar` 已按 RFC4180 双写转义） |
| P2 | API key 硬编码为占位符 `"Your-API-Key"`，base_url 硬编码为第三方代理 `api.chatanywhere.tech` | `utils/public_function.py:103-107` | 抽出 `configs/LLM_Configs.yaml`，key 由环境变量 `TRACEEVO_API_KEY` 注入 |
| P3 | **管线中混入了第二个 backbone**：`evidence_retriever()` 硬编码 `gpt-5.1`，`BeliefGraph.generate_belief_text()` 硬编码 `gpt-3.5-turbo`，均无视配置 | `utils/public_function.py:424`、`belief/graph.py:184` | 统一改为读取 `default_model_path()`，全部走 S0 backbone |
| P4 | 结果 CSV 路径 `"Correct-Path-Here"`、图 HTML 输出路径 `/data/luoyu6/...` 均为作者机器残留 | `main.py:38,171` | 改为 CLI 参数 `--out-csv / --html-dir / --json-dir / --log-dir / --cases` |
| P5 | 无任何重试：网关一次瞬时故障即中止一整个病例 | `utils/public_function.py` | 加指数退避重试（最多 5 次）。**实测必要**：10 例中共 3 次瞬时故障——2 次 `Internal server error` 被重试救回（case 5、6），1 次 `[Errno 54] Connection reset by peer` 因当时 `retry_on` 未覆盖网络层错误导致 case 8 **整体中止**，补全后重跑成功。<br>⚠️ 副作用：为覆盖这类故障，`retry_on` 现在包含 `"API call error"`，这会让**非瞬时**错误（如模型名非法）也重试 5 次后才抛出。S1 起应改为按异常类型而非消息子串判定 |

另有一处非阻断性问题：`prompts/prompt_generation.py:336` 残留调试语句 `print("here we are")`，每次专家内容生成都会打印。未删除，因为它不影响行为，且保留上游原貌便于比对。

---

## 4. 主要结果

### 4.1 诊断指标（case 0–9，LLM-as-a-Judge，6 次重复评分）

| 指标 | S0（10 例，`deepseek-v4-flash`） | 论文（138 例，`GPT-5.1`） |
|---|---|---|
| Match（=2 分） | **25.0 ± 8.4%**（单次极差 20–40%） | 31.88% |
| Relevant（≥1 分） | **61.7 ± 7.5%**（单次极差 50–70%） | 74.64% |

> ⚠️ **不得将二者等同**。样本量 10 vs 138，backbone 不同，本次无 Human-as-a-Judge，且论文只报告单次评分而未报告重复方差。**「数值接近」不是复现成功的证据**——在本次测量的 ±8pp 噪声下，10 例样本根本无法区分 25% 与 32%。
>
> `results.csv` 中的单次 Match 30.0% / Relevant 70.0% 是 6 次中的一次（run 1），仅为逐例分析提供索引；**报告任何指标时都必须带上重复次数与方差**。详见 §4.3。

### 4.2 逐例结果

| case | 预测 | 参考答案 | 分数 | LLM 调用 | prompt tok | 图节点/边 | 回溯 |
|---|---|---|---|---|---|---|---|
| 0 | Traumatic supraglottic edema | Chemical burns of pharynx/esophagus | 0 | 15 | 22,027 | 8 / 11 | 0 |
| 1 | Lymphocytic interface dermatitis | Inverse lichen planus | 1 | 62 | 104,426 | 22 / 49 | 0 |
| 2 | Third-degree AV block | Trifascicular heart block | 2 | 37 | 61,852 | 14 / 27 | 0 |
| 3 | Left vertebral dissection CVA | Acute cerebral infarction (posterior circ.) | 0 | 46 | 79,917 | 20 / 37 | 0 |
| 4 | Acupuncture hemopneumothorax | Traumatic hemopneumothorax w/ pulm. hemorrhage | 2 | 25 | 38,051 | 10 / 22 | 0 |
| 5 | Metastatic renal carcinoma | Diffuse liver metastasis from RCC | 1 | 69 | 113,889 | 21 / 44 | **1** |
| 6 | Penile B-cell lymphoma | Richter's transformation (penile ulcer) | 1 | 46 | 70,171 | 13 / 27 | 0 |
| 7 | Acute bacterial otomastoiditis | Facial palsy + hearing loss (aural irrigation) | 0 | 62 | 100,278 | 19 / 42 | 0 |
| 8 | Radiation-induced eosinophilia | EPPER (eosinophilic eruption, radiotherapy) | 1 | 92\* | 144,284 | 14 / 27 | **1** |
| 9 | Cardiac lymphoma infiltration | Secondary cardiac lymphoma | 2 | 62 | 102,285 | 22 / 46 | **1** |

\* case 8 的计数含首次因网关连接重置而中止的那次尝试。

**开销汇总**：516 次 LLM 调用 / 837,180 prompt tokens / 536,632 completion tokens，单例 15–92 次调用（中位数 ≈ 54）。单例波动很大，是 S5 规划算力与 S6 预算固定时的关键输入。

### 4.3 Judge 稳定性（重要：影响后续所有阶段的评估）

**同一份预测、同一 judge prompt、同一模型，仅因采样随机性重复评分 6 次：**

| 重复 | Match | Relevant | 分数分布（0/1/2） |
|---|---|---|---|
| run 1 | 30.0% | 70.0% | 3 / 4 / 3 |
| rep 1 | 20.0% | 60.0% | 4 / 4 / 2 |
| rep 2 | 20.0% | 60.0% | 4 / 4 / 2 |
| rep 3 | 20.0% | 50.0% | 5 / 3 / 2 |
| rep 4 | **40.0%** | 70.0% | 3 / 3 / 4 |
| rep 5 | 20.0% | 60.0% | 4 / 4 / 2 |
| **均值 ± 标准差** | **25.0 ± 8.4** | **61.7 ± 7.5** | — |
| **极差** | 20 → 40（**20pp**） | 50 → 70（**20pp**） | — |

**逐例稳定性**（6 次评分中 **7 例完全一致，3 例不稳定**）：

| 不稳定的 case | 6 次得分 | 标准差 |
|---|---|---|
| case 2 | 2, 0, 0, 0, 2, 2 | 1.10 |
| case 4 | 2, 2, 2, 0, 2, 0 | 1.03 |
| case 8 | 1, 1, 1, 2, 2, 1 | 0.52 |

不稳定性是**双向**的（case 2 既给过 2 分也给过 0 分），说明这不是简单的「系统性偏严/偏松」，而是 judge 在特定病例上确实无法稳定判定。论文 Figure 8 报告 10 例中 4 例不一致（本次为 3 例），量级一致，这也是论文引入 Human-as-a-Judge 的原因。

**对后续阶段的直接影响（必须在 S1 前解决）**：
- S3/S4 要证明「可信经验带来提升」。当前 judge 噪声标准差已达 **8.4pp**，若预期提升幅度小于此，实验**无法区分信号与噪声**；
- 因此 E3/E4 的病例数必须显著大于 10，且**必须报告多次 judge 重复的均值与标准差**，不能只跑一次评分；
- 建议 S3 起把「多次重复的均值 ± 标准差」写入指标定义，并把「提升 > 2σ」作为可判定的成功判据；
- 每次 judge 调用的 prompt/response 已全量落盘（`judge_raw.jsonl` 及 `judge_raw_rep*.jsonl`），满足计划 §14.7。

> 注：`results.csv` 中的 `judge_score` 取自 run 1（即首次完整评分），后续所有阶段的基准对比应统一使用同一重复策略，不可混用单次结果。

### 4.4 关键观察：Evidence 节点无来源约束，且语义被混用

> ⚠️ **本节数字为探索性结果，已被 S1 取代。** 评审决定 D2 判定：本节使用的分类程序是一次性的
> 分析脚本，不是交付包中可复现的自动化流程，因此「27.7%」**不得作为论文正式实验数据**。
> S1 通过事件绑定重新计算了该指标，见 `S1_provenance/grounding_results.csv` 与
> `grounding_sensitivity.csv`（后者给出分类规则在不同阈值下的取值，说明该数字对规则选择敏感）。
> 本节的**定性结论**（Evidence 类型被混用作多种语义）仍然成立，并在 S1 中被 `source_class`
> 与 `semantic_kind` 两个字段显式建模。

10 例共 80 个 Evidence 节点（最终图），按来源分类：

| 类别 | 数量 | 占比 | 说明 |
|---|---|---|---|
| A. ingest 期生成（输入侧） | 33 | 41.3% | 来自主诉与体检的复述，可 100% 回溯到输入文本，**合理** |
| B. 可回溯到外部证据库 | 28 | 35.0% | 与 `Diagnostic Tests` 原文高度重合，**合理** |
| C. 缺失/阴性陈述 | 6 | 7.5% | 如「Immunohistochemistry … results are absent」，**陈述真实但本质不是证据** |
| D. 可回溯到输入文本 | 0 | — | — |
| E. 无任何来源 | **13** | **16.3%** | 见下 |

只看 `UpdateGraph` 阶段新增的 47 个 Evidence 节点：**27.7% 无来源**。E 类样例（`/tmp/evidence_categories.csv` 的完整版已随交付保存）：

- `case5 node-20`: `PAX8 positivity is classically associated with renal cell carcinoma` —— **教科书知识**，被写成 Evidence 节点；
- `case3 node-15`: `Radiologist analysis states that left vertebral artery fenestration and nec…` —— **专家分析结论**，被写成 Evidence 节点；
- `case5 node-21`: `Pathologist and radiologist recommend dedicated renal imaging, CD10/CAIX st…` —— **建议**，被写成 Evidence 节点；
- `case2 node-12`: `Echocardiography excludes significant structural or valvular disease` —— **由证据推出的判断**，被写成 Evidence 节点；
- `case0 node-8`: `No infectious symptoms or allergic triggers identified` —— **阴性判断**。

**结论**：GoS 的 Evidence 节点是一个语义万能桶，至少混装了 5 类不同性质的东西：检索到的患者数据、输入复述、缺失陈述、医学背景知识、以及专家结论/建议。这带来两个直接后果：

1. **`support`/`refute` 边的语义不可信**。一条 `support` 边可能来自真实检索证据，也可能来自 LLM 自己写下的教科书常识——而「|Esup| ≥ η」正是 drill-down 的判据（η=3）。
2. **结论可回溯性无法在 GoS 内部验证**。计划 §5.3 要求「最终诊断能够回溯到 Evidence」，但此处的 Evidence 未必指向真实读取过的数据。

→ 这为计划 H1/H2 提供了**来自基线自身的实证**：Provenance Graph 必须把「Evidence 节点」拆成 (真实数据读取事件, 输入复述, 缺失陈述, 背景知识, 结论/建议)，并给每个节点绑定产生它的 `EVIDENCE_READ` / `ANALYSIS` 事件。这是 S1 schema 设计的直接输入。

### 4.5 关键观察：图历史在内存中并不存在（对 S1 的硬约束）

`BeliefGraph.to_dict()`（`belief/graph.py:98-110`）返回的 `nodes` / `edges` 是**活动字典的引用，没有拷贝**。`Central_Agent.graphs` 里追加的每一帧因此都是同一份数据：实测 10 例中**每一例的所有快照哈希完全相同**。

后果：
- 上游 HTML 可视化里的「图演化」滑块实际展示的是同一张最终图，N 次；
- **下游无法从 `graphs` 重建「第 t 步图长什么样」**，因此计划 §5.3「每一次 UpdateGraph 都必须能追溯到产生该变化的事件集合」**不可能靠现有快照实现**。

→ S1 必须在 hook 处**深拷贝**图状态（或记录 delta），不能复用 `central_agent.graphs`。这是 S0 发现的、必须写进 S1 设计的硬约束。

---

## 5. 与论文设置的差异（计划 §10 要求逐条记录）

| 项 | 论文 | 本次 S0 | 影响 |
|---|---|---|---|
| Backbone | `GPT-5.1-2025-11-13` | `deepseek-v4-flash` | **绝对数值不可比**；后续所有方法必须固定在此模型上做相对比较 |
| 接入方式 | `api.chatanywhere.tech` | 本机 opencode 网关 `localhost:18080` | 仅传输层差异 |
| 温度 | 0.8（仓库配置） | 0.8 | 一致 |
| `max_tokens` | 仓库配置 4096 | **未发送** | 上游 `llm_generate_response` 接收 `max_tokens` 参数、写进 meta 计费，**却从不放进请求 payload**。S0 保留该行为（`send_max_tokens: false`）以忠实复现，仅记录此差异 |
| 最大交互轮数 | 3 | 3（`session_max_steps`） | 一致 |
| 每专家检索预算 | 3 | 3（`max_retrieval_steps`） | 一致 |
| δ（confidence gap） | 敏感性分析给出曲线，正文未写死 | 0.3（仓库配置） | 沿用作者配置 |
| η（min support） | 同上 | 3（仓库配置） | 沿用作者配置 |
| Judge | LLM-as-a-Judge **+** Human-as-a-Judge（临床研究者 110+ 小时人工评分） | **仅 LLM-as-a-Judge** | 缺少人工评分；论文指出 LLM judge 在医学域不稳定、偏向长回答，故本次 Match 数值尤其只能作相对参考 |
| 病例数 | 138 例全量 | 10 例 Dev-Small | 计划 §10 的开发纪律要求 |
| 价格 | $0.12/case | 未计（网关返回 cost=0） | 换模型后原本也不可比 |

---

## 6. 当前最明显的复现问题（按严重度排序）

1. **Evidence 节点无来源约束，且语义被混用——TraceEvo 的动机在此得到基线自身的实证（详见 §4.4）。**
   `UpdateGraph` 阶段中心智能体可自由 `add_node(node_type="Evidence", label=...)`，代码里**不存在任何与 `evidence_texts` 外部信息库的核对**。量化结果：`UpdateGraph` 新增的 47 个 Evidence 节点中 **27.7%（13 个）无法回溯到任何来源**，且 Evidence 类型混装了教科书知识、专家结论、建议等非证据内容。由于 `|Esup(v_hyp)| ≥ η` 是 drill-down 的判据，**这些边的存在直接影响了状态机的推进决策**。
   → 计划 H1 成立：GoS 的因果图无法表达「这条证据是谁、通过哪次检索、从哪个版本拿到的」。**S1 的溯源埋点有明确落点，且必须先把 Evidence 拆成多个语义子类。**

2. **结论可回溯性无法在 GoS 内部验证。**
   最终 `prediction` 由 LLM 在 `construct_report_or_refine_prompt` 中直接给出，代码没有「结论必须连通到 Evidence 节点」的检查。计划 §6.1 的 `E(τ)`（结论—证据一致性）在 GoS 里没有对应实现——这也说明审计模块是新增能力而非重复建设。

3. **回溯机制几乎不触发。**
   10 例中仅 case 5、8、9 各触发 1 次 backtracking（共 3 次），且 case 8 那次实际删除了 1 个层级。`gap_delta=0.3` + `min_support=3` + **强制 `max_steps=2` 推进**三者叠加，使得同层往往还没比较出高低就被迫下钻。这对 S2 的异常注入设计有直接影响：**A4（Corrupted Intermediate Result）注入后若不构造特定的置信度分布，很可能不会自然触发回溯路径**，注入器需要显式设计能引发回溯的场景。

4. **图历史在内存中不存在，S1 不能复用 `central_agent.graphs`（详见 §4.5）。**
    `to_dict()`（`belief/graph.py:98-110`）返回引用而非拷贝，10 例中每例的所有快照哈希完全相同。计划 §5.3 的「每次 UpdateGraph 可追溯到事件集合」**必须靠 hook 处深拷贝实现**，否则 S1 的交叉映射无数据可依。

5. **`delete_level_nodes` 不级联删除证据。**
   回溯只删该层 Hypothesis 节点，挂在这些假设上的 Evidence 节点会变成孤立节点残留在图中，并被后续 `generate_belief_text` 一起喂给 LLM。这是潜在的上下文污染源，S2 审计「完整性」时需注意区分「真实孤立证据」与「回溯残留」。

6. **层级链回溯实现脆弱（潜在崩溃点）。**
   `find_ancestor_node()` 收集**所有**入边（不区分 `refines`/`support`/`refute`）后取 `parent_edges[0]` 作为父节点。若第一条入边来自 Evidence 节点，第 411 行读取 `["attrs"]["level"]` 会抛 `KeyError`。实测因 `refines` 边总先插入而未触发，但这是 S2 注入 A5（Trace Deletion，删除关键 event）时**最可能被踩中的崩溃点**。

7. **单例开销波动极大。**
   单例 LLM 调用 15–92 次（相差 6 倍），prompt token 22k–144k。S5 的预算固定与算力规划必须按最坏情况留余量，否则 E6（额外开销）测量会被长尾病例主导。

8. **上游 judge 脚本不产出指标。**
   `evaluate_Medical.py` 只把评分原文 `print` 出来，不解析、不落盘、不算 Match/Relevant，且 `data_path = "Correct-Path-Here"`。论文的指标无法由官方脚本复算。已新增 `evaluate_Medical_scoring.py`：沿用上游 judge system prompt 与 5 例分组，增加分数解析、逐例落盘、以及按计划 §14.7 要求的 **judge prompt + 原始 response 全量留存**（`judge_raw.jsonl`）。

---

## 7. 进入 S1 的门槛自查（计划 §12）

| 门槛 | 状态 |
|---|---|
| 10 个病例可稳定执行 | ✅ 10/10 全部产出预测与报告（case 8 首次因网关故障中止，重试机制补全后成功） |
| 指标脚本可重复运行 | ✅ `evaluate_Medical_scoring.py`，输入结果 CSV 即可重算，judge prompt/response 全量留存；已重复运行 6 次并据此量化了噪声（§4.3） |
| 不存在未解释的严重运行错误 | ✅ 唯一一次失败（case 8）已定位为网关瞬时连接重置，非代码缺陷 |

> **对计划的一处必要修订建议**：门槛「10 个病例可稳定执行」已满足，但 §4.3 表明**judge 噪声在 10 例规模下与预期效应量同量级**。计划 §9.2 的 E3/E4 若仍按小样本执行，将无法支撑「自进化有效」的结论。建议在 S3 开始前把 Dev-Evolve 的病例数下限写入计划（建议 ≥40），并要求所有指标报告多次 judge 重复的均值±方差。

---

## 8. 交付物清单

| 文件 | 说明 |
|---|---|
| `S0_report.md` | 本文件 |
| `REVIEW_REQUEST.md` | 阶段评审请求（计划 §13 格式） |
| `environment.txt` | 主机 / Python / 依赖 / commit / backbone 记录 |
| `gos_code_mapping.md` | Algorithm 1/2 与数据结构 → 源码位置映射表 |
| `config.yaml` | 冻结的运行配置（含替代模型声明） |
| `results.csv` | 逐例预测、参考答案、judge 分数、调用/token/图统计 |
| `gos_predictions_raw.csv` | 上游 `main.py` 直接写出的结果表（未经后处理），保留以便核对 |
| `judge_raw.jsonl` / `judge_raw_rep{1..5}.jsonl` | 6 次评分各自的 judge prompt 与原始 response（§14.7） |
| `judge_scores*.csv` / `metrics_summary*.json` | 6 次评分的逐例分数与汇总指标（§4.3 的方差来源） |
| `evidence_grounding_analysis.csv` | 80 个 Evidence 节点的逐节点来源分类（§4.4） |
| `raw_outputs/` | 逐例完整轨迹、因果图 JSON/HTML、摘要（详见其内 `README.md`） |
| `run_configs/` | 本次实际生效的 4 个 GoS 配置文件快照 |
| `run.log` | 完整运行日志（含所有 LLM 调用与重试） |
| `git_commit.txt` | 上游 commit 与 S0 工作副本 commit |
| `git_diff.patch` | S0 补丁全文（相对上游导入），可 `git apply` 复现 |
