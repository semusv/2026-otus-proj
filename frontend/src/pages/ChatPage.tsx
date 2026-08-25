import { useEffect, useRef, useState } from 'react'
import { ApiError, chatStream } from '../lib/api'
import type { ChatStatus, Citation, RelatedAct, StatusEvent } from '../lib/sse'
import PipelineChips from '../components/PipelineChips'
import ClearanceBadge, { StatusBadge } from '../components/Badges'

interface ChatPageProps {
  onUnauthorized: () => void
}

export interface UiMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  streaming?: boolean
  stages?: StatusEvent[]
  citations?: Citation[]
  relatedActs?: RelatedAct[]
  status?: ChatStatus
  replans?: number
  notes?: string[]
  traceId?: string | null
  error?: string
}

const SUGGESTIONS = [
  'Какие требования к раскрытию информации эмитентами?',
  'Что регулирует закон о защите прав потребителей?',
  'Как определяются полномочия Центрального банка?',
]

export default function ChatPage({ onUnauthorized }: ChatPageProps) {
  const [messages, setMessages] = useState<UiMessage[]>([])
  const [input, setInput] = useState('')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [streaming, setStreaming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  const patchLast = (patch: (msg: UiMessage) => UiMessage) => {
    setMessages((prev) => {
      if (prev.length === 0) return prev
      const next = [...prev]
      next[next.length - 1] = patch(next[next.length - 1])
      return next
    })
  }

  const send = async (raw: string) => {
    const text = raw.trim()
    if (text.length === 0 || streaming) return
    setInput('')
    setStreaming(true)

    const userMsg: UiMessage = { id: crypto.randomUUID(), role: 'user', text }
    const assistantMsg: UiMessage = {
      id: crypto.randomUUID(),
      role: 'assistant',
      text: '',
      streaming: true,
      stages: [],
    }
    setMessages((prev) => [...prev, userMsg, assistantMsg])

    const controller = new AbortController()
    abortRef.current = controller

    try {
      await chatStream(
        { message: text, session_id: sessionId ?? undefined, stream: true },
        {
          onStatus: (event) =>
            patchLast((msg) => ({ ...msg, stages: [...(msg.stages ?? []), event] })),
          onToken: (event) => patchLast((msg) => ({ ...msg, text: msg.text + event.delta })),
          onDone: (event) => {
            setSessionId(event.session_id)
            patchLast((msg) => ({
              ...msg,
              text: event.answer.length > 0 ? event.answer : msg.text,
              streaming: false,
              citations: event.citations,
              relatedActs: event.related_acts,
              status: event.status,
              replans: event.replans,
              notes: event.notes,
              traceId: event.trace_id,
            }))
          },
          onError: (event) =>
            patchLast((msg) => ({ ...msg, streaming: false, error: `${event.code}: ${event.message}` })),
        },
        controller.signal,
      )
      // поток завершён без done/error — снять флаг стриминга
      patchLast((msg) => (msg.streaming ? { ...msg, streaming: false } : msg))
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) onUnauthorized()
        patchLast((msg) => ({
          ...msg,
          streaming: false,
          error:
            err.status === 0
              ? err.message
              : `Ошибка API (${err.status}): ${err.message}`,
        }))
      } else {
        patchLast((msg) => ({ ...msg, streaming: false, error: 'Неизвестная ошибка запроса' }))
      }
    } finally {
      abortRef.current = null
      setStreaming(false)
    }
  }

  return (
    <section className="chat-page">
      <div className="chat-scroll">
        {messages.length === 0 && (
          <div className="chat-empty">
            <p className="chat-empty-title">Спросите что-нибудь о корпусе правовых актов</p>
            <div className="suggest">
              {SUGGESTIONS.map((q) => (
                <button key={q} className="chip chip-suggest" onClick={() => void send(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((msg) =>
          msg.role === 'user' ? (
            <div key={msg.id} className="row-user">
              <div className="bubble bubble-user">{msg.text}</div>
            </div>
          ) : (
            <AssistantMessage key={msg.id} msg={msg} />
          ),
        )}
        <div ref={bottomRef} />
      </div>

      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault()
          void send(input)
        }}
      >
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              void send(input)
            }
          }}
          placeholder={streaming ? 'Агент отвечает…' : 'Ваш вопрос… (Enter — отправить)'}
          rows={2}
          maxLength={8000}
          disabled={streaming}
        />
        {streaming ? (
          <button
            type="button"
            className="btn btn-danger"
            onClick={() => abortRef.current?.abort()}
          >
            Стоп
          </button>
        ) : (
          <button type="submit" className="btn btn-primary" disabled={input.trim().length === 0}>
            Отправить
          </button>
        )}
      </form>
    </section>
  )
}

function AssistantMessage({ msg }: { msg: UiMessage }) {
  return (
    <div className="bubble bubble-assistant">
      {msg.stages !== undefined && msg.stages.length > 0 && (
        <PipelineChips marks={msg.stages} active={msg.streaming === true} />
      )}

      {msg.text.length > 0 && (
        <p className="answer">
          {msg.text}
          {msg.streaming && <span className="caret" aria-hidden="true" />}
        </p>
      )}

      {msg.streaming && msg.text.length === 0 && (
        <p className="muted thinking">
          Думаю над вопросом<span className="ellipsis" aria-hidden="true" />
        </p>
      )}

      {msg.error !== undefined && <div className="error-box">{msg.error}</div>}

      {msg.citations !== undefined && msg.citations.length > 0 && (
        <div className="citations">
          <h4>Источники</h4>
          <div className="citation-list">
            {msg.citations.map((c) => (
              <div key={`${c.source_id}-${c.act_id}-${c.chunk_no}`} className="citation-card">
                <span className="mono cite-mark">[{c.source_id}]</span>
                <span className="cite-title">{c.title}</span>
                <span className="cite-meta muted mono">
                  чанк {c.chunk_no} · {c.act_id}
                </span>
                <ClearanceBadge clearance={c.clearance} />
              </div>
            ))}
          </div>
        </div>
      )}

      {msg.relatedActs !== undefined && msg.relatedActs.length > 0 && (
        <div className="graph-path">
          <h4>Путь по графу</h4>
          <div className="path-chips">
            {msg.relatedActs.map((act, i) => (
              <span key={`${act.id ?? i}-wrap`} className="path-item">
                {i > 0 && <span className="path-arrow">→</span>}
                <span
                  className="chip chip-path"
                  title={act.id !== undefined ? act.id : undefined}
                >
                  {act.title ?? act.id ?? 'акт'}
                </span>
              </span>
            ))}
          </div>
        </div>
      )}

      {!msg.streaming && (msg.status !== undefined || msg.traceId != null) && (
        <div className="meta-row">
          {msg.status !== undefined && <StatusBadge status={msg.status} />}
          {msg.replans !== undefined && msg.replans > 0 && (
            <span className="badge badge-neutral">re-plan ×{msg.replans}</span>
          )}
          {msg.notes !== undefined &&
            msg.notes.filter((n) => n.length > 0).map((n) => (
              <span key={n} className="badge badge-warn mono">
                {n}
              </span>
            ))}
          {msg.traceId != null && (
            <span className="meta-trace mono" title="X-Trace-Id (сквозная трассировка)">
              trace: {msg.traceId.slice(0, 12)}…
            </span>
          )}
        </div>
      )}
    </div>
  )
}
