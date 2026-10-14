/**
 * 悬停浮层：鼠标停在节点上时，跟着光标显示这个节点的完整信息。
 *
 * 形状对齐 `医疗溯因智能体/gos_repo/viz/story_template.html` 的 `#tooltip`：
 * 深色底、跟随光标 `+14/+14`、`max-width: 340px`、`pointer-events: none`。
 *
 * **为什么不把浮层挂在节点组件里。** React Flow 给每个节点套了一层带
 * `transform` 的容器，选中/高亮还会再加 `box-shadow` 与描边；浮层挂在里面
 * 就会被节点的层级和 `.graph-pane` 的 `overflow: hidden` 一起裁掉 —— 靠近
 * 画布边缘的节点，浮层会缺一半。挂在画布层、用鼠标位置定位就没有这个问题。
 *
 * **两处对参考项目的修正：**
 *   1. 参考项目的浮层只做了横向夹取（`min(x+14, 宽-350)`），光标靠下时
 *      浮层直接溢出卡片底部。这里横竖都夹。
 *   2. 参考项目的浮层高度是内容撑开的，夹取时无从知道它会多高 —— 所以
 *      这里用 `maxHeight` + 纵向夹取，长内容内部滚动。
 */
import { useEffect, useRef, useState } from 'react'

import type { GraphFlowNode } from './nodes/GraphNode'

const OFFSET = 14
/** 与 `.node-tip` 的 max-width + padding 对齐；横向夹取要用到它。 */
const TIP_W = 340

interface Props {
  /** 当前悬停的节点 id（store 里的 `hovered`）。 */
  hovered: string | null
  nodes: readonly GraphFlowNode[]
  /** 光标在画布内的位置。由画布层统一跟踪。 */
  at: { x: number; y: number } | null
  /** 画布尺寸，用于夹取。 */
  pane: { w: number; h: number }
}

export function NodeTip({ hovered, nodes, at, pane }: Props) {
  const ref = useRef<HTMLDivElement | null>(null)
  // 浮层自身高度只有渲染完才知道，而夹取要用到它 —— 先渲染再量再修正。
  // 用 state 而不是直接在 DOM 上写样式：后者在 React 重渲染时会被覆盖。
  const [h, setH] = useState(0)

  const node = hovered ? nodes.find(n => n.id === hovered) : null
  const tip = node?.data.tip ?? null

  useEffect(() => {
    if (!tip) { setH(0); return }
    const el = ref.current
    if (el) setH(el.getBoundingClientRect().height)
  }, [tip, hovered])

  if (!tip || !at) return null

  const left = Math.max(4, Math.min(at.x + OFFSET, pane.w - TIP_W - 10))
  const top = Math.max(4, Math.min(at.y + OFFSET, Math.max(4, pane.h - h - 10)))

  return (
    <div ref={ref} className="node-tip" style={{ left, top }}>
      <div className="nt-head">
        <b>{tip.head}</b>
        {tip.badge ? (
          <span className={`nt-badge ${tip.badgeTone ?? ''}`}>{tip.badge}</span>
        ) : null}
      </div>

      <div className="nt-label">{node?.data.label}</div>

      {tip.rows.map((r, i) => (
        <div key={i} className="nt-row">
          <span className="nt-k">{r.k}</span>
          <span className={`nt-v ${r.tone ?? ''}`}>{r.v}</span>
        </div>
      ))}

      {tip.note ? <div className="nt-note">{tip.note}</div> : null}
    </div>
  )
}
