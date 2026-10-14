/**
 * 双图审计溯源工作台。
 *
 * 演示模式与真实分析模式共用这一个页面 —— 它不知道数据来自封存的
 * artifact 还是正在运行的病例，因为后端交出的协议完全相同。前端代码里
 * 不出现任何具体病例号（病例号来自 URL）。
 *
 * **推理图按步拉取，不预加载最终图。** 每次回放位置变化都向
 * `/reasoning-graph?step=N` 取该步的图（本地缓存）。多一次请求换来的是：
 * 界面上出现的图**必然**是那一步真实的图，而不是前端自己从增量算出来的
 * 一个近似。后者一旦与后端重建逻辑有出入，演示就变成了自证 —— 而那种
 * 出入不会报错，只会让图看起来「差不多」。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'

import { api, ApiError } from '../api/client'
import type {
  AuditView, BindingIndex, CaseRef, CaseSummary, DemoScript, ExperienceView,
  GraphDeltaView, LedgerView, ProvenanceGraph as PGraph,
  ProvenanceNode, ReasoningGraph as RGraph, Vocabulary,
} from '../types/protocol'
import { useWorkbench } from '../stores/workbench'
import { computeHighlights } from '../lib/linkage'
import { ProvenanceLegend, ReasoningLegend } from '../components/common/Legend'
import { Sidebar } from '../components/sidebar/Sidebar'
import { ReasoningGraph } from '../components/graph/ReasoningGraph'
import { ProvenanceGraph } from '../components/graph/ProvenanceGraph'
import { AuditPanel } from '../components/panels/AuditPanel'
import { LedgerPanel } from '../components/panels/LedgerPanel'
import { ExperiencePanel } from '../components/panels/ExperiencePanel'

interface Static {
  summary: CaseSummary
  deltas: GraphDeltaView[]
  binding: BindingIndex
  audit: AuditView
  ledger: LedgerView
  experience: ExperienceView
  demo: DemoScript
  cases: CaseRef[]
}

// `step` 不再是这里的标签 —— 当前步骤详情已并入左栏（那里空间更自然，
// 而且它本来就是「这一步在讲什么」的延续，不是另一块要切换的东西）。
type Tab = 'audit' | 'ledger' | 'settle'

const PLAY_MS = 1900

export function Workbench() {
  const { caseId = '' } = useParams()
  const [vocab, setVocab] = useState<Vocabulary | null>(null)
  const [data, setData] = useState<Static | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('audit')
  // 下边栏默认收起：默认视图是两张铺满的图（见 .panels 的 CSS）
  const [panelOpen, setPanelOpen] = useState(false)
  const [graph, setGraph] = useState<RGraph | null>(null)
  // 执行图与推理图一样按步取 —— 右图也必须随协作过程长出来
  const [prov, setProv] = useState<PGraph | null>(null)

  const {
    step, playing, selectedNodeId, selectedEventId, focusedAnomalyId,
    pathOnly, setStep, setPlaying, selectNode, selectEvent, toggleAnomaly,
    setPathOnly, init,
  } = useWorkbench()

  const cache = useRef(new Map<number, RGraph>())
  const provCache = useRef(new Map<number, PGraph>())

  // ---- 词表与静态数据 -----------------------------------------------
  useEffect(() => {
    let alive = true
    setData(null); setError(null); setGraph(null); setProv(null)
    cache.current.clear(); provCache.current.clear()
    Promise.all([
      api.vocabulary(), api.summary(caseId), api.deltas(caseId),
      api.binding(caseId), api.audit(caseId),
      api.ledger(caseId), api.experience(caseId), api.demoScript(caseId),
      api.cases(),
    ]).then(([v, summary, deltas, binding, audit, ledger,
              experience, demo, cases]) => {
      if (!alive) return
      setVocab(v)
      setData({ summary, deltas, binding, audit, ledger,
                experience, demo, cases })
      // 从**第一步**开始，不是最后一步。一进来就看到最终图，等于把
      // 「推理图随协作逐步长出来」这件事直接跳过 —— 而那是这个系统
      // 要证明的第一件事。参考项目同样从第 1 步开始。
      //
      // `?step=N` 可以直达某一步（分享「看第 3 步这里」用得上）。
      const url = new URLSearchParams(window.location.search)
      const want = Number(url.get('step'))
      init(caseId, Number.isInteger(want) && want > 0
        ? Math.min(want, deltas.length - 1) : 0)
    }).catch((e: unknown) => {
      if (!alive) return
      setError(e instanceof ApiError ? e.message : String(e))
    })
    return () => { alive = false }
  }, [caseId, init])

  // ---- 当前步的推理图 -----------------------------------------------
  useEffect(() => {
    if (!data) return
    const cached = cache.current.get(step)
    if (cached) { setGraph(cached); return }
    let alive = true
    api.reasoning(caseId, step).then(g => {
      if (!alive) return
      cache.current.set(step, g)
      setGraph(g)
    }).catch(() => { /* 保持上一张图，不白屏 */ })
    return () => { alive = false }
  }, [caseId, step, data])

  // ---- 当前步的执行溯源图 -------------------------------------------
  // 与上面那张对称：慢一步的请求不覆盖快一步的结果（`alive`），失败时保留
  // 上一张图而不是白屏。
  useEffect(() => {
    if (!data) return
    const cached = provCache.current.get(step)
    if (cached) { setProv(cached); return }
    let alive = true
    api.provenance(caseId, step).then(g => {
      if (!alive) return
      provCache.current.set(step, g)
      setProv(g)
    }).catch(() => { /* 保持上一张图 */ })
    return () => { alive = false }
  }, [caseId, step, data])

  // ---- 自动播放 ------------------------------------------------------
  const last = (data?.deltas.length ?? 1) - 1
  useEffect(() => {
    if (!playing) return
    if (step >= last) { setPlaying(false); return }
    const t = setTimeout(() => setStep(step + 1, last), PLAY_MS)
    return () => clearTimeout(t)
  }, [playing, step, last, setStep, setPlaying])

  // ---- 键盘：与参考项目的操作习惯一致 --------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return
      if (e.key === 'ArrowRight') { setPlaying(false); setStep(step + 1, last) }
      if (e.key === 'ArrowLeft') { setPlaying(false); setStep(step - 1, last) }
      if (e.key === ' ') {
        e.preventDefault()
        setPlaying(!playing)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [step, last, playing, setStep, setPlaying])

  // ---- 派生 ----------------------------------------------------------
  const delta = data?.deltas[step] ?? null

  // 全帧节点并集的**加入顺序**。列式布局靠它让同一列里的节点在整段回放中
  // 位置稳定（参考项目用全帧并集定序，节点全程不动）；没有它就只能按当前步
  // 的顺序，每加一个节点整列重排一次。顺序本身不含标签与分数。
  const seedOrder = useMemo(() => {
    const seen = new Set<string>()
    const out: string[] = []
    for (const d of data?.deltas ?? []) {
      for (const id of d.addedNodes) {
        if (!seen.has(id)) { seen.add(id); out.push(id) }
      }
    }
    return out
  }, [data])

  const highlights = useMemo(() => computeHighlights({
    binding: data?.binding ?? null,
    nodeId: selectedNodeId,
    eventId: selectedEventId,
    anomalyId: focusedAnomalyId,
    reasoningEdges: graph?.edges ?? [],
    provenanceEdges: prov?.edges ?? [],
    deltas: data?.deltas ?? [],
  }), [data, prov, selectedNodeId, selectedEventId, focusedAnomalyId, graph])

  const selectedNode = useMemo(
    () => graph?.nodes.find(n => n.id === selectedNodeId) ?? null,
    [graph, selectedNodeId])

  const selectedEvent = useMemo(
    () => prov?.nodes.find(n => n.id === selectedEventId) ?? null,
    [prov, selectedEventId])

  // 节点详情所需的相关操作/分析事件。执行图按当前步裁剪，因此只会显示
  // 此时已经发生的事件；节点引用与 binding 锚点提供直接关联，父事件补上
  // 紧邻的证据读取/工具操作（例如专家分析所依据的影像结果）。
  const selectedNodeEvents = useMemo<readonly ProvenanceNode[]>(() => {
    if (!selectedNode || !prov || !data) return []
    const refs = data.binding.nodeToEvents[selectedNode.id]
    const events = new Map(
      prov.nodes.filter(n => n.eventId).map(n => [n.id, n]),
    )
    const related = new Set<string>()
    const bareId = selectedNode.nodeId.replace(/^gos:/, '')
    for (const event of events.values()) {
      if (event.causalNodeRefs.some(ref => ref.replace(/^gos:/, '') === bareId)) {
        related.add(event.id)
      }
    }
    if (refs) {
      for (const id of [refs.createdByEvent, refs.graphUpdateEvent,
                        refs.bestAnalysisEvent, ...refs.sourceEvents]) {
        if (id && events.has(id)) related.add(id)
      }
    }
    // 只补一层父事件：保留直接分析所用的证据读取/工具操作，不把整份病例
    // 的历史链都塞进节点详情。
    for (const id of [...related]) {
      for (const parent of events.get(id)?.parentEventIds ?? []) {
        if (events.has(parent)) related.add(parent)
      }
    }
    return prov.nodes.filter(n => n.eventId && related.has(n.id))
  }, [selectedNode, prov, data])

  const onSeek = useCallback((s: number) => {
    setPlaying(false)
    setStep(s, last)
  }, [last, setPlaying, setStep])

  // `null` 表示「点了空白处」—— 这是取消选中，不是选中一个叫 null 的东西。
  // 选中不再切换下边栏的标签 —— 详情已经在左栏，切标签只会把用户
  // 没在看的那块内容顶到眼前。展开与否由用户自己决定。
  const onPickEvent = useCallback((id: string | null) => {
    selectEvent(id); selectNode(null)
  }, [selectEvent, selectNode])

  const onPickNode = useCallback((id: string | null) => {
    selectNode(id); selectEvent(null)
  }, [selectNode, selectEvent])

  if (error) {
    return (
      <div className="error">
        <div>加载失败</div>
        <div style={{ fontSize: 12 }}>{error}</div>
      </div>
    )
  }
  if (!data || !vocab) return <div className="loading">正在载入病例数据…</div>

  const hasSelection = highlights.kind !== 'none'
  const t = data.summary

  return (
    <div className="app">
      <header className="topbar">
        <h1>🩺 {vocab.ui.app_title}</h1>
        <span className="tag">{t.caseId}</span>
        {/* 展示轨迹要在**最显眼的地方**说清楚，不能只藏在审计面板里 ——
            这一页的因果图是编排的，读者默认它跟别的病例一样是跑出来的。 */}
        {!t.auditApplicable
          ? <span className="tag tag-showcase">展示轨迹 · 无审计产物</span>
          : null}
        {t.outcomeLabel
          ? <span className="tag">结果验证：{t.outcomeLabel}</span>
          : null}
        {t.gateLabel
          ? <span className="tag">经验门控：{t.gateLabel}</span>
          : null}
        <span className="spacer" />
        <span style={{ fontSize: 11.5, color: 'var(--header-tag-text)' }}>
          步骤 {step + 1} / {data.deltas.length}
          {graph ? ` · 节点 ${graph.nodes.length} · 边 ${graph.edges.length}` : ''}
        </span>
        <select
          className="case-select"
          value={caseId}
          onChange={(ev) => { window.location.href = `/workbench/${ev.target.value}` }}
        >
          {data.cases.map(c => (
            <option key={c.caseId} value={c.caseId}>
              {c.caseId}{c.isDemo ? '（演示）' : ''} · {c.runLabel}
            </option>
          ))}
        </select>
      </header>

      <div className="workbench">
        <Sidebar vocab={vocab} caseInput={data.demo.caseInput}
                 deltas={data.deltas} demo={data.demo}
                 current={step} playing={playing} graph={graph}
                 onSeek={onSeek}
                 onTogglePlay={() => {
                   if (step >= last && !playing) setStep(0, last)
                   setPlaying(!playing)
                 }}
                 selectedNode={selectedNode}
                 selectedNodeEvents={selectedNodeEvents}
                 selectedEvent={selectedEvent}
                 audit={data.audit}
                 onPickNode={onPickNode}
                 onPickEvent={onPickEvent} />

        <main className="col-main">
          <div className="graphs">
            <section className="graph-pane">
              <ReasoningLegend />
              <div className="graph-count">
                {graph ? `${graph.nodes.length} 节点 · ${graph.edges.length} 关系` : ''}
              </div>
              {graph
                ? <ReasoningGraph
                    graph={graph}
                    delta={delta}
                    highlights={highlights}
                    pathOnly={pathOnly}
                    dimOthers
                    selectedNodeId={selectedNodeId}
                    onSelect={onPickNode}
                    seedOrder={seedOrder} />
                : <div className="loading">载入推理图…</div>}
            </section>

            <section className="graph-pane">
              <ProvenanceLegend />
              <div className="graph-count">
                {prov ? `${prov.nodes.length} 节点 · ${prov.edges.length} 关系` : ''}
              </div>
              {prov ? <ProvenanceGraph
                graph={prov}
                delta={delta}
                highlights={highlights}
                pathOnly={pathOnly}
                dimOthers
                selectedEventId={selectedEventId}
                onSelect={(id) => { selectEvent(id); selectNode(null) }} />
                : <div className="loading">载入执行图…</div>}
            </section>
          </div>

          {/* 下边栏：**默认收起**，默认视图就是两张铺满的图。
              参考项目也是「图是主角」，而先前这里常驻 264px，把两张图压在
              一个不高的区域里 —— 演示最该看的东西被最该是辅助的东西挤掉了。
              操作条并进标签行：那三个按钮是操作图的，收起来会让它们无处安放；
              同处一行之后，收起状态只剩一条 40px 的横条。 */}
          <div className={`panels ${panelOpen ? 'open' : ''}`}>
            <div className="panel-head">
              {(['audit', 'ledger', 'settle'] as Tab[]).map(k => (
                <span key={k} className={`tab ${tab === k && panelOpen ? 'on' : ''}`}
                      onClick={() => {
                        // 点已展开的同一个标签 = 收起。省一个「必须去点箭头」
                        // 的动作，也让「我点错了」有一个自然的撤销方式。
                        if (tab === k && panelOpen) setPanelOpen(false)
                        else { setTab(k); setPanelOpen(true) }
                      }}>
                  {{ audit: vocab.ui.panel_audit, ledger: vocab.ui.panel_ledger,
                     settle: '结果与经验', step: vocab.ui.panel_step }[k]}
                </span>
              ))}

              <span className="head-sep" />

              <button className={`act-btn ${pathOnly ? 'on' : ''}`}
                      onClick={() => setPathOnly(!pathOnly)}>
                {pathOnly ? vocab.ui.action_full_graph : vocab.ui.action_focus_path}
              </button>
              <button className="act-btn"
                      disabled={!selectedNode?.matchedRecordId}
                      onClick={() => {
                        const r = selectedNode?.matchedRecordId
                        if (r) onPickEvent(r)
                      }}>
                {vocab.ui.action_locate_source}
              </button>
              <button className="act-btn"
                      disabled={!selectedNode?.bestAnalysisEvent}
                      onClick={() => {
                        if (selectedNode?.bestAnalysisEvent) {
                          onPickEvent(selectedNode.bestAnalysisEvent)
                        }
                      }}>
                {vocab.ui.action_view_evidence}
              </button>

              {hasSelection
                ? <span className="act-caption">{highlights.caption}</span>
                : <span className="act-caption" style={{ color: 'var(--text-4)' }}>
                    点击任一节点或事件查看它的来源与影响（← → 切换步骤，空格播放）
                  </span>}

              <button className="panel-toggle"
                      title={panelOpen ? '收起下边栏' : '展开审计信息 / 账本 / 结果与经验'}
                      onClick={() => setPanelOpen(!panelOpen)}>
                {panelOpen ? '收起 ▾' : '展开 ▴'}
              </button>
            </div>

            {panelOpen ? (
              <div className="panel-body">
                {tab === 'audit' ? (
                  <AuditPanel audit={data.audit} vocab={vocab}
                              focusedAnomalyId={focusedAnomalyId}
                              onToggleAnomaly={toggleAnomaly} />
                ) : null}
                {tab === 'ledger' ? (
                  <LedgerPanel ledger={data.ledger} vocab={vocab}
                               onPickEvent={onPickEvent} />
                ) : null}
                {tab === 'settle' ? (
                  <ExperiencePanel experience={data.experience} vocab={vocab} />
                ) : null}
              </div>
            ) : null}
          </div>
        </main>
      </div>
    </div>
  )
}
