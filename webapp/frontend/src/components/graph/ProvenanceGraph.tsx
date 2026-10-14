/**
 * 协作执行溯源图（右）。
 *
 * 自上而下的 DAG。这个方向和左侧时间轴一致 —— 两张图与时间轴共享同一个
 * 「向下 = 时间前进」的读法，所以眼睛不需要在页面内切换方向约定。
 *
 * 节点上的 `→N` 是跨图引用的数量，也是这个系统论点的最小可视化单位：
 * 一个执行事件指向了它产生的推理节点。点它会同时点亮左边。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background, Controls, ReactFlow, ReactFlowProvider, useReactFlow, useStore,
  type NodeTypes, type EdgeTypes,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import type { GraphDeltaView, ProvenanceGraph as PGraph } from '../../types/protocol'
import {
  GraphNode, boxOf, nodeSizeOf, provToNodeData, shapeOf, type GraphFlowNode,
} from './nodes/GraphNode'
import { ProvEdge, type ProvFlowEdge } from './edges/ProvEdge'
import { centerOn, focusViewport, layout, neighborhood } from './layout'
import { useForceGraph } from './useForceGraph'
import { NodeTip } from './NodeTip'
import { useWorkbench } from '../../stores/workbench'
import type { Highlights } from '../../lib/linkage'
import { FIT_MIN_ZOOM } from '../../theme/graph'

const nodeTypes = { gnode: GraphNode } as unknown as NodeTypes
const edgeTypes = { prov: ProvEdge } as unknown as EdgeTypes

interface Props {
  graph: PGraph
  delta: GraphDeltaView | null
  highlights: Highlights
  pathOnly: boolean
  dimOthers: boolean
  selectedEventId: string | null
  onSelect: (eventId: string | null) => void
}

function ProvenanceGraphInner({
  graph, delta, highlights, pathOnly, dimOthers, selectedEventId, onSelect,
}: Props) {
  // 与推理图同构：本层只描述「有哪些节点、长什么样、盒子多大」，
  // 位置由活着的力模拟驱动（见 useForceGraph）。
  const structural = useMemo(() => {
    const nodes: (GraphFlowNode & { size: { w: number; h: number } })[] =
      graph.nodes.map(n => {
      const data = provToNodeData({
        kind: n.kind, kindLabel: n.kindLabel, label: n.label,
        labelEn: n.labelEn, actorLabel: n.actorLabel, actorIcon: n.actorIcon,
        actorRoleLabel: n.actorRoleLabel,
        step: n.step, causalCount: n.causalNodeRefs.length,
        anomalyRoot: n.anomalyRoot, downstream: n.downstreamOfAnomaly,
        statusLabel: n.statusLabel, timestamp: n.timestamp,
        eventHash: n.eventHash, io: n.io,
      })
      return {
        id: n.id,
        type: 'gnode' as const,
        position: { x: 0, y: 0 },
        data,
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
        size: { w: boxOf(data).w, h: boxOf(data).h },
        initialWidth: boxOf(data).w,
        initialHeight: boxOf(data).h,
        measured: { width: boxOf(data).w, height: boxOf(data).h },
      }
    })
    // 同 ReasoningGraph：边的裁剪要按两端的图形算。
    const shape = new Map<string, { r: number; diamond: boolean }>()
    for (const n of nodes) shape.set(n.id, shapeOf(n.data))

    const edges: ProvFlowEdge[] = graph.edges.map(e => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: 'prov' as const,
      data: {
        relation: e.relation,
        relationLabel: e.relationLabel,
        lit: false,
        dimmed: false,
        sourceShape: shape.get(e.source),
        targetShape: shape.get(e.target),
      },
    }))
    return { nodes, edges }
  }, [graph])

  const { nodes: placed, onNodeDragStart, onNodeDrag, onNodeDragStop } =
    useForceGraph(structural, 'TB', { nodesep: 12, ranksep: 54 }, nodeSizeOf)

  const nodes = useMemo(() => {
    // 执行图的「本步增量」不是节点增删（事件节点一开始就都在），
    // 而是「本步的图更新事件」。用它的 id 点亮那一个节点。
    const touched = new Set<string>()
    if (delta) touched.add(delta.eventId)

    const hasSelection = highlights.kind !== 'none'
    const visible = hasSelection ? highlights.provenance : null

    return placed.map(n => {
      const deltaState: 'added' | 'updated' | null =
        touched.has(n.id) ? 'added' : null
      const lit = visible ? visible.has(n.id) : false
      return {
        ...n,
        hidden: pathOnly && hasSelection && !lit,
        // 选中状态以本系统的 store 为唯一来源（见 ReasoningGraph 的同处注释）。
        selected: n.id === selectedEventId,
        data: {
          ...n.data,
          delta: deltaState,
          lit,
          dimmed: dimOthers && hasSelection && !lit,
        },
      }
    })
  }, [placed, delta, highlights, pathOnly, dimOthers, selectedEventId])

  const edges = useMemo(() => {
    const hasSelection = highlights.kind !== 'none'
    return structural.edges.map(e => {
      const lit = hasSelection && highlights.provenanceEdges.has(e.id)
      return {
        ...e,
        hidden: pathOnly && hasSelection && !lit,
        data: {
          ...e.data!,
          lit,
          dimmed: dimOthers && hasSelection && !lit,
        },
      }
    })
  }, [structural.edges, highlights, pathOnly, dimOthers])

  // **跟随当前步骤**：焦点 = 用户选中的事件，没有选中时是「本步的图更新事件」。
  // 37 个节点、依赖链深 15 层的溯源图整体不可读，所以镜头默认跟着当前这一步走，
  // 「当前事件在协作链的哪个位置」在演示当下就是最该回答的问题。
  //
  // 用 useReactFlow() 而不是 onInit 回调。这个版本的 @xyflow/react
  // 类型定义里**没有 `onInit`** —— 它不会报类型错（多传的 prop 被忽略），
  // 只是那个 ref 永远是 null，于是后续所有视口操作都静默地没有发生。
  // 「没报错但也没生效」正是最难查的一类。
  const rf = useReactFlow()
  // 画布尺寸取自 React Flow 自己测量并存进 store 的值，不用自己的 ref。
  // 自己包一层 div 去量会踩进「父级高度 auto ↔ 子级 height:100%」的循环，
  // 实测量出来是 190×0；而 React Flow 这个值一定是准的 —— 图能正常渲染
  // 本身就证明了它拿到了正确的尺寸。
  const paneW = useStore(s2 => s2.width)
  const paneH = useStore(s2 => s2.height)
  const focusId = selectedEventId ?? delta?.eventId ?? null
  // **依赖的是「有哪些节点 / 焦点是谁」，不是坐标** —— 理由同 ReasoningGraph：
  // 模拟每帧都会重建 nodes，把它放进依赖会让下面这个 effect 每帧重跑，
  // 而它内部的 `setTimeout(120ms)` 每次都被清掉，于是**一次都不执行**，
  // 画布停在 defaultViewport 上、图全在框外。
  const visibleKey = nodes.filter(x => !x.hidden).map(x => x.id).join(',')
  const nodesRef = useRef(nodes)
  nodesRef.current = nodes
  // dagre 的种子坐标（每个节点终将到达的位置）。视口按它算 —— 见下面
  // setTimeout 里的说明。按 `visibleKey` 记忆，避免每帧重跑 dagre。
  const seedsRef = useRef<typeof nodes | null>(null)
  seedsRef.current = useMemo(
    () => (paneW > 0 ? layout(nodes, graph.edges, 'TB', nodeSizeOf,
                              { nodesep: 12, ranksep: 54 }) : null),
    [visibleKey, paneW, graph.edges, nodes])

  // **自己算视口**，不调 React Flow 的 fitView —— 原因见 layout.focusViewport。
  //
  // 两种视图，分工明确（**先前这里是「框住焦点的一跳邻域」，那是错的**）：
  //
  //   有焦点 → **对准**焦点节点（`centerOn`）。先前用「适配邻域包围盒」，
  //     而执行图是按时间纵排的：一个事件的上下游可以横跨十几个 rank，
  //     包围盒比画布高得多，适配要么被缩放下限截住（内容溢出、顶端节点被
  //     削掉一半），要么缩到 0.2 倍。两者都不是「聚焦」。
  //   无焦点 → **装下整张图**（下限放到 0.14：重叠的根因已在上游修掉，
  //     缩小只是变小，不再是噪点，所以不需要靠隐藏标签来救）。
  useEffect(() => {
    if (paneW <= 0 || paneH <= 0) return
    const t = setTimeout(() => {
      const pane = { w: paneW, h: paneH }
      // **按 dagre 的种子算，不按当前坐标** —— 理由同 ReasoningGraph：
      // 模拟是活的，120ms 时新事件节点还在半路上，按那一刻的坐标适配会把
      // 它们算漏在画布外。
      const nodes = seedsRef.current ?? nodesRef.current
      const vp = focusId
        // 一跳邻域只用来决定**拉多远**，不用来决定镜头指向谁 ——
        // 指向始终是焦点本身（见 centerOn 的说明）。
        ? centerOn(nodes, focusId, nodeSizeOf, pane, 0.9,
                   neighborhood(focusId, graph.edges, 1))
        : focusViewport(nodes,
                        nodes.filter(x => !x.hidden).map(x => x.id),
                        nodeSizeOf, pane, 0.14, 0.14, FIT_MIN_ZOOM)
      if (vp) rf.setViewport(vp, { duration: 300 })
    }, 120)
    return () => clearTimeout(t)
  }, [visibleKey, focusId, rf, paneW, paneH])

  // ---- 悬停浮层的光标位置（同 ReasoningGraph 的说明）-----------------
  const hovered = useWorkbench(s2 => s2.hovered)
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
      // 并覆盖掉下面 effect 里的聚焦适配 —— 而聚焦是本图的默认视图，
      // 全图只是缩出去时的兜底。两个适配来源并存时，后跑的那个说了算，
      // 那种「谁赢」不该由时序决定。
      defaultViewport={{ x: 0, y: 0, zoom: 0.55 }}
      minZoom={0.15}
      maxZoom={1.8}
      proOptions={{ hideAttribution: true }}
      // 可自由拖动。松手后不弹回，节点随剩余能量松弛（见 useForceGraph）。
      nodesDraggable
      // **必须关掉自动平移**（同 ReasoningGraph）：React Flow 默认会在你把
      // 节点拖到画布边缘时自动平移画布，好让你拖到视野外。而我们把节点钉在
      // 锚点附近的半径内（`layout.REF.maxPull`）—— 节点先停住、光标继续走，
      // 于是它判定「用户想拖出去」并一路平移，**整张图被推出画布**。
      // 用户看到的就是「一拖节点就消失」。
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
export function ProvenanceGraph(props: Props) {
  return (
    <ReactFlowProvider>
      <ProvenanceGraphInner {...props} />
    </ReactFlowProvider>
  )
}
