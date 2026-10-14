/**
 * 拖动语义自检 —— 量**真实的浏览器里**拖一个节点会发生什么。
 *
 *     node scripts/check-drag.mjs [url]
 *
 * 它验的是用户明确提出的两条，都是截图看不出来的：
 *
 *   1. **拖动一个节点会连带邻居一起动。** 截图里「邻居跟着挪了 40px」和
 *      「邻居纹丝不动」几乎一样 —— 除非把两张图并排量坐标。
 *   2. **松手即定住，不回弹。** 这条更隐蔽：松手瞬间和松手 1 秒后长得一样，
 *      而「弹回去」发生在中间的几帧里。
 *   3. **远拖之后镜头不能自己跑掉。** React Flow 有个 `autoPanOnNodeDrag`：
 *      节点被拖到画布边缘时它自动平移画布，好让你「拖到视野外」。而我们的
 *      节点被钉在锚点附近（`layout.REF.maxPull`）—— 节点先停住、光标继续走，
 *      于是它判定用户想拖出去，**一路把镜头拉走，整张图被推出画布**。
 *      用户报的就是这个：「一拖节点就消失」。它躲过了本脚本前面所有检查，
 *      因为被拖的那个节点**全程都好好地跟着光标**，跑掉的是别的节点。
 *   6. **拖动过程中，边不能整批消失。** 与第 5 条同源：`parseHandles` 里
 *      `return !userNode.measured ? undefined : 上一次的 handleBounds` ——
 *      它**只认 `measured`**，而每帧重建的对象上没有它，于是 handleBounds 被
 *      清空、节点变成「未初始化」、`getEdgePosition` 返回 null，**所有连到它
 *      的边连元素都不生成**（不是画不出来）。症状正是「拖动节点，连接边短暂
 *      消失」。注意它和第 5 条**不是同一个字段的判据**：可见性读
 *      `measured ?? width ?? initialWidth`（`initialWidth` 能救），
 *      handleBounds 只读 `measured` —— 所以先修好节点、边照坏。
 *   5. **拖动过程中，节点不能变成 `visibility: hidden`。**
 *      React Flow 用 `visibility: hasDimensions ? 'visible' : 'hidden'` 渲染每个
 *      节点，而 `hasDimensions` 是从**我们交出去的对象**上读 `measured` 的。
 *      模拟活着时我们每帧交出新对象，`measured` 被重置成 undefined ——
 *      **整张图的节点全部不可见**，模拟一冷（约 2 秒）又自己回来。
 *      这就是用户报的「一拖节点就消失，等 2 秒出现」。
 *      它躲过了本脚本此前所有检查：`getBoundingClientRect` 照常有值、
 *      `opacity` 是 1、`display` 是 block、节点数一个不少 —— 只有
 *      `getComputedStyle(...).visibility` 和**截图像素**看得见。
 *   4. **点一个节点之后，整张图不能被镜头怼成只剩一个。** 这一条曾经
 *      真的坏过：选中节点的视口是 0.95 倍，画布里只剩它自己，看起来就是
 *      「一点/一拖，图就消失了」。它坏得悄无声息 —— 节点还在 DOM 里、
 *      没有异常、没有 NaN，只有量「画布内还剩几个」才看得出来。
 *      （参考项目在这一点上是**回弹**的，实测回 94%；我们刻意不照抄，
 *      取舍写在 README 的「拖动」一节。）
 *
 * 做法与 shoot.mjs 同源（CDP over WebSocket，Node 22+ 自带），但这里不看
 * 像素，只看 `transform: translate(...)` —— React Flow 把每个节点的坐标写
 * 在那里，是**图坐标**（还没乘视口变换），所以量到的数字可以直接和
 * layout.ts 里的锚点比。
 *
 * 三个采样点：拖之前、按住不放时、松手并等模拟冷下来之后。
 */
import { spawn } from 'node:child_process'
import { setTimeout as sleep } from 'node:timers/promises'

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const url = process.argv[2] || 'http://127.0.0.1:5173/workbench/case-85?step=5'
// **必须从接口拿邻接**：DOM 里读不到边表，而「挑一个邻居最多的节点」正是
// 这个脚本的关键一步 —— 早先它按「屏幕上最近的节点」挑，挑中了
// `gos:node-10`，那个节点在推理图里**一条边都没有**，于是量出来的「连带」
// 只有 13px，看起来像功能没做，其实是拿一个孤立节点在测。
const API = process.argv[3] || 'http://127.0.0.1:8001'
const PORT = 9334

const chrome = spawn(CHROME, [
  '--headless', '--disable-gpu', '--hide-scrollbars',
  `--remote-debugging-port=${PORT}`,
  '--window-size=1680,1000',
  '--user-data-dir=/tmp/traceevo-cdp-drag',
  url,
], { stdio: 'ignore' })
const cleanup = (c) => { chrome.kill(); process.exit(c) }
process.on('exit', () => chrome.kill())

let target = null
for (let i = 0; i < 40 && !target; i++) {
  await sleep(250)
  try {
    const list = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()
    target = list.find(t => t.type === 'page' && t.webSocketDebuggerUrl)
  } catch { /* 端口还没起来 */ }
}
if (!target) { console.error('连不上 Chrome 调试端口'); cleanup(2) }

const ws = new WebSocket(target.webSocketDebuggerUrl)
let msgId = 0
const pending = new Map()
ws.addEventListener('message', ev => {
  const m = JSON.parse(ev.data)
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id) }
})
await new Promise(r => ws.addEventListener('open', r))
const send = (method, params = {}) => new Promise(res => {
  const id = ++msgId
  pending.set(id, res)
  ws.send(JSON.stringify({ id, method, params }))
})
const evaluate = async (expr) => (await send('Runtime.evaluate', {
  expression: expr, returnByValue: true, awaitPromise: true,
}))?.result?.value

for (let i = 0; i < 60; i++) {
  await sleep(300)
  if (await evaluate(`document.querySelectorAll('.react-flow__node').length > 0`)) break
}
// **等模拟冷下来再量基线。** 刚进页面时力还在收敛，那时量到的位移是初始松弛，
// 不是拖动引起的 —— 会把「连通性」误报成通过。
await sleep(4000)

// 所有节点的图坐标（不是屏幕坐标）。
const positions = () => evaluate(`(() => {
  const out = {}
  for (const el of document.querySelectorAll('.react-flow__node')) {
    const m = /translate\\(([-\\d.]+)px,\\s*([-\\d.]+)px\\)/.exec(el.style.transform || '')
    if (m) out[el.getAttribute('data-id')] = { x: +m[1], y: +m[2] }
  }
  return out
})()`)

// 从接口取两张图的边表，算出度数最高的节点 —— 它一定有邻居。
const caseId = /\/workbench\/([^/?]+)/.exec(url)?.[1]
const step = /[?&]step=(\d+)/.exec(url)?.[1] ?? '5'
const graphs = {}
for (const which of ['reasoning', 'provenance']) {
  try {
    const g = await (await fetch(`${API}/api/cases/${caseId}/${which}-graph?step=${step}`)).json()
    const deg = new Map()
    for (const e of g.edges ?? []) {
      deg.set(e.source, (deg.get(e.source) ?? 0) + 1)
      deg.set(e.target, (deg.get(e.target) ?? 0) + 1)
    }
    const top = [...deg.entries()].sort((a, b) => b[1] - a[1])[0]
    graphs[which] = { hub: top?.[0], degree: top?.[1] ?? 0 }
  } catch (e) {
    console.error(`取 ${which} 图失败：${e.message}`)
  }
}
console.log('接口给出的候选：', Object.entries(graphs)
  .map(([k, v]) => `${k} → ${v.hub}(${v.degree})`).join('  '))

// 只统计**同一个 React Flow 实例**里的节点：两张图各有自己的坐标系与模拟，
// 拖推理图的节点时执行图的 37 个节点当然一动不动 —— 早先没分组，于是
// 「13/51 动了」被读成「连带很弱」，其实 51 里有 37 个是另一张图的。
const pickTarget = async (id) => evaluate(`(() => {
  const el = document.querySelector('[data-id="' + ${JSON.stringify(id)} + '"]')
  if (!el) return null
  const r = el.getBoundingClientRect()
  return { id: ${JSON.stringify(id)}, pane: el.closest('.react-flow') ? 1 : 0,
           cx: r.left + r.width / 2, cy: r.top + r.height / 2 }
})()`)

let failures = 0
const check = (name, ok, detail = '') => {
  console.log(`  ${ok ? '✓' : '✗'} ${name}${detail ? `  ${detail}` : ''}`)
  if (!ok) failures++
}

for (const [which, info] of Object.entries(graphs)) {
  if (!info.hub) continue
  const t = await pickTarget(info.hub)
  if (!t) { console.log(`\n[${which}] 页面上找不到 ${info.hub}，跳过`); continue }
  console.log(`\n=== ${which}：拖 ${t.id}（${info.degree} 个邻居）===`)

  const before = await positions()
  const inPane = await evaluate(`(() => {
    const el = document.querySelector('[data-id="' + ${JSON.stringify(info.hub)} + '"]')
    const pane = el.closest('.react-flow')
    return [...pane.querySelectorAll('.react-flow__node')]
      .map(e => e.getAttribute('data-id')).filter(Boolean)
  })()`)
  const ids = inPane.filter(i => before[i])
  console.log(`  同一个图里的节点：${ids.length}`)

  const tx = t.cx + 170, ty = t.cy + 90
  await send('Input.dispatchMouseEvent', {
    type: 'mousePressed', x: t.cx, y: t.cy, button: 'left', clickCount: 1 })
  for (let i = 1; i <= 8; i++) {
    await send('Input.dispatchMouseEvent', {
      type: 'mouseMoved', button: 'left',
      x: t.cx + (tx - t.cx) * i / 8, y: t.cy + (ty - t.cy) * i / 8 })
    await sleep(50)
  }
  await sleep(1200)
  const held = await positions()
  await send('Input.dispatchMouseEvent', {
    type: 'mouseReleased', x: tx, y: ty, button: 'left', clickCount: 1 })
  await sleep(5000)
  const after = await positions()

  const d = (p, q) => Math.hypot(p.x - q.x, p.y - q.y)
  const drag = d(held[t.id], before[t.id])
  check('拖得动', drag > 60, `${drag.toFixed(1)}px`)

  const others = ids.filter(i => i !== t.id && before[i] && held[i])
  const moved = others.map(i => ({ id: i, d: d(held[i], before[i]) }))
    .sort((a, b) => b.d - a.d)
  const hit = moved.filter(m => m.d > 1)
  check('拖动会**连带**其它节点一起动', hit.length > 0,
    hit.length ? `${hit.length}/${others.length} 个动了，最远 ${hit[0].id} ${hit[0].d.toFixed(1)}px` +
      `（拖动距离的 ${(100 * hit[0].d / drag).toFixed(0)}%）` : '一个都没动')

  // **松手即定住**：锚点跟到落点，节点留在被放下的地方。
  // 阈值取 25% —— 实测约 10%（只剩连杆把邻居拉回后的一点回拖）。
  // 参考项目在这里是 94% 的回弹，两者是**刻意不同**的，见 README。
  const back = d(after[t.id], before[t.id])
  const drop = d(after[t.id], held[t.id])
  check('松手即定住（留在落点附近，不回弹）', drop < drag * 0.25,
    `离落点 ${drop.toFixed(1)}px（拖动的 ${(100 * drop / drag).toFixed(0)}%），` +
    `离原位置 ${back.toFixed(1)}px`)
}

// ---------------------------------------------------------------- 可见性
// 「点一个节点，整张图还在吗？」——被拖的那个节点问题不大，危险的是**镜头**。
// 选中会把视口对准该节点；对准的倍率一旦过大，其余节点就全被挤出画布，
// 用户的感受就是「图消失了」。节点还在 DOM 里，所以任何「有没有 NaN /
// 有没有异常」的检查都发现不了它。
console.log('\n=== 选中之后整张图还在吗 ===')
{
  const paneOf = (id) => `(() => {
    const el = document.querySelector('[data-id="' + ${JSON.stringify(id)} + '"]')
    if (!el) return null
    const pane = el.closest('.react-flow').getBoundingClientRect()
    const ns = [...el.closest('.react-flow').querySelectorAll('.react-flow__node')]
    const vis = ns.filter(n => {
      const b = n.getBoundingClientRect()
      return b.width > 0 && b.right > pane.left && b.left < pane.right &&
             b.bottom > pane.top && b.top < pane.bottom
    }).length
    const z = /scale\\(([\\d.]+)\\)/.exec(
      el.closest('.react-flow').querySelector('.react-flow__viewport').style.transform)
    return { 总数: ns.length, 可见: vis, 缩放: +(z ? z[1] : 0) }
  })()`

  for (const id of ['gos:node-5', 'gos:node-8', 'gos:node-1']) {
    const t = await evaluate(`(() => {
      const el = document.querySelector('[data-id="' + ${JSON.stringify(id)} + '"]')
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 }
    })()`)
    if (!t) { console.log(`  · ${id} 不在页面上，跳过`); continue }
    await send('Input.dispatchMouseEvent', {
      type: 'mousePressed', x: t.x, y: t.y, button: 'left', clickCount: 1 })
    await send('Input.dispatchMouseEvent', {
      type: 'mouseReleased', x: t.x, y: t.y, button: 'left', clickCount: 1 })
    await sleep(1200)
    const r = await evaluate(paneOf(id))
    check(`选中 ${id} 之后大部分图仍可见`, r && r.可见 / r.总数 > 0.5,
      r ? `可见 ${r.可见}/${r.总数}，缩放 ${r.缩放.toFixed(2)}` : '取不到')
    // 收拾干净，免得影响后面的用例
    await evaluate(`document.querySelector('.react-flow__pane')?.click(); null`)
    await sleep(600)
  }
}

// ------------------------------------------------------------ 镜头会不会跑
// 远拖一个节点，然后问两件事：**视口有没有动**、**图还在不在**。
// 分开问是必要的：视口平移与「节点被拖散」都会让图看起来消失，
// 而它们的修法完全不同。
console.log('\n=== 远拖之后镜头跑不跑 ===')
{
  const cameraOf = (id) => `(function () {
    var el = document.querySelector('[data-id="' + ${JSON.stringify(id)} + '"]')
    if (!el) return null
    var pane = el.closest('.react-flow')
    var box = pane.getBoundingClientRect()
    var ns = [...pane.querySelectorAll('.react-flow__node')]
    var vis = ns.filter(function (n) {
      var b = n.getBoundingClientRect()
      return b.width > 0 && b.right > box.left && b.left < box.right &&
             b.bottom > box.top && b.top < box.bottom
    }).length
    return { tf: pane.querySelector('.react-flow__viewport').style.transform,
             可见: vis, 总数: ns.length }
  })()`

  for (const [id, dx, dy] of [['gos:node-5', 620, 400], ['gos:node-1', -600, -420]]) {
    const before = await evaluate(cameraOf(id))
    if (!before) { console.log(`  · ${id} 不在页面上，跳过`); continue }
    const t = await evaluate(`(function () {
      var e = document.querySelector('[data-id="' + ${JSON.stringify(id)} + '"]')
      var r = e.getBoundingClientRect()
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 }
    })()`)
    await send('Input.dispatchMouseEvent', {
      type: 'mousePressed', x: t.x, y: t.y, button: 'left', clickCount: 1 })
    for (let i = 1; i <= 25; i++) {
      await send('Input.dispatchMouseEvent', {
        type: 'mouseMoved', button: 'left',
        x: t.x + (dx * i) / 25, y: t.y + (dy * i) / 25 })
      await sleep(30)
    }
    await send('Input.dispatchMouseEvent', {
      type: 'mouseReleased', x: t.x + dx, y: t.y + dy, button: 'left', clickCount: 1 })
    await sleep(2500)
    const after = await evaluate(cameraOf(id))
    check(`远拖 ${id} 之后镜头没动`, after.tf === before.tf,
      after.tf === before.tf ? after.tf : `${before.tf} → ${after.tf}`)
    check(`远拖 ${id} 之后整张图仍可见`, after.可见 / after.总数 > 0.8,
      `可见 ${after.可见}/${after.总数}`)
  }
}

// -------------------------------------------------------- 拖动时节点可不可见
// 见文件头的第 5 条。这里在**按住不放的时候**量，因为故障只在模拟跑着时出现。
console.log('\n=== 拖动过程中节点与边可不可见 ===')
{
  // 一次采样同时量三件事：有多少节点被隐藏、有多少节点、有多少边。
  // 节点数与边数分开报，是因为「节点还在但边没了」正好是一种真实故障。
  const probe = () => evaluate(`[...document.querySelectorAll('.react-flow')].map(function (p) {
    var ns = [...p.querySelectorAll('.react-flow__node')]
    var h = ns.filter(function (n) {
      var cs = getComputedStyle(n)
      return cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0'
    }).length
    return h + '隐藏/' + ns.length + '节点/' + p.querySelectorAll('.react-flow__edge').length + '边'
  }).join('  |  ')`)
  const hiddenNow = probe

  const before = await hiddenNow()
  check('拖动前没有隐藏节点', /^0隐藏\//.test(before.split('  |  ')[0]), before)
  check('拖动前边都在', !before.includes('/0边'), before)

  const ID = 'gos:node-5'
  const t = await evaluate(`(function () {
    var e = document.querySelector('[data-id="' + ${JSON.stringify(ID)} + '"]')
    if (!e) return null
    var r = e.getBoundingClientRect()
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 }
  })()`)
  if (t) {
    await send('Input.dispatchMouseEvent', {
      type: 'mousePressed', x: t.x, y: t.y, button: 'left', clickCount: 1 })
    for (let i = 1; i <= 25; i++) {
      await send('Input.dispatchMouseEvent', {
        type: 'mouseMoved', button: 'left',
        x: t.x + (620 * i) / 25, y: t.y + (400 * i) / 25 })
      await sleep(30)
    }
    await sleep(300)
    // **按住不放时**量：这是模拟最热、每帧都在重建节点对象的一刻。
    // 连续采样，因为这类故障是「每帧重建 → 每帧被清掉」，单点采样可能刚好
    // 落在两次清空之间而漏掉。
    let held = ''
    for (let k = 0; k < 12; k++) {
      const s2 = await probe()
      if (!held) held = s2
      if (s2.includes('/0边') || !/^0隐藏\//.test(s2.split('  |  ')[0])) held = s2
      await sleep(60)
    }
    check('拖住不放时没有隐藏节点', /^0隐藏\//.test(held.split('  |  ')[0]), held)
    check('拖住不放时边没有整批消失', !held.includes('/0边'), held)
    await send('Input.dispatchMouseEvent', {
      type: 'mouseReleased', x: t.x + 620, y: t.y + 400, button: 'left', clickCount: 1 })
    await sleep(2500)
    const after = await hiddenNow()
    check('松手之后没有隐藏节点', /^0隐藏\//.test(after.split('  |  ')[0]), after)
    check('松手之后边都在', !after.includes('/0边'), after)
  }
}

console.log(failures === 0 ? '\n全部通过' : `\n${failures} 项未通过`)
cleanup(failures === 0 ? 0 : 1)
