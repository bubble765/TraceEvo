/**
 * 模型调用账本。
 *
 * 这一页要回答的是图回答不了的问题：**哪些调用发生过却没有进入正式推理
 * 状态**。图只画被采纳的东西，所以只看图，「过程」是一份清理过的过程。
 *
 * 「未提交」在这里被显式标注为**中性状态**而不是问题。检索阶段的调用
 * 本来就不提交到任何图元素（这是设计如此），把 9 条这样的调用涂成警告色
 * 会把正常流程显示成一片异常。所以它用暖黄（提示）而不是红（异常），
 * 并在表头上写明原因。
 */
import type { LedgerView, Vocabulary } from '../../types/protocol'

interface Props {
  ledger: LedgerView
  vocab: Vocabulary
  onPickEvent?: (eventId: string) => void
}

export function LedgerPanel({ ledger, vocab, onPickEvent }: Props) {
  // **没有账本 ≠ 账本是空的。** 恒为空时会渲染成「共 0 次调用」，读起来是
  // 「这次运行没调用过模型」；而展示轨迹的真相是「根本没有账本可读」。
  if (!ledger.applicable) {
    return (
      <>
        <div className="sec-title">模型调用账本</div>
        <div className="empty" style={{ lineHeight: 1.7 }}>
          {ledger.notApplicableReason || '本页无调用账本'}
        </div>
      </>
    )
  }

  return (
    <>
      <div className="sec-title">
        共 {ledger.nCalls} 次调用
        <span style={{ fontWeight: 400, color: 'var(--text-3)', marginLeft: 8 }}>
          已接受 {ledger.nAccepted} · 已丢弃 {ledger.nDiscarded} ·
          未提交 {ledger.nUncommitted}
        </span>
      </div>

      {ledger.nUncommitted > 0 ? (
        <div className="note">
          未提交 = 调用被接受，但没有图元素承接它的输出。
          <b>这不等于错误</b>：检索与杂项阶段本就如此。它是为了让
          「图上看不到的过程」在这本账里仍然可查。
        </div>
      ) : null}

      <table className="ledger">
        <thead>
          <tr>
            <th>调用</th>
            <th>阶段</th>
            <th>角色</th>
            <th>绑定</th>
            <th>提交到</th>
            <th>承接事件</th>
          </tr>
        </thead>
        <tbody>
          {ledger.rows.map(r => (
            <tr key={r.callId} className={r.isUncommitted ? 'uncommitted' : ''}>
              <td className="c-id mono">{r.callId.replace('call-case-', '')}</td>
              <td>{r.stageLabel ?? '—'}</td>
              <td>{r.actorRoleLabel ?? '—'}</td>
              <td>{r.bindingLabel ?? '—'}</td>
              <td>{r.committedToState
                ? (r.commitTargetLabel ?? '—')
                : <span style={{ color: 'var(--tier-p2)' }}>未提交</span>}</td>
              <td>
                {r.committedEventIds.length === 0
                  ? <span style={{ color: 'var(--text-3)' }}>—</span>
                  : r.committedEventIds.map(e => (
                    <span key={e} className="chip link mono"
                          onClick={() => onPickEvent?.(e)}>
                      {e.replace('evt-case-', '')}
                    </span>
                  ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {ledger.rows.length === 0
        ? <div className="empty">{vocab.ui.no_data}</div>
        : null}
    </>
  )
}
