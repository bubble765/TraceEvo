/**
 * 双图数据协议。与 `webapp/backend/schemas.py` 一一对应。
 *
 * 两条不可违反的约定，它们在类型层面被表达出来：
 *
 * 1. **算法判断字段是只读的。** `tier` / `anomaly` / `outcome` / `gate`
 *    四类字段全部 `readonly`，前端没有本地推导路径。这不是风格偏好：
 *    一旦前端能自己算 tier，界面上就会存在两个可能不一致的 tier 来源，
 *    而「展示后端判定」的整个前提就没了。
 *
 * 2. **中文文案不在前端硬编码。** 每个枚举值都成对下发（`tier` + `tierLabel`）。
 *    前端不做词表查询 —— 查不到会渲染成空白，而空白是看不出来的错。
 *
 * 类型名与后端 Pydantic 模型同名，出现分歧时以 schemas.py 为准并同步这里。
 */

// ------------------------------------------------------------------ 通用
export interface CaseRef {
  readonly caseId: string
  readonly caseNumber: number
  readonly runId: string
  readonly runLabel: string
  /**
   * sealed = 封存产物（只读）；live = 真实分析模式产生；
   * showcase = **手写展示轨迹**（没有审计产物，界面上要说清楚）。
   */
  readonly kind: 'sealed' | 'live' | 'showcase'
  readonly isDemo: boolean
  readonly outcomeLabel: string | null
  readonly gateLabel: string | null
}

export interface CaseSummary {
  readonly caseId: string
  readonly runDir: string
  readonly kind: 'run' | 'showcase'
  /** false = 这一页**没有审计产物**（手写轨迹），不是「审计跑出来是空的」。 */
  readonly auditApplicable: boolean
  readonly auditNote: string
  readonly nEvents: number
  readonly nDeltas: number
  readonly nRecords: number
  readonly nNodes: number
  readonly tierCounts: Readonly<Record<string, number>>
  readonly nAnomalies: number
  readonly anomalyTypes: readonly string[]
  readonly outcome: string | null
  readonly outcomeLabel: string | null
  readonly gate: string | null
  readonly gateLabel: string | null
  /** null = **不适用**（展示轨迹无可校验的东西）；false 才是校验没通过。 */
  readonly integrityOk: boolean | null
  readonly replayOk: boolean | null
  readonly prediction: string | null
}

// -------------------------------------------------------------- 知识库目录
export interface KnowledgeDistribution {
  readonly label: string
  readonly count: number
}

export interface KnowledgeSampleField {
  readonly label: string
  readonly value: string
}

export interface KnowledgeSample {
  readonly title: string
  readonly category: string
  readonly fields: readonly KnowledgeSampleField[]
}

export interface KnowledgeBaseEntry {
  readonly id: 'patient_evidence' | 'medical_knowledge' | 'experience' | string
  readonly name: string
  readonly recordCount: number
  readonly recordCountLabel: string
  readonly taskCount: number | null
  readonly samples: readonly KnowledgeSample[]
  readonly distributions: readonly KnowledgeDistribution[]
}

export interface KnowledgeBasesView {
  readonly privacyNote: string
  readonly knowledgeBases: readonly KnowledgeBaseEntry[]
}

// ------------------------------------------------------------ 医学因果推理图
export interface RiskFlags {
  readonly unsourced: boolean
  readonly anomalyRoots: readonly string[]
}

export interface ReasoningNode {
  /** 带前缀，如 "gos:node-11"。图内引用的唯一形式。 */
  readonly id: string
  /** 裸 id，如 "node-11"。与 delta 对齐时用它。 */
  readonly nodeId: string
  readonly nodeType: string
  readonly typeLabel: string
  /** 中文展示文本。翻译发生在后端展示层，不参与任何判定。 */
  readonly label: string
  /** 推理管线产出的英文原文。审计与核对看它，界面用 tooltip 提供。 */
  readonly labelEn: string
  readonly tier: string | null
  readonly tierLabel: string | null
  readonly tierDesc: string | null
  /**
   * 当前分数。真实运行中的假设分数由后端按步从图更新响应与规划焦点重建；
   * 展示轨迹还保留参考图给 Signal / Evidence 节点写入的静态分数。
   *
   * 注意它不是参考项目 `belief/confidence.py` 那套工程化公式（z_t 累积 +
   * 层内归一化）算出来的数：本仓库冻结的 `gos/` 把那段移除了，走的是
   * 「LLM 直出」，两者数值不同。审计要的是前者。
   *
   * null = **没有读数**，与「读数是 0」是两件事，`confidenceNote` 说明原因。
   */
  readonly confidence: number | null
  readonly confidenceNote: string
  /** 该读数来自哪个事件。 */
  readonly confidenceEventId: string | null
  /**
   * 相对**上一步**的变化量。null = 没有可比的前值（刚出现）；
   * 0 = 这一步没重新评估它（持平）。两者是不同的信息，不要合并。
   */
  readonly confidenceDelta: number | null
  /**
   * 这次读数附带的理由（`confidence` 为什么会是这个数）。
   *
   * 与 `confidence` **同源同步** —— 它跟着时间轴走，不是节点上的静态字段。
   * 非假设节点没有置信度读数，它们的 `why` 是静态来源说明
   * （如「影像医师开具·左踝 MRI」）。null = 两处都没有。
   */
  readonly why: string | null
  /**
   * 层号。主来源是 bindings（参考项目把 level 存在节点自身的 attrs 上，
   * 因此**每个**假设都有层级）；没有时回落到规划焦点读数。
   */
  readonly confidenceLevel: number | null
  /** 指向该假设的 support / refute 边数，由后端从回放的图里数出来。 */
  readonly beliefSupports: number | null
  readonly beliefRefutes: number | null
  /** 来源匹配度的展示名。真实存在的每节点数值，来自 bindings.json。 */
  readonly matchCoverageLabel: string
  /** Showcase 没有审计判定时为 null。 */
  readonly usedInReasoning: boolean | null
  readonly usedLabel: string | null
  readonly createdByEvent: string | null
  readonly createdAtStep: number | null
  readonly sourceClass: string | null
  readonly sourceClassLabel: string | null
  readonly semanticKind: string | null
  readonly semanticKindLabel: string | null
  readonly matchTier: string | null
  readonly matchedRecordId: string | null
  /**
   * 来源匹配度。**只在绑定了检索记录时才有值**，否则为 null ——
   * 那种情况下 `matchNote` 说明为什么（来自病例输入 / 该类型不比对 /
   * 检索过但无记录被绑定）。对信号与假设显示 0.00 会被读成「匹配得很差」，
   * 而那是在回答一个没人问的问题。
   */
  readonly matchCoverage: number | null
  readonly matchNote: string | null
  readonly bestAnalysisEvent: string | null
  readonly risk: RiskFlags
}

export interface ReasoningEdge {
  readonly id: string
  readonly source: string
  readonly target: string
  /** support | refute | refines */
  readonly relation: string
  readonly relationLabel: string
  readonly createdAtEvent: string | null
  readonly visibleFromStep: number | null
}

export interface ReasoningGraph {
  readonly caseId: string
  readonly finalGraphHash: string | null
  readonly nodes: readonly ReasoningNode[]
  readonly edges: readonly ReasoningEdge[]
}

// ------------------------------------------------------------ 执行溯源图
export interface IOSummary {
  readonly input: string
  readonly output: string
  readonly inputEn: string
  readonly outputEn: string
  readonly inputTruncated: boolean
  readonly outputTruncated: boolean
}

export interface ProvenanceNode {
  readonly id: string
  readonly kind: string
  readonly kindLabel: string
  readonly label: string
  readonly labelEn: string
  readonly eventId: string | null
  readonly step: number | null
  readonly actor: string | null
  /** 智能体中文名，如「影像医师」。 */
  readonly actorLabel: string | null
  /** 头像 emoji，让「这一步是谁做的」在窄卡片上一眼可辨。 */
  readonly actorIcon: string | null
  readonly actorRole: string | null
  readonly actorRoleLabel: string | null
  readonly model: string | null
  readonly status: string | null
  readonly statusLabel: string | null
  readonly reasonCode: string | null
  readonly reasonCodeLabel: string | null
  readonly timestamp: string | null
  readonly eventHash: string | null
  readonly prevHash: string | null
  readonly parentEventIds: readonly string[]
  readonly inputRefs: readonly string[]
  readonly outputRefs: readonly string[]
  /** 跨图引用：本事件产生/影响了哪些推理图节点。联动靠它。 */
  readonly causalNodeRefs: readonly string[]
  readonly io: IOSummary
  readonly anomalyRoot: boolean
  readonly downstreamOfAnomaly: boolean
}

export interface ProvenanceEdge {
  readonly id: string
  readonly source: string
  readonly target: string
  readonly relation: string
  readonly relationLabel: string
}

export interface ProvenanceGraph {
  readonly caseId: string
  readonly nodes: readonly ProvenanceNode[]
  readonly edges: readonly ProvenanceEdge[]
}

// ------------------------------------------------------------ 增量
export interface EdgeChange {
  readonly source: string
  readonly target: string
  readonly relation: string
  readonly relationLabel: string
}

export interface ConfidenceChange {
  readonly nodeId: string
  readonly before: number | null
  readonly after: number | null
}

export interface GraphDeltaView {
  readonly deltaId: string
  readonly eventId: string
  /** 回放位置（delta 序号，0 起）。时间轴用它定位。 */
  readonly step: number | null
  /** GoS 规划轮次（事件上的 step_id）。一轮里可以有多次图更新。 */
  readonly agentStep: number | null
  readonly eventKind: string | null
  readonly eventKindLabel: string | null
  readonly actor: string | null
  readonly actorRoleLabel: string | null
  readonly timestamp: string | null
  readonly addedNodes: readonly string[]
  readonly removedNodes: readonly string[]
  readonly updatedNodes: readonly string[]
  /** 当前推理 frontier 的节点 id（用于展示当前焦点，不改变因果关系）。 */
  readonly focusNodeId: string | null
  /** Backtrack 前的焦点节点；与 focusNodeId 一起展示焦点迁移。 */
  readonly backtrackFromNodeId: string | null
  readonly addedEdges: readonly EdgeChange[]
  readonly removedEdges: readonly EdgeChange[]
  /**
   * 这一步真正被改动的假设置信度。新建立的假设**不在其中** —— 那是
   * 「从无到有」，不是一次变化（与参考项目 `apply_confidence_update`
   * 只在不等于旧值时才记一条同口径）。
   */
  readonly confidenceChanges: readonly ConfidenceChange[]
  readonly beforeHash: string
  readonly afterHash: string
  readonly beforeHashShort: string
  readonly afterHashShort: string
  readonly replayOk: boolean | null
  readonly summaryLabel: string
  /**
   * 这一步的标题。**手写展示轨迹自带一个**（对齐参考项目的步骤名，如
   * 「知识会诊：RAG 检索临床推理经验」）；真实运行为空串，界面回落到
   * 「事件类型 · 轮次 N」。
   */
  readonly title: string
  /** 逐步解说。真实运行由事件事实生成；展示轨迹可以是手写文案。 */
  readonly narrative: string
  readonly isKeyStep: boolean
  readonly keyStepReason: string
}

// ------------------------------------------------------------ 双向联动
export interface NodeEvents {
  readonly nodeId: string
  readonly createdByEvent: string | null
  readonly graphUpdateEvent: string | null
  readonly bestAnalysisEvent: string | null
  readonly upstreamEvents: readonly string[]
  readonly sourceEvents: readonly string[]
  readonly recordIds: readonly string[]
  readonly anomalies: readonly string[]
}

export interface EventNodes {
  readonly eventId: string
  readonly created: readonly string[]
  readonly updated: readonly string[]
  readonly removed: readonly string[]
  readonly affectedHypotheses: readonly string[]
}

export interface BindingIndex {
  readonly caseId: string
  readonly nodeToEvents: Readonly<Record<string, NodeEvents>>
  readonly eventToNodes: Readonly<Record<string, EventNodes>>
  readonly anomalyPaths: Readonly<Record<string, AnomalyPath>>
}

// ------------------------------------------------------------ 审计
export interface ThresholdContext {
  readonly directMinCoverage: number | null
  readonly softMinCoverage: number | null
  readonly softMinCharSimilarity: number | null
  readonly inputInferenceMinCoverage: number | null
  readonly expertSynthesisMinCoverage: number | null
  readonly minContentTokens: number | null
  readonly source: string
}

export interface TierDecisionView {
  readonly nodeId: string
  readonly label: string
  readonly labelEn: string
  readonly nodeType: string | null
  readonly nodeTypeLabel: string | null
  readonly provenanceTier: string
  readonly tierLabel: string
  readonly tierDesc: string
  readonly auditLabel: string | null
  readonly auditLabelText: string | null
  readonly derivation: string | null
  readonly derivationLabel: string | null
  readonly derivationKind: string | null
  readonly derivationKindLabel: string | null
  readonly bestRecordId: string | null
  readonly bestRecordHash: string | null
  readonly tokenCoverage: number | null
  readonly charSimilarity: number | null
  readonly bestSpanRatio: number | null
  readonly analysisCoverage: number | null
  readonly inputCoverage: number | null
  readonly usedInReasoning: boolean
  readonly usage: string | null
  readonly externallyGrounded: boolean
  readonly lineageComplete: boolean
  readonly bestAnalysisEvent: string | null
  readonly thresholds: ThresholdContext
}

export interface AnomalyPath {
  readonly anomalyId: string
  readonly type: string
  readonly typeLabel: string
  readonly typeNote: string
  readonly detectorRule: string | null
  readonly detectorRuleLabel: string | null
  readonly severity: string | null
  /** 异常检测器自身的置信度（与节点置信度无关，后者不存在）。 */
  readonly confidence: number | null
  readonly rootEventId: string | null
  readonly rootNodeId: string | null
  /** 「潜在注入根因」或「可观测故障起点」。 */
  readonly latentRootNote: string
  readonly affectedEvents: readonly string[]
  readonly affectedNodes: readonly string[]
  readonly affectedHypotheses: readonly string[]
  readonly pathToConclusion: readonly string[]
  readonly graphPath: readonly string[]
  readonly reachesReport: boolean
  readonly evidence: readonly Record<string, unknown>[]
}

export interface IntegrityView {
  readonly ok: boolean
  readonly nEvents: number
  readonly tipHash: string | null
  readonly expectedTipHash: string | null
  readonly nIssues: number
  readonly issues: readonly unknown[]
}

export interface ReplayCheck {
  readonly step: number
  readonly deltaId: string
  readonly beforeHash: string
  readonly expectedAfterHash: string
  readonly replayedHash: string | null
  readonly ok: boolean | null
}

export interface ReplayView {
  readonly nDeltas: number
  readonly allOk: boolean
  /** false 表示还没开始重放（产物未落盘），不是「校验失败」。 */
  readonly applicable: boolean
  readonly checks: readonly ReplayCheck[]
}

export interface GroundingView {
  readonly evidenceTotal: number
  readonly evidenceSourceBound: number
  readonly evidenceSourceBoundRate: number | null
  readonly nNodesBound: number
  readonly nRetrievalRecords: number
  readonly bySourceClass: Readonly<Record<string, number>>
  readonly bySourceClassLabel: Readonly<Record<string, number>>
  readonly bySemanticKind: Readonly<Record<string, number>>
  readonly bySemanticKindLabel: Readonly<Record<string, number>>
}

export interface AuditView {
  readonly caseId: string
  /**
   * false = 这一页**没有审计产物**（手写展示轨迹）。此时下面那些字段全部
   * 不可信也**不该显示** —— 它们要么是空数组、要么是 false，而「空的
   * P0–P3」会被读成「审计判过、没有无来源断言」。
   */
  readonly applicable: boolean
  readonly notApplicableReason: string
  readonly integrity: IntegrityView
  readonly replay: ReplayView
  readonly grounding: GroundingView
  readonly tierCounts: Readonly<Record<string, number>>
  readonly tierCountsLabel: Readonly<Record<string, number>>
  readonly tiers: readonly TierDecisionView[]
  readonly anomalies: readonly AnomalyPath[]
}

// ------------------------------------------------------------ 账本
export interface LedgerRow {
  readonly callId: string
  readonly stage: string | null
  readonly stageLabel: string | null
  readonly actorId: string | null
  readonly actorRole: string | null
  readonly actorRoleLabel: string | null
  readonly modelId: string | null
  readonly binding: string | null
  readonly bindingLabel: string | null
  readonly parseStatus: string | null
  readonly parseStatusLabel: string | null
  readonly committedToState: boolean
  readonly commitTarget: string | null
  readonly commitTargetLabel: string | null
  readonly committedEventIds: readonly string[]
  readonly discardReason: string | null
  readonly discardReasonLabel: string | null
  readonly promptHash: string | null
  readonly responseHash: string | null
  readonly stepId: number | null
  readonly timestamp: string | null
  /** 被采纳但没有图元素承接。注意这不等于错误（检索阶段本就如此）。 */
  readonly isUncommitted: boolean
}

export interface LedgerView {
  readonly caseId: string
  /** false = **没有账本**（手写轨迹）。与 `nCalls === 0` 不是一回事。 */
  readonly applicable: boolean
  readonly notApplicableReason: string
  readonly nCalls: number
  readonly nAccepted: number
  readonly nDiscarded: number
  readonly nUncommitted: number
  readonly rows: readonly LedgerRow[]
}

// ------------------------------------------------------------ 经验
export interface OutcomeView {
  readonly verdict: string | null
  readonly verdictLabel: string | null
  readonly verdictDesc: string | null
  readonly judgeScores: readonly (number | null)[]
  readonly unanimous: boolean | null
  readonly prediction: string | null
  readonly referenceSha256: string | null
}

export interface GateView {
  readonly eligibility: string | null
  readonly eligibilityLabel: string | null
  readonly eligibilityDesc: string | null
  readonly reasons: readonly string[]
  readonly nAnomalies: number
  readonly nUsedP3: number
  readonly nUsedP1: number
}

export interface ExperienceCard {
  readonly memoryId: string | null
  readonly partition: string
  readonly partitionLabel: string
  readonly partitionDesc: string
  readonly title: string
  readonly failureMode: string | null
  readonly failureModeLabel: string | null
  readonly avoidanceRule: string | null
  readonly suggestedChecks: readonly string[]
  readonly rendered: string | null
  readonly renderedEn: string | null
  readonly sourceTraceId: string | null
  readonly auditSummaryHash: string | null
  readonly outcomeVerdict: string | null
  readonly extractorVersion: string | null
}

export interface ForbiddenCheck {
  readonly checked: boolean
  readonly containsGold: boolean
  readonly containsPatientText: boolean
  readonly containsReferenceDiagnosis: boolean
  readonly note: string
}

export interface ExperienceView {
  readonly caseId: string
  /**
   * false = 这一页**没有判题与门控**（手写展示轨迹）。没有它的话，面板只能
   * 把空值渲染成「待判定」—— 那是在说「还在等结果」，而真相是根本没有结果
   * 会来。
   */
  readonly applicable: boolean
  readonly notApplicableReason: string
  readonly outcome: OutcomeView
  readonly gate: GateView
  readonly card: ExperienceCard | null
  readonly forbidden: ForbiddenCheck
  readonly headline: string
}

// ------------------------------------------------------------ 演示脚本
export interface DemoStep {
  readonly index: number
  readonly deltaId: string
  readonly eventId: string
  readonly title: string
  readonly narrative: string
  readonly highlights: readonly string[]
  readonly addedNodes: readonly string[]
  readonly isKeyStep: boolean
}

export interface CaseInput {
  readonly symptom: string
  readonly symptomEn: string
  readonly evidence: readonly string[]
  readonly evidenceEn: readonly string[]
}

export interface DemoScript {
  readonly caseId: string
  readonly steps: readonly DemoStep[]
  readonly epilogue: string
  readonly caseInput: CaseInput
}

// ------------------------------------------------------------ 执行档案
export interface RawArtifactRef {
  readonly name: string
  readonly description: string
  readonly available: boolean
}

export interface RawIndex {
  readonly caseId: string
  readonly artifacts: readonly RawArtifactRef[]
}

// ------------------------------------------------------------ 词表
export interface Vocabulary {
  readonly tier: Readonly<Record<string, string>>
  readonly tierShort: Readonly<Record<string, string>>
  readonly tierDesc: Readonly<Record<string, string>>
  readonly tierOrder: readonly string[]
  readonly auditLabel: Readonly<Record<string, string>>
  readonly derivation: Readonly<Record<string, string>>
  readonly derivationKind: Readonly<Record<string, string>>
  readonly derivationKindLabel: Readonly<Record<string, string>>
  readonly semanticKind: Readonly<Record<string, string>>
  readonly sourceClass: Readonly<Record<string, string>>
  readonly nodeType: Readonly<Record<string, string>>
  readonly relation: Readonly<Record<string, string>>
  readonly provEdge: Readonly<Record<string, string>>
  readonly eventType: Readonly<Record<string, string>>
  readonly provKind: Readonly<Record<string, string>>
  readonly status: Readonly<Record<string, string>>
  readonly reasonCode: Readonly<Record<string, string>>
  readonly anomalyType: Readonly<Record<string, string>>
  readonly anomalyNote: Readonly<Record<string, string>>
  readonly detectorRule: Readonly<Record<string, string>>
  readonly partition: Readonly<Record<string, string>>
  readonly partitionDesc: Readonly<Record<string, string>>
  readonly outcome: Readonly<Record<string, string>>
  readonly outcomeDesc: Readonly<Record<string, string>>
  readonly gateRule: Readonly<Record<string, string>>
  readonly callStage: Readonly<Record<string, string>>
  readonly parseStatus: Readonly<Record<string, string>>
  readonly commitTarget: Readonly<Record<string, string>>
  readonly ledgerBinding: Readonly<Record<string, string>>
  readonly discardReason: Readonly<Record<string, string>>
  readonly actorRole: Readonly<Record<string, string>>
  readonly agentName: Readonly<Record<string, string>>
  readonly agentIcon: Readonly<Record<string, string>>
  readonly failurePattern: Readonly<Record<string, string>>
  readonly ui: Readonly<Record<string, string>>
}
