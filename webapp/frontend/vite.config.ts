import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 后端地址只在这里出现一次。`src/` 里的任何文件都不写主机名与端口 ——
// 前端代码里出现 `localhost:8000` 就等于把部署形态焊进了业务逻辑，
// 换一个端口就要改视图层。
//
// 端口由 `TRACEEVO_API` 注入（`run.sh` 会设置它）。默认值只在直接跑
// `npm run dev` 时生效 —— 那种用法下没有 run.sh 帮忙对齐端口。
const API_TARGET = process.env.TRACEEVO_API ?? 'http://127.0.0.1:8000'

// 开发时前端跑在 5173，`/api` 反代到后端；生产由后端静态托管 dist。
export default defineConfig({
  plugins: [react()],
  server: {
    // 显式绑定 IPv4。Vite 默认只监听 `localhost`，在 macOS 上会解析成
    // `::1` —— 于是 `localhost:5173` 能开而 `127.0.0.1:5173` 连不上。
    // 两个地址在浏览器里长得一样、在不同地方被写成不同形式（脚本打印、
    // 终端提示、别人发来的链接），只监听其中一个就会得到「打不开」，
    // 而且报错信息里看不出是 IPv6/IPv4 的问题。
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    // 关掉 sourcemap：产物由后端静态托管，不需要把源码一并发布。
    sourcemap: false,
  },
})
