/**
 * 「当前步骤详情」面板。三种内容按优先级择一显示：
 *
 *   选中了推理节点  -> 该节点的来源审计（tier 判定依据 + 度量 + 阈值）
 *   选中了执行事件  -> 该事件的输入输出与跨图引用
 *   都没选          -> 当前 delta 的增量明细
 *
 * 之所以是「择一」而不是三块并列：这三者回答的是同一个问题（「现在看的
 * 是什么」），并列会让面板变成一堆需要自己找的信息，而演示时观众只有
 * 几秒钟扫一眼。默认回落到 delta，保证任何时候这个面板都有内容。
 *
 * 度量条上的红线是**判定阈值**，不是参考值。它让「0.25 为什么不够」
 * 变成看图就懂的事，而不是需要记住 0.35 这个数字。
 */
import { Metric } from '../common/Metric'
import type {
  AuditView, GraphDeltaView, ProvenanceNode, ReasoningNode, Vocabulary,
} from '../../types/protocol'

function tierCls(tier: string | null): string {
  if (!tier) return 'none'
  return 't-' + tier.slice(0, 2).toLowerCase()
}

interface Props {
  vocab: Vocabulary
  delta: GraphDeltaView | null
  selectedNode: ReasoningNode | null
  selectedNodeEvents: readonly ProvenanceNode[]
  selectedEvent: ProvenanceNode | null
  audit: AuditView
  onPickEvent: (eventId: string) => void
  onPickNode: (nodeId: string) => void
}

export function StepPanel({
  vocab, delta, selectedNode, selectedNodeEvents, selectedEvent, audit,
  onPickEvent, onPickNode,
}: Props) {
  if (selectedNode) {
    return <NodeDetail node={selectedNode} audit={audit} vocab={vocab}
                       agentEvents={selectedNodeEvents}
                       onPickEvent={onPickEvent} />
  }
  if (selectedEvent) {
    return <EventDetail event={selectedEvent} onPickNode={onPickNode} />
  }
  return <DeltaDetail delta={delta} onPickNode={onPickNode} />
}

// ------------------------------------------------------------------ 节点
function NodeDetail({
  node, audit, vocab, agentEvents, onPickEvent,
}: {
  node: ReasoningNode; audit: AuditView; vocab: Vocabulary
  agentEvents: readonly ProvenanceNode[]
  onPickEvent: (e: string) => void
}) {
  const dec = audit.tiers.find(t => t.nodeId === node.nodeId)
  const cls = tierCls(node.tier)

  return (
    <>
      <div className="sec-title">
        推理节点 <span className="mono">{node.id}</span>
        <span className={`badge ${cls}`} style={{ marginLeft: 8 }}>
          {node.tierLabel ?? '未分级'}
        </span>
      </div>

      <div className="note">{node.label}</div>
      {/* 英文原文一并给出。译文是展示层的东西，可能被润色；原文是推理管线
          真正写下的那句话 —— 审计要核对的是它。 */}
      {node.labelEn && node.labelEn !== node.label
        ? <div className="note orig">原文：{node.labelEn}</div>
        : null}

      {node.tierDesc ? <div className="note info">{node.tierDesc}</div> : null}

      <div className="sec-title">
        智能体分析与操作（{agentEvents.length}）
      </div>
      {agentEvents.length ? (
        <div className="agent-trace-list">
          {agentEvents.map(event => (
            <article className="agent-trace-card" key={event.id}>
              <div className="agent-trace-head">
                <span>{event.actorIcon ?? '🧠'}</span>
                <b>{event.actorLabel ?? event.actor ?? '智能体'}</b>
                {event.actorRoleLabel
                  ? <span className="agent-trace-role">{event.actorRoleLabel}</span>
                  : null}
                <span className="agent-trace-kind">{event.kindLabel}</span>
              </div>
              <div className="agent-trace-title">
                {event.label}
                {event.step !== null ? ` · 轮次 ${event.step}` : ''}
              </div>
              {event.io.input ? (
                <div className="agent-trace-body">
                  <b>操作：</b>{event.io.input}{event.io.inputTruncated ? ' …' : ''}
                </div>
              ) : null}
              {event.io.output ? (
                <div className="agent-trace-body">
                  <b>分析 / 结果：</b>{event.io.output}
                  {event.io.outputTruncated ? ' …' : ''}
                </div>
              ) : null}
              <span className="chip link mono"
                    onClick={() => onPickEvent(event.id)}>
                查看事件 {event.id}
              </span>
            </article>
          ))}
        </div>
      ) : (
        <div className="empty">当前步骤尚无与该节点关联的智能体操作记录</div>
      )}

      {node.risk.unsourced ? (
        <div className="note warn">
          <b>无来源断言。</b>该陈述既不能绑定到病例输入或检索记录，
          也不属于合法的推导来源，却参与了支持/反驳路径。
          下方「为什么是这一级」给出了逐项度量。
        </div>
      ) : null}

      <div className="sec-title">为什么是这一级</div>
      {dec ? (
        <>
          <div className="kv">
            <dt>推导来源</dt>
            <dd>{dec.derivationLabel ?? '—'}
              {dec.derivationKindLabel
                ? <span style={{ color: 'var(--text-3)' }}>
                    （{dec.derivationKindLabel}）</span>
                : null}</dd>
          </div>
          <Metric label="标记覆盖率" value={dec.tokenCoverage}
                  threshold={dec.thresholds.directMinCoverage} />
          <Metric label="字符相似度" value={dec.charSimilarity}
                  threshold={dec.thresholds.softMinCharSimilarity} />
          <Metric label="最长公共片段" value={dec.bestSpanRatio} />
          <Metric label="输入覆盖率" value={dec.inputCoverage}
                  threshold={dec.thresholds.inputInferenceMinCoverage} />
          <Metric label="分析覆盖率" value={dec.analysisCoverage}
                  threshold={dec.thresholds.expertSynthesisMinCoverage} />
          <div className="kv" style={{ marginTop: 6 }}>
            <dt>最接近记录</dt>
            <dd className="mono">{dec.bestRecordId ?? '—'}</dd>
            <dt>参与推理</dt>
            <dd>{dec.usedInReasoning ? vocab.ui.reached : vocab.ui.unreached}</dd>
            <dt>外部有据</dt>
            <dd>{dec.externallyGrounded ? '是' : '否'}</dd>
            <dt>血脉完整</dt>
            <dd>{dec.lineageComplete ? '是' : '否'}</dd>
          </div>
          {dec.usage
            ? <div className="note">{dec.usage}</div>
            : null}
        </>
      ) : <div className="empty">审计未提供该节点的判定</div>}

      <div className="sec-title">来源链</div>
      <div className="kv">
        <dt>创建于事件</dt>
        <dd>{node.createdByEvent
          ? <span className="chip link mono"
                  onClick={() => onPickEvent(node.createdByEvent!)}>
              {node.createdByEvent}</span>
          : '—'}</dd>
        <dt>来源类别</dt>
        <dd>{node.sourceClassLabel ?? '—'}</dd>
        <dt>语义类型</dt>
        <dd>{node.semanticKindLabel ?? '—'}</dd>
      </div>
      {dec?.bestAnalysisEvent ? (
        <div className="kv">
          <dt>内容来自</dt>
          <dd><span className="chip link mono"
                    onClick={() => onPickEvent(dec.bestAnalysisEvent!)}>
              {dec.bestAnalysisEvent}</span></dd>
        </div>
      ) : null}

      {node.risk.anomalyRoots.length > 0 ? (
        <div className="note warn">
          该节点是 {node.risk.anomalyRoots.length} 条异常的根因：
          {node.risk.anomalyRoots.map(a =>
            <span key={a} className="chip mono" style={{ marginLeft: 4 }}>{a}</span>)}
        </div>
      ) : null}
    </>
  )
}

// ------------------------------------------------------------------ 事件
function EventDetail({
  event, onPickNode,
}: { event: ProvenanceNode; onPickNode: (n: string) => void }) {
  return (
    <>
      <div className="sec-title">
        执行事件 <span className="mono">{event.id}</span>
      </div>

      <div className="kv">
        <dt>类型</dt><dd>{event.kindLabel}</dd>
        <dt>智能体</dt>
        <dd>{event.actor ?? '—'}
          {event.actorRoleLabel
            ? <span style={{ color: 'var(--text-3)' }}>（{event.actorRoleLabel}）</span>
            : null}</dd>
        <dt>规划轮次</dt><dd className="num">{event.step ?? '—'}</dd>
        <dt>状态</dt><dd>{event.statusLabel ?? '—'}</dd>
        <dt>模型</dt><dd className="mono">{event.model ?? '—'}</dd>
        <dt>时间</dt><dd className="mono">{event.timestamp ?? '—'}</dd>
      </div>

      <div className="sec-title">输入 / 输出</div>
      <div className="kv">
        <dt>输入</dt>
        <dd style={{ color: 'var(--text-2)' }}>
          {event.io.input || '—'}
          {event.io.inputTruncated ? ' …' : ''}</dd>
        <dt>输出</dt>
        <dd style={{ color: 'var(--text-2)' }}>
          {event.io.output || '—'}
          {event.io.outputTruncated ? ' …' : ''}</dd>
      </div>
      {event.io.outputEn && event.io.outputEn !== event.io.output
        ? <div className="note orig">原文输出：{event.io.outputEn.slice(0, 500)}</div>
        : null}

      <div className="sec-title">哈希链</div>
      <div className="kv">
        <dt>event_hash</dt>
        <dd className="mono">{event.eventHash?.slice(0, 24) ?? '—'}…</dd>
        <dt>prev_hash</dt>
        <dd className="mono">{event.prevHash?.slice(0, 24) ?? '—'}…</dd>
      </div>

      {event.causalNodeRefs.length > 0 ? (
        <>
          <div className="sec-title">
            跨图引用（{event.causalNodeRefs.length} 个推理节点）
          </div>
          <div>
            {event.causalNodeRefs.map(r => (
              <span key={r} className="chip link mono"
                    onClick={() => onPickNode(r.startsWith('gos:') ? r : `gos:${r}`)}>
                {r}
              </span>
            ))}
          </div>
        </>
      ) : (
        <div className="note">
          本事件未直接关联推理节点。它属于过程骨架（规划、调用、检索），
          内容通过后续的分析与图更新进入推理图。
        </div>
      )}

      {event.anomalyRoot ? (
        <div className="note warn">
          <b>可观测故障起点。</b>审计把该事件认定为一条异常的可观测起点。
        </div>
      ) : null}
    </>
  )
}

// ------------------------------------------------------------------ 增量
function DeltaDetail({
  delta, onPickNode,
}: { delta: GraphDeltaView | null; onPickNode: (n: string) => void }) {
  if (!delta) return <div className="empty">尚无可回放的步骤</div>

  return (
    <>
      <div className="sec-title">
        第 {((delta.step ?? 0) + 1)} 步 · {delta.eventKindLabel ?? '—'}
        {delta.isKeyStep
          ? <span className="badge t-p3" style={{ marginLeft: 8 }}>关键步</span>
          : null}
      </div>

      {/* 这里**刻意不再渲染 `delta.narrative`。** 它由左栏的 `Narrative`
          负责，而并入左栏之后两者会挨在一起 —— 同一段话连出现两次，
          读者会以为是两条不同的说明。叙事只留一处。 */}

      {delta.keyStepReason
        ? <div className="note warn">{delta.keyStepReason}</div>
        : null}

      <div className="kv">
        <dt>增量 id</dt><dd className="mono">{delta.deltaId}</dd>
        <dt>事件</dt><dd className="mono">{delta.eventId}</dd>
        <dt>规划轮次</dt><dd className="num">{delta.agentStep ?? '—'}</dd>
        <dt>哈希</dt>
        <dd className="mono">{delta.beforeHashShort} → {delta.afterHashShort}</dd>
        <dt>重放</dt>
        <dd>{delta.replayOk === true
          ? <span className="badge ok">校验通过</span>
          : delta.replayOk === false
            ? <span className="badge warn">未通过</span>
            : '—'}</dd>
      </div>

      {delta.addedNodes.length > 0 ? (
        <>
          <div className="sec-title">新增节点</div>
          <div>{delta.addedNodes.map(n =>
            <span key={n} className="chip link mono"
                  onClick={() => onPickNode(n)}>{n}</span>)}</div>
        </>
      ) : null}

      {delta.updatedNodes.length > 0 ? (
        <>
          <div className="sec-title">更新节点</div>
          <div>{delta.updatedNodes.map(n =>
            <span key={n} className="chip link mono"
                  onClick={() => onPickNode(n)}>{n}</span>)}</div>
        </>
      ) : null}

      {delta.addedEdges.length > 0 ? (
        <>
          <div className="sec-title">新增关系（{delta.addedEdges.length}）</div>
          <div>
            {delta.addedEdges.slice(0, 24).map((e, i) => (
              <span key={i} className="chip mono">
                {e.source.replace('gos:', '')} {e.relationLabel}
                {' '}{e.target.replace('gos:', '')}
              </span>
            ))}
            {delta.addedEdges.length > 24
              ? <span className="chip">…共 {delta.addedEdges.length} 条</span>
              : null}
          </div>
        </>
      ) : null}
    </>
  )
}
