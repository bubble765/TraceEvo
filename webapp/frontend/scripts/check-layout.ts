/**
 * 布局自检。**不需要测试框架**，直接用 node 跑（Node 22.6+ 能直接执行 TS）：
 *
 *     cd webapp/frontend && node scripts/check-layout.ts
 *
 * 三件事，每一件都是「看不出来但会坏掉」的那类：
 *
 *   1. **有没有重叠。** 力导向的整个存在理由就是消重叠，而重叠在截图里
 *      只表现为「有点挤」，肉眼判不了。
 *   2. **力有没有把 dagre 的分层推翻。** 锚点是语义（左→右 = 推理推进、
 *      上→下 = 时间前进）。力只该松弛，不该重排。
 *   3. **净空够不够。** 画在盒子外面的那些墨迹（等级徽章、跨图引用徽章、
 *      alarm 虚线框、lit 光环）盒子模型刻意没算，余量得由碰撞给。
 *
 * **这里跑的是 `settle()`，不是界面上那套。** 界面用的是活着的模拟
 * （`createSimulation` + 实时 tick），坐标由上一帧继承而来 —— 那是**路径
 * 相关**的：从第 1 步走到第 5 步，与直接跳到第 5 步，落点可能不同。这是
 * 参考项目的行为，我们照搬（见 README）。而 `settle` 每次从 dagre 种子出
 * 发、同步跑到冷，是输入的纯函数，所以**「两次运行逐位相同」这条断言只对
 * `settle` 成立**，它测的是力的确定性（d3-force 内部无随机数），不是界面
 * 的可复现性。别把它读成后者。
 */
import { columnSeedsOf, createSimulation, layout, settle } from '../src/components/graph/layout.ts'
import { nodeBox, wrapLabel } from '../src/theme/graph.ts'

// ------------------------------------------------------------------ 造图
// 标签取自 case-85 的真实文本，长度分布覆盖「1 行短标签」到「3 行长标签」，
// 这是盒子尺寸的两端 —— 重叠恰恰总发生在两端之间。
const LABELS = [
  '右眼持续性视力下降1.5年', '63岁男性，既往有高血压、高脂血症和多次脑卒中病史',
  '糖尿病性视网膜病变相关水肿', '视网膜中央静脉阻塞', '高血压性视网膜病变',
  '眼内炎症', '视网膜血管炎', '葡萄膜炎或特发性黄斑水肿', '黄斑囊样水肿',
  '未发现眼内炎症的临床或实验室证据', 'OCT示视网膜神经纤维层增厚',
  '荧光素眼底血管造影示黄斑区渗漏', '玻璃体腔注射抗VEGF治疗',
  '视网膜静脉阻塞', '缺血性视神经病变', '青光眼性视盘改变',
  '白内障', '玻璃体混浊', '视盘水肿', '黄斑前膜',
]
const KINDS = ['signal', 'evidence', 'hypothesis', 'conclusion'] as const

function makeGraph(n: number, m: number) {
  const nodes = Array.from({ length: n }, (_, i) => {
    const label = LABELS[i % LABELS.length]
    const kind = KINDS[i % KINDS.length]
    const lines = wrapLabel(label, 14, 3)
    const radius = kind === 'signal' ? 30 : kind === 'evidence' ? 26
      : kind === 'hypothesis' ? 28 : 30
    return { id: `n${i}`, position: { x: 0, y: 0 }, data: { lines, radius } }
  })
  const edges = Array.from({ length: m }, (_, k) => {
    const a = k % n
    const b = (k * 7 + 3) % n
    return { id: `e${k}`, source: `n${a}`, target: `n${Math.abs(b - a) === 0 ? (a + 1) % n : b}` }
  }).filter(e => e.source !== e.target)
  return { nodes, edges }
}

// ------------------------------------------------------------------ 断言
type Box = { x: number; y: number; w: number; h: number }
let failures = 0

function check(name: string, ok: boolean, detail = ''): void {
  console.log(`  ${ok ? '✓' : '✗'} ${name}${detail ? `  ${detail}` : ''}`)
  if (!ok) failures++
}

function boxesOf(
  nodes: { data: { lines: string[]; radius: number } }[],
  placed: { position: { x: number; y: number } }[],
  slack = 0,
): Box[] {
  return placed.map((n, i) => {
    const b = nodeBox(nodes[i].data.lines, nodes[i].data.radius)
    return {
      x: n.position.x - slack, y: n.position.y - slack,
      w: b.w + slack * 2, h: b.h + slack * 2,
    }
  })
}

function overlaps(boxes: Box[]): [number, number][] {
  const out: [number, number][] = []
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j]
      const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)
      const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
      if (ox > 0.01 && oy > 0.01) out.push([i, j])
    }
  }
  return out
}

const sizeOf = (n: { data: { lines: string[]; radius: number } }) =>
  nodeBox(n.data.lines, n.data.radius)

function maxShift(
  from: { position: { x: number; y: number } }[],
  to: { position: { x: number; y: number } }[],
): number {
  let m = 0
  to.forEach((nd, i) => {
    const p0 = from[i].position
    m = Math.max(m, Math.hypot(nd.position.x - p0.x, nd.position.y - p0.y))
  })
  return m
}

// ------------------------------------------------------------------ 跑
for (const dir of ['LR', 'TB'] as const) {
  console.log(`\n=== ${dir} ===`)
  for (const [n, m] of [[5, 6], [13, 27], [37, 74], [60, 120]] as const) {
    const { nodes, edges } = makeGraph(n, m)
    const a = settle(nodes as never, edges as never, dir, sizeOf as never, {}, {})
    const b = settle(nodes as never, edges as never, dir, sizeOf as never, {}, {})

    // 1. 零重叠（用节点自己的外接盒，不加 slack —— 盒子里已经含标签）
    const ov = overlaps(boxesOf(nodes, a as never))
    check(`${n} 节点：无重叠`, ov.length === 0,
      ov.length ? `重叠 ${ov.length} 对，例如 ${JSON.stringify(ov.slice(0, 3))}` : '')

    // 2. 确定性：逐位相等（只对 settle 成立，见文件头）
    const same = a.every((x, i) =>
      x.position.x === (b[i] as never as { position: { x: number } }).position.x &&
      x.position.y === (b[i] as never as { position: { y: number } }).position.y)
    check(`${n} 节点：settle 两次逐位相同`, same)

    // 3. 形变有界：力只该微调，不该把 dagre 的布局整体推翻
    const base = layout(nodes as never, edges as never, dir, sizeOf as never, {})
    const shift = maxShift(base as never, a as never)
    check(`${n} 节点：相对 dagre 的最大位移 < 220px`, shift < 220,
      `实测 ${shift.toFixed(1)}px`)
  }
}

// ------------------------------------------------------------------ 净空
// **先记一个实测结论，它推翻了我原先的假设：dagre 不会重叠。**
// 即使把 `nodesep` 压到 0，它排出来的盒子也只是**相邻**而不相交 ——
// 因为它是按完整的盒子（圆 + 标签）算间距的。所以「标签互相压叠」的根因
// 从来不在 dagre，而在**盒子算错了**（字宽表把拉丁字符低估到 1/1.86，
// 见 theme/graph.ts）。那条修好之后，重叠就没了。
//
// 那力还负责什么？两件事，都不是「把 dagre 排开的东西再排一遍」：
//
//   1. **净空**。`slack=10` 让碰撞盒比真实盒子大一圈，于是两两之间至少有
//      20px 的余量。画在盒子外面那些墨迹（等级徽章左 8px、跨图引用徽章右
//      10px、alarm 虚线框 5px、lit 光环 6px）本来会互相蹭上 —— 盒子模型
//      刻意没把它们算进去，余量就得由碰撞来给。
//   2. **手感**。弱连杆 + 分离让排布不是机械压实的一格一格，这是「力导向」
//      三个字在观感上的兑现。
//
// 所以这里断言的是**净空**，不是「把重叠解开」：后者在本项目的输入下
// 根本不会发生，断言它等于永远通过。
console.log('\n=== 净空（含徽章等盒子外墨迹的余量）===')
const SLACK = 10
for (const dir of ['LR', 'TB'] as const) {
  const { nodes, edges } = makeGraph(24, 40)
  const before = layout(nodes as never, edges as never, dir, sizeOf as never,
                        { nodesep: 6, ranksep: 40 })
  const after = settle(nodes as never, edges as never, dir, sizeOf as never,
                       { nodesep: 6, ranksep: 40 })

  const tight = overlaps(boxesOf(nodes, before as never, SLACK))
  const roomy = overlaps(boxesOf(nodes, after as never, SLACK))
  check(`${dir}：dagre 的排布**不够**净空（用例有效）`, tight.length > 0,
    `含 ${{ LR: '左右', TB: '上下' }[dir]} 徽章余量时已经互相蹭上 ${tight.length} 对`)
  check(`${dir}：力导向给出了净空`, roomy.length === 0,
    roomy.length ? `仍挤着 ${roomy.length} 对` : '')

  const shift = maxShift(before as never, after as never)
  check(`${dir}：动的是位置，不是把 dagre 推翻`,
    shift > 1 && shift < 220, `最大位移 ${shift.toFixed(1)}px`)
}

// ------------------------------------------------------------------ 活的模拟
// 上面全部走 `settle`（同步跑到冷）。但界面用的是**活着**的模拟，而参考项目
// 那两个手感 ——「拖动连带邻居」与「松手不回弹」—— 恰恰只存在于活的那条路上。
// 这里把三段语义各钉一条，用 `createSimulation` 直接验：
//
//   * 拖动前先钉住并重新加热 ⇒ 邻居必须**动**（连带）；
//   * 松手只清 fx/fy、**不**还原坐标 ⇒ 节点停在拖动落点附近（不回弹）；
//   * 松手后 alphaTarget 归零 ⇒ 模拟自己冷下来，不需要外部停表。
console.log('\n=== 活的模拟：拖动语义（对齐参考项目）===')
{
  const { nodes, edges } = makeGraph(13, 27)
  const { sim, handle } = createSimulation(
    nodes as never, edges as never, 'LR', sizeOf as never, {}, {})
  // 从「冷」开始量，否则量到的是进入时的初始松弛，不是拖动引起的。
  for (let i = 0; i < 400 && handle.hot(); i++) sim.tick()
  check('松弛到冷之后模拟自动停表（不会永远 tick）', !handle.hot())

  // 选一个**有邻居**的节点，否则「连带」无从谈起。
  const moved = new Set<string>()
  const id = 'n4'
  const before = handle.centerOf(id)!
  const neigh = new Set<string>()
  for (const e of edges) {
    if (e.source === id) neigh.add(e.target)
    if (e.target === id) neigh.add(e.source)
  }
  const nb = [...neigh].filter(x => x !== id).map(x => [x, { ...handle.centerOf(x)! }] as const)
  check('用例有效：被拖的节点有邻居可观察', nb.length > 0, `${nb.length} 个邻居`)

  handle.pin(id, before.x, before.y)
  handle.reheat()
  // 把被拖的节点钉到远处，然后跑若干帧。
  handle.pin(id, before.x + 160, before.y)
  for (let i = 0; i < 30; i++) sim.tick()
  for (const [nid] of nb) moved.add(nid)
  const nbShift = Math.max(...nb.map(([nid, p]) =>
    Math.hypot(handle.centerOf(nid)!.x - p.x, handle.centerOf(nid)!.y - p.y)))
  check('拖动一个节点会**连带**邻居一起动', nbShift > 0.5,
    `最远的邻居位移 ${nbShift.toFixed(2)}px（${moved.size} 个邻居）`)

  // 松手：锚点跟到落点 + 把该节点的边按当前几何重铸，然后放开。
  // 这两步缺一不可 —— 只挪锚点仍回弹 61%（连杆还绷着），只重铸边也不行。
  const atRelease = { ...handle.centerOf(id)! }
  handle.reanchor(id)
  handle.unpin(id)
  handle.cool()
  for (let i = 0; i < 300 && handle.hot(); i++) sim.tick()
  const after = handle.centerOf(id)!
  const back = Math.hypot(after.x - before.x, after.y - before.y)
  const drop = Math.hypot(after.x - atRelease.x, after.y - atRelease.y)
  check('松手即定住（留在落点，不回弹）', back > 100 && drop < 0.25 * 160,
    `离原位置 ${back.toFixed(1)}px，离落点 ${drop.toFixed(1)}px（拖动的 ${(100 * drop / 160).toFixed(0)}%）`)

  handle.stop()
}

// ============================================================ 列式锚点
//
// 推理图的锚点是**语义列**（Signal / Evidence / H1 / H2 / Knowledge），
// 不是 dagre 的拓扑分层 —— 见 `columnSeedsOf` 的说明。这一段盯住两件事，
// 两件都是「坏了也看不出来」的：
//
//   1. 列序对不对。排错了不会报错，只是图读起来是散的。
//   2. **追加入列不会挪动已有节点。** 这一条是列式布局与 dagre 的真正分野：
//      dagre 每次重排全体，列式锚点必须让老节点待在原地，否则时间轴每
//      走一步，整张图就重排一次。
{
  const mk = (id: string, col: string, label: string) => ({
    id,
    data: { column: col, lines: wrapLabel(label, 78, 3),
            radius: col === 'Signal' ? 34 : col.startsWith('H') ? 28 : 26 },
  })
  const sizeOf = (n: { data: { lines: string[]; radius: number } }) =>
    nodeBox(n.data.lines, n.data.radius)

  const base = [
    mk('gos:node-1', 'Signal', '左踝扭伤后严重肿胀、无法负重行走'),
    mk('gos:node-2', 'Evidence', '左踝重度肿胀，外踝前下方压痛'),
    mk('gos:node-3', 'Evidence', '被动活动疼痛加剧，无骨擦音'),
    mk('gos:node-6', 'H1', '踝关节骨折'),
    mk('gos:node-7', 'H1', '软组织损伤'),
    mk('gos:node-9', 'H2', '距腓前韧带（ATFL）完全断裂'),
    mk('gos:node-11', 'Knowledge', '避免确认偏误：被反驳假设应回溯'),
  ]
  const order = base.map(n => n.id)

  const s = columnSeedsOf(base, sizeOf, n => n.data.column, order)!
  const xOf = (id: string) => s.cx[base.findIndex(n => n.id === id)]

  check('列序：Signal → Evidence → H1 → H2 → Knowledge',
    xOf('gos:node-1') < xOf('gos:node-2')
    && xOf('gos:node-2') < xOf('gos:node-6')
    && xOf('gos:node-6') < xOf('gos:node-9')
    && xOf('gos:node-9') < xOf('gos:node-11'),
    [xOf('gos:node-1'), xOf('gos:node-2'), xOf('gos:node-6'),
     xOf('gos:node-9'), xOf('gos:node-11')].map(Math.round).join(' < '))

  check('同列同 x', xOf('gos:node-2') === xOf('gos:node-3')
    && xOf('gos:node-6') === xOf('gos:node-7'))

  // 每列仍从固定行号起排；Signal / Evidence 额外错开半个行距，避免它们
  // 指向同一假设时两条直线重叠成「重复边」。错开量不依赖当前节点数量，
  // 因而时间轴追加节点不会挪动已有节点。
  const firstY = (col: string) => {
    const i = base.findIndex(n => n.data.column === col)
    return s.cy[i]
  }
  check('Signal / Evidence 输入列错开、假设层首行对齐',
    firstY('Signal') < firstY('H1')
    && firstY('Evidence') > firstY('H1')
    && firstY('H1') === firstY('H2'),
    `列首 y ${[firstY('Signal'), firstY('H1'), firstY('Evidence')]
      .map(v => v.toFixed(0)).join(' / ')}`)

  // **追加入列不能挪动已有节点。** 这是列式锚点相对 dagre 的全部好处，
  // 也是时间轴一步一步走时图不重排的原因。
  const grown = [...base, mk('gos:node-4', 'Evidence', 'X 光：踝关节未见骨折线，骨皮质连续')]
  const s2 = columnSeedsOf(grown, sizeOf, n => n.data.column,
                           [...order, 'gos:node-4'])!
  const moved = base.filter((n, i) =>
    Math.hypot(s2.cx[i] - s.cx[i], s2.cy[i] - s.cy[i]) > 1e-9)
  check('新增一个节点不动任何已有节点', moved.length === 0,
    moved.length ? `${moved.length} 个被挪动（${moved.map(n => n.id).join('、')}）`
                 : '0 个被挪动')
}

console.log(failures === 0
  ? '\n全部通过'
  : `\n${failures} 项未通过`)
process.exit(failures === 0 ? 0 : 1)
