/**
 * 边的几何 —— **照搬参考项目** `医疗溯因智能体/gos_repo/viz/story_template.html`。
 *
 * 参考项目里那条线的画法只有三行，但它决定了整张图的观感：
 *
 * ```js
 * const dx = t.x - s.x, dy = t.y - s.y;
 * const dist = Math.max(1, Math.hypot(dx, dy));
 * const ux = dx / dist, uy = dy / dist;
 * // 起点从**圆周**往外推 4px，终点推到**圆周**再退 4px
 * x1 = s.x + ux * (R[s] + 4)   y1 = s.y + uy * (R[s] + 4)
 * x2 = t.x - ux * (R[t] + 4)   y2 = t.y - uy * (R[t] + 4)
 * ```
 *
 * 三个要点，缺一个就不是那个观感：
 *
 *   1. **直线**，不是折线也不是贝塞尔。两节点中心一连，方向就是它。
 *   2. **端点裁到图形边缘**，且**从中心连线算方向**。裁的目的是让线不钻进
 *      圆里；用中心连线而不是端点连线，是因为端点会随裁剪移动 —— 用端点
 *      连线算方向会自相矛盾（移动端点 → 方向变 → 端点再移动）。
 *   3. **留 4px 缝**。线贴着圆边会看起来粘在一起，4px 让它「接上但不相交」。
 *
 * 我们先前用的是 `getSmoothStepPath`（直角折线）**加上箭头**，而且 Handle
 * 挂在节点外接盒的上下边缘 —— 那个盒子含了圆下方的标签，于是线是从**标签
 * 底下**出发的。两处都不是参考项目的做法。
 */

/** 端点与图形之间留的缝（参考项目写死 4）。 */
export const EDGE_GAP = 4

/**
 * 端点的形状。只取画线需要的两件事：多大、是不是菱形。
 *
 * 单独定义而不是直接用 `NodeData`，是为了让这里不依赖节点模块 ——
 * 边与节点互相 import 会绕成环。
 */
export interface Shape {
  r: number
  diamond: boolean
}

/**
 * 沿方向 `(ux, uy)`（单位向量）从图形中心走到**图形边缘**的距离。
 *
 * 圆是常数 `r`。菱形不是 —— 它是边长 `r*1.7` 的正方形转了 45°，顶点朝上下
 * 左右：沿轴走到的是**顶点**（最远），沿对角线走到的是**边**（最近）。用一个
 * 常数糊弄，要么沿轴时线钻进菱形，要么沿对角线时留一大块空。
 *
 * 菱形边界是 `|x| + |y| = a`（`a` = 顶点到中心的距离 `r*1.7*√2/2`），
 * 于是方向 `(ux, uy)` 上的距离就是 `a / (|ux| + |uy|)`。
 */
export function edgeDistance(shape: Shape | undefined, ux: number, uy: number): number {
  if (!shape) return 0
  if (!shape.diamond) return shape.r
  const a = (shape.r * 1.7 * Math.SQRT2) / 2
  return a / (Math.abs(ux) + Math.abs(uy) || 1)
}

/**
 * 图形中心相对**外接盒顶边**的纵向偏移。
 *
 * `.gnode` 是 `flex-direction: column; align-items: center`，图形是第一个子
 * 元素 —— 所以它水平居中（`x = 盒宽/2`）、贴着盒顶。圆盒高 `2r`，中心在 `r`；
 * 菱形盒高 `1.7r`，中心在 `0.85r`。
 */
export function shapeCenterOffset(shape: Shape | undefined): number {
  if (!shape) return 0
  return shape.diamond ? shape.r * 0.85 : shape.r
}

export interface Trimmed {
  x1: number
  y1: number
  x2: number
  y2: number
  /** 裁剪后的实际长度，用于「新边画出来」的动画。 */
  length: number
}

/**
 * 把中心到中心的线段裁成「圆周到圆周、各留 4px」的那一段。
 *
 * `(sx, sy)` / `(tx, ty)` 是两端**图形中心**的流坐标 —— 由 React Flow 从
 * Handle 读出来，而 Handle 现在摆在图形中心上（见 `GraphNode`）。
 */
export function trimLine(
  sx: number, sy: number, tx: number, ty: number,
  source: Shape | undefined, target: Shape | undefined,
): Trimmed {
  const dx = tx - sx
  const dy = ty - sy
  const dist = Math.max(1, Math.hypot(dx, dy))
  const ux = dx / dist
  const uy = dy / dist
  const d1 = edgeDistance(source, ux, uy) + EDGE_GAP
  const d2 = edgeDistance(target, ux, uy) + EDGE_GAP
  return {
    x1: sx + ux * d1,
    y1: sy + uy * d1,
    x2: tx - ux * d2,
    y2: ty - uy * d2,
    // 两个图形挨得比两个缝还近时（拖到一起了），长度会算成负数 ——
    // 夹到 1，否则 dasharray 会是负值，线直接消失。
    length: Math.max(1, dist - d1 - d2),
  }
}

/** 裁剪后线段的 SVG path。直线，所以只有 M 和 L。 */
export function linePath(t: Trimmed): string {
  return `M ${t.x1} ${t.y1} L ${t.x2} ${t.y2}`
}
