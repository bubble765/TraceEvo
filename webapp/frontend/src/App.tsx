/**
 * 路由。
 *
 * 病例号来自 URL，不在代码里 —— `src/` 下任何位置出现 `case-85` 都是
 * 设计缺陷（演示病例由后端的 `config.DEMO_CASE` 决定并通过 `/api/cases`
 * 的 `isDemo` 标记下发）。这条约束让同一个页面直接成为「真实分析模式」
 * 的页面，而不需要另写一份。
 */
import { Navigate, Route, Routes } from 'react-router-dom'

import { Overview } from './views/Overview'
import { KnowledgeBases } from './views/KnowledgeBases'
import { Workbench } from './views/Workbench'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Overview />} />
      <Route path="/knowledge-bases" element={<KnowledgeBases />} />
      <Route path="/workbench" element={<Navigate to="/" replace />} />
      <Route path="/workbench/:caseId" element={<Workbench />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
