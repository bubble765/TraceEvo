/**
 * 端到端重叠检查：**量浏览器里真正渲染出来的盒子**，不是算出来的。
 *
 *     cd webapp/frontend && node scripts/check-rendered.mjs "http://127.0.0.1:5173/workbench/case-85?step=4"
 *
 * `check-layout.ts` 验的是布局数学（零重叠、确定性），但它相信 `nodeBox`
 * 算出来的尺寸。真正会出问题的是**「算出来的盒子」与「浏览器画出来的东西」
 * 不一致** —— 中文标签按 78px 折行、实际渲染 146px，文本就会在 DOM 里折成
 * 更多行，而每个行 div 是固定 13px 的，多出来的行直接画到下一个节点身上。
 * 那类错误在布局数学里是完全正确的，只有量渲染结果才看得见。
 *
 * 做法：用无头 Chrome 把页面 dump 成 DOM。React Flow 把每个节点的位置写成
 * `transform: translate(Xpx, Ypx)`，盒宽写在 `.gnode` 的 inline style 上，
 * 盒高可以由「圆的高度 + 间隙 + 行数×行高」还原 —— 全是渲染后的真值，
 * 不需要执行任何注入脚本。
 */
import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const url = process.argv[2] || 'http://127.0.0.1:5173/workbench/case-85?step=4'

if (!existsSync(CHROME)) {
  console.error(`找不到 Chrome：${CHROME}`)
  process.exit(2)
}

const dom = execFileSync(CHROME, [
  '--headless', '--disable-gpu', '--dump-dom',
  '--virtual-time-budget=12000', url,
], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, stdio: ['ignore', 'pipe', 'ignore'] })

const LINE_H = 13
const LABEL_GAP = 5

// 按节点切块再逐块解析，不用一个跨全篇的正则。
//
// 踩过的坑：`data-id="..."` 在**每个 node 内部还会再出现一次**（React Flow
// 给两个 Handle 也写了 data-id，如 `1-gos:node-1-null-target`），而 Handle
// 排在 `.gnode-shape` 前面。用「下一个 data-id 出现处」当块边界，会在圆的
// 高度之前把块截断 —— 于是每个节点都解析失败，脚本报「0 个节点、无重叠」，
// 一个**看起来通过**的假阳性。
// **结尾不留引号。** React Flow 会往节点 class 后面追加它自己的标记
// （开了拖动之后是 `nopan draggable`），写死 `...gnode"` 就一个都切不出来。
// 这是本脚本第三次栽在「选择器太严 → 解析出 0 个节点 → 报无重叠」上，
// 所以下面那道 `nodes.length === 0` 的闸门是必须的：它把假阳性拦住了。
const CHUNKS = dom.split('class="react-flow__node react-flow__node-gnode').slice(1)

const nodes = []
for (const c of CHUNKS) {
  const id = c.match(/data-id="([^"]+)"/)?.[1]
  const t = c.match(/transform:\s*translate\(([-\d.]+)px,\s*([-\d.]+)px\)/)
  // 两处都要留余量，两处都踩过：
  //   * `class="gnode[^"]*"` —— `.gnode` 还会带 diamond / lit / dimmed /
  //     delta-added 等类名，写死成 `class="gnode"` 会漏掉**本步新增的节点**，
  //     而那恰恰是最该检查重叠的一批。
  //   * 结尾不锚 `px"` —— style 的值是 `width: 79.31px;`，`px` 后还有分号。
  // 两种写法的失败方式都一样：静静少解析几个节点，然后报「无重叠」。
  const w = c.match(/class="gnode[^"]*"\s+style="width:\s*([\d.]+)px/)
  const shape = c.match(/class="gnode-shape[^"]*"\s+style="[^"]*height:\s*([\d.]+)px/)
  if (!id || !t || !w || !shape) continue
  const lines = (c.match(/height:\s*13px;\s*line-height:\s*13px/g) || []).length
  nodes.push({
    id,
    x: +t[1], y: +t[2], w: +w[1],
    h: +shape[1] + LABEL_GAP + lines * LINE_H,
    lines,
  })
}

if (nodes.length === 0) {
  console.error('一个节点都没解析出来 —— 说明选择器与产物对不上了，' +
                '不是「没有重叠」。修脚本，别信这个结果。')
  process.exit(2)
}

// 两张图是各自独立的 React Flow 实例，坐标系不通用，必须分开比。
const isReasoning = id => id.startsWith('gos:')
const groups = {
  推理图: nodes.filter(n => isReasoning(n.id)),
  溯源图: nodes.filter(n => !isReasoning(n.id)),
}

let bad = 0
for (const [name, list] of Object.entries(groups)) {
  const hits = []
  for (let i = 0; i < list.length; i++) {
    for (let j = i + 1; j < list.length; j++) {
      const a = list[i], b = list[j]
      const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)
      const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
      if (ox > 0.5 && oy > 0.5) hits.push(`${a.id} × ${b.id}  重叠 ${ox.toFixed(1)}×${oy.toFixed(1)}px`)
    }
  }
  bad += hits.length
  console.log(`${name}：${list.length} 个节点，重叠 ${hits.length} 对`)
  for (const h of hits.slice(0, 8)) console.log('    ' + h)
}

const widest = nodes.reduce((a, n) => (n.w > a.w ? n : a), { w: 0, id: '-' })
const tallest = nodes.reduce((a, n) => (n.h > a.h ? n : a), { h: 0, id: '-' })
console.log(`\n最宽节点 ${widest.id} ${widest.w}px；最高节点 ${tallest.id} ${tallest.h}px`)
console.log(bad === 0 ? '渲染结果无重叠' : `${bad} 对重叠`)
process.exit(bad === 0 ? 0 : 1)
