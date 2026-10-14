/**
 * 推理关系边。**几何与配色照搬参考项目** `gos_repo/viz/story_template.html`。
 *
 * ```js
 * const EDGE_COLORS = { support: "#22c55e", refute: "#ef4444", refines: "#94a3b8", kb: "#8b5cf6" };
 * const EDGE_W      = { support: 2.4,    refute: 2.6,    refines: 1.8,    kb: 2.2 };
 * ```
 * ```css
 * .edge { stroke-width: 2.2; opacity: .85; }
 * ```
 *
 * 三点与先前不同，都是「看起来不对」的直接原因：
 *
 *   1. **直线**（`linePath`），不是 `getSmoothStepPath` 的直角折线。
 *   2. **端点裁到图形边缘 + 4px**（`trimLine`），线不再钻进圆里、也不从标签
 *      底下出发。
 *   3. **没有箭头。** 参考项目一条 marker 都没有 —— 它靠颜色区分关系。
 *      先前我们用「实线+箭头 / 虚线+平头 / 点线+空心箭头」三套线型编码三种
 *      逻辑动作，理由是「颜色在投影偏色、转黑白、色觉差异下会失效」。
 *      那个理由本身没错，但用户明确要求与参考项目一致，所以这里以颜色为准；
 *      线型的可用性是**主动放弃**的，不是遗漏。
 *      （真要找回线型通道，位置在 `styleOf` 的 `dash` 上，一条线的事。）
 *
 * 标签只在被高亮时显示。常驻标签在 33 条边的图上会互相压叠，结果是每条都
 * 读不清 —— 边数少的图可以常驻，这张不行。
 */
import {
  BaseEdge, EdgeLabelRenderer, useInternalNode, type Edge, type EdgeProps,
} from '@xyflow/react'

import {
  linePath, shapeCenterOffset, trimLine, type Shape,
} from './geometry'

/** 节点外接盒的宽度。`measured` 是真测量值，`width` 是我们在建节点时就给的
 *  `initialWidth`（见 nodes/GraphNode）—— 两者总有其一，不必等测量。 */
function boxWidth(n: { measured?: { width?: number }; width?: number }): number {
  return n.measured?.width ?? n.width ?? 0
}

export interface RelationEdgeData extends Record<string, unknown> {
  relation: string
  relationLabel: string
  lit: boolean
  dimmed: boolean
  fresh?: boolean
  /** 来源是知识节点（`Knowledge`）。见 `styleOf` 的说明。 */
  kb?: boolean
  /** 两端图形的形状（圆心/菱形大小），用来把线段裁到边缘。 */
  sourceShape?: Shape
  targetShape?: Shape
}

export type RelationFlowEdge = Edge<RelationEdgeData, 'relation'>

/** 参考项目的配色与线宽，逐个抄下来。 */
const EDGE_COLORS: Record<string, string> = {
  support: '#22c55e',
  refute: '#ef4444',
  refines: '#94a3b8',
  kb: '#8b5cf6',
}
const EDGE_W: Record<string, number> = {
  support: 2.4,
  refute: 2.6,
  refines: 1.8,
  kb: 2.2,
}

function styleOf(relation: string, kb: boolean): { stroke: string; width: number } {
  // 知识边是**关系不变、来源不同**：`KB104 --support--> H1b` 与
  // `E1 --support--> H1b` 都是 support，但一条是本次诊疗查出来的，
  // 一条是从库里取的。参考项目给它们一个独立颜色（紫），图例也单列一条
  // 「知识支持」—— 不区分的话，读者会以为支持边都来自检查。
  const key = kb ? 'kb' : relation
  return {
    stroke: EDGE_COLORS[key] ?? EDGE_COLORS.support,
    width: EDGE_W[key] ?? EDGE_W.support,
  }
}

export function RelationEdge({
  id, source, target, data, selected,
}: EdgeProps<RelationFlowEdge>) {
  const rel = data?.relation ?? 'support'
  const s = styleOf(rel, Boolean(data?.kb))
  const lit = Boolean(data?.lit) || selected
  const dimmed = Boolean(data?.dimmed)
  const fresh = Boolean(data?.fresh)

  // **不能用 React Flow 给的 sourceX/sourceY。** 它把 Handle 的锚点**按
  // `position` 吸附到节点外接盒的边**上（`getHandlePosition` 里写死的
  // `case Position.Bottom: return { x: x + width/2, y: y + height }`），
  // 完全无视 Handle 自己摆在哪 —— 所以把 Handle 挪到圆心是无效的，线依旧从
  // **标签底下**出发，甚至扎进下面那个节点的圆里。
  //
  // 改用 `useInternalNode` 现取两个节点的真实几何：绝对位置 + 外接盒宽度，
  // 于是中心 = `(x + w/2, y + 图形中心偏移)`。它订阅 store，节点一动就重算。
  const sn = useInternalNode(source)
  const tn = useInternalNode(target)
  // 还没测量出来时先不画 —— 画一条从 (0,0) 飞过来的线比不画更糟。
  if (!sn || !tn) return null
  const t = trimLine(
    sn.internals.positionAbsolute.x + boxWidth(sn) / 2,
    sn.internals.positionAbsolute.y + shapeCenterOffset(data?.sourceShape),
    tn.internals.positionAbsolute.x + boxWidth(tn) / 2,
    tn.internals.positionAbsolute.y + shapeCenterOffset(data?.targetShape),
    data?.sourceShape, data?.targetShape,
  )

  return (
    <>
      <BaseEdge
        id={id}
        path={linePath(t)}
        className={['rel-edge', lit ? 'lit' : '', dimmed ? 'dimmed' : '',
                    fresh ? 'fresh' : ''].filter(Boolean).join(' ')}
        style={{
          stroke: s.stroke,
          strokeWidth: lit ? s.width + 0.6 : s.width,
          // 新边「画出来」：参考项目是 `dasharray = len; dashoffset = len`
          // 再过渡到 0。这里用同一套数值，只是交给 CSS 动画驱动。
          ...(fresh ? { strokeDasharray: t.length, '--edge-len': `${t.length}` } : {}),
        }}
      />
      {lit && data?.relationLabel && (
        <EdgeLabelRenderer>
          <div
            className="rel-label"
            style={{
              transform: `translate(-50%,-50%) translate(${(t.x1 + t.x2) / 2}px,${(t.y1 + t.y2) / 2}px)`,
            }}>
            {data.relationLabel}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}
