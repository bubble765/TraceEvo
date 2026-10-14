/**
 * 结算面板：结果验证与经验门控的并列。
 *
 * 这一块是论文的落点，所以它的排版承担论证：
 *
 *   两栏并排   —— 「结果验证正确」与「可定位失败经验」同时可见。
 *                 上下排列会让人先读到一个再读另一个，从而得出片面结论。
 *   结论句     —— 一句话把两者的矛盾说出来，不让读者自己去合成。
 *   两张卡片并置 —— 左边「为审计永久保存的完整轨迹」，右边「可长期保存的
 *                 抽象内容」。这个对照本身就是隐私边界的可视化：不是承诺
 *                 「我们不会存病历」，而是让人看到存下来的到底是什么。
 *
 * `forbidden` 的检查结果**逐项列出已比对与未比对的内容**。参考答案在
 * 本部署中不可读，「不含参考答案」因此无法验证 —— 面板必须这么说，
 * 而不是打一个它验证不了的勾。
 *
 * 手写展示轨迹（`applicable === false`）走一个提前返回：那里没有判题也
 * 没有门控，渲染成「待判定」会被读成「还在等结果」。同一个诚实原则，只是
 * 这一页缺的不是「能不能验证」而是「有没有东西可验」。
 */
import type { ExperienceView, Vocabulary } from '../../types/protocol'

interface Props {
  experience: ExperienceView
  vocab: Vocabulary
  onPickEvent?: (eventId: string) => void
}

export function ExperiencePanel({ experience: e, vocab }: Props) {
  // **没有判题与门控 ≠ 还没判。** 恒为空时会渲染成「待判定 / 待判定」，
  // 读起来是「这次运行还在等结果」；而展示轨迹的真相是**根本没有结果会来**。
  // 这个区别正是这一页存在的理由（结果正确 ≠ 过程可信），所以不能让它
  // 退化成一个「空面板」—— 要说明缺的是哪两样东西。
  if (!e.applicable) {
    return (
      <>
        <div className="sec-title">结果与经验</div>
        <div className="empty" style={{ lineHeight: 1.7 }}>
          {e.notApplicableReason || '本页无判题与门控产物'}
        </div>
        <div className="note">
          这一页本来就该显示<b>结果验证</b>（参考答案比对）与<b>经验门控</b>
          （能不能进经验库）的并列。展示轨迹两者都没有，所以这里不显示
          「待判定」—— 那会被读成「还在等结果」。
        </div>
      </>
    )
  }

  const outcomeGood = e.outcome.verdict === 'verified_success'
  const gateGood = e.gate.eligibility === 'eligible'

  return (
    <>
      <div className="verdict-row">
        <div className={`verdict ${outcomeGood ? 'good' : 'bad'}`}>
          <div className="verdict-label">结果验证</div>
          <div className="verdict-value">
            {e.outcome.verdictLabel ?? '待判定'}
          </div>
          <div style={{ fontSize: 10.5, color: 'var(--text-3)', marginTop: 3 }}>
            {e.outcome.judgeScores.length > 0
              ? `三次独立评判 ${e.outcome.judgeScores.join('|')}`
                + (e.outcome.unanimous ? ' · 一致' : ' · 不一致')
              : '—'}
          </div>
          {e.outcome.prediction ? (
            <div style={{ fontSize: 10.5, color: 'var(--text-3)', marginTop: 2 }}>
              系统结论：{e.outcome.prediction}
            </div>
          ) : null}
        </div>

        <div className={`verdict ${gateGood ? 'good' : 'bad'}`}>
          <div className="verdict-label">经验门控</div>
          <div className="verdict-value">
            {e.gate.eligibilityLabel ?? '待判定'}
          </div>
          <div style={{ fontSize: 10.5, color: 'var(--text-3)', marginTop: 3 }}>
            异常 {e.gate.nAnomalies} 条 · 参与推理的 P3 {e.gate.nUsedP3} 条
          </div>
        </div>
      </div>

      <div className={`headline ${gateGood && outcomeGood ? 'ok' : ''}`}>
        {e.headline}
      </div>

      {e.gate.eligibilityDesc
        ? <div className="note">{e.gate.eligibilityDesc}</div>
        : null}

      {e.gate.reasons.length > 0 ? (
        <>
          <div className="sec-title">门控依据</div>
          <div>
            {e.gate.reasons.map((r, i) => (
              <div key={i} className="chip" style={{ display: 'block', marginBottom: 3 }}>
                {r}
              </div>
            ))}
          </div>
        </>
      ) : null}

      <div className="sec-title">可长期保存的抽象内容</div>
      {e.card ? (
        <>
          <div className="kv">
            <dt>经验 id</dt><dd className="mono">{e.card.memoryId ?? '—'}</dd>
            <dt>分区</dt><dd>{e.card.partitionLabel}</dd>
            <dt>类型</dt><dd>{e.card.title}</dd>
            <dt>失败模式</dt>
            <dd>
              <span className="badge warn">
                {e.card.failureModeLabel ?? e.card.failureMode ?? '—'}
              </span>
            </dd>
            <dt>规避规则</dt><dd>{e.card.avoidanceRule ?? '—'}</dd>
            <dt>建议检查</dt>
            <dd>{e.card.suggestedChecks.length > 0
              ? e.card.suggestedChecks.join('；') : '—'}</dd>
            <dt>来源轨迹</dt><dd className="mono">{e.card.sourceTraceId ?? '—'}</dd>
            <dt>审计摘要</dt>
            <dd className="mono">{e.card.auditSummaryHash?.slice(0, 16) ?? '—'}…</dd>
            <dt>抽取器</dt><dd className="mono">{e.card.extractorVersion ?? '—'}</dd>
          </div>
          {e.card.rendered
            ? <div className="rendered">{e.card.rendered}</div>
            : null}
        </>
      ) : <div className="empty">该病例未产生经验卡</div>}

      <div className="sec-title">隐私边界检查</div>
      <div className="note warn">
        {e.forbidden.note}
      </div>
      <div className="kv">
        <dt>患者原文</dt>
        <dd>{e.forbidden.containsPatientText
          ? <span className="badge warn">命中</span>
          : <span className="badge ok">未出现</span>}</dd>
        <dt>系统结论</dt>
        <dd>{e.forbidden.containsReferenceDiagnosis
          ? <span className="badge warn">命中</span>
          : <span className="badge ok">未出现</span>}</dd>
        <dt>参考答案</dt>
        <dd><span className="badge none">本部署不可读</span></dd>
      </div>

      <div className="note">
        <b>{vocab.ui.full_trace_audit_only}</b>：完整轨迹与哈希链保存在
        执行档案中，可通过 <span className="mono">/api/cases/&lt;id&gt;/raw</span>
        {' '}读取；长期经验只保留上面这张卡片里的抽象内容。
      </div>
    </>
  )
}
