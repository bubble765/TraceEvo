/**
 * 度量条：一个数值 + 它要比较的阈值。
 *
 * **阈值必须画出来。** 只显示 `0.25` 无法回答「为什么这算低」；把判定
 * 阈值作为一条竖线画在同一个条上，0.25 与 0.35 的关系就是自明的。
 * 这正是审计面板要回答的问题 —— 「现有证据为什么不足以支撑这句话」
 * 的答案不在数值里，在数值与阈值的关系里。
 *
 * 阈值线可以超出当前量程（数值很小时），此时被夹到边缘并改变颜色，
 * 表示「阈值在更远处」而不是「阈值就在 100%」。
 */
interface Props {
  label: string
  value: number | null | undefined
  threshold?: number | null
  /** 阈值是「必须超过」还是「必须低于」。 */
  mode?: 'min' | 'max'
  format?: (v: number) => string
}

export function Metric({
  label, value, threshold, mode = 'min', format,
}: Props) {
  if (value === null || value === undefined) {
    return (
      <div className="metric">
        <span className="metric-label">{label}</span>
        <span className="metric-num">—</span>
      </div>
    )
  }

  const fmt = format ?? ((v: number) => v.toFixed(3))
  const pct = Math.max(0, Math.min(1, value)) * 100

  let cls = ''
  let threshPct: number | null = null
  let threshOffScale = false
  if (threshold !== null && threshold !== undefined) {
    const pass = mode === 'min' ? value >= threshold : value <= threshold
    cls = pass ? 'good' : 'bad'
    if (threshold <= 1 && threshold >= 0) {
      threshPct = threshold * 100
    } else {
      threshOffScale = true
    }
  }

  return (
    <div className="metric">
      <span className="metric-label">{label}</span>
      <div className="metric-bar">
        <div className={`metric-fill ${cls}`} style={{ width: `${pct}%` }} />
        {threshPct !== null && (
          <div
            className="metric-thresh"
            style={{
              left: `${threshPct}%`,
              background: threshOffScale ? 'var(--tier-p3)' : undefined,
            }}
          />
        )}
      </div>
      <span className="metric-num num">{fmt(value)}</span>
    </div>
  )
}
