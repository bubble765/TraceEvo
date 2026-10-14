/**
 * 左栏：本次输入 / 步骤列表 / 步骤叙事 + 详情 / 播放控制。
 *
 * 结构对齐 `医疗溯因智能体/gos_repo/viz/story_template.html` 的左栏 ——
 * 同一套内容的用途自上而下排开，读的人不需要在页面间跳：
 *
 *   输入是什么（静态，永远在最上面）
 *   ↓ 走到了哪一步（可点，随时跳）
 *   ↓ 这一步在做什么 + 详情（随步变化，可滚动）
 *   ↓ 怎么控制（固定在底部）
 *
 * **叙事文字全部来自后端**（`GraphDeltaView.narrative`），前端不生成任何
 * 一句描述。前端生成的话，讲稿就会与图的实际变化脱节 —— 而那种脱节不会
 * 报错，只会让人看到一句与画面不符的说明。
 *
 * **详情为什么在这里，而不是原先的下边栏标签页**：它回答的是「这一步/
 * 这个节点是怎么回事」，与上面的叙事是同一个问题的两段答案，分开在两处
 * 反而要来回看。而且下边栏常驻会把两张图压小 —— 那是演示最该看的东西。
 */
import type {
  AuditView, CaseInput, DemoScript, GraphDeltaView,
  ProvenanceNode, ReasoningGraph, ReasoningNode, Vocabulary,
} from '../../types/protocol'
import { StepPanel } from '../panels/StepPanel'

interface Props {
  vocab: Vocabulary
  caseInput: CaseInput
  deltas: readonly GraphDeltaView[]
  demo: DemoScript | null
  current: number
  playing: boolean
  graph: ReasoningGraph | null
  onSeek: (step: number) => void
  onTogglePlay: () => void
  /** 详情区要的：当前选中的节点/事件与审计判定。 */
  selectedNode: ReasoningNode | null
  selectedNodeEvents: readonly ProvenanceNode[]
  selectedEvent: ProvenanceNode | null
  audit: AuditView
  onPickNode: (nodeId: string | null) => void
  onPickEvent: (eventId: string | null) => void
}

export function Sidebar({
  vocab, caseInput, deltas, demo, current, playing, graph,
  onSeek, onTogglePlay,
  selectedNode, selectedNodeEvents, selectedEvent, audit, onPickNode, onPickEvent,
}: Props) {
  const delta = deltas[current] ?? null
  const last = deltas.length - 1

  return (
    <aside className="col-side">
      <InputCard input={caseInput} />
      <StepList deltas={deltas} current={current} onSeek={onSeek} />
      {/* 叙事与详情共用一个滚动区：它们讲的是同一件事（这一步在做什么、
          这一步的东西各自是什么），分成两个各自能滚的框会让读者在两个
          滚动条之间来回找。 */}
      <div className="side-scroll">
        <Narrative delta={delta} graph={graph}
                   title={demo?.steps[current]?.title ?? null} />
        <div className="side-detail">
          <StepPanel vocab={vocab} delta={delta}
                     selectedNode={selectedNode}
                     selectedNodeEvents={selectedNodeEvents}
                     selectedEvent={selectedEvent}
                     audit={audit}
                     onPickEvent={onPickEvent}
                     onPickNode={onPickNode} />
        </div>
      </div>
      <Controls current={current} last={last} playing={playing}
                onSeek={onSeek} onTogglePlay={onTogglePlay} />
    </aside>
  )
}

// ------------------------------------------------------------------ 输入
function InputCard({ input }: { input: CaseInput }) {
  if (!input.symptom && input.evidence.length === 0) return null
  return (
    <div className="input-card">
      <div className="in-title">📥 本次输入</div>
      {input.symptom ? (
        <div className="in-row">
          <b>症状：</b>{input.symptom}
        </div>
      ) : null}
      {input.evidence.length > 0 ? (
        <div className="in-row" style={{ marginTop: 4 }}>
          <b>初始证据：</b>
          {input.evidence.map((e, i) => (
            <div key={i} style={{ paddingLeft: 10 }}>· {e}</div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

// ------------------------------------------------------------------ 步骤
const KIND_ICON: Record<string, string> = {
  规划: '🧭', 专家调用: '🤝', 工具调用: '🔧', 证据读取: '📄',
  专家分析: '🔬', 推理图更新: '🧠', 回溯: '↩️', 深入分析: '🔍',
  最终报告: '📋',
}

function StepList({
  deltas, current, onSeek,
}: {
  deltas: readonly GraphDeltaView[]
  current: number
  onSeek: (n: number) => void
}) {
  return (
    <div className="tl-list">
      {deltas.map((d, i) => {
        const cls = [
          'tl-step',
          i < current ? 'done' : '',
          i === current ? 'active' : '',
        ].filter(Boolean).join(' ')
        return (
          <div key={d.deltaId} className={cls} onClick={() => onSeek(i)}>
            <span className="tl-idx num">{i + 1}</span>
            <span style={{ flex: 'none' }}>
              {d.backtrackFromNodeId
                ? '↩️' : KIND_ICON[d.eventKindLabel ?? ''] ?? '•'}
            </span>
            {/* 手写展示轨迹自带步骤名（对齐参考项目的叫法），有就显示它 ——
                「知识会诊：RAG 检索临床推理经验」比「推理图更新 · 轮次 2」
                更说明这一步在干什么。真实运行为空，回落到事件类型。 */}
            <span className="tl-kind">
              {d.title || d.eventKindLabel || '—'}
              {!d.title && (d.agentStep ?? 0) > 0 ? ` · 轮次 ${d.agentStep}` : ''}
            </span>
            {d.isKeyStep ? <span className="tl-key">关键步</span> : null}
            <span className="tl-state">
              {i < current ? '✓' : i === current ? '●' : '○'}
            </span>
          </div>
        )
      })}
      {deltas.length === 0
        ? <div className="empty">该病例尚无增量记录</div>
        : null}
    </div>
  )
}

// ------------------------------------------------------------------ 叙事
function Narrative({
  delta, graph, title,
}: {
  delta: GraphDeltaView | null
  graph: ReasoningGraph | null
  title: string | null
}) {
  if (!delta) return <div className="narrative"><div className="empty">尚未开始</div></div>

  const typeOf = new Map(
    (graph?.nodes ?? []).map(n => [n.id, n.typeLabel]))
  const nodeOf = new Map((graph?.nodes ?? []).map(n => [n.id, n]))
  const focusLabel = (id: string) => {
    const node = nodeOf.get(id)
    if (!node) return id.replace('gos:', '')
    const level = node.confidenceLevel ? ` · L${node.confidenceLevel}` : ''
    return `${node.nodeId} ${node.label}${level}`
  }
  const byType = new Map<string, number>()
  for (const id of delta.addedNodes) {
    const t = typeOf.get(id) ?? '节点'
    byType.set(t, (byType.get(t) ?? 0) + 1)
  }

  return (
    <div className="narrative">
      <div className="n-title">
        第 {((delta.step ?? 0) + 1)} 步
        {title ? ` · ${title.replace(/^第 \d+ 步 · /, '')}` : ''}
      </div>
      <div className="n-actor">
        <span>🧠</span>
        <span>中央智能体</span>
        {delta.agentStep !== null
          ? <span style={{ color: 'var(--text-4)' }}>规划轮次 {delta.agentStep}</span>
          : null}
      </div>
      <div className="n-text">{delta.narrative}</div>

      <div className="n-changes">
        {delta.backtrackFromNodeId && delta.focusNodeId ? (
          <div className="l-focus-shift">
            <span>↩ Backtrack 焦点切换</span>
            <strong>{focusLabel(delta.backtrackFromNodeId)} → {focusLabel(delta.focusNodeId)}</strong>
          </div>
        ) : delta.focusNodeId ? (
          <div className="l-focus-current">
            当前焦点：{focusLabel(delta.focusNodeId)}
          </div>
        ) : null}
        {byType.size > 0 ? (
          <div className="l-add">
            + {[...byType].map(([t, n]) => `${n} 个${t}`).join('、')}
          </div>
        ) : null}
        {delta.removedNodes.length > 0 ? (
          <div className="l-refute">− {delta.removedNodes.length} 个节点被删除</div>
        ) : null}
        {delta.addedEdges.slice(0, 6).map((e, i) => (
          <div key={i} className={`l-${e.relation}`}>
            {e.relation === 'support' ? '🟢 支持'
              : e.relation === 'refute' ? '🔴 反驳' : '⚪ 细化'}
            {' '}{e.source.replace('gos:', '')} → {e.target.replace('gos:', '')}
          </div>
        ))}
        {delta.addedEdges.length > 6 ? (
          <div style={{ color: 'var(--text-4)' }}>
            …共 {delta.addedEdges.length} 条新关系
          </div>
        ) : null}
      </div>

      <div className="n-hash">
        <span className="mono">{delta.beforeHashShort} → {delta.afterHashShort}</span>
        {delta.replayOk === true
          ? <span style={{ color: 'var(--e-support)' }}>重放校验通过</span>
          : delta.replayOk === false
            ? <span style={{ color: 'var(--e-refute)' }}>重放未通过</span>
            : null}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ 控制
function Controls({
  current, last, playing, onSeek, onTogglePlay,
}: {
  current: number
  last: number
  playing: boolean
  onSeek: (n: number) => void
  onTogglePlay: () => void
}) {
  return (
    <div className="controls">
      <button onClick={() => onSeek(current - 1)} disabled={current <= 0}
              title="上一步 (←)">⏮ 上一步</button>
      <button className="primary" onClick={() => onSeek(current + 1)}
              disabled={current >= last} title="下一步 (→)">
        下一步 ▶
      </button>
      <button onClick={onTogglePlay} title="自动播放 (空格)">
        {playing ? '⏸ 暂停' : '▶ 自动播放'}
      </button>
      <button onClick={() => onSeek(0)} title="回到开头">↺</button>
      <span className="num" style={{ fontSize: 11.5, color: 'var(--text-4)',
                                     marginLeft: 'auto' }}>
        {current + 1}/{last + 1}
      </span>
    </div>
  )
}
