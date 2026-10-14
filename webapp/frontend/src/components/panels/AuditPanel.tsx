/**
 * 审计信息面板。
 *
 * 三块内容按「先看结论、再看证据」排序：
 *   1. 两个校验徽章（哈希链、增量重放）—— 一行回答「这份轨迹可信吗」
 *   2. 证据等级分布 —— 回答「这次推理有多少是无来源的」
 *   3. 异常卡 —— 每条可点，点了会在两张图上同时高亮它的影响链
 *
 * 异常卡上**同时**显示「可观测故障起点」与「潜在注入根因」两个概念。
 * 它们不是同义词：前者是审计在轨迹上能看到的最早异常事件，后者是注入
 * 实验的已知答案。自然发生的异常（例如本病例）只有前者。把它们混为
 * 一谈，就等于把审计的能力说得比实际更强。
 */
import type { AnomalyPath, AuditView, Vocabulary } from '../../types/protocol'

interface Props {
  audit: AuditView
  vocab: Vocabulary
  focusedAnomalyId: string | null
  onToggleAnomaly: (id: string) => void
}

export function AuditPanel({ audit, vocab, focusedAnomalyId, onToggleAnomaly }: Props) {
  const { integrity, replay, grounding, tierCounts, anomalies } = audit

  // **不适用就到此为止。** 手写展示轨迹没有审计产物，下面那些块会渲染成
  // 「P0 0 / P1 0 / …」和「未检出异常」—— 那是两句**断言**（「审计判过，
  // 没有无来源断言、没有异常」），而这份数据从来没被审计过。显示成空的
  // 比显示成「全 0」诚实，但还不够：读者会以为审计跑过且通过了。
  if (!audit.applicable) {
    return (
      <>
        <div className="sec-title">审计信息</div>
        <div className="empty" style={{ lineHeight: 1.7 }}>
          {audit.notApplicableReason || '本页无审计产物'}
          <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text-3)' }}>
            这一页的因果图与置信度是<b>编排</b>的展示数据。来源类、检索匹配度、
            P0–P3 等级、完整性哈希、重放校验、模型调用账本都不存在 ——
            它们没有被算出来，也没有被跳过。
          </div>
        </div>
      </>
    )
  }

  return (
    <>
      <div className="sec-title">校验</div>
      <div style={{ marginBottom: 4 }}>
        <span className={`badge ${integrity.ok ? 'ok' : 'warn'}`}>
          {integrity.ok ? vocab.ui.integrity_ok : vocab.ui.integrity_bad}
        </span>
        <span style={{ fontSize: 11, color: 'var(--text-3)', marginLeft: 8 }}>
          {integrity.nEvents} 事件 · {integrity.nIssues} 处问题
        </span>
      </div>
      <div>
        <span className={`badge ${!replay.applicable ? 'none'
            : replay.allOk ? 'ok' : 'warn'}`}>
          {!replay.applicable
            ? '重放待生成'
            : replay.allOk ? vocab.ui.replay_ok : vocab.ui.replay_bad}
        </span>
        <span style={{ fontSize: 11, color: 'var(--text-3)', marginLeft: 8 }}>
          {replay.nDeltas} 步增量
        </span>
      </div>

      <div className="sec-title">证据等级分布</div>
      <div>
        {vocab.tierOrder.map(t => (
          <span key={t} className="chip">
            {vocab.tierShort[t] ?? t}
            {' '}
            {tierCounts[t] ?? 0}
          </span>
        ))}
      </div>

      <div className="sec-title">
        来源绑定
      </div>
      <div className="kv">
        <dt>事实性证据</dt>
        <dd className="num">{grounding.evidenceTotal} 条</dd>
        <dt>已绑定来源</dt>
        <dd className="num">
          {grounding.evidenceSourceBound} 条
          {grounding.evidenceSourceBoundRate !== null
            ? `（${(grounding.evidenceSourceBoundRate * 100).toFixed(1)}%）`
            : ''}
        </dd>
        <dt>检索记录</dt>
        <dd className="num">{grounding.nRetrievalRecords} 条</dd>
      </div>

      <div className="sec-title">异常（{anomalies.length}）</div>
      {anomalies.length === 0
        ? <div className="empty">未检出异常</div>
        : anomalies.map((a: AnomalyPath) => {
          const on = focusedAnomalyId === a.anomalyId
          return (
            <div key={a.anomalyId}
                 className={`anomaly-card ${on ? 'on' : ''}`}
                 onClick={() => onToggleAnomaly(a.anomalyId)}>
              <div className="anomaly-top">
                <span className="anomaly-title">{a.typeLabel}</span>
                <span className="badge warn">{a.severity ?? '—'}</span>
                {a.reachesReport
                  ? <span className="badge warn">抵达报告</span>
                  : <span className="badge none">未抵达报告</span>}
                {on ? <span className="badge" style={{ marginLeft: 'auto' }}>
                        已聚焦
                      </span> : null}
              </div>
              <div className="anomaly-body">
                <div>{a.detectorRuleLabel ?? '—'}</div>
                <div style={{ marginTop: 3 }}>
                  <span style={{ color: 'var(--text-3)' }}>{a.latentRootNote}：</span>
                  <span className="mono">{a.rootEventId ?? '—'}</span>
                  {a.rootNodeId
                    ? <span className="mono"> / {a.rootNodeId}</span>
                    : null}
                </div>
                <div style={{ marginTop: 3, color: 'var(--text-3)' }}>
                  影响 {a.affectedEvents.length} 个事件 ·
                  {' '}{a.affectedNodes.length} 个节点 ·
                  {' '}{a.affectedHypotheses.length} 个假设
                </div>
                <div style={{ marginTop: 3, fontSize: 10.5, color: 'var(--text-3)' }}>
                  {a.typeNote}
                </div>
              </div>
            </div>
          )
        })}
    </>
  )
}
