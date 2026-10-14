/**
 * 双图联动：从「用户选了什么」算出「两张图各高亮什么」。
 *
 * 这是前端唯一一处知道两张图如何对应的代码。它刻意是**纯函数** ——
 * 输入是后端下发的 `BindingIndex` 与选区，输出是两组 id 集合，没有
 * 组件状态、没有副作用。理由：联动一旦分散进各个组件，就会出现
 * 「点这里高亮对了、点那里高亮错了」这类只在特定路径下出现的 bug，
 * 而这种 bug 不会被报错，只会让评审看到一个错误的高亮。
 *
 * 它同时是**唯一的降级点**：`BindingIndex` 里查不到的 id 一律被忽略，
 * 不抛错、不猜。真实分析模式下产物是逐步落盘的，索引里暂时缺项是正常
 * 状态，不是错误 —— 那时候应该少高亮一个节点，而不是让整个界面白屏。
 */
import type {
  AnomalyPath, BindingIndex, GraphDeltaView, ProvenanceEdge, ReasoningEdge,
} from '../types/protocol'

export type HighlightKind = 'none' | 'node' | 'event' | 'anomaly'

export interface Highlights {
  kind: HighlightKind
  /** 推理图中需要强调的节点 id（`gos:node-N`）。 */
  reasoning: ReadonlySet<string>
  /** 执行图中需要强调的节点 id（`evt-…` / `agent:…` / `tool:…` / `rec-…`）。 */
  provenance: ReadonlySet<string>
  reasoningEdges: ReadonlySet<string>
  provenanceEdges: ReadonlySet<string>
  /** 一句话说明当前高亮的含义，显示在操作条上。 */
  caption: string
}

const EMPTY: Highlights = {
  kind: 'none',
  reasoning: new Set(),
  provenance: new Set(),
  reasoningEdges: new Set(),
  provenanceEdges: new Set(),
  caption: '',
}

/** 推理边的 id 规则与后端 `services/reasoning_graph.py` 保持一致。 */
export function reasoningEdgeId(src: string, rel: string, dst: string): string {
  return `re:${src}->${dst}:${rel}`
}

function edgesTouching<T extends { source: string; target: string; id: string }>(
  edges: readonly T[], ids: ReadonlySet<string>,
): Set<string> {
  const out = new Set<string>()
  for (const e of edges) {
    if (ids.has(e.source) || ids.has(e.target)) out.add(e.id)
  }
  return out
}

export interface LinkageInput {
  binding: BindingIndex | null
  nodeId: string | null
  eventId: string | null
  anomalyId: string | null
  reasoningEdges: readonly ReasoningEdge[]
  provenanceEdges: readonly ProvenanceEdge[]
  deltas: readonly GraphDeltaView[]
}

export function computeHighlights(input: LinkageInput): Highlights {
  const { binding, nodeId, eventId, anomalyId } = input

  // 异常优先：聚焦异常时，整条影响链是主角，节点选中退居其次。
  if (anomalyId && binding) {
    const a: AnomalyPath | undefined = binding.anomalyPaths[anomalyId]
    if (a) return fromAnomaly(a, input)
  }
  if (nodeId && binding) {
    const n = binding.nodeToEvents[nodeId]
    if (n) return fromNode(nodeId, n, input)
  }
  if (eventId && binding) {
    const e = binding.eventToNodes[eventId]
    if (e) return fromEvent(eventId, e, input)
  }
  return EMPTY
}

// ------------------------------------------------------------------ 节点
function fromNode(
  nodeId: string,
  n: BindingIndex['nodeToEvents'][string],
  input: LinkageInput,
): Highlights {
  // 执行侧要强调的是「这个节点从哪来」的完整链：创建它的图更新、
  // 产出它内容的那次分析、它依据的所有上游事件与其中的内容性事件。
  // 只高亮 createdByEvent 会漏掉真正的来源 —— 图更新只是写入动作，
  // 内容早在几步之前的检索与分析里就确定了。
  const prov = new Set<string>()
  const add = (x: string | null) => { if (x) prov.add(x) }
  add(n.createdByEvent)
  add(n.graphUpdateEvent)
  add(n.bestAnalysisEvent)
  n.upstreamEvents.forEach(add)
  n.sourceEvents.forEach(add)
  // 记录 id 本身就是执行图的节点 id（`rec-…`），直接可用。
  n.recordIds.forEach(add)

  const reasoning = new Set([nodeId])

  // 异常根节点所在的异常，也一并接入执行图。
  for (const aid of n.anomalies) {
    const a = input.binding?.anomalyPaths[aid]
    if (!a) continue
    a.affectedEvents.forEach(x => prov.add(x))
    a.pathToConclusion.forEach(x => prov.add(x))
    if (a.rootEventId) prov.add(a.rootEventId)
  }

  return {
    kind: 'node',
    reasoning,
    provenance: prov,
    reasoningEdges: edgesTouching(input.reasoningEdges, reasoning),
    provenanceEdges: edgesTouching(input.provenanceEdges, prov),
    caption: n.anomalies.length
      ? `节点 ${nodeId} · 是 ${n.anomalies.length} 条异常的根因`
      : `节点 ${nodeId} · 已高亮其在执行图中的来源链`,
  }
}

// ------------------------------------------------------------------ 事件
function fromEvent(
  eventId: string,
  e: BindingIndex['eventToNodes'][string],
  input: LinkageInput,
): Highlights {
  const reasoning = new Set<string>([
    ...e.created, ...e.updated, ...e.removed, ...e.affectedHypotheses,
  ])
  // 专家分析事件不一定直接改图（图更新是另一步），但它的内容是某些
  // 节点的来源。漏掉这层，「点分析事件」会看起来什么也没高亮。
  for (const [nid, ne] of Object.entries(input.binding?.nodeToEvents ?? {})) {
    if (ne.bestAnalysisEvent === eventId) reasoning.add(nid)
  }

  const delta = input.deltas.find(d => d.eventId === eventId)
  if (delta) {
    delta.addedNodes.forEach(x => reasoning.add(x))
    delta.updatedNodes.forEach(x => reasoning.add(x))
    delta.removedNodes.forEach(x => reasoning.add(x))
  }

  const prov = new Set<string>([eventId])
  // 执行图上一跳的邻居：它调用了谁、被谁依赖。没有这层，点一个事件
  // 只亮一个孤立的框，看不出它在协作结构里的位置。
  for (const pe of input.provenanceEdges) {
    if (pe.source === eventId) prov.add(pe.target)
    if (pe.target === eventId) prov.add(pe.source)
  }
  for (const nid of reasoning) {
    const ne = input.binding?.nodeToEvents[nid]
    if (!ne) continue
    ne.sourceEvents.forEach(x => prov.add(x))
  }

  const parts: string[] = []
  if (e.created.length) parts.push(`新增 ${e.created.length} 节点`)
  if (e.updated.length) parts.push(`更新 ${e.updated.length} 节点`)
  if (e.removed.length) parts.push(`删除 ${e.removed.length} 节点`)

  return {
    kind: 'event',
    reasoning,
    provenance: prov,
    reasoningEdges: edgesTouching(input.reasoningEdges, reasoning),
    provenanceEdges: edgesTouching(input.provenanceEdges, prov),
    caption: `事件 ${eventId}` + (parts.length ? ` · ${parts.join(' · ')}` : ''),
  }
}

// ------------------------------------------------------------------ 异常
function fromAnomaly(a: AnomalyPath, input: LinkageInput): Highlights {
  const reasoning = new Set<string>([
    ...a.affectedNodes, ...a.graphPath,
  ])
  if (a.rootNodeId) reasoning.add(a.rootNodeId)

  const prov = new Set<string>([
    ...a.affectedEvents, ...a.pathToConclusion,
  ])
  if (a.rootEventId) prov.add(a.rootEventId)

  return {
    kind: 'anomaly',
    reasoning,
    provenance: prov,
    reasoningEdges: edgesTouching(input.reasoningEdges, reasoning),
    provenanceEdges: edgesTouching(input.provenanceEdges, prov),
    caption: `${a.typeLabel}（${a.anomalyId}） · ${a.latentRootNote}`
      + (a.reachesReport ? ' · 抵达最终报告' : ''),
  }
}

/**
 * 「仅显示关联路径」：把与高亮无关的节点隐藏掉。
 *
 * 与 `computeHighlights` 分开，因为两者的语义不同：高亮是「注意这里」，
 * 隐藏是「其余的先不看」。把两者合成一个集合，就没法表达「高亮但保留
 * 上下文」这个状态 —— 而那恰恰是默认视图。
 */
export function visibleSets(
  allReasoning: readonly string[],
  allProvenance: readonly string[],
  h: Highlights,
  pathOnly: boolean,
): { reasoning: Set<string>; provenance: Set<string> } {
  if (!pathOnly || h.kind === 'none') {
    return {
      reasoning: new Set(allReasoning),
      provenance: new Set(allProvenance),
    }
  }
  return {
    reasoning: new Set(h.reasoning),
    provenance: new Set(h.provenance),
  }
}
