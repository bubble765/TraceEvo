/**
 * 工作台状态。
 *
 * 只存**用户意图**（看到第几步、选中了什么、聚焦哪条异常），不存派生结果。
 * 高亮集合、可见节点、边的显隐全部在 `lib/linkage.ts` 里由意图 + 后端数据
 * 现算 —— 存派生结果意味着有两份真相，而它们会在某次更新后不一致。
 *
 * 两张图共享同一个 step：时间轴只有一个。这是「双图联动」的第一层含义，
 * 也是最容易被实现成两个独立播放器的第一层。
 */
import { create } from 'zustand'

interface WorkbenchState {
  caseId: string | null
  /** 回放位置：delta 序号，0 起。`null` 表示「尚未加载」。 */
  step: number
  playing: boolean
  /** 选中的推理图节点（`gos:node-N`）。 */
  selectedNodeId: string | null
  /** 选中的执行图事件（`evt-…`）。 */
  selectedEventId: string | null
  /** 聚焦的异常（`case-85-A1-001`）。与选中节点可同时存在。 */
  focusedAnomalyId: string | null
  /** 「仅显示关联路径」：隐藏与当前选区无关的节点与边。 */
  pathOnly: boolean
  /** 悬停。与选中分开 —— 悬停是临时的预览，不改动选中状态。 */
  hovered: string | null

  init: (caseId: string, lastStep: number) => void
  setStep: (step: number, lastStep: number) => void
  setPlaying: (playing: boolean) => void
  selectNode: (nodeId: string | null) => void
  selectEvent: (eventId: string | null) => void
  toggleAnomaly: (anomalyId: string) => void
  clearAnomaly: () => void
  setPathOnly: (on: boolean) => void
  setHovered: (id: string | null) => void
  reset: () => void
}

const EMPTY = {
  caseId: null,
  step: 0,
  playing: false,
  selectedNodeId: null,
  selectedEventId: null,
  focusedAnomalyId: null,
  pathOnly: false,
  hovered: null,
}

export const useWorkbench = create<WorkbenchState>((set) => ({
  ...EMPTY,

  init: (caseId, lastStep) =>
    set({ ...EMPTY, caseId, step: lastStep }),

  setStep: (step, lastStep) => {
    const clamped = Math.max(0, Math.min(step, lastStep))
    // 回放位置一变，之前选中的节点/事件可能已经不在图上。不清掉的话，
    // 详情面板会继续显示一个「当前图上不存在」的节点 —— 那种界面状态
    // 无法解释，看的人只会以为自己点错了。
    set({
      step: clamped,
      selectedNodeId: null,
      selectedEventId: null,
      hovered: null,
    })
  },

  setPlaying: (playing) => set({ playing }),

  selectNode: (nodeId) =>
    set({ selectedNodeId: nodeId, hovered: null }),

  selectEvent: (eventId) =>
    set({ selectedEventId: eventId, hovered: null }),

  toggleAnomaly: (anomalyId) =>
    set((s) => ({
      focusedAnomalyId: s.focusedAnomalyId === anomalyId ? null : anomalyId,
      pathOnly: s.focusedAnomalyId === anomalyId ? s.pathOnly : true,
    })),

  clearAnomaly: () => set({ focusedAnomalyId: null, pathOnly: false }),

  setPathOnly: (pathOnly) => set({ pathOnly }),

  setHovered: (hovered) => set({ hovered }),

  reset: () => set({ ...EMPTY }),
}))
