// Типы и парсер SSE-событий POST /api/chat (stream=true).
//
// Источник правды — коммиченный контракт docs/api/openapi.yaml: описание эндпоинта /api/chat
// задаёт события `status`, `token`, `done`, `error` в формате `event: <имя>\ndata: <JSON>\n\n`.
// Payload'ы событий в yaml не схематизированы (эндпоинт объявлен как object), поэтому типы ниже
// повторяют текстовое описание контракта; структура done соответствует ChatResponse бэкенда
// (backend/app/schemas/chat.py) — session_id, answer, citations, status, replans,
// related_acts, notes, trace_id.

export type ChatStatus = 'ok' | 'degraded' | 'empty'

/** Стадии конвейера агента — фактические события статуса графа (backend/app/agents/graph.py). */
export type ChatStage =
  | 'guardrails'
  | 'planner'
  | 'retrieve'
  | 'fusion_rerank'
  | 'generate'
  | 'evaluate'
  | 'guardrails_out'

export const CHAT_STAGES: readonly ChatStage[] = [
  'guardrails',
  'planner',
  'retrieve',
  'fusion_rerank',
  'generate',
  'evaluate',
  'guardrails_out',
]

export const STAGE_LABELS: Record<ChatStage, string> = {
  guardrails: 'Guardrails',
  planner: 'Planner',
  retrieve: 'Retrieval',
  fusion_rerank: 'Fusion + Rerank',
  generate: 'Generate',
  evaluate: 'Evaluate',
  guardrails_out: 'Citations check',
}

export interface Citation {
  source_id: string
  act_id: string
  title: string
  chunk_no: number
  clearance: string
}

/** Соседние акты графа: list[dict[str, str]] в контракте ({id, title, ...}). */
export type RelatedAct = Record<string, string>

export interface StatusEvent {
  stage: ChatStage
  /** Номер итерации агента (re-plan), начиная с planner. */
  iteration?: number
  /** Выбранные планировщиком инструменты, напр. ["vec", "graph"]. */
  tools?: string[]
  [key: string]: unknown
}

export interface TokenEvent {
  delta: string
}

export interface ChatDoneEvent {
  session_id: string
  answer: string
  citations: Citation[]
  status: ChatStatus
  replans: number
  related_acts: RelatedAct[]
  notes: string[]
  trace_id: string | null
}

export interface ErrorEventData {
  code: string
  message: string
}

export interface ChatStreamHandlers {
  onStatus?: (event: StatusEvent) => void
  onToken?: (event: TokenEvent) => void
  onDone?: (event: ChatDoneEvent) => void
  onError?: (event: ErrorEventData) => void
}

export interface SseFrame {
  event: string
  data: string
}

const FRAME_SEPARATORS = ['\r\n\r\n', '\n\n'] as const

/** Ищет конец фрейма; возвращает [индекс начала разделителя, длина разделителя] или null. */
function findFrameEnd(buffer: string): [number, number] | null {
  let best: [number, number] | null = null
  for (const sep of FRAME_SEPARATORS) {
    const idx = buffer.indexOf(sep)
    if (idx !== -1 && (best === null || idx < best[0])) best = [idx, sep.length]
  }
  return best
}

function parseFrame(raw: string): SseFrame | null {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith(':')) continue // keepalive/comment по спецификации SSE
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''))
  }
  // Кадр без data (только комментарии/event) по спецификации SSE не диспетчится.
  if (dataLines.length === 0) return null
  return { event, data: dataLines.join('\n') }
}

/**
 * Инкрементальный парсер SSE: буферизует частичные чанки сети, режет поток на фреймы
 * по пустой строке, поддерживает \n и \r\n. Вызов push возвращает только полные фреймы.
 */
export class SseParser {
  private buffer = ''

  push(chunk: string): SseFrame[] {
    this.buffer += chunk
    const frames: SseFrame[] = []
    for (;;) {
      const end = findFrameEnd(this.buffer)
      if (end === null) break
      const raw = this.buffer.slice(0, end[0])
      this.buffer = this.buffer.slice(end[0] + end[1])
      if (raw.trim().length === 0) continue
      const frame = parseFrame(raw)
      if (frame !== null) frames.push(frame)
    }
    return frames
  }

  flush(): SseFrame[] {
    const rest = this.buffer
    this.buffer = ''
    if (rest.trim().length === 0) return []
    const frame = parseFrame(rest)
    return frame === null ? [] : [frame]
  }
}

/** Разбирает data-JSON события чата и маршрутизирует в обработчики; битые кадры игнорирует. */
export function dispatchChatEvent(
  name: string,
  dataJson: string,
  handlers: ChatStreamHandlers,
): void {
  let parsed: unknown
  try {
    parsed = JSON.parse(dataJson)
  } catch {
    return
  }
  switch (name) {
    case 'status':
      handlers.onStatus?.(parsed as StatusEvent)
      break
    case 'token':
      handlers.onToken?.(parsed as TokenEvent)
      break
    case 'done':
      handlers.onDone?.(parsed as ChatDoneEvent)
      break
    case 'error':
      handlers.onError?.(parsed as ErrorEventData)
      break
    default:
      break
  }
}
