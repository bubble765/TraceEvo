/**
 * 系统总览（落地页）。
 *
 * 六个入口中，**只有工作台已实现**；其余五个显式标注「第二阶段」并且
 * 不可点击。把它们做成可点但跳到空页面，会让人以为功能存在只是没数据 ——
 * 那是在演示里制造一个会被当场拆穿的印象。标注未完成，是这里唯一诚实的
 * 做法，也让「这一版交付了什么」在首页就一目了然。
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { CaseRef, Vocabulary } from '../types/protocol'

const PHASE2 = [
  { name: '真实病例分析', desc: '输入主诉/查体/辅助检查，实时跑一次多智能体协作' },
  { name: '审计中心', desc: '跨病例的 tier 分布、异常检出率与聚合指标' },
  { name: '经验中心', desc: '门控分区、经验卡库与注入模板预览' },
  { name: '执行档案', desc: '封存产物原文、哈希链与重放校验明细' },
  { name: '历史任务', desc: '按运行块浏览全部可复现病例' },
]

export function Overview() {
  const [vocab, setVocab] = useState<Vocabulary | null>(null)
  const [cases, setCases] = useState<CaseRef[]>([])

  useEffect(() => {
    api.vocabulary().then(setVocab).catch(() => {})
    api.cases().then(setCases).catch(() => {})
  }, [])

  const demo = cases.find(c => c.isDemo) ?? cases[0]

  return (
    <div className="app">
      <header className="topbar">
        <h1>{vocab?.ui.app_title ?? 'TraceEvo 双图审计溯源'}</h1>
        <span className="sub">{vocab?.ui.app_subtitle ?? ''}</span>
      </header>

      <div style={{ flex: 1, overflowY: 'auto', padding: '28px 32px' }}>
        <div style={{ maxWidth: 980, margin: '0 auto' }}>
          <h2 style={{ fontSize: 18, margin: '0 0 6px' }}>
            医学推理图只能看到它支持什么
          </h2>
          <p style={{ color: 'var(--text-2)', marginTop: 0, marginBottom: 24 }}>
            执行溯源图才能回答这些支持是从哪里来的。
            本系统把两张图并排放置，让「结果正确」与「过程可信」可以被分别检查。
          </p>

          {demo ? (
            <Link to={`/workbench/${demo.caseId}`}
                  style={{ textDecoration: 'none', color: 'inherit' }}>
              <div style={{
                border: '1px solid var(--accent)',
                background: 'var(--accent-soft)',
                borderRadius: 'var(--r-lg)',
                padding: '16px 20px',
                marginBottom: 22,
                cursor: 'pointer',
              }}>
                <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--accent)' }}>
                  进入双图工作台 · {demo.caseId}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 4 }}>
                  {demo.runLabel}
                  {/* 入口卡是评审看到的第一屏，得在图出现之前就说清这一页
                      没有审计产物。「展示轨迹」四个字不够 —— 它听起来仍然
                      像是某次运行留下的东西。 */}
                  {demo.kind === 'showcase'
                    ? <span className="tag tag-showcase"
                            style={{ marginLeft: 8 }}>
                        手写数据 · 无审计产物
                      </span>
                    : null}
                  {demo.outcomeLabel ? ` · 结果验证：${demo.outcomeLabel}` : ''}
                  {demo.gateLabel ? ` · 经验门控：${demo.gateLabel}` : ''}
                </div>
              </div>
            </Link>
          ) : null}

          <h3 style={{ fontSize: 13, color: 'var(--text-2)', marginBottom: 8 }}>
            系统入口
          </h3>
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
            gap: 10,
          }}>
            <Card title="踝关节展示" desc="L2 被 MRI 反驳后回溯到最高分 L1 继续深挖，直至最终报告；执行溯源与双图联动"
                  ready to={demo ? `/workbench/${demo.caseId}` : undefined} />
            <Card title="三个知识库" desc="查看 Patient Evidence、Medical Knowledge 与 Experience 的库内记录样例和规模统计"
                  ready to="/knowledge-bases" />
            {PHASE2.map(p => (
              <Card key={p.name} title={p.name} desc={p.desc} ready={false} />
            ))}
          </div>

          <h3 style={{ fontSize: 13, color: 'var(--text-2)', margin: '26px 0 8px' }}>
            当前可浏览病例（{cases.length}）
          </h3>
          <div>
            {cases.slice(0, 40).map(c => (
              <Link key={c.caseId} to={`/workbench/${c.caseId}`}
                    style={{ textDecoration: 'none' }}
                    title={c.kind === 'showcase'
                      ? '展示轨迹：手写数据，无审计产物' : c.runLabel}>
                <span className="chip link mono">{c.caseId}</span>
                {/* 列表里也要能一眼分出哪些是手写的 —— 点进去之前就该知道，
                    而不是到了里面才发现这一页没有审计产物。 */}
                {c.kind === 'showcase'
                  ? <span className="chip chip-showcase">展示</span>
                  : null}
              </Link>
            ))}
            {cases.length > 40
              ? <span className="chip">…共 {cases.length} 例</span>
              : null}
          </div>
        </div>
      </div>
    </div>
  )
}

function Card({
  title, desc, ready, to,
}: { title: string; desc: string; ready: boolean; to?: string }) {
  const inner = (
    <div style={{
      border: '1px solid var(--border)',
      borderRadius: 'var(--r-lg)',
      padding: '13px 15px',
      background: 'var(--surface)',
      height: '100%',
      opacity: ready ? 1 : 0.62,
    }}>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
        {title}
        {!ready
          ? <span className="badge none" style={{ marginLeft: 8 }}>第二阶段</span>
          : null}
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--text-2)', lineHeight: 1.55 }}>
        {desc}
      </div>
    </div>
  )
  return ready && to
    ? <Link to={to} style={{ textDecoration: 'none', color: 'inherit' }}>{inner}</Link>
    : inner
}
