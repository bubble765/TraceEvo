/// <reference types="vite/client" />

// 让 TS 认识 CSS 与静态资源导入。Vite 在构建时处理它们，但类型系统
// 不知道 `.css` 是可导入的模块 —— 缺了这个文件，每一处样式导入都会
// 报 TS2307，而构建其实是好的。
declare module '*.css' {
  const content: string
  export default content
}
