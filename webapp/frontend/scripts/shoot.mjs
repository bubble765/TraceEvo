/**
 * 截图 + 可选的悬停/点击，用 Chrome DevTools Protocol 驱动。
 *
 *     node scripts/shoot.mjs <url> <输出png> [hover 的节点 id] [--click]
 *
 * 为什么不用 `chrome --screenshot`：那条路只能「打开、等、拍」，**没法把
 * 鼠标放到某个东西上**。而悬停浮层、hover 高亮这类东西恰恰只有鼠标在上面
 * 才存在 —— 用 `--screenshot` 验证它们，只能得到「浮层没出现」，
 * 分不清是「没写对」还是「没悬停」。
 *
 * CDP 走 WebSocket，Node 22+ 自带 WebSocket 客户端，所以不需要任何依赖。
 */
import { spawn } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import { setTimeout as sleep } from 'node:timers/promises'

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const [url, out, hoverArg, ...flags] = process.argv.slice(2)
// 位置参数里可能混进 `--flag=…`（比如只按方向键、不悬停时，
// `--arrow=2` 会落到第 3 个位置）—— 认出来丢进 flags，不要当成节点 id。
const hoverId = hoverArg && !hoverArg.startsWith('--') ? hoverArg : undefined
if (hoverArg && hoverArg.startsWith('--')) flags.unshift(hoverArg)
if (!url || !out) {
  console.error('用法: node scripts/shoot.mjs <url> <out.png> [hoverId] [--click]')
  process.exit(2)
}
const PORT = 9333

const chrome = spawn(CHROME, [
  '--headless', '--disable-gpu', '--hide-scrollbars',
  `--remote-debugging-port=${PORT}`,
  '--window-size=1680,1000',
  '--user-data-dir=/tmp/traceevo-cdp-profile',
  url,
], { stdio: 'ignore' })

function cleanup(code) { chrome.kill(); process.exit(code) }
process.on('exit', () => chrome.kill())

// 等 Chrome 把调试端口挂上
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
  if (m.id && pending.has(m.id)) {
    pending.get(m.id)(m.result)
    pending.delete(m.id)
  }
})
await new Promise(r => ws.addEventListener('open', r))

const send = (method, params = {}) => new Promise(res => {
  const id = ++msgId
  pending.set(id, res)
  ws.send(JSON.stringify({ id, method, params }))
})

const evaluate = async (expr) => {
  const r = await send('Runtime.evaluate', {
    expression: expr, returnByValue: true, awaitPromise: true,
  })
  return r?.result?.value
}

// 等应用真正把图渲染出来再动手：页面加载完 ≠ React 渲染完 ≠ 力导向算完。
for (let i = 0; i < 60; i++) {
  await sleep(300)
  const ready = await evaluate(
    `document.querySelectorAll('.react-flow__node').length > 0`)
  if (ready) break
}
await sleep(1200)

// 按 N 次右方向键前进 N 步。时间轴的位置只在 store 里，不在 URL 上，
// 所以「拍到第 3 步的图」只能靠真的按键走过去 —— 与用户走的是同一条路。
const arrowFlag = flags.find(f => f.startsWith('--arrow='))
if (arrowFlag) {
  const n = Number(arrowFlag.split('=')[1]) || 0
  for (let i = 0; i < n; i++) {
    for (const type of ['keyDown', 'keyUp']) {
      await send('Input.dispatchKeyEvent', {
        type, key: 'ArrowRight', code: 'ArrowRight',
        windowsVirtualKeyCode: 39, nativeVirtualKeyCode: 39 })
    }
    await sleep(350)
  }
  // 走完最后一步还要等图**重新取回来并重新布局** —— 时间轴的指针先动，
  // 图随后才到。等太短会拍到中转态，那是截图工具的假象，不是应用的 bug。
  //
  // 1800ms 不够：连按 N 次时每次都会发一个取图请求，最后一次可能要等
  // 两三秒才回来；而且**视口的自动适配是在图到了之后再加 120ms 才算的**。
  // 实测连按 3 次时，第 4 步的适配发生在按完 4 秒之后 —— 1800ms 拍到的
  // 是「节点已经在图上、镜头还没跟过去」，看起来就像节点跑到画布外面。
  await sleep(4500)
}

// 图随步骤变大会跑出可视区，悬停就落不到目标上了。先点「适应视图」再悬停 ——
// 顺序不能反：hover 块在下面，所以这一步必须排在它前面。
if (flags.includes('--fit')) {
  const ok = await evaluate(
    `(() => { const el = document.querySelector('.react-flow__controls-fitview')
      if (!el) return false; el.click(); return true })()`)
  if (!ok) console.error('找不到适应视图按钮')
  await sleep(900)
}

if (hoverId) {
  // 从 DOM 里量出目标节点的真实屏幕坐标，再按坐标派发鼠标事件 ——
  // 比派发到元素上更接近真实交互（React 合成事件按坐标命中）。
  const box = await evaluate(`(() => {
    const el = document.querySelector('[data-id="${hoverId}"] .gnode')
    if (!el) return null
    const r = el.getBoundingClientRect()
    return { x: r.left + r.width / 2, y: r.top + 6 }
  })()`)
  if (!box) { console.error(`页面上找不到节点 ${hoverId}`); cleanup(3) }

  // 先移开再移入：直接一次 mouseMoved 有时不会触发 mouseenter/mouseover 链
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 5, y: 5 })
  await sleep(120)
  for (const type of ['mouseMoved']) {
    await send('Input.dispatchMouseEvent', { type, x: box.x, y: box.y })
  }
  await sleep(500)
  if (flags.includes('--click')) {
    await send('Input.dispatchMouseEvent', {
      type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1,
    })
    await send('Input.dispatchMouseEvent', {
      type: 'mouseReleased', x: box.x, y: box.y, button: 'left', clickCount: 1,
    })
    await sleep(900)
  }
}

// 拖动：按住 → 移动若干步 → **先拍一张（拖住不放）** → 松手 → 再拍一张。
// 两张合起来才能验「拖得动」和「松手回弹」两件事；只拍一张的话，
// 静止不动的图与「拖不动」在截图里长得一模一样。
const dragFlag = flags.find(f => f.startsWith('--drag='))
if (dragFlag && hoverId) {
  const [, dx, dy] = dragFlag.split('=')
  const box = await evaluate(`(() => {
    const el = document.querySelector('[data-id="${hoverId}"] .gnode')
    if (!el) return null
    const r = el.getBoundingClientRect()
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 }
  })()`)
  if (!box) { console.error(`找不到节点 ${hoverId}`); cleanup(3) }
  const tx = box.x + Number(dx), ty = box.y + Number(dy)
  await send('Input.dispatchMouseEvent', {
    type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1 })
  for (let i = 1; i <= 8; i++) {
    await send('Input.dispatchMouseEvent', {
      type: 'mouseMoved', button: 'left',
      x: box.x + (tx - box.x) * i / 8, y: box.y + (ty - box.y) * i / 8 })
    await sleep(40)
  }
  await sleep(300)
  const held = await send('Page.captureScreenshot', { format: 'png' })
  writeFileSync(out.replace(/\.png$/, '-held.png'), Buffer.from(held.data, 'base64'))
  await send('Input.dispatchMouseEvent', {
    type: 'mouseReleased', x: tx, y: ty, button: 'left', clickCount: 1 })
  await sleep(600)
}

// 点某个选择器（如展开下边栏的标签）。用 DOM 的 .click() 而不是派发鼠标
// 坐标：这里要的是「点了会怎样」，元素在哪不重要，而按坐标点还得先算位置。
const clickSel = (flags.find(f => f.startsWith('--click-sel=')) || '').split('=')[1]
if (clickSel) {
  const ok = await evaluate(
    `(() => { const el = document.querySelector(${JSON.stringify(clickSel)})
      if (!el) return false; el.click(); return true })()`)
  if (!ok) { console.error(`找不到可点击的元素：${clickSel}`); cleanup(5) }
  await sleep(900)
}

const shot = await send('Page.captureScreenshot', { format: 'png' })
if (!shot?.data) { console.error('截图失败'); cleanup(4) }
writeFileSync(out, Buffer.from(shot.data, 'base64'))
console.log(`已写出 ${out}`)
cleanup(0)
