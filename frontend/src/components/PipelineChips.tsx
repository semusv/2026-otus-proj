// Чипы стадий конвейера агента: фиксированный порядок, подсветка пройденных,
// бейдж итерации re-plan на planner и выбранных инструментов (planner/retrieve).

import { CHAT_STAGES, STAGE_LABELS, type StatusEvent } from '../lib/sse'

interface PipelineChipsProps {
  marks: StatusEvent[]
  /** true во время стриминга — активная (последняя) стадия пульсирует. */
  active?: boolean
}

export default function PipelineChips({ marks, active = false }: PipelineChipsProps) {
  // последняя отметка для каждой стадии (повтор planner при re-plan заменяет предыдущую)
  const latest = new Map<string, StatusEvent>()
  for (const mark of marks) latest.set(mark.stage, mark)

  const lastIndex = marks.length > 0 ? marks[marks.length - 1].stage : undefined

  return (
    <div className="pipeline" aria-label="стадии конвейера агента">
      {CHAT_STAGES.map((stage) => {
        const mark = latest.get(stage)
        if (mark === undefined) {
          return (
            <span key={stage} className="pchip pchip-idle">
              {STAGE_LABELS[stage]}
            </span>
          )
        }
        const isCurrent = active && stage === lastIndex
        const replanned = typeof mark.iteration === 'number' && mark.iteration > 0
        const tools = Array.isArray(mark.tools) && mark.tools.length > 0 ? mark.tools.join('+') : null
        return (
          <span
            key={stage}
            className={`pchip pchip-done${isCurrent ? ' pchip-active' : ''}`}
            title={tools !== null ? `инструменты: ${tools}` : undefined}
          >
            {STAGE_LABELS[stage]}
            {replanned && <em className="pchip-extra">re-plan ×{mark.iteration}</em>}
            {tools !== null && <em className="pchip-extra">{tools}</em>}
          </span>
        )
      })}
    </div>
  )
}
