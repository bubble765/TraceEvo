/**
 * 后端 API 客户端。
 *
 * 只做三件事：拼 URL、取 JSON、把错误变成可读的中文。**不做任何数据加工**
 * —— 加工属于服务层，前端加工一次就等于多出一份可能与后端不一致的语义。
 *
 * 所有路径以 `/api` 开头（相对路径），由 Vite 反代到后端。前端不知道
 * 后端跑在哪个端口，也不需要知道。
 */
import type {
  AuditView, BindingIndex, CaseRef, CaseSummary, DemoScript, ExperienceView,
  GraphDeltaView, LedgerView, ProvenanceGraph, RawIndex, ReasoningGraph,
  KnowledgeBasesView, Vocabulary,
} from '../types/protocol'

const BASE = '/api'

/** 后端返回的 detail 是给人看的；网络层的错误也要有话说。 */
export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

async function get<T>(path: string): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`)
  } catch {
    throw new ApiError(0, '无法连接后端服务，请确认后端已启动')
  }
  if (!res.ok) {
    let detail = `请求失败（HTTP ${res.status}）`
    try {
      const body = await res.json()
      if (body?.detail) detail = String(body.detail)
    } catch {
      /* 响应体不是 JSON，保留默认文案 */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

export async function getText(path: string): Promise<string> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new ApiError(res.status, `请求失败（HTTP ${res.status}）`)
  return res.text()
}

// ------------------------------------------------------------------ 端点
export const api = {
  vocabulary: () => get<Vocabulary>('/i18n'),

  knowledgeBases: () => get<KnowledgeBasesView>('/knowledge-bases'),

  cases: () => get<{ cases: CaseRef[] }>('/cases').then(r => r.cases),

  summary: (c: string) => get<CaseSummary>(`/cases/${c}/summary`),

  /** `step` 是回放位置；不传给出最终图。 */
  reasoning: (c: string, step?: number) =>
    get<ReasoningGraph>(
      `/cases/${c}/reasoning-graph` + (step === undefined ? '' : `?step=${step}`)),

  // `step` 与 reasoning 同义：只取到该步为止已发生的事件。
  // 两张图都按步取，右图才不是一张从头到尾不动的背景。
  provenance: (c: string, step?: number) =>
    get<ProvenanceGraph>(
      `/cases/${c}/provenance-graph` + (step === undefined ? '' : `?step=${step}`)),

  deltas: (c: string) =>
    get<{ deltas: GraphDeltaView[] }>(`/cases/${c}/deltas`).then(r => r.deltas),

  binding: (c: string) => get<BindingIndex>(`/cases/${c}/binding`),

  audit: (c: string) => get<AuditView>(`/cases/${c}/audit`),

  ledger: (c: string) => get<LedgerView>(`/cases/${c}/ledger`),

  experience: (c: string) => get<ExperienceView>(`/cases/${c}/experience`),

  demoScript: (c: string) => get<DemoScript>(`/cases/${c}/demo-script`),

  rawIndex: (c: string) => get<RawIndex>(`/cases/${c}/raw`),

  rawText: (c: string, artifact: string) =>
    getText(`/cases/${c}/raw/${artifact}`),
}
