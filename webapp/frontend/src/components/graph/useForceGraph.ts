/**
 * 把「活着的力模拟」接到 React Flow 上。两张图共用这一份。
 *
 * **为什么需要一个 hook 而不是在 useMemo 里算完。** 先前坐标是在 `useMemo`
 * 里跑完 d3 模拟就冻结的，于是拖动一个节点时邻居纹丝不动、松手是瞬移回原位。
 * 参考项目里模拟**一直在跑**（d3 自己的定时器），拖动会把 alpha 重新加热，
 * 整张图跟着响应，松手后再慢慢冷下来。那两件事都要求模拟活着，而活的东西
 * 不属于渲染期 —— 它是副作用。
 *
 * 代价要说清楚：每个 tick 都会 `setState`，也就是每帧重建一次节点数组。
 * 节点 ≤64 个时这没问题；再多就得换成直接写 React Flow 的内部 store。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { useReactFlow, useStore, type Edge, type Node } from '@xyflow/react'
import {
  createSimulation, type Direction, type Sim, type Size,
} from './layout'

interface Positioned {
  id: string
  position: { x: number; y: number }
  size: Size
}

/**
 * 把节点盒子的中心夹进当前画布可见范围，返回**流坐标**。
 *
 * 为什么需要：**「松手即定住」把一个洞打开了** —— 以前松手会弹回原位，
 * 现在节点停在你放下的地方，于是一个被拖到画布外的节点就**永久留在画布外**，
 * 看起来就是「拖一下就没了」。右图尤其容易触发：它是按焦点放大的，画布里
 * 只看得见图的一小部分，一次普通的拖动就能把节点送出可见范围。
 *
 * 夹的是**屏幕坐标**下的盒子（`zoom` 要乘进去），不是流坐标 —— 否则放大时
 * 会允许节点跑到可视区外很远。`min`/`max` 的顺序保证节点比画布还大时中心
 * 仍停在画布内，而不是两边互相覆盖成 NaN。
 */
function clampToPane(
  cx: number, cy: number, w: number, h: number,
  vp: { w: number; h: number; x: number; y: number; z: number },
): [number, number] {
  if (!(vp.z > 0) || vp.w <= 0 || vp.h <= 0) return [cx, cy]
  const hw = (w / 2) * vp.z
  const hh = (h / 2) * vp.z
  // 流 → 屏幕 → 夹取 → 流。节点比画布还大时上下界会交叉，
  // `Math.min(Math.max(...))` 取的是下界（节点贴左边/上边），
  // 不会两边互相覆盖成 NaN。
  const sx = Math.min(Math.max(cx * vp.z + vp.x, hw), vp.w - hw)
  const sy = Math.min(Math.max(cy * vp.z + vp.y, hh), vp.h - hh)
  return [(sx - vp.x) / vp.z, (sy - vp.y) / vp.z]
}

/** 锚点计算函数：给节点与尺寸，交出每个节点的种子中心。 */
export type AnchorOf<N> = (
  nodes: N[], sizeOf: (n: N) => Size,
) => { cx: Float64Array; cy: Float64Array; sizes: Size[] } | null

/**
 * @param structural 结构层的节点与边（位置无所谓，模拟会覆盖）
 * @param direction  分层方向：推理图 LR、执行图 TB
 * @param gaps       dagre 的间距，决定锚点
 * @param sizeOf     盒子尺寸 —— 与布局、渲染同一个真源
 * @param anchor     自定义锚点。不给就走 dagre 的拓扑分层（溯源图）；
 *                   推理图给的是语义列播种（见 layout.columnSeedsOf）。
 *                   **必须是稳定引用** —— 它只在 effect 里被读，不进依赖数组，
 *                   否则每渲染一次就重建模拟，图会永远在抖（同 `sizeOf`/`gaps`）。
 */
export function useForceGraph<N extends Node & Positioned, E extends Edge>(
  structural: { nodes: N[]; edges: E[] },
  direction: Direction,
  gaps: { nodesep?: number; ranksep?: number },
  sizeOf: (n: N) => Size,
  // `NoInfer`：N 只能从 `structural` 推。不加的话 `anchor` 也成了一个推断源，
  // 两个来源在 `data: Record<string, unknown>` 与 `NodeData` 之间打架，
  // 报出来的错指向 `nodeSizeOf` —— 而那个函数没有任何问题。
  anchor?: AnchorOf<NoInfer<N>>,
) {
  const { nodes: snodes, edges: sedges } = structural

  // 画布尺寸与视口，用来把拖动的落点夹在画布内（见 clampToPane）。
  //
  // 尺寸用 `useStore` 订阅（它很少变）；**变换则必须在拖动的那一刻现读** ——
  // 订阅 `transform` 会让整个图在**平移的每一帧**重渲染，52 个节点下拖画布
  // 会明显发涩。`useReactFlow()` 的实例是稳定的，`getViewport()` 现读现用。
  const paneW = useStore(s => s.width)
  const paneH = useStore(s => s.height)
  const rf = useReactFlow()

  // 上一帧的位置（盒子中心）。跨步骤继承 —— 参考项目靠它让图**滑**过去
  // 而不是跳过去。也是它路径相关的原因：从第 1 步走到第 5 步，与直接跳到
  // 第 5 步，落点可能不同。
  const prevRef = useRef(new Map<string, { x: number; y: number }>())
  const simRef = useRef<Sim | null>(null)
  const [centers, setCenters] = useState<Map<string, { x: number; y: number }>>(
    () => new Map())

  // 锚点函数走 ref：它是「怎么算种子」的配方，不是依赖。放进依赖数组会在
  // 每次渲染时重建模拟（同 `sizeOf`/`gaps` 的说明）。
  const anchorRef = useRef(anchor)
  anchorRef.current = anchor

  useEffect(() => {
    if (snodes.length === 0) return
    const { sim, handle } = createSimulation(
      snodes, sedges, direction, sizeOf, gaps, {
        prev: prevRef.current,
        seeds: anchorRef.current?.(snodes, sizeOf) ?? undefined,
      })
    simRef.current = handle

    const onTick = () => {
      const cs = handle.centers()
      const m = new Map<string, { x: number; y: number }>()
      for (let i = 0; i < snodes.length; i++) m.set(snodes[i].id, cs[i])
      prevRef.current = m
      setCenters(m)
    }
    sim.on('tick', onTick)
    // 把预演后的第一帧立刻显示出来，否则图会先出现在旧位置上再跳一下
    onTick()
    sim.restart()      // ← 模拟从这里开始活着

    return () => { sim.on('tick', null); sim.stop(); simRef.current = null }
    // `sizeOf` 与 `gaps` 是常量来源，不参与依赖：把它们放进依赖会让每次
    // 渲染都重建模拟，图会永远抖。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snodes, sedges, direction])

  const nodes = useMemo(() => snodes.map(n => {
    const c = centers.get(n.id)
    if (!c) return n
    return {
      ...n,
      position: { x: c.x - n.size.w / 2, y: c.y - n.size.h / 2 },
    }
  }), [snodes, centers])

  // ---- 拖动：与参考项目的 d3.drag 三个回调一一对应 --------------------
  // 与参考项目**刻意不同**的一处：松手时把锚点搬到落点（`reanchor`）。
  // 参考项目松手只清 fx/fy、锚点不动，节点于是缓缓回到原处（实测回弹 94%）。
  // 这里让节点留在用户放下的地方，只有邻居松弛过去。取舍见 README。
  const onNodeDragStart = useCallback((_: unknown, n: Positioned) => {
    const sim = simRef.current
    if (!sim) return
    const c = sim.centerOf(n.id) ?? { x: 0, y: 0 }
    sim.pin(n.id, c.x, c.y)
    sim.reheat()          // ← 整张图从这里开始跟着动
  }, [])

  const onNodeDrag = useCallback((_: unknown, n: Positioned) => {
    const sim = simRef.current
    if (!sim) return
    // React Flow 给的是外接盒左上角，模拟用的是盒子中心
    const w = n.size?.w ?? 0
    const h = n.size?.h ?? 0
    const cx = n.position.x + w / 2
    const cy = n.position.y + h / 2
    // **绝不把非有限值写进模拟。** `pin` 写的是 `fx/fy`，而 d3 每帧执行
    // `node.x = node.fx` —— 一个 NaN 会顺着连杆传给邻居、再传遍全图，
    // 表现是「一拖动节点，图就没了」，而且看不出是哪一步引入的。
    // 宁可这一帧不钉，也不能污染坐标。
    if (!Number.isFinite(cx) || !Number.isFinite(cy)) return
    const v = rf.getViewport()
    // `transform` 理论上总有值，但这里一旦取到 undefined 就是
    // `s.transform[0]` 抛 TypeError —— 那是渲染期崩溃，整个图会消失。
    const vp = { w: paneW, h: paneH, x: v?.x ?? 0, y: v?.y ?? 0, z: v?.zoom ?? 1 }
    sim.pin(n.id, ...clampToPane(cx, cy, w, h, vp))
  }, [rf, paneW, paneH])

  const onNodeDragStop = useCallback((_: unknown, n: Positioned) => {
    const sim = simRef.current
    if (!sim) return
    // 三个方法都用 `?.` 调。不是为了防御「不可能发生」，是为了防御**热更新**：
    // Vite 改 `layout.ts` 时，React 会保留组件状态（`simRef` 就是状态），
    // 于是 `simRef.current` 可能是一个**由旧版模块造出来的** handle ——
    // 新代码调 `reanchor` 就是 `undefined is not a function`，抛在
    // `onNodeDragStop` 里，React Flow 的拖拽收尾就此中断。
    // 静默少做一步（节点回弹）远好过抛异常。
    sim.reanchor?.(n.id)   // 锚点跟到落点 —— 少了这一步就是「松手回弹」
    sim.unpin?.(n.id)
    sim.cool?.()
  }, [])

  return { nodes, onNodeDragStart, onNodeDrag, onNodeDragStop }
}
