/**
 * 图的视觉语言：节点形状/配色、边的线型、以及中文标签的折行。
 *
 * 折行函数是这一层的核心。中英文的显示宽度不同（一个汉字约占两个字母的
 * 宽度），按字符数折行会让「视网膜静脉阻塞」和「OCT」占的地方差一倍，
 * 结果是同一张图上的标签长短参差。按**显示宽度**折行（中文=2、ASCII=1）
 * 才让每条标签的视觉长度可预期。
 *
 * 形状承担类型：推理图的节点是圆、执行图的实体（智能体/工具/记录）是
 * 菱形。同一个屏幕上两种形状 = 两类东西，比两种颜色更不容易混。
 */

export interface NodeStyle {
  /** 节点填充色。 */
  color: string
  /** 圆半径（px）。重要性高的节点更大 —— 尺寸是第二重编码。 */
  radius: number
  /** 圆内的字母。用单字母是因为圆只有 50 来像素，四个字放不进去。 */
  letter: string
}

/**
 * 推理图：按节点类型上色。
 *
 * 色值与半径**照抄参考项目**（`viz/story_template.html` 的 `NODE_COLORS` /
 * `NODE_R`）：Signal 34、Evidence 26、Hypothesis 28、Knowledge 22。
 * 半径是评审会并排比的东西 —— 差 4px 看不出来，但那不是「看不出来」的理由。
 */
export const REASONING_STYLE: Record<string, NodeStyle> = {
  Signal: { color: '#ef4444', radius: 34, letter: 'S' },
  Evidence: { color: '#22c55e', radius: 26, letter: 'E' },
  Hypothesis: { color: '#f97316', radius: 28, letter: 'H' },
  // 知识会诊拉进图的 RAG 条目：紫色**菱形**，与四类推理节点并列。
  // 形状本身在说「这不是一条证据，是一条知识」。
  Knowledge: { color: '#8b5cf6', radius: 22, letter: 'K' },
  Conclusion: { color: '#0f172a', radius: 30, letter: 'C' },
}
const REASONING_FALLBACK: NodeStyle =
  { color: '#94a3b8', radius: 26, letter: '?' }

export function reasoningStyle(t: string): NodeStyle {
  return REASONING_STYLE[t] ?? REASONING_FALLBACK
}

/**
 * 执行图：按事件类型上色。
 *
 * 类别比推理图多，所以用「同一色族表达同一类事」来降低辨识负担：
 *   控制流（规划/图更新）偏蓝，协作（专家调用/工具）偏紫青，
 *   取数（证据读取/分析）偏青绿，报告用深色收尾。
 */
export const PROV_STYLE: Record<string, NodeStyle> = {
  plan: { color: '#64748b', radius: 20, letter: 'P' },
  agent_call: { color: '#8b5cf6', radius: 22, letter: 'A' },
  tool_call: { color: '#06b6d4', radius: 20, letter: 'T' },
  evidence_read: { color: '#14b8a6', radius: 20, letter: 'R' },
  analysis: { color: '#0d9488', radius: 22, letter: 'N' },
  graph_update: { color: '#2563eb', radius: 24, letter: 'G' },
  backtrack: { color: '#7c3aed', radius: 20, letter: 'B' },
  drill_down: { color: '#7c3aed', radius: 20, letter: 'D' },
  final_report: { color: '#0f172a', radius: 24, letter: 'F' },
  // 实体节点：菱形，形状本身就在说「这不是一次执行，是一个东西」
  agent: { color: '#8b5cf6', radius: 20, letter: 'A' },
  tool: { color: '#06b6d4', radius: 18, letter: 'T' },
  record: { color: '#14b8a6', radius: 18, letter: 'R' },
  action: { color: '#94a3b8', radius: 18, letter: '·' },
}
const PROV_FALLBACK: NodeStyle = { color: '#94a3b8', radius: 18, letter: '·' }

export function provStyle(k: string): NodeStyle {
  return PROV_STYLE[k] ?? PROV_FALLBACK
}

/** 实体节点画成菱形。 */
export const DIAMOND_KINDS = new Set(['agent', 'tool', 'record'])

/** 推理关系边。线型也要区分 —— 投影偏色、截图转黑白时颜色会失效，线型不会。 */
export const EDGE_STYLE: Record<string, {
  color: string; dash?: string; width: number; label: string
}> = {
  support: { color: '#22c55e', width: 2.4, label: '支持' },
  refute: { color: '#ef4444', width: 2.6, dash: '7 4', label: '反驳' },
  refines: { color: '#94a3b8', width: 1.8, dash: '2 3', label: '细化' },
  propagate: { color: '#0ea5e9', width: 2.0, dash: '5 4', label: '传播' },
}

/** 执行图边：只有结构关系，用统一的浅色 + 线型区分。 */
export const PROV_EDGE_STYLE: Record<string, {
  color: string; dash?: string; width: number
}> = {
  invoke: { color: '#a5b4fc', width: 1.4 },
  depend: { color: '#cbd5e1', width: 1.3, dash: '5 4' },
  read: { color: '#99f6e4', width: 1.4 },
  generate: { color: '#bfdbfe', width: 1.4 },
  propagate: { color: '#bae6fd', width: 1.3, dash: '4 3' },
}

// ------------------------------------------------------------------ 折行
/**
 * 字宽表，单位是**像素**（11px 字号、`--font-sans` 字体栈下逐字符实测）。
 *
 * **这里曾经用「汉字算 2、其余算 1」的显示宽度**，再乘一个 5.6px 的换算。
 * 那个模型对中文准（实测比值 0.98），对拉丁则错得离谱 —— 比例字体的字宽
 * 本来就随字符变化：
 *
 *     标签                预测      实测      比值
 *     未发现眼内炎症…      179.2px   176.0px   0.98   ← 中文没问题
 *     ABCDEFGHIJKLMN       78.4px   101.5px   1.30
 *     WWWWWWWWWWWWWW       78.4px   146.0px   1.86   ← 差近一倍
 *     rec-case-85-0001    151.2px   155.0px   1.03
 *
 * 后果不是「看起来有点挤」：盒子按 78px 算，文本实际占 146px，于是它在
 * DOM 里**重新折行成更多行**，而那些行 div 是固定 `height: 13px` 的 ——
 * 多出来的行直接画到下一个节点身上。这就是「节点的文本信息重叠在一起」
 * 的真正来源。盒子算错，布局怎么排都会重叠。
 */
const W_CJK = 11.0
const W_NARROW = 3.5
const W_WIDE = 9.6
const W_UPPER = 7.6
const W_LOWER = 6.2
const W_DIGIT = 6.4

const NARROW = new Set([...'iljtfrI.,;:!|\'()[]{}/\\ '])
const WIDE = new Set([...'WMmw@%&'])

/** 单个字符的像素宽度。 */
function charPx(ch: string): number {
  const c = ch.charCodeAt(0)
  // 汉字、假名与全角标点。与旧的 CJK 正则同范围，只是换算成了实测的 11px。
  if ((c >= 0x3000 && c <= 0x9fff) || (c >= 0xff00 && c <= 0xffef)) return W_CJK
  if (NARROW.has(ch)) return W_NARROW
  if (WIDE.has(ch)) return W_WIDE
  if (c >= 48 && c <= 57) return W_DIGIT
  if (c >= 65 && c <= 90) return W_UPPER
  return W_LOWER
}

/**
 * 折行到不超过 `maxPx` 像素一行，最多 `maxLines` 行，超出在末行加省略号。
 *
 * `maxPx` 现在是**像素**而不是「半角字符数」。旧单位有个隐蔽的坏处：
 * 同一个数字对中文和对拉丁的含义差一倍，调用方无从判断自己给的预算到底多宽。
 * 换算关系：旧默认 14 单位 ≈ 78px，15 单位 ≈ 84px。
 */
export function wrapLabel(
  text: string, maxPx = 78, maxLines = 3,
): string[] {
  const s = String(text ?? '').trim()
  if (!s) return ['']

  const lines: string[] = []
  let cur = ''
  let w = 0
  for (const ch of s) {
    if (ch === '\n') {
      lines.push(cur); cur = ''; w = 0
      continue
    }
    const cw = charPx(ch)
    if (w + cw > maxPx && cur) {
      lines.push(cur); cur = ch; w = cw
    } else {
      cur += ch; w += cw
    }
  }
  if (cur) lines.push(cur)

  if (lines.length > maxLines) {
    const kept = lines.slice(0, maxLines)
    kept[maxLines - 1] = kept[maxLines - 1].slice(0, -1) + '…'
    return kept
  }
  return lines
}

/**
 * 折行后的像素宽度，用于给节点定尺寸。
 *
 * 乘 1.03 是**安全余量**，不是微调：字宽表是在这一套字体栈下实测的，换台
 * 机器、换个字号缩放，实际渲染就可能比预测宽一点点。而盒宽若小于真实文本宽，
 * 文本就会在 DOM 里折成多一行 —— 那是**垂直**方向的溢出，正好压到下一个节点。
 * 宁可每个盒子宽 3%，也不要那种重叠。
 */
export function labelWidth(lines: string[]): number {
  const w = Math.max(...lines.map(l =>
    [...l].reduce((a, c) => a + charPx(c), 0)), 1)
  return w * 1.03
}

export const LINE_H = 13

/**
 * 圆与标签之间的间隙。
 *
 * **是间隙，不是「距圆心」的距离。** 早先用的是参考项目的 `r + 26` 形式，
 * 但那套公式只在半径固定时成立；这里半径按类型变化（22–30），写成
 * `26 - r` 之后 r=30 的起始信号节点会得到 -4 的边距 —— 标签直接压在圆上。
 * 用绝对间隙就没有这个耦合。
 */
export const LABEL_GAP = 5

// 这里原本有一个 `MIN_LABEL_ZOOM = 0.52`：低于这个缩放就隐藏标签，理由是
// 「11px 的字缩到 0.42 是噪点，缩略视图该给的是结构」。那个理由在当时是对的
// —— 因为当时标签会互相重叠，缩小之后一片糊。
//
// 重叠的根因修掉之后（字宽表 + 锚定力导向的矩形分离 + 逐层收尾扫描），
// 标签在任何缩放下都是**分开的**，缩小只是变小，不再是噪点。留一个阈值反而
// 让整图视图变成「一片没有文字的圆圈」。所以这个常量连同它的门控一起去掉了：
// 可读性由排版保证，不再由「藏起来」保证。

/** 自动适配视口时的缩放下限。低于它宁可不铺满，也不能把内容压到读不清。 */
export const FIT_MIN_ZOOM = 0.6

/**
 * 节点外接盒尺寸。
 *
 * **布局与渲染必须用同一个函数。** 分开算的话，dagre 按一个尺寸排布、
 * 组件按另一个尺寸绘制，两者一旦不一致，节点就会互相压叠 —— 而且看起来
 * 像是「间距没调好」，很难定位到是两处尺寸不同步。
 *
 * 这里只计三样：图形、图形与标签之间的间隙、标签行。
 * **等级徽章与脚印都不计**，因为它们被绝对定位成浮层（见 CSS）——
 * 计进去就等于为每个节点多留 30px 空白，9 个节点一排就是 270px 的浪费，
 * 而这一排正是把整张图压到读不清的那一排。
 *
 * **图形只计圆，菱形按它真正的外接盒算。** 菱形是 `radius * 1.7` 的正方形
 * 转 45°（见 `.gnode-shape.is-diamond` 与 `GraphNode` 的宽高），外接盒是
 * `radius * 1.7 * √2 ≈ radius * 2.4` —— 按 `radius * 2` 算会少 20%，
 * 而那 20% 正是节点互相压叠的量。之前只有溯源图有菱形（半径 18–20，
 * 差 8px 不明显），推理图加上 Knowledge 之后必须算对。
 */
const DIAMOND_SCALE = 1.7 * Math.SQRT2

export function nodeBox(lines: string[], radius: number,
                        diamond = false): { w: number; h: number } {
  const across = radius * (diamond ? DIAMOND_SCALE : 2)
  const w = Math.max(across, labelWidth(lines))
  // 图形的外接高度 + 间隙 + 标签行高。
  const h = across + LABEL_GAP + lines.length * LINE_H
  return { w, h }
}
