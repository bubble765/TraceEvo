/**
 * dagre 布局。
 *
 * 两张图用不同的方向，这不是审美决定：
 *
 *   推理图 **从左到右**。它的语义是「证据 -> 假设 -> 结论」，
 *   阅读方向就是推理推进的方向。
 *   执行图 **从上到下**。它的语义是时间与依赖的展开，纵向天然表达
 *   先后顺序，而且和左侧时间轴并排时，两者的「向下」是同一个方向。
 *
 * **尺寸是逐节点算的，不是统一的。** 节点是圆，半径按类型不同；标签在
 * 圆下方，长度按内容不同。用统一尺寸会让 dagre 按一个假想的方块排布，
 * 结果是长标签的节点互相压叠 —— 这类问题在渲染出来之前看不出来。
 */
import dagre from 'dagre'
import {
  forceLink, forceManyBody, forceSimulation, forceX, forceY,
  type Force, type SimulationNodeDatum,
} from 'd3-force'
import type { Node } from '@xyflow/react'

export type Direction = 'LR' | 'TB'

export interface Size {
  w: number
  h: number
}

/**
 * 视口控制所需的最小接口。
 *
 * 只声明用到的那一个方法，而不是 `ReactFlowInstance`：后者的泛型参数
 * 会随节点数据类型一起变化，把它当成 ref 的类型会让「实例的具体类型」
 * 与「声明的类型」产生不必要的协变冲突 —— 而我们真正需要的只是
 * 「节点集合变了，重新适配一下视口」。
 */
export interface ViewportHandle {
  fitView: (opts?: {
    padding?: number
    duration?: number
    minZoom?: number
    maxZoom?: number
    /** 只适配这些节点（含其邻居），用于「聚焦到选中项」。 */
    nodes?: { id: string }[]
  }) => void
}

export interface Viewport {
  x: number
  y: number
  zoom: number
}

/**
 * 自己算「聚焦到这几个节点」的视口。
 *
 * **不用 React Flow 的 `fitView({nodes})`。** 它内部走一条异步队列
 * （`fitViewQueued` → `nodeQueue` → `requestAnimationFrame` → `setNodes`），
 * 触发链上任何一环不落地，返回的 Promise 就永远不 resolve，而视口纹丝不动。
 * 实测在无头浏览器里就是这样：不报错、不生效、查无可查。
 *
 * 而这里需要的一切**本来就在手上** —— dagre 已经算出了每个节点的位置，
 * `boxOf` 算出了尺寸，容器尺寸从 DOM 读。自己算一次加减乘除，比依赖一条
 * 看不见的异步链要可靠得多，而且可以单测。
 */
export function focusViewport(
  nodes: readonly { id: string; position: { x: number; y: number } }[],
  ids: readonly string[],
  sizeOf: (n: never) => Size,
  pane: { w: number; h: number },
  padding = 0.15,
  minZoom = 0.3,
  maxZoom = 1.2,
): Viewport | null {
  const want = new Set(ids)
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
  for (const n of nodes) {
    if (!want.has(n.id)) continue
    const s = sizeOf(n as never)
    x0 = Math.min(x0, n.position.x)
    y0 = Math.min(y0, n.position.y)
    x1 = Math.max(x1, n.position.x + s.w)
    y1 = Math.max(y1, n.position.y + s.h)
  }
  if (!Number.isFinite(x0) || x1 <= x0 || pane.w <= 0 || pane.h <= 0) return null

  const w = x1 - x0
  const h = y1 - y0
  const zoom = Math.max(minZoom, Math.min(maxZoom,
    Math.min(pane.w / (w * (1 + padding)), pane.h / (h * (1 + padding)))))

  // 把内容包围盒的中心挪到画布中心
  return {
    x: pane.w / 2 - (x0 + w / 2) * zoom,
    y: pane.h / 2 - (y0 + h / 2) * zoom,
    zoom,
  }
}

/**
 * 把镜头**对准**一个节点（不是把它和邻居一起框住）。
 *
 * 这两件事看着像，用起来差很远。框住邻居是「适配一个包围盒」，而包围盒的大小
 * 由邻居决定 —— 执行图是按时间纵排的，一个事件的上下游可以横跨十几个 rank，
 * 那个包围盒比画布高得多，适配的结果要么被下限截住（内容溢出、被裁），要么
 * 缩到 0.2 倍（什么都看不清）。两种都不是「聚焦」。
 *
 * 对准则是：**焦点永远在正中、永远是这个大小**，邻居在它周围，看不全就自己
 * 平移。演示时「当前这一步在哪」是个确定的问题，而它有确定的答案；不该由
 * 邻居分布来回答。
 */
export function centerOn(
  nodes: readonly { id: string; position: { x: number; y: number } }[],
  id: string,
  sizeOf: (n: never) => Size,
  pane: { w: number; h: number },
  zoom = 0.9,
  /**
   * 这些节点也要看得见（通常是焦点的一跳邻域）。给了就在必要时**拉远**
   * 到刚好装下它们，但**不改变对准的目标** —— 焦点仍然在正中。
   *
   * 早先这里是「要么对准焦点（看不到邻居），要么框住邻居（焦点跑偏、
   * 还可能被缩放下限截住）」。两种都不对：前者让画布大面积空着，后者让
   * 镜头跟着邻居的分布跑。正确的问题是「**以焦点为中心**，要拉到多远才
   * 能把邻居也收进来」—— 中心是确定的，距离按内容算。
   */
  fitIds?: readonly string[],
): Viewport | null {
  const n = nodes.find(x => x.id === id)
  if (!n || pane.w <= 0 || pane.h <= 0) return null
  const s = sizeOf(n as never)

  let z = zoom
  if (fitIds && fitIds.length) {
    const want = new Set(fitIds)
    const fx = n.position.x + s.w / 2
    const fy = n.position.y + s.h / 2
    // **要按「焦点到每个成员最远边缘的距离」算，不能按包围盒算。**
    //
    // 包围盒的适配倍率只在「视图对准包围盒中心」时成立，而这里明确是
    // **对准焦点**的 —— 焦点通常落在包围盒的边上，于是包围盒朝另一侧
    // 伸出去两倍，那一侧的邻居全被挤出画布。这正是「选中一个节点，图就
    // 消失了大半」的根因：节点没丢，是镜头没框住它们。
    // 实测：选中一个 9 度的节点时只有 6/15 个节点可见。
    //
    // 正确的量是：视图半宽必须装得下「焦点到该成员最远边缘」。
    let cap = Infinity
    for (const m of nodes) {
      if (!want.has(m.id)) continue
      const ms = sizeOf(m as never)
      const dx = Math.abs(m.position.x + ms.w / 2 - fx) + ms.w / 2
      const dy = Math.abs(m.position.y + ms.h / 2 - fy) + ms.h / 2
      cap = Math.min(cap, (pane.w / 2) / (dx * 1.12), (pane.h / 2) / (dy * 1.12))
    }
    if (Number.isFinite(cap)) z = Math.max(Math.min(z, cap), 0.3)  // 地板 0.3
  }

  return {
    x: pane.w / 2 - (n.position.x + s.w / 2) * z,
    y: pane.h / 2 - (n.position.y + s.h / 2) * z,
    zoom: z,
  }
}

/**
 * 某个节点在边表里的 n 跳邻域（含自身）。
 *
 * 它**不再**用来决定视口框住谁（那个想法被否掉了：包围盒由邻居决定，
 * 而邻居的分布是数据的偶然）。现在的用途是告诉 `centerOn` 「拉到多远
 * 才能把邻居也收进来」—— 中心仍然是焦点，它只影响距离。
 */
export function neighborhood(
  id: string,
  edges: readonly { source: string; target: string }[],
  hops = 1,
): string[] {
  let frontier = new Set<string>([id])
  const out = new Set<string>([id])
  for (let i = 0; i < hops; i++) {
    const next = new Set<string>()
    for (const e of edges) {
      if (frontier.has(e.source) && !out.has(e.target)) next.add(e.target)
      if (frontier.has(e.target) && !out.has(e.source)) next.add(e.source)
    }
    for (const n of next) out.add(n)
    frontier = next
    if (next.size === 0) break
  }
  return [...out]
}

export function layout<N extends Node>(
  nodes: N[], edges: readonly { source: string; target: string }[], direction: Direction,
  sizeOf: (n: N) => Size,
  gaps: { nodesep?: number; ranksep?: number } = {},
): N[] {
  const seeds = seedsOf(nodes, edges, direction, sizeOf, gaps)
  if (!seeds) return nodes
  return writeBack(nodes, seeds)
}

/**
 * dagre 的原始输出：每个节点的**中心点**与它的盒子尺寸。
 *
 * `layout` 与 `forceLayout` 都从这里出发，两者因此不可能对「种子在哪」
 * 产生分歧。这个抽取不是为了少写几行，是为了堵一个具体的坑：
 * `layout` 写出去的是左上角（`p.x - w/2`），而 `(p.x - w/2) + w/2 !== p.x`
 * —— 同层但宽度不同的节点反推回来的中心会差 1 ulp，按层分桶就散了架。
 * 而下面整套算法都建立在「同层节点在层轴上坐标**逐位相等**」之上。
 */
function seedsOf<N extends Node>(
  nodes: N[], edges: readonly { source: string; target: string }[], direction: Direction,
  sizeOf: (n: N) => Size,
  gaps: { nodesep?: number; ranksep?: number },
): { cx: Float64Array; cy: Float64Array; sizes: Size[] } | null {
  if (nodes.length === 0) return null

  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({
    rankdir: direction,
    nodesep: gaps.nodesep ?? (direction === 'LR' ? 28 : 22),
    ranksep: gaps.ranksep ?? (direction === 'LR' ? 96 : 62),
    marginx: 28,
    marginy: 28,
  })

  const sizes: Size[] = new Array(nodes.length)
  for (let i = 0; i < nodes.length; i++) {
    const s = sizeOf(nodes[i])
    sizes[i] = s
    g.setNode(nodes[i].id, { width: s.w, height: s.h })
  }
  for (const e of edges) {
    // dagre 只按 id 连边，不关心边是否真实存在；悬空边会让它抛错，
    // 所以在数据层就挡掉（后端的 build() 也做同样的过滤）。
    if (g.hasNode(e.source) && g.hasNode(e.target)) {
      g.setEdge(e.source, e.target)
    }
  }

  dagre.layout(g)

  const cx = new Float64Array(nodes.length)
  const cy = new Float64Array(nodes.length)
  for (let i = 0; i < nodes.length; i++) {
    const p = g.node(nodes[i].id)
    cx[i] = p.x
    cy[i] = p.y
  }
  return { cx, cy, sizes }
}

/**
 * 按**语义列**播种：每个节点落在它那一列，列内均分。
 *
 * 这是参考项目 `viz/story_player.py::layout_nodes` 的做法，也是它的因果图
 * 看上去成列的原因。我们先前用 dagre 的拓扑分层当锚点，而拓扑分层与语义
 * 不是一回事：`E4 --support--> H2a` 会让 MRI 证据排在**假设那一列**，
 * `KB104 --support--> H1b` 会把知识节点排进证据列 —— 图是对的，读起来是散的。
 *
 * 参考的列序与列内 y 都写死在它自己的 viewBox 坐标里
 * （`COL_X = {Signal: 90, Evidence: 300, H1: 520, H2: 690, H3: 860,
 * Knowledge: 960}`、y 在 [90, 580] 均分）。这里只照抄**列序与列内均分**，
 * 具体间距按我们自己的盒宽算 —— 把 90/300/520 这些数搬过来是照搬别人的
 * 坐标系，两边盒子尺寸不同，搬过来只会得到一个更差的间距。
 *
 * `order` 是**全帧节点并集**的加入顺序（参考用全帧并集定序，节点全程不动）。
 * 我们从后端拿不到未来的节点，但 `deltas[].addedNodes` 已经给出了这个顺序 ——
 * 它不含标签与分数，所以不算泄露未来的内容。
 */
export const COLUMN_ORDER = ['Signal', 'Evidence', 'H1', 'H2', 'H3',
                             'Knowledge'] as const

/**
 * 输入来源不能只错开一点：Signal 与 Evidence 都连向同一个假设时，
 * 若证据节点恰好落在「症状 → 假设」的直线路径上，两条不同关系仍会重叠，
 * 看起来像重复边。固定错开输入列，让两类关系在图上可辨，同时不改变语义列。
 */
const DEFAULT_COLUMN_Y_OFFSETS: Readonly<Record<string, number>> = {
  Signal: -54,
  Evidence: 120,
}

export function columnSeedsOf<N extends Node>(
  nodes: N[],
  sizeOf: (n: N) => Size,
  columnOf: (n: N) => string,
  order: readonly string[],
  gaps: { columnGap?: number; rowGap?: number; columnWidth?: number
          nominalRows?: number; columnYOffset?: Readonly<Record<string, number>> } = {},
): { cx: Float64Array; cy: Float64Array; sizes: Size[] } | null {
  if (nodes.length === 0) return null
  const colGap = gaps.columnGap ?? 150
  const rowGap = gaps.rowGap ?? 168
  /** 列宽取常数，**不取该列最宽的那个盒子** —— 取 max 的话，一个长标签
   *  的新节点入列会让该列变宽，**它右边所有列一起平移**。 */
  const colW = gaps.columnWidth ?? 80
  /** 纵向按「名义行数」居中，不按该列的实际行数。见下面 cy 的说明。 */
  const rows = gaps.nominalRows ?? 4
  const yOffset = gaps.columnYOffset ?? DEFAULT_COLUMN_Y_OFFSETS

  const sizes: Size[] = nodes.map(n => sizeOf(n))
  const rank = new Map<string, number>()
  order.forEach((id, i) => { if (!rank.has(id)) rank.set(id, i) })

  // 先分组：列键 -> 该列节点的下标（按并集顺序排，没有就按传入顺序）
  const cols = new Map<string, number[]>()
  for (let i = 0; i < nodes.length; i++) {
    const key = columnOf(nodes[i])
    const arr = cols.get(key)
    if (arr) arr.push(i)
    else cols.set(key, [i])
  }
  const rankOf = (id: string, fallback: number) => rank.get(id) ?? fallback
  for (const idx of cols.values()) {
    idx.sort((a, b) => rankOf(nodes[a].id, a) - rankOf(nodes[b].id, b))
  }

  // 列按 COLUMN_ORDER 排；认不出的列排在已知列之后，彼此之间按首次出现。
  const known = COLUMN_ORDER as readonly string[]
  const unknown = [...cols.keys()].filter(k => !known.includes(k))
  const seq = [...known.filter(k => cols.has(k)), ...unknown]

  const cx = new Float64Array(nodes.length)
  const cy = new Float64Array(nodes.length)
  for (let c = 0; c < seq.length; c++) {
    const idx = cols.get(seq[c])!
    const x = c * (colW + colGap) + colW / 2
    // 列内位置是 **(列, 行号) 的纯函数**：行距固定、起点固定、与这一列
    // 有几个节点无关。
    //
    // 这两处「固定」是列式锚点相对 dagre 的全部好处。改成「按这一列的实际
    // 高度居中」的话，同列里每加入一个节点，**已有节点全部平移半个行距**，
    // 而时间轴每走一步都可能加节点 —— 整张图于是每一步重排一次。
    // 参考项目能按实际高度均分，是因为它一次把全帧节点发给播放器；我们按步
    // 取图，拿不到未来。`scripts/check-layout.ts` 盯着这条。
    const y0 = -(rows - 1) * rowGap / 2
    for (let k = 0; k < idx.length; k++) {
      const i = idx[k]
      cx[i] = x
      cy[i] = y0 + k * rowGap + (yOffset[seq[c]] ?? 0)
    }
  }
  return { cx, cy, sizes }
}

function writeBack<N extends Node>(
  nodes: N[], s: { cx: Float64Array; cy: Float64Array; sizes: Size[] },
): N[] {
  return nodes.map((n, i) => ({
    ...n,
    position: { x: s.cx[i] - s.sizes[i].w / 2, y: s.cy[i] - s.sizes[i].h / 2 },
  }))
}

// ==================================================================== 力导向
/**
 * **这一节照搬参考项目** `医疗溯因智能体/gos_repo/viz/story_template.html`
 * 的力模拟。它原先不是这样写的 —— 先前我把 d3 的模拟在 `useMemo` 里跑完
 * 就**停掉**并把坐标冻结下来，于是：
 *
 *   * 拖动一个节点时，**邻居纹丝不动**（没有活着的模拟去响应）；
 *   * 松手是**瞬移回原位**（清掉覆盖值，坐标又来自那次冻结的计算）；
 *
 * 而参考项目里模拟是**一直活着**的（d3 自己的定时器在跑）。拖动时把节点
 * 用 `fx/fy` 钉住并把 alpha 重新加热到 0.25，整张图跟着响应；松手清掉
 * `fx/fy`、alpha 归零，节点随剩余的能量**松弛**回去 —— 不是弹回。
 *
 * 所以现在的结构是：`seedsOf` 给出**锚点**（dagre 的分层），模拟以锚点 +
 * 上一帧位置为初值**持续运行**，坐标由 tick 驱动渲染。
 *
 * 与参考项目唯一的偏离是分离力用矩形而不是圆，见 `separation`。
 */
import type { Simulation } from 'd3-force'

/** 参考项目的力参数。除了 `linkDistance`，其余逐个抄下来，不做"优化"。 */
export const REF = {
  /**
   * **唯一一处数值偏离，而且必须偏离。**
   *
   * 参考项目写死 150，那是**它自己的种子间距**的度量 —— 它的种子是手写的列坐标。
   * 我们的种子是 dagre 的分层结果（LR 图 `ranksep` 96、TB 图 62），拿 150 去要求
   * 一条本来 96 的边，等于每条边都带一个 54px 的永久弹簧误差，而误差沿链**累积**：
   * 实测 13 节点偏移 587px、37 节点 412px，整张分层图被推散。换成 `ranksep` 常数
   * 也不行 —— 跨两层的边本来就不是 `ranksep` 远。
   *
   * 正确的是**每条边取自己两端的种子距离**，于是 t=0 时每条边都恰好零力，种子布局
   * 本身就是平衡态，力只做局部松弛。实测位移降到 29px / 107px，净空缺口从 11 对
   * 降到 0。
   *
   * 所以这里不存 `linkDistance`，见 `createSimulation` 里的 `seedDistance`。
   */
  linkStrength: 0.5,
  charge: -320,
  /**
   * 分层轴锚定强、另一个轴弱 —— 参考项目是 0.3 / 0.12。
   *
   * 参考项目里 0.3 永远给 x，因为它的种子是**竖着的列**（x 就是分层轴）。
   * 我们两张图的分层轴不同（推理图 LR、执行图 TB），照抄常数值会把 TB 图的
   * **弱锚定**放在分层轴上、**强锚定**放在自由轴上，正好反了。所以按方向取：
   * 分层轴用强的那个。
   */
  anchorRank: 0.3,
  anchorCross: 0.12,
  alpha: 0.7,
  alphaDecay: 0.05,
  /** 拖动时重新加热到的目标 alpha。 */
  dragAlpha: 0.25,
  /**
   * 一个节点离它**原始种子**最远能被拖多少（流坐标像素）。
   *
   * **这是「松手即定住」必须付的代价，不能省。** 节点要停在被放下的地方，
   * 连接它的边就不能绷着 —— 于是只能把邻居一起拽走（或反过来被拽回去）。
   * 也就是说「定住」必然**永久改变布局**，改动量正比于拖多远。
   *
   * 不设上限时实测过一次：拖动 770px（比整张图 366px 还宽）之后，图的流坐标
   * 包围盒从 366×1149 涨到 **1463×1239**，一多半节点被推出画布 —— 用户看到的
   * 就是「一拖节点就消失」。而且反复拖会持续累积。
   *
   * 200px ≈ 两倍 rank 间距：足够把压在一起的标签挪开（拖动本来的用途），
   * 又不足以把分层结构拆掉。相对**原始**种子量，所以反复拖也不累积。
   */
  maxPull: 200,
  /**
   * 预热的上限帧数。**实际是跑到冷为止**，不是跑固定帧数 —— 原因见
   * `createSimulation` 里的预热循环。
   */
  preTickCap: 400,
} as const

/** 图内节点在模拟里的样子。坐标是**外接盒中心**（见 writeBack）。 */
interface SimNode extends SimulationNodeDatum {
  id: string
  index: number
  x: number
  y: number
  /** 锚点：dagre 给的盒子中心。 */
  sx: number
  sy: number
  /** 外接盒的半宽 / 半高，用于矩形分离。 */
  hw: number
  hh: number
  /**
   * **原始**种子中心。与 `sx/sy` 不同：`sx/sy` 会被 `reanchor` 搬走，
   * 而它永远不动 —— 拖动半径就是相对它量的（见 `REF.maxPull`）。
   */
  ox: number
  oy: number
  /** 被拖过 —— 下一步要重新播种（参考项目的行为）。 */
  dragged: boolean
}

/**
 * 盒子之间要留的空隙。
 *
 * `2 × 10`：画在盒子**外面**的墨迹往外伸约 10px（等级徽章左 8、跨图引用徽章
 * 右 10、alarm 虚线框 5、lit 光环 6），盒子模型刻意没算它们，所以两个盒子
 * 之间得留出两边各 10px 的余量，才轮不到那些墨迹互相蹭。
 * 与 `scripts/check-layout.ts` 的 `SLACK` 是同一个数，改一处要改两处。
 */
const SEPARATION_PAD = 20

/**
 * 矩形分离力 —— **与参考项目的唯一一处实质偏离。**
 *
 * 参考项目用的是 `forceCollide`，半径 `NODE_R + 6 + min(26, label.length * 1.4)`。
 * 那个圆圆心在节点中心，而标签挂在圆的**下方** —— 它根本没把标签圈进去，是
 * 间距启发式。我们这里标签更长（中文折 3 行），照抄会直接退回「标签互相压叠」。
 *
 * 补成「外接盒的外接圆」也不行，而且是**两个方向都不对**：60×104 的盒子外接圆
 * 半径 66，两个横向相邻的节点被要求隔开 132（实际只需要 80），横向白留一倍；
 * 而斜向相邻时圆又是**不够**的 —— 外接圆只保证「圆不重叠」，不保证「矩形不
 * 重叠」，对角线方向差得最多。实测 TB 图按圆分离后仍挤着 8 对。
 *
 * 所以逐对按**轴对齐矩形**分离，沿重叠较小的那个轴推开（形变代价最小）。
 * ≤64 个节点时每帧约 2000 次比较，可以忽略。整块改动记在 README。
 */
function separation(ds: SimNode[]): void {
  for (let i = 0; i < ds.length; i++) {
    const a = ds[i]
    for (let j = i + 1; j < ds.length; j++) {
      const b = ds[j]
      const dx = b.x - a.x
      const dy = b.y - a.y
      const ox = a.hw + b.hw + SEPARATION_PAD - Math.abs(dx)
      if (ox <= 0) continue
      const oy = a.hh + b.hh + SEPARATION_PAD - Math.abs(dy)
      if (oy <= 0) continue
      // 中心完全重合时没有方向可依，用 index 定一个 —— 不能用随机数，
      // 那会毁掉 settle 的确定性（d3 自己也用 index 生成确定的 jiggle）。
      const sx = dx < 0 ? -1 : dx > 0 ? 1 : (a.index < b.index ? 1 : -1)
      const sy = dy < 0 ? -1 : dy > 0 ? 1 : (a.index < b.index ? 1 : -1)
      if (ox < oy) {
        const s = (sx * ox) / 2
        a.x -= s
        b.x += s
      } else {
        const s = (sy * oy) / 2
        a.y -= s
        b.y += s
      }
    }
  }
}

export interface SimOptions {
  /** 上一帧的位置（盒子中心）。有就继承 —— 参考项目靠这个让图「滑」过去而不是跳。 */
  prev?: Map<string, { x: number; y: number }>
  /** 预热上限帧数，测试里可以调小。 */
  preTickCap?: number
  /**
   * 锚点。不给就走 dagre 的拓扑分层（溯源图用的就是它）；给了就用它
   * —— 推理图给的是按语义列播的种（见 `columnSeedsOf`）。
   *
   * 形状与 `seedsOf` 的返回一致，所以两种锚点在后续代码里没有分别。
   */
  seeds?: { cx: Float64Array; cy: Float64Array; sizes: Size[] } | null
}

export interface Sim {
  /** 每个节点的盒子中心，按传入的 nodes 顺序。 */
  centers: () => { x: number; y: number }[]
  /** 某个节点当前的盒子中心。 */
  centerOf: (id: string) => { x: number; y: number } | null
  /** 钉住一个节点（拖动中）。 */
  pin: (id: string, x: number, y: number) => void
  /** 松开一个节点 —— 清 fx/fy，让它随力松弛。 */
  unpin: (id: string) => void
  /**
   * 把锚点搬到节点当前所在处 —— **松手不回弹的全部实现。**
   *
   * 锚定力是 `(sx - x) * strength`：把 `sx/sy` 设为当前位置，锚定力就恰好
   * 是零，节点于是留在被放下的地方，只有连杆与分离力继续作用。
   *
   * 这是**刻意偏离参考项目**的一处（参考项目松手只清 fx/fy，锚点不动，
   * 节点因此缓缓回到原处 —— 实测回弹 94%）。取舍记在 README。
   *
   * 不影响连杆的静止长度：`forceLink.distance()` 在初始化时就把每条边的
   * 长度算好存起来了，之后再改 `sx/sy` 不会回头去动它。
   */
  reanchor: (id: string) => void
  /** 拖动开始时调用：重新加热，让整张图响应。 */
  reheat: () => void
  /** 拖动结束时调用：目标 alpha 归零，模拟自然冷却。 */
  cool: () => void
  /** 模拟是否还在跑（alpha 高于阈值）。 */
  hot: () => boolean
  stop: () => void
  /** 被拖过的节点 id —— 下一步要重新播种。 */
  draggedIds: () => Set<string>
}

/**
 * 建一个**活着**的模拟，返回前先预热到冷。调用方接上
 * `simulation.on('tick', ...)` 驱动渲染；此后只有拖动会唤醒它。
 */
export function createSimulation<N extends Node>(
  nodes: N[],
  edges: readonly { source: string; target: string }[],
  direction: Direction,
  sizeOf: (n: N) => Size,
  gaps: { nodesep?: number; ranksep?: number } = {},
  opts: SimOptions = {},
): { sim: Simulation<SimNode, undefined>; handle: Sim } {
  // 锚点：调用方给了就用它，否则退回 dagre 的拓扑分层。
  const seeds = opts.seeds ?? seedsOf(nodes, edges, direction, sizeOf, gaps)
  if (!seeds) {
    throw new Error('createSimulation 需要至少一个节点')
  }
  const { sizes } = seeds
  const prev = opts.prev

  const sn: SimNode[] = nodes.map((n, i) => {
    const p = prev?.get(n.id)
    return {
      id: n.id,
      index: i,
      // 继承上一帧位置；没有就落在锚点上。这是参考项目「图会滑过去而不是
      // 跳过去」的来源 —— 也是它**路径相关**的原因（从第 1 步走到第 5 步
      // 与直接跳到第 5 步，落点可能不同）。
      x: p ? p.x : seeds.cx[i],
      y: p ? p.y : seeds.cy[i],
      sx: seeds.cx[i],
      sy: seeds.cy[i],
      ox: seeds.cx[i],
      oy: seeds.cy[i],
      hw: sizes[i].w / 2,
      hh: sizes[i].h / 2,
      dragged: false,
    }
  })
  const byId = new Map(sn.map(d => [d.id, d]))
  /** 哪个轴是分层轴：LR 是 x，TB 是 y。 */
  const rank = direction === 'LR' ? 'x' : 'y'
  const links = edges
    .map(e => ({ source: e.source, target: e.target }))
    .filter(e => byId.has(e.source) && byId.has(e.target)
                 && e.source !== e.target)

  // 每条边的静止长度 = 它两端**种子**的距离。见 REF.linkDistance 的说明：
  // 这是让种子布局本身成为平衡态的唯一写法，否则每条边都带一个永久弹簧误差。
  //
  // `restOverride` 是拖动之后按当前几何重铸的那几条边（见 reanchor）。
  // 键用「两端 id 排序后拼接」——与方向无关，同一条边的两个方向是同一个键。
  const restOverride = new Map<string, number>()
  const linkKey = (a: SimNode | string, b: SimNode | string) => {
    const x = typeof a === 'string' ? a : a.id
    const y = typeof b === 'string' ? b : b.id
    return x < y ? `${x}|${y}` : `${y}|${x}`
  }
  const seedDistance = (e: { source: SimNode | string; target: SimNode | string }) => {
    const a = typeof e.source === 'string' ? byId.get(e.source)! : e.source
    const b = typeof e.target === 'string' ? byId.get(e.target)! : e.target
    return restOverride.get(linkKey(a, b)) ?? Math.hypot(a.sx - b.sx, a.sy - b.sy)
  }

  const sim = forceSimulation<SimNode>(sn)
    .force('link', forceLink<SimNode, { source: string; target: string }>(links)
      .id(d => d.id)
      .distance(seedDistance as never)
      .strength(REF.linkStrength))
    .force('charge', forceManyBody().strength(REF.charge))
    .force('x', forceX<SimNode>(d => d.sx).strength(rank === 'x' ? REF.anchorRank : REF.anchorCross))
    .force('y', forceY<SimNode>(d => d.sy).strength(rank === 'y' ? REF.anchorRank : REF.anchorCross))
    // **分离力必须排在最后。** d3 按**注册顺序**逐个调用 force，而锚定力每帧都
    // 会把节点拽回种子位置 —— 排在前面的分离刚把盒子推开，后面的锚定又推了回去，
    // 实测 TB 图因此留下 17 对压在净空里的盒子。排在最后，它就是每帧最后一个
    // 写坐标的人，约束在 tick 结束时必然成立。
    //
    // 也不看 alpha（同 forceCollide）：它是硬约束，不是随冷却衰减的软力 ——
    // 一衰减，最后几帧就会留下压在一起没解开的盒子。
    .force('separation', (() => { separation(sn) }) as Force<SimNode, undefined>)
    .alpha(REF.alpha).alphaDecay(REF.alphaDecay)
    // **不停表**。参考项目的模拟一直在跑，这正是「拖动连带邻居」和
    // 「松手松弛而不是弹回」的来源。停掉它，那两件事都不会发生。
    .stop()
  // **预热到冷，不是跑固定帧数。** 先前只跑 12 帧（参考项目的数字），
  // 12 帧之后 alpha 还在 0.7 上下 —— 交出去之后模拟自己还要跑两秒才停，
  // 而外层的视口是按**那一刻**的坐标算的：于是图在用户眼前飘出画布。
  // 实测截图里两张图都是空的（15/15 与 37 个节点全在框外）。
  // 参考项目可以只跑 12 帧，是因为它不需要「先算准视口再显示」。
  // 跑到冷之后：第一帧就是终态，视口算得准，画面也不再自己动 ——
  // 运动只剩下一个来源，就是用户拖动。
  for (let i = 0; i < (opts.preTickCap ?? REF.preTickCap); i++) {
    if (sim.alpha() <= sim.alphaMin()) break
    sim.tick()
  }

  /** 边的某一端 —— d3 在初始化时会把 id 换成节点对象，两种都要吃。 */
  const endId = (v: unknown): string =>
    typeof v === 'string' ? v : (v as SimNode).id

  /** d3 的力把参数缓存进内部数组，改完访问器必须重新 initialize 才生效。 */
  const reinitForce = (name: string) => {
    const f = sim.force(name) as unknown as
      { initialize?: (n: SimNode[]) => void } | undefined
    f?.initialize?.(sn)
  }

  const handle: Sim = {
    centers: () => sn.map(d => ({ x: d.x, y: d.y })),
    centerOf: id => {
      const d = byId.get(id)
      return d ? { x: d.x, y: d.y } : null
    },
    pin: (id, x, y) => {
      const d = byId.get(id)
      if (!d) return
      // **非有限值一律不写。** d3 每帧执行 `node.x = node.fx`，一个 NaN 会
      // 顺着连杆传给邻居、再传遍全图 —— 表现是「一拖动，图就没了」，而且
      // 事后看不出是哪里引入的。宁可这一帧不钉。
      if (!Number.isFinite(x) || !Number.isFinite(y)) return
      // 把落点夹在「离原始种子 maxPull」的圆内。见 REF.maxPull：
      // 不夹的话一次远拖就能把整张图拆了，而且会累积。
      const dx = x - d.ox
      const dy = y - d.oy
      const dist = Math.hypot(dx, dy)
      const cap = REF.maxPull
      if (dist > cap) {
        d.fx = d.ox + (dx / dist) * cap
        d.fy = d.oy + (dy / dist) * cap
      } else {
        d.fx = x
        d.fy = y
      }
      d.dragged = true
    },
    unpin: id => {
      const d = byId.get(id)
      if (!d) return
      // 只清钉住，**不还原坐标** —— 节点停在它被拖到的地方。
      d.fx = null
      d.fy = null
    },
    reanchor: id => {
      const d = byId.get(id)
      if (!d) return
      d.sx = d.x
      d.sy = d.y
      // **只挪锚点还不够，连杆也要跟着重铸。** 边的静止长度是在初始化时按
      // 种子算好存下的，节点被拖走 300px 之后，那 9 条边全都比静止长度长
      // 三百多像素 —— 松手时它们合力把节点拽回去。实测只挪锚点仍回弹 61%，
      // 连邻居一起挪也只有 40%（而且邻居被永久带偏 174px）。
      //
      // 所以把**该节点连出去的每一段**按当前几何重铸成新的静止长度：松手
      // 那一刻这些边恰好零力，节点就真的停住了。只重铸这一圈，不动图里
      // 其它边 —— 全局重铸会把整张图的平衡态一起改掉。
      {
        for (const l of links) {
          const s1 = endId(l.source)
          const t1 = endId(l.target)
          if (s1 !== id && t1 !== id) continue
          const a = byId.get(s1), b = byId.get(t1)
          if (a && b) restOverride.set(linkKey(s1, t1), Math.hypot(b.x - a.x, b.y - a.y))
        }
        reinitForce('link')
      }
      // **改完必须让 x/y 两个力重新初始化。** d3 的 forceX / forceY 在
      // `initialize()` 里就把目标坐标算好塞进内部数组（`xz[i] = +x(node)`），
      // 之后每帧只读那个数组，**不再调用访问器**。所以光改 `sx/sy` 是彻底
      // 无声的 —— 实测四种改法结果逐位相同，就是这个原因。forceLink 把静止
      // 长度同样缓存成 `distances[i]`，重铸之后也得重新初始化才生效。
      reinitForce('x')
      reinitForce('y')
    },
    reheat: () => sim.alphaTarget(REF.dragAlpha).restart(),
    cool: () => sim.alphaTarget(0),
    hot: () => sim.alpha() > sim.alphaMin(),
    stop: () => sim.stop(),
    draggedIds: () => new Set(sn.filter(d => d.dragged).map(d => d.id)),
  }
  return { sim, handle }
}

/**
 * 同步跑到稳定，返回坐标。**只给自检脚本用** —— 界面上走的是
 * `createSimulation` + 实时 tick，因为只有活着的模拟才有参考项目的手感。
 */
export function settle<N extends Node>(
  nodes: N[],
  edges: readonly { source: string; target: string }[],
  direction: Direction,
  sizeOf: (n: N) => Size,
  gaps: { nodesep?: number; ranksep?: number } = {},
  opts: SimOptions & { maxTicks?: number } = {},
): N[] {
  if (nodes.length === 0) return nodes
  const { sim, handle } = createSimulation(nodes, edges, direction, sizeOf,
                                           gaps, opts)
  const max = opts.maxTicks ?? 400
  for (let i = 0; i < max && handle.hot(); i++) sim.tick()
  const centers = handle.centers()
  const sizes = nodes.map(sizeOf)
  sim.stop()
  return nodes.map((n, i) => ({
    ...n,
    position: {
      x: centers[i].x - sizes[i].w / 2,
      y: centers[i].y - sizes[i].h / 2,
    },
  }))
}
