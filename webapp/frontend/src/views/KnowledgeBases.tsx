import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api, ApiError } from '../api/client'
import type { KnowledgeBaseEntry, KnowledgeBasesView } from '../types/protocol'

export function KnowledgeBases() {
  const [data, setData] = useState<KnowledgeBasesView | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api.knowledgeBases().then(value => {
      if (alive) setData(value)
    }).catch((reason: unknown) => {
      if (!alive) return
      setError(reason instanceof ApiError ? reason.message : String(reason))
    })
    return () => { alive = false }
  }, [])

  return (
    <div className="app">
      <header className="topbar">
        <h1>TraceEvo 双图审计溯源</h1>
        <span className="sub">三个知识库 · 内容概览</span>
        <span className="spacer" />
        <Link className="kb-home-link" to="/">返回系统总览</Link>
      </header>

      <main className="kb-page">
        <div className="kb-page-heading">
          <div>
            <h2>数据库内容样例</h2>
            <p>中文简要整理，按类别查看关键信息；内容规模列在样例之后。</p>
          </div>
          <Link to="/" className="kb-back-link">← 返回首页</Link>
        </div>

        {error ? <div className="kb-error">知识库目录加载失败：{error}</div> : null}
        {!data && !error ? <div className="kb-loading">正在读取本地知识库目录…</div> : null}
        {data ? (
          <>
            <div className="kb-privacy-note">
              <span aria-hidden="true">🔒</span>
              <span>{data.privacyNote}</span>
            </div>
            <section className="kb-grid" aria-label="知识库目录">
              {data.knowledgeBases.map(base => <KnowledgeBaseCard key={base.id} base={base} />)}
            </section>
          </>
        ) : null}
      </main>
    </div>
  )
}

function KnowledgeBaseCard({ base }: { base: KnowledgeBaseEntry }) {
  const maximum = Math.max(1, ...base.distributions.map(row => row.count))

  return (
    <article className={`kb-card kb-card-${base.id}`}>
      <div className="kb-card-head">
        <h3>{base.name}</h3>
      </div>

      <section className="kb-samples">
        {base.samples.length ? base.samples.map((sample, index) => (
          <div className="kb-sample" key={`${sample.title}-${index}`}>
            <div className="kb-sample-meta">
              <strong>{sample.title}</strong>
              <span>{sample.category}</span>
            </div>
            <dl className="kb-sample-fields">
              {sample.fields.map(field => (
                <div className="kb-sample-field" key={field.label}>
                  <dt>{field.label}</dt>
                  <dd>{field.value}</dd>
                </div>
              ))}
            </dl>
          </div>
        )) : <div className="kb-empty">当前没有可展示的记录。</div>}
      </section>

      <section className="kb-scale">
        <h4>内容规模</h4>
        <div className="kb-scale-total">
          <strong>{base.recordCount.toLocaleString()}</strong>
          <span>{base.recordCountLabel}</span>
          {base.taskCount !== null ? (
            <span>{base.taskCount.toLocaleString()} 个任务</span>
          ) : null}
        </div>
        {base.distributions.length ? (
          <div className="kb-distributions">
            {base.distributions.map(row => (
              <div className="kb-distribution" key={row.label}>
                <div className="kb-distribution-label">
                  <span>{row.label}</span><b>{row.count.toLocaleString()}</b>
                </div>
                <div className="kb-bar-track">
                  <div className="kb-bar" style={{ width: `${Math.max(row.count ? 3 : 0, row.count / maximum * 100)}%` }} />
                </div>
              </div>
            ))}
          </div>
        ) : null}
      </section>
    </article>
  )
}
