/**
 * 双图共用的圆形节点。
 *
 * 形状设计对齐 `医疗溯因智能体/gos_repo/viz/story_template.html`：
 * 圆形、按类型填充、中心一个字母、标签折行放在圆下方。
 *
 * 为什么要用单字母而不是把类型名写进圆里：圆只有 40–60 像素，四个汉字
 * 塞不下，硬塞就要缩到读不清的字号 —— 那等于没有信息。字母 + 顶部图例
 * 的组合，读者看一眼图例就能解码，而且节点本身保持干净。
 *
 * 三种修饰各自承担一件事，互不抢：
 *   **等级徽章**（圆上方）  这个节点有多可信
 *   **P3 虚线圈**（圆周围） 这个节点是无来源断言 —— 是异常，要跳出来
 *   **增量徽章**（标签下方）这一步它变了多少
 *
 * 把三者都做成颜色，就没有任何一种是看得清的。
 */
import {
  Handle, Position, type Node, type NodeProps,
} from '@xyflow/react'

import {
  DIAMOND_KINDS, LABEL_GAP, LINE_H, nodeBox, provStyle,
  reasoningStyle, wrapLabel,
} from '../../../theme/graph'
import { useWorkbench } from '../../../stores/workbench'

// ------------------------------------------------------------------ 数据
/** 悬停浮层里的一行「标签 : 值」。 */
export interface TipRow {
  k: string
  v: string
  /** 值的语气：正常 / 通过 / 警示 / 弱化 / 等宽。 */
  tone?: 'ok' | 'warn' | 'dim' | 'mono'
}

/**
 * 悬停浮层的内容。**在派生节点数据时算好**，浮层组件只负责画。
 *
 * 为什么是一份结构化数据而不是拼接好的字符串：以前的 `title` 就是拼好的，
 * 于是它只能当原生提示用（延迟高、样式不可控、没法给某一行单独上色）。
 * 分行之后，「哪一行是判定依据、哪一行是哈希」这件事就落在数据里，
 * 而不是落在字符串的换行符里。
 */
export interface TipData {
  head: string
  badge?: string | null
  badgeTone?: 't-p0' | 't-p1' | 't-p2' | 't-p3'
  rows: TipRow[]
  note?: string
}

export interface NodeData extends Record<string, unknown> {
  /** 折行后的中文标签。在派生时算好，避免每次渲染重算。 */
  lines: string[]
  label: string
  labelEn: string
  color: string
  radius: number
  letter: string
  diamond: boolean
  /** 等级（P0–P3），仅推理图。 */
  tierShort: string | null
  tierLabel: string | null
  /** 是异常根因 / 无来源断言。 */
  alarm: boolean
  /** 顶部小徽章：执行图的智能体头像，或推理图的类型名。 */
  topBadge: string
  /** 增量状态。 */
  delta: 'added' | 'updated' | 'removed' | null
  /** 被当前选区关联到 —— 高亮。 */
  lit: boolean
  /** 有选区但与本节点无关 —— 压暗。 */
  dimmed: boolean
  /** 当前推理 frontier 或 backtrack 前的焦点。 */
  frontierState: 'active' | 'backtrack-from' | null
  /** 跨图引用数（执行图）。 */
  causalCount: number
  /** 节点分数；Hypothesis 用于图心，其余类型的静态分数只在详情中显示。 */
  score: number | null
  /** 相对**上一步**的变化量。null = 没有可比的前值（刚出现）；0 = 持平。 */
  confDelta: number | null
  /**
   * 分层锚点用的**语义列**键：`Signal` / `Evidence` / `H1` / `H2` / … /
   * `Knowledge`。空串 = 不分列（执行溯源图走 dagre 的拓扑分层）。
   *
   * 在这里算而不是在布局里算，是因为它需要 `nodeType` 与 `confidenceLevel`
   * 两个协议字段 —— 那两样只有派生节点数据的地方才有。
   */
  column: string
  /** 悬停浮层的内容（取代原先的原生 `title`）。 */
  tip: TipData
}

export type GraphFlowNode = Node<NodeData, 'gnode'>

/** 计算节点外接盒。布局与组件渲染都走它，两处尺寸因此不可能不同步。 */
export function boxOf(d: Pick<NodeData, 'lines' | 'radius' | 'diamond'>) {
  return nodeBox(d.lines, d.radius, d.diamond)
}

/** 给 dagre 用的尺寸函数（它拿到的是整个节点，不只是 data）。 */
export const nodeSizeOf = (n: GraphFlowNode) => boxOf(n.data)

/**
 * 图形中心在**外接盒坐标**里的纵坐标。横坐标永远是 `box.w / 2`。
 *
 * `.gnode` 是 `flex-direction: column; align-items: center`，图形是第一个
 * 子元素 —— 所以它水平居中、贴着盒顶：
 *
 *   圆：盒是 `r*2` 见方，圆心在 `r`；
 *   菱形：盒是 `r*1.7` 见方再转 45°，中心在 `r*1.7/2 = r*0.85`。
 *
 * 这个值只有一个用处：**把 Handle 摆到图形中心**，好让边的方向从中心连线
 * 算出来（而不是从外接盒边缘 —— 那个盒子含了圆下方的标签，线会从标签底下
 * 出发，正是先前看着不对的原因）。
 */
export function shapeCenterY(d: Pick<NodeData, 'radius' | 'diamond'>): number {
  return d.diamond ? d.radius * 0.85 : d.radius
}

/** 图形的形状描述，交给边的几何用（见 edges/geometry.ts）。 */
export function shapeOf(d: Pick<NodeData, 'radius' | 'diamond'>): {
  r: number; diamond: boolean
} {
  return { r: d.radius, diamond: d.diamond }
}

// ------------------------------------------------------------------ 组件
export function GraphNode({ id, data, selected }: NodeProps<GraphFlowNode>) {
  const setHovered = useWorkbench(s => s.setHovered)
  const r = data.radius
  const lines = data.lines
  // 容器宽度取外接盒宽度（与 dagre 用的是同一个函数）。不设宽度的话，
  // 折行后的标签会按内容自然宽度排，与 dagre 假想的盒子对不上，节点
  // 之间就会看起来疏密不均。
  const box = boxOf(data)

  // 圆心显示假设置信度；Signal / Evidence 即使有参考 score，仍显示 S / E。
  const scoreText = data.column.startsWith('H') && data.score !== null
    ? data.score.toFixed(2) : null
  // 变化徽标只在**真的变了**时才出现 —— 与参考项目一致（|Δ| < 1e-6 不显示）。
  // 「没变」是常态，常驻一个 ▲ +0.00 会把真正在动的那些淹掉。
  const showDelta = data.confDelta !== null && Math.abs(data.confDelta) > 1e-6

  const cls = [
    'gnode',
    data.diamond ? 'diamond' : '',
    data.delta ? `delta-${data.delta}` : '',
    data.lit ? 'lit' : '',
    data.dimmed ? 'dimmed' : '',
    data.frontierState ? `frontier-${data.frontierState}` : '',
    data.alarm ? 'alarm' : '',
    selected ? 'selected' : '',
  ].filter(Boolean).join(' ')

  // 悬停**不改选中**，只写 store 里的 hovered —— 与既有的
  // 「悬停是临时预览、选中是用户意图」的约定一致。浮层本身不挂在节点里，
  // 由画布层的 NodeTip 统一画（原因见 NodeTip.tsx）。
  return (
    <div className={cls} style={{ width: box.w }}
         onMouseEnter={() => setHovered(id)}
         onMouseLeave={() => setHovered(null)}>
      {/* Handle 摆在**图形中心**，不是外接盒边缘。边的方向与裁剪都以它
          为起点算（见 edges/geometry.ts）—— 参考项目就是这么做的。
          `translate(-50%,-50%)` 让 Handle 自己以该点为中心。 */}
      <Handle type="target" position={Position.Top} className="gnode-handle"
              style={{ left: box.w / 2, top: shapeCenterY(data),
                       transform: 'translate(-50%,-50%)' }} />

      <div className={`gnode-shape ${data.diamond ? 'is-diamond' : ''}
                       ${showDelta ? (data.confDelta! > 0 ? 'conf-up' : 'conf-down') : ''}`}
           style={{
             width: data.diamond ? r * 1.7 : r * 2,
             height: data.diamond ? r * 1.7 : r * 2,
             background: data.color,
           }}>
        {/* 圆心显示两样东西：置信度读数（假设节点）与它相对上一步的变化。
            参考项目也是这个分工 —— 角标给数、下方给 Δ、变化时圆闪一下。

            与参考项目的一处**有意差别**：它把 Δ 摆在圆**外面**的下方
            （`dy 36`），我们摆在圆内。因为圆外的位置会与标签抢地盘：
            标签只隔 5px（`LABEL_GAP`），而节点外接盒的高度是布局与分离力
            共用的 —— 让 Δ 参与布局会使盒高随步骤变化，整张图在时间轴上
            一 scrubbing 就抖。摆在圆内则完全不影响盒高。

            另一处：非假设节点仍然显示类型字母（S/E/H/C）。参考项目把它们的
            角标 `fill` 设成 `transparent`，等于不显示；我们保留字母，是因为
            双图并列时「这个圆是什么」比参考项目更重要（见本文件顶部说明）。 */}
        <span className={scoreText !== null ? 'gnode-center' : 'gnode-center plain'}>
          <span className={scoreText !== null ? 'gnode-letter is-score' : 'gnode-letter'}>
            {scoreText ?? data.letter}
          </span>
          {showDelta ? (
            <span className="gnode-confdelta">
              {data.confDelta! > 0 ? '▲ +' : '▼ '}
              {Math.abs(data.confDelta!).toFixed(2)}
            </span>
          ) : null}
        </span>

        {/* 三个浮层都挂在圆上，不占布局高度。9 个节点一排时，
            每个浮层占 16px 就是 144px —— 那正是把图压到读不清的量。 */}
        {data.tierShort ? (
          <span className={`gnode-tier t-${data.tierShort.toLowerCase()}`}
                title={data.tierLabel ?? ''}>
            {data.tierShort}
          </span>
        ) : null}

        {data.topBadge ? <span className="gnode-topbadge">{data.topBadge}</span> : null}

        {data.frontierState ? (
          <span className={`gnode-frontier-tag ${data.frontierState}`}>
            {data.frontierState === 'active' ? '当前焦点' : '回溯自'}
          </span>
        ) : null}

        {data.causalCount > 0 ? (
          <span className="gnode-causal"
                title={`关联 ${data.causalCount} 个推理节点`}>
            →{data.causalCount}
          </span>
        ) : null}
      </div>

      <div className="gnode-label" style={{ marginTop: LABEL_GAP }}>
        {lines.map((l, i) => (
          <div key={i} style={{ height: LINE_H, lineHeight: `${LINE_H}px` }}>{l}</div>
        ))}
      </div>

      <Handle type="source" position={Position.Bottom} className="gnode-handle"
              style={{ left: box.w / 2, top: shapeCenterY(data),
                       transform: 'translate(-50%,-50%)' }} />
    </div>
  )
}

// ------------------------------------------------------------------ 构造
/** 推理图节点 → 通用节点数据。 */
/** 置信度读数的展示行。没有读数 ≠ 读数是 0，两者必须分开。 */
function beliefRow(
  score: number | null, delta: number | null, level: number | null,
  supports: number | null, refutes: number | null,
): TipRow {
  if (score === null) {
    // 「暂无读数」而不是 0.00：深挖产生但从未成为规划焦点的假设，它的分数
    // 没有落在任何产物里。写成 0.00 会被读成「置信度为零」。
    return { k: '置信度', v: '暂无读数', tone: 'dim' }
  }
  let v = score.toFixed(2)
  if (delta !== null && Math.abs(delta) > 1e-9) {
    // 箭头带符号，数字不带 —— 与参考项目的 deltaText 同形
    v += delta > 0 ? `  ▲ +${delta.toFixed(2)}` : `  ▼ ${Math.abs(delta).toFixed(2)}`
  }
  if (level !== null) v += `（L${level}）`
  if (supports !== null) v += `  支持 ${supports} · 反驳 ${refutes ?? 0}`
  return { k: '置信度', v, tone: delta === null || delta === 0 ? undefined
    : (delta > 0 ? 'ok' : 'warn') }
}

export function reasoningToNodeData(n: {
  nodeId: string; label: string; labelEn: string; typeLabel: string
  nodeType: string; tier: string | null; tierLabel: string | null
  tierDesc: string | null
  usedLabel: string | null; matchCoverage: number | null
  matchCoverageLabel: string
  matchNote: string | null
  confidence: number | null; confidenceDelta: number | null
  confidenceLevel: number | null
  beliefSupports: number | null; beliefRefutes: number | null
  why: string | null
  confidenceEventId: string | null
  createdByEvent: string | null; sourceClassLabel: string | null
  semanticKindLabel: string | null
}) {
  const st = reasoningStyle(n.nodeType)
  const isKnowledge = n.nodeType === 'Knowledge'
  // 78px ≈ 7 个汉字一行，与参考项目的 14 单位一致（换算见 wrapLabel 说明）
  const lines = wrapLabel(n.label, 78, 3)
  const tierShort = n.tierLabel ? n.tierLabel.slice(0, 2) : null

  const rows: TipRow[] = []
  if (n.labelEn && n.labelEn !== n.label) {
    rows.push({ k: '原文', v: n.labelEn, tone: 'dim' })
  }
  // 这一步的读数理由。**放在置信度那一行下面**，因为它回答的就是
  // 「为什么是这个数」；排到匹配度、创建事件之后就会被读成另一件事。
  if (n.why) rows.push({ k: '理由', v: n.why })
  rows.push(beliefRow(n.confidence, n.confidenceDelta, n.confidenceLevel,
                      n.beliefSupports, n.beliefRefutes))
  // 匹配度只在真有记录可比对时给数；否则给一句「为什么没有数」。
  // 这两者绝不能用同一个 0.00 表达（见 protocol.ts 的 matchNote 说明）。
  if (n.matchCoverage !== null) {
    rows.push({
      k: n.matchCoverageLabel,
      v: n.matchCoverage.toFixed(2),
      // 0.35 是审计的软匹配阈值（见 audit.DEFAULT_CFG）。低于它就意味着
      // 这条证据撑不住 —— 那正是「图上有一条支撑边、审计说它不算」的地方。
      tone: n.matchCoverage >= 0.35 ? 'ok' : 'warn',
    })
  } else if (n.matchNote) {
    rows.push({ k: n.matchCoverageLabel, v: n.matchNote, tone: 'dim' })
  }
  if (n.createdByEvent) rows.push({ k: '创建事件', v: n.createdByEvent, tone: 'mono' })
  if (n.sourceClassLabel) rows.push({ k: '来源类', v: n.sourceClassLabel })
  if (n.semanticKindLabel) rows.push({ k: '语义类', v: n.semanticKindLabel })
  if (n.usedLabel) rows.push({ k: '参与推理', v: n.usedLabel })

  return {
    lines,
    label: n.label,
    labelEn: n.labelEn,
    color: st.color,
    radius: st.radius,
    letter: st.letter,
    // 知识节点画成菱形（参考项目同款），其余是圆。
    diamond: isKnowledge,
    tierShort,
    tierLabel: n.tierLabel,
    alarm: n.tier?.startsWith('P3') ?? false,
    topBadge: n.typeLabel,
    delta: null as NodeData['delta'],
    lit: false,
    dimmed: false,
    frontierState: null,
    causalCount: 0,
    score: n.confidence,
    confDelta: n.confidenceDelta,
    // 语义列：假设按层分列，其余按类型分列。布局用它播种（见 columnSeedsOf）。
    column: n.nodeType === 'Hypothesis'
      ? `H${n.confidenceLevel ?? 1}` : n.nodeType,
    tip: {
      // 参考项目的表头是「类型 [L1]」；`nodeId` 对它之外的读者没有含义，
      // 而层级是读图时真正要抓的信息。
      head: `${n.typeLabel}${n.confidenceLevel ? ` [L${n.confidenceLevel}]` : ''}`,
      badge: tierShort,
      badgeTone: n.tier
        ? (`t-${n.tier.slice(0, 2).toLowerCase()}` as TipData['badgeTone'])
        : undefined,
      rows,
      note: n.tierDesc ?? undefined,
    },
  }
}

/** 执行图节点 → 通用节点数据。 */
export function provToNodeData(n: {
  kind: string; kindLabel: string; label: string; labelEn: string
  actorLabel: string | null; actorIcon: string | null
  actorRoleLabel?: string | null
  step: number | null; causalCount: number
  anomalyRoot: boolean; downstream: boolean
  statusLabel?: string | null
  timestamp?: string | null
  eventHash?: string | null
  io?: { input: string; output: string }
}) {
  const st = provStyle(n.kind)
  const lines = wrapLabel(n.label, 84, 2)

  const rows: TipRow[] = []
  if (n.actorLabel) {
    rows.push({ k: '执行', v: n.actorLabel + (n.actorRoleLabel ? `（${n.actorRoleLabel}）` : '') })
  }
  if (n.step !== null) rows.push({ k: '规划轮次', v: String(n.step) })
  if (n.statusLabel) rows.push({ k: '状态', v: n.statusLabel })
  if (n.io?.input) rows.push({ k: '输入', v: n.io.input })
  if (n.io?.output) rows.push({ k: '输出', v: n.io.output })
  if (n.timestamp) rows.push({ k: '时间', v: n.timestamp, tone: 'mono' })
  if (n.eventHash) rows.push({ k: '事件哈希', v: n.eventHash.slice(0, 16) + '…', tone: 'mono' })
  if (n.causalCount > 0) {
    rows.push({ k: '跨图引用', v: `${n.causalCount} 个推理节点`, tone: 'ok' })
  }

  return {
    lines,
    label: n.label,
    labelEn: n.labelEn,
    color: st.color,
    radius: st.radius,
    letter: st.letter,
    diamond: DIAMOND_KINDS.has(n.kind),
    // 执行图不分语义列：它的分层是时间顺序，dagre 的拓扑分层才是对的锚。
    column: '',
    tierShort: null,
    tierLabel: null,
    alarm: n.anomalyRoot,
    topBadge: n.actorIcon ?? '',
    delta: null as NodeData['delta'],
    lit: false,
    dimmed: false,
    frontierState: null,
    causalCount: n.causalCount,
    // 执行图没有置信度这回事 —— 那是推理图的概念，不要在这里凭空造一个。
    score: null,
    confDelta: null,
    tip: {
      head: n.kindLabel + (n.step !== null ? ` · 轮次 ${n.step}` : ''),
      badge: n.anomalyRoot ? '异常根因' : null,
      badgeTone: n.anomalyRoot ? ('t-p3' as const) : undefined,
      rows,
      note: n.anomalyRoot
        ? '可观测故障起点'
        : (n.downstream ? '位于异常影响链上' : undefined),
    },
  }
}
