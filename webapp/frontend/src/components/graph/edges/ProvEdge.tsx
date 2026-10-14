/**
 * 执行溯源图边。
 *
 * **几何与推理图完全一致**（直线、端点裁到图形边缘外 4px、无箭头），
 * 见 `edges/geometry.ts` —— 两张图并排放在一起，线的画法必须是同一套，
 * 否则「左边是推理、右边是执行」这件事会先被读成「两种不同的图」。
 *
 * 与推理图的差别只在**配色语义**：这里**颜色统一、线型统一**，唯一的区分是
 * 「是否被高亮」。原因是执行图的边表达的是结构（谁调用了谁、谁依赖谁），
 * 不是三种逻辑动作；给它们上不同颜色会暗示一种不存在的分类，也会与推理图
 * 的「绿=支持 红=反驳」争夺同一套色彩语义。
 *
 * 参考项目里唯一有颜色的非逻辑边是 kb 边（紫色虚线 `#8b5cf6`，`6 4`）。
 * 我们没有 kb 关系，所以这里不需要虚线通道。
 */
import { BaseEdge, useInternalNode, type Edge, type EdgeProps } from '@xyflow/react'

import {
  linePath, shapeCenterOffset, trimLine, type Shape,
} from './geometry'

/** 节点外接盒的宽度。`measured` 是真测量值，`width` 是我们在建节点时就给的
 *  `initialWidth`（见 nodes/GraphNode）—— 两者总有其一，不必等测量。 */
function boxWidth(n: { measured?: { width?: number }; width?: number }): number {
  return n.measured?.width ?? n.width ?? 0
}

export interface ProvEdgeData extends Record<string, unknown> {
  relation: string
  relationLabel: string
  lit: boolean
  dimmed: boolean
  /** 两端图形的形状，用来把线段裁到边缘。 */
  sourceShape?: Shape
  targetShape?: Shape
}

export type ProvFlowEdge = Edge<ProvEdgeData, 'prov'>

/** 参考项目 `.edge` 的线宽与不透明度，两张图共用。 */
const BASE_W = 2.2
const LIT_W = 2.8

/** 各种关系只是同一族灰的一点点偏移，读起来仍然是「一种线」。 */
function strokeOf(relation: string, lit: boolean): string {
  if (lit) return '#f5b301'
  switch (relation) {
    case 'read': return '#9b8fc0'
    case 'invoke': return '#8f9bad'
    default: return '#c2c9d2'
  }
}

export function ProvEdge({
  id, source, target, data, selected,
}: EdgeProps<ProvFlowEdge>) {
  const lit = Boolean(data?.lit) || Boolean(selected)
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
    <BaseEdge
      id={id}
      path={linePath(t)}
      className={['pv-edge', lit ? 'lit' : '',
                  data?.dimmed ? 'dimmed' : ''].filter(Boolean).join(' ')}
      style={{
        stroke: strokeOf(data?.relation ?? '', lit),
        strokeWidth: lit ? LIT_W : BASE_W,
      }}
    />
  )
}
