/**
 * 医学因果推理图（左）。
 *
 * **布局与高亮分离。** dagre 只在图结构变化时重算，高亮只改节点 data 上
 * 的 lit/dimmed 标记。把两者合在一起（在同一个 useMemo 里算）会让每次
 * 点击节点都触发一次全图重排 —— 图会「跳」一下，节点跑到别的位置。
 * 那不是动画，是布局抖动，而它恰好发生在用户点击的瞬间，最容易被误读
 * 成「点这里会让图变化」。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background, Controls, ReactFlow, ReactFlowProvider, useReactFlow, useStore,
  type NodeTypes, type EdgeTypes,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import type { GraphDeltaView, ReasoningGraph as RGraph } from '../../types/protocol'
import {
  GraphNode, boxOf, nodeSizeOf, reasoningToNodeData, shapeOf, type GraphFlowNode,
} from './nodes/GraphNode'
import { centerOn, columnSeedsOf, focusViewport, neighborhood } from './layout'
import { useForceGraph, type AnchorOf } from './useForceGraph'
import { NodeTip } from './NodeTip'
import { useWorkbench } from '../../stores/workbench'
import { reasoningEdgeId } from '../../lib/linkage'
import type { Highlights } from '../../lib/linkage'
import { RelationEdge, type RelationFlowEdge } from './edges/RelationEdge'

const nodeTypes = { gnode: GraphNode } as unknown as NodeTypes
const edgeTypes = { relation: RelationEdge } as unknown as EdgeTypes

/** 结构层的节点：节点数据 + 我们提前算好的盒子尺寸。 */
type ReasoningFlowNode = GraphFlowNode & { size: { w: number; h: number } }

interface Props {
  graph: RGraph
  delta: GraphDeltaView | null
  highlights: Highlights
  pathOnly: boolean
  dimOthers: boolean
  selectedNodeId: string | null
  onSelect: (nodeId: string | null) => void
  /**
   * 全帧节点并集的加入顺序（从 `deltas[].addedNodes` 拼出）。
   * 列式锚点靠它让同一列内的节点在整段回放中位置稳定 —— 见
   * `layout.columnSeedsOf`。不给就退化成「按当前步的顺序」。
   */
  seedOrder?: readonly string[]
}

function ReasoningGraphInner({
  graph, delta, highlights, pathOnly, dimOthers, selectedNodeId, onSelect,
  seedOrder,
}: Props) {
  // ---- 锚点：语义列 ------------------------------------------------
  // 参考项目的因果图锚的是**语义列**（Signal / Evidence / H1 / H2 /
  // Knowledge），不是 dagre 的拓扑分层 —— 见 `layout.columnSeedsOf` 的说明。
  //
  // `seedOrder` 是**全帧节点并集的加入顺序**（从 deltas 拼出来）。参考用它
  // 定序，好让同一列里的节点在整段回放中位置不变；我们的接口按步取图，
  // 拿不到未来节点，只有顺序（不含标签与分数）。
  //
  // 依赖 `seedOrder`：换病例时顺序变了要重新播种。节点增删本身会触发
  // effect（`snodes` 在依赖里），这里不必再写一遍。
  const anchor = useCallback<AnchorOf<ReasoningFlowNode>>(
    (ns, sizeOf) => columnSeedsOf(
      ns, sizeOf,
      n => n.data.column,
      seedOrder ?? [],
      // 列间距按**最宽的一行标签**留：标签是横向溢出的那一侧
      // （圆只有 50 来像素，标签能到 80+），列挨太近时是标签先撞上，
      // 而碰撞力会把节点推开 —— 表现成「列歪了」，不像是间距不够。
      // 行间距同理，按标签高度（最多 3 行）留：168 > 任何盒高（≤118），
      // 所以固定行距不会让相邻两行的盒子压上。
      { columnGap: 150, columnWidth: 80, rowGap: 168, nominalRows: 4 },
    ),
    [seedOrder],
  )
  // ---- 结构层：只在节点/边的集合变化时重建 --------------------------
  // 这里**不再跑力模拟**。力模拟是活的（见 useForceGraph），交给它驱动位置；
  // 本层只负责「有哪些节点、每个长什么样、盒子多大」。
  const structural = useMemo(() => {
    const nodes: ReasoningFlowNode[] =
      graph.nodes.map(n => {
        const data = reasoningToNodeData(n)
        const node = {
          id: n.id,
          type: 'gnode' as const,
          position: { x: 0, y: 0 },
          data,
        }
        // **`measured` 必须给，`initialWidth`/`initialHeight` 也一起给。**
        //
        // 模拟活着时我们每帧都交出一个**新对象**（`{...n, position}`），而
        // React Flow 在 `adoptUserNodes` 里只从**这个对象**上读已测量状态：
        //
        //   `measured: { width: userNode.measured?.width, ... }`
        //
        // 我们的对象上没有 `measured`，于是它每帧被重置成 undefined。这一个
        // 字段撑起两件事，而且**坏的顺序不同**，所以分两次才被发现：
        //
        //   `visibility: hasDimensions ? 'visible' : 'hidden'`
        //     —— 它读 `measured ?? width ?? initialWidth`，所以 `initialWidth`
        //        能救。先前只给 `initial*` 时，节点是可见的。
        //   `parseHandles(userNode, internalNode)`
        //     —— `return !userNode.measured ? undefined : 上一次的 handleBounds`
        //        它只认 `measured`。没有就把 handleBounds 清空 → 节点变成
        //        「未初始化」→ `getEdgePosition` 返回 null → **所有连到它的边
        //        整个不渲染**（不是画不出来，是元素都不生成）。
        //
        // 于是症状是：拖动时节点还在、**边全没了**，松手约 2 秒后一起回来。
        //
        // 我们**本来就精确知道每个盒子多大** —— `boxOf` 是布局、碰撞力、渲染
        // 共用的同一个真源（`check-rendered.mjs` 逐次核对过渲染值与它一致）。
        // 交给 React Flow，它就不必先量再显示，也不会因为重建对象而把状态清掉。
        // `initial*` 只在首次测量前当内联尺寸用，`measured` 是要一直留着的那个。
        const box = boxOf(data)
        return {
          ...node,
          size: { w: box.w, h: box.h },
          initialWidth: box.w,
          initialHeight: box.h,
          measured: { width: box.w, height: box.h },
        }
      })
    // 边的几何要按两端的**图形**裁剪，所以先把每个节点的形状查出来
    // （见 edges/geometry.ts —— 参考项目是从中心连线算出裁剪点的）。
    const shape = new Map<string, { r: number; diamond: boolean }>()
    for (const n of nodes) shape.set(n.id, shapeOf(n.data))
    // 知识边单独一色。**从源节点的类型推**，不新增协议字段 ——
    // 参考项目的 `isKbEdge` 也是这么做的（`e.kb || 源节点是 Knowledge`）。
    const isKb = new Set(graph.nodes
      .filter(n => n.nodeType === 'Knowledge').map(n => n.id))

    const edges: RelationFlowEdge[] = graph.edges.map(e => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: 'relation' as const,
      data: {
        relation: e.relation,
        relationLabel: e.relationLabel,
        lit: false,
        dimmed: false,
        kb: isKb.has(e.source),
        sourceShape: shape.get(e.source),
        targetShape: shape.get(e.target),
      },
    }))
    return { nodes, edges }
  }, [graph])

  // 活着的力模拟驱动位置；拖动三个回调与参考项目的 d3.drag 一一对应。
  const { nodes: placed, onNodeDragStart, onNodeDrag, onNodeDragStop } =
    useForceGraph(structural, 'LR', { nodesep: 22, ranksep: 96 }, nodeSizeOf,
                  anchor)

  // ---- 视觉层：本步增量 + 选区高亮 ---------------------------------
  const nodes = useMemo(() => {
    const added = new Set(delta?.addedNodes ?? [])
    const updated = new Set(delta?.updatedNodes ?? [])
    const removed = new Set(delta?.removedNodes ?? [])
    const hasSelection = highlights.kind !== 'none'
    const visible = hasSelection ? highlights.reasoning : null

    return placed.map(n => {
      const deltaState: 'added' | 'updated' | 'removed' | null =
        added.has(n.id) ? 'added'
          : updated.has(n.id) ? 'updated'
            : removed.has(n.id) ? 'removed' : null
      const lit = visible ? visible.has(n.id) : false
      // pathOnly 时未命中的节点由 React Flow 的 hidden 处理，这里只负责压暗。
      const dimmed = dimOthers && hasSelection && !lit
      const frontierState: 'active' | 'backtrack-from' | null =
        n.id === delta?.focusNodeId ? 'active'
        : n.id === delta?.backtrackFromNodeId ? 'backtrack-from' : null
      return {
        ...n,
        hidden: pathOnly && hasSelection && !lit,
        // 选中状态由**本系统的 store** 决定，不用 React Flow 的内部选中
        // （所以下面关了 elementsSelectable）。两个来源会各自维护一份
        // 「谁被选中」，而它们在一个操作里不会同时更新 —— 结果是先点 A
        // 再点 B 时，A 可能仍然显示为选中。
        selected: n.id === selectedNodeId,
        data: { ...n.data, delta: deltaState, lit, dimmed, frontierState },
      }
    })
  }, [placed, delta, highlights, pathOnly, dimOthers, selectedNodeId])

  const edges = useMemo(() => {
    const added = new Set(
      (delta?.addedEdges ?? []).map(
        e => reasoningEdgeId(e.source, e.relation, e.target)))
    const hasSelection = highlights.kind !== 'none'
    return structural.edges.map(e => {
      const lit = hasSelection && highlights.reasoningEdges.has(e.id)
      return {
        ...e,
        hidden: pathOnly && hasSelection && !lit,
        data: {
          ...e.data!,
          lit,
          dimmed: dimOthers && hasSelection && !lit,
          fresh: added.has(e.id),
        },
      }
    })
  }, [structural.edges, delta, highlights, pathOnly, dimOthers])

  // ---- 视口 ---------------------------------------------------------
  // 没有选中时适配**整张图**；选中了就**对准**那个节点。两种视图对应两个
  // 问题：「这张图长什么样」和「这个节点在哪、它周围是什么」。
  //
  // 用 `focusViewport` 自己算，不用 React Flow 的 fitView（原因见
  // layout.focusViewport）。**这里踩过一个正好相反的坑**：先前用的是
  // `fitView({padding: 0.16, minZoom: 0.6})`，本意是「别缩到读不清」，
  // 实际效果是——图一旦装不进 0.6 倍，fitView 就**不再缩小**，于是内容
  // 溢出画布被裁掉：截图里顶部的起始信号只剩半个圆，底部一排 E 节点齐刷刷
  // 断在边缘。它不报错，只是把「装不下」呈现成了「被裁掉」。
  // 这里反过来：适配的**下限**低到总能装下（0.2），读不清不靠隐藏标签来回避
  // （重叠的根因已经在上游修掉，缩小只是变小，不是噪点）。
  const rf = useReactFlow()
  // 画布尺寸取自 React Flow 自己测量的值（同 ProvenanceGraph 的注释）。
  const paneW = useStore(s => s.width)
  const paneH = useStore(s => s.height)
  // **依赖的是「有哪些节点」和「选中谁」，不是坐标。**
  // 模拟每跑一帧都会重建 nodes（坐标变了），把 nodes 放进依赖会让下面这个
  // effect 每帧重跑一次 —— 而它内部是 `setTimeout(120ms)`：每次重跑都清掉
  // 上一个定时器，于是它**永远等不到 120ms**，一次都不执行。表现是画布一直
  // 停在 defaultViewport 上，图全在框外（实测两张图都是空的）。
  // 另外，拖动时也不该重新适配视口 —— 那是用户在挪东西，不是图变了。
  const visibleKey = nodes.filter(x => !x.hidden).map(x => x.id).join(',')
  // 视口要用最新坐标，但不能依赖它：坐标从 ref 里读。
  const nodesRef = useRef(nodes)
  nodesRef.current = nodes
  useEffect(() => {
    if (paneW <= 0 || paneH <= 0) return
    const t = setTimeout(() => {
      const pane = { w: paneW, h: paneH }
      const live = nodesRef.current
      // **视口按锚点算，不按当前坐标。** 这一个字之差是一个实测到的缺陷：
      // 模拟是活的（`useForceGraph`），新节点从上一帧的位置滑向锚点要几秒；
      // 120ms 时它们还在半路上，按那一刻的坐标适配就把**刚加入的节点算漏在
      // 画布外** —— 第 4 步的两条紫色知识边指向画布右侧看不见的地方，
      // 而它们正是这一步的主角。锚点是它们终将到达的位置，按下标算才对。
      const s = anchor(live, nodeSizeOf)
      const nodes = s
        ? live.map((n, i) => ({
            ...n,
            position: { x: s.cx[i] - s.sizes[i].w / 2,
                        y: s.cy[i] - s.sizes[i].h / 2 },
          }))
        : live
      const vp = selectedNodeId
        // 选中 → **对准**它，但只拉到「它和它的一跳邻居都进得来」为止。
        //
        // 这里原本写的是「0.95 倍，不要邻域」，理由是「邻域包围盒可能横跨
        // 整张图，适配出来要么被截住要么缩到读不清」。那个理由是**错位的**：
        // 它说的是「框住邻域」，而 `centerOn` 的 `fitIds` 做的是另一件事 ——
        // **中心仍然是焦点**，`fitIds` 只决定**拉多远**，而且有 0.3 的地板。
        //
        // 错位一直没被发现，是因为这个 effect 当时**从来没有执行过**
        // （依赖 `nodes` 被每帧推迟，见上）。effect 修好之后，0.95 第一次
        // 真的生效了 —— 而我们的图比参考项目大得多，0.95 倍下画布里**只剩
        // 被选中的那一个节点**。任何一次点击（拖动收尾也常常算点击）都会让
        // 整张图看起来「消失」。同 ProvenanceGraph 的写法。
        ? centerOn(nodes, selectedNodeId, nodeSizeOf, pane, 0.95,
                   neighborhood(selectedNodeId, graph.edges, 1))
        : focusViewport(nodes,
                        nodes.filter(x => !x.hidden).map(x => x.id),
                        nodeSizeOf, pane, 0.16, 0.2, 1.2)
      if (vp) rf.setViewport(vp, { duration: 280 })
    }, 120)
    return () => clearTimeout(t)
    // `anchor` 不进依赖：它只随 seedOrder（换病例）变，而那时 visibleKey
    // 也变了。放进去会在每次渲染时清掉定时器，effect 一次都执行不到。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleKey, selectedNodeId, rf, paneW, paneH])

  // ---- 悬停浮层的光标位置 -------------------------------------------
  // 在画布层跟踪，而不是在每个节点里跟踪：节点有几十个，给每个都挂一个
  // mousemove 是几十个监听器做同一件事；而且节点被 React Flow 的 transform
  // 包着，从节点里拿到的坐标还要逆变换回画布坐标。
  const hovered = useWorkbench(s => s.hovered)
  const [at, setAt] = useState<{ x: number; y: number } | null>(null)
  const hostRef = useRef<HTMLDivElement | null>(null)
  const onMove = useCallback((e: React.MouseEvent) => {
    const r = hostRef.current?.getBoundingClientRect()
    if (!r) return
    setAt({ x: e.clientX - r.left, y: e.clientY - r.top })
  }, [])

  const onNodeClick = useCallback(
    (_: unknown, node: { id: string }) => onSelect(node.id), [onSelect])

  return (
    <div className="rf-host" ref={hostRef} onMouseMove={onMove}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodeClick={onNodeClick}
        onNodeDragStart={onNodeDragStart}
        onNodeDrag={onNodeDrag}
        onNodeDragStop={onNodeDragStop}
        onPaneClick={() => onSelect(null)}
        // 刻意**不设 fitView prop**：它会在节点测量完成后自动跑一次全图适配，
        // 覆盖掉下面 effect 的结果 —— 两个适配来源并存时，后跑的那个说了算，
        // 而「谁后跑」不该决定用户看到什么。（同 ProvenanceGraph。）
        defaultViewport={{ x: 0, y: 0, zoom: 0.6 }}
        minZoom={0.15}
        maxZoom={1.8}
        proOptions={{ hideAttribution: true }}
        // 可自由拖动。松手后**不弹回** —— 节点随剩余能量松弛到新的
        // 平衡，整张图在拖动期间一起响应。见 useForceGraph。
        nodesDraggable
        // **必须关掉自动平移。** React Flow 默认会在你「把节点拖到画布边缘」
        // 时自动平移画布，好让你拖到视野外。而我们把节点钉在锚点附近的
        // 半径内（见 layout.REF.maxPull）—— 节点先停住、光标继续走，于是它
        // 判定「用户想拖出去」并开始平移，**镜头一路跟着光标跑，整张图被
        // 推出画布**。用户看到的就是「一拖节点就消失」。
        // 实测：节点流坐标只挪了 ~130px 就停住，视口却从 194px 平移到 -436px。
        autoPanOnNodeDrag={false}
        nodesConnectable={false}
        elementsSelectable={false}
      >
        <Background gap={20} size={1} color="#e2e8f0" />
        <Controls showInteractive={false} />
      </ReactFlow>
      <NodeTip hovered={hovered} nodes={nodes} at={at}
               pane={{ w: paneW, h: paneH }} />
    </div>
  )
}

/**
 * 外层只做一件事：提供 `ReactFlowProvider`。
 *
 * `useReactFlow()` 必须在 Provider 内部调用，而 Provider 又必须包住
 * `<ReactFlow>` —— 所以「用 hook 拿实例」和「渲染 ReactFlow」不能在同一个
 * 组件里。拆一层是最小代价。
 */
export function ReasoningGraph(props: Props) {
  return (
    <ReactFlowProvider>
      <ReasoningGraphInner {...props} />
    </ReactFlowProvider>
  )
}
