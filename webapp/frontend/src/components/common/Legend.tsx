/**
 * 图例浮层：贴在每张画布的左上角，不占版面高度。
 *
 * 两张图各有一份，因为它们的编码方式不同 —— 推理图按**节点类型**上色、
 * 按**关系**分线型；执行图按**事件类型**上色、只有结构边。用一份通用图例
 * 覆盖两张图，就得把两套编码塞在一起，结果是读者要在图例里先找哪一半
 * 适用于当前这张图。
 *
 * 颜色值写在这里而不是从 `/api/i18n` 取：这是**视觉编码**，不是业务文案。
 * 文案（「起始信号」）仍然来自词表，改词会跟着变；但颜色是设计决定，
 * 后端不该关心。
 */
import { EDGE_STYLE, PROV_EDGE_STYLE, PROV_STYLE, REASONING_STYLE } from '../../theme/graph'

function Dot({ color, diamond }: { color: string; diamond?: boolean }) {
  return <span className={`dot ${diamond ? 'diamond' : ''}`}
               style={{ background: color }} />
}

function Line({ color, dash }: { color: string; dash?: string }) {
  return (
    <span className="line"
          style={{ borderTopColor: color, borderTopStyle: dash ? 'dashed' : 'solid' }} />
  )
}

/** 推理图图例。 */
export function ReasoningLegend() {
  const rel = (k: string) => EDGE_STYLE[k]
  return (
    <div className="legend">
      <span><Dot color={REASONING_STYLE.Signal.color} />起始信号</span>
      <span><Dot color={REASONING_STYLE.Evidence.color} />医学证据</span>
      <span><Dot color={REASONING_STYLE.Hypothesis.color} />诊断假设</span>
      {/* 知识节点是菱形 —— 图例里也得是菱形，否则读者会照着圆去找它。
          它只在有知识会诊的病例里出现，但图例是全局的，不能按当前图变。 */}
      <span><Dot color={REASONING_STYLE.Knowledge.color} diamond />
        知识库 (RAG)</span>
      <span><Dot color={REASONING_STYLE.Conclusion.color} />最终结论</span>
      <span><Line color={rel('support').color} />支持</span>
      <span><Line color={rel('refute').color} dash={rel('refute').dash} />反驳</span>
      <span><Line color={rel('refines').color} dash={rel('refines').dash} />细化</span>
      {/* 知识边：关系同是 support，来源是知识库而不是本次检查。 */}
      <span><Line color="#8b5cf6" />知识支持</span>
    </div>
  )
}

/** 执行图图例。 */
export function ProvenanceLegend() {
  const kinds = ['plan', 'agent_call', 'tool_call', 'evidence_read',
                 'graph_update', 'final_report']
  const labels: Record<string, string> = {
    plan: '规划', agent_call: '专家调用', tool_call: '工具调用',
    evidence_read: '证据读取', graph_update: '推理图更新', final_report: '最终报告',
  }
  return (
    <div className="legend">
      {kinds.map(k => (
        <span key={k}>
          <Dot color={PROV_STYLE[k].color} />{labels[k]}
        </span>
      ))}
      <span>
        <Dot color={PROV_STYLE.agent.color} diamond />智能体/工具/记录
      </span>
      <span><Line color={PROV_EDGE_STYLE.depend.color}
                  dash={PROV_EDGE_STYLE.depend.dash} />依赖</span>
      <span><Line color={PROV_EDGE_STYLE.invoke.color} />调用</span>
    </div>
  )
}
