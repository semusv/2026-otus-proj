import { Fragment, useEffect, useRef, useState } from 'react'
import { ApiError, chatStream, getActContent, adminUpdateActClearance, type ActContentResponse } from '../lib/api'
import { getAuth } from '../lib/auth'
import type { ChatStatus, Citation, RelatedAct, StatusEvent } from '../lib/sse'
import PipelineChips from '../components/PipelineChips'
import ClearanceBadge, { StatusBadge } from '../components/Badges'
import Modal from '../components/Modal'

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

  /** Новый диалог: прерывает активный стриминг, чистит ленту и session_id. */
  const startNewChat = () => {
    if (streaming) abortRef.current?.abort()
    setMessages([])
    setSessionId(null)
    setInput('')
  }

  return (
    <section className="chat-page">
      <div className="chat-toolbar">
        <span className="muted chat-session mono" title="Идентификатор диалога передаётся в /api/chat">
          {sessionId !== null ? `диалог ${sessionId.slice(0, 8)}…` : 'новый диалог'}
        </span>
        <button
          type="button"
          className="btn btn-ghost btn-small"
          onClick={startNewChat}
          disabled={messages.length === 0 && sessionId === null}
        >
          + Новый чат
        </button>
      </div>
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
  const [hlSource, setHlSource] = useState<string | null>(null)
  const flashTimer = useRef<number | undefined>(undefined)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const [actModal, setActModal] = useState<{ open: boolean; loading: boolean; data: ActContentResponse | null; error: string | null }>({
    open: false,
    loading: false,
    data: null,
    error: null,
  })

  useEffect(() => () => window.clearTimeout(flashTimer.current), [])

  const openActModal = async (actId: string) => {
    setActModal({ open: true, loading: true, data: null, error: null })
    try {
      const data = await getActContent(actId)
      setActModal({ open: true, loading: false, data, error: null })
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Не удалось загрузить акт'
      setActModal({ open: true, loading: false, data: null, error: message })
    }
  }

  const closeActModal = () => setActModal((prev) => ({ ...prev, open: false }))

  const changeActClearance = async (clearance: 'PUBLIC' | 'INTERNAL' | 'SECRET') => {
    if (actModal.data === null) return
    const confirmed = window.confirm(
      `Сменить гриф акта ${actModal.data.act.act_id}: ${actModal.data.act.clearance} → ${clearance}?`,
    )
    if (!confirmed) return
    try {
      const updated = await adminUpdateActClearance(actModal.data.act.act_id, clearance)
      setActModal((prev) => (prev.data !== null ? { ...prev, data: { ...prev.data, act: updated } } : prev))
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Не удалось сменить гриф'
      setActModal((prev) => ({ ...prev, error: message }))
    }
  }

  const isAdmin = getAuth()?.role === 'admin'

  /** Подсветить источник и подскроллить к нему (маркер ↔ карточка цитаты). */
  const focusSource = (sourceId: string, scrollTarget: 'card' | 'mark') => {
    setHlSource(sourceId)
    window.clearTimeout(flashTimer.current)
    flashTimer.current = window.setTimeout(() => setHlSource(null), 1600)
    const selector =
      scrollTarget === 'card' ? `[data-src="${sourceId}"]` : `[data-mark="${sourceId}"]`
    rootRef.current?.querySelector(selector)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  return (
    <div className="bubble bubble-assistant" ref={rootRef}>
      {msg.stages !== undefined && msg.stages.length > 0 && (
        <PipelineChips marks={msg.stages} active={msg.streaming === true} />
      )}

      {msg.text.length > 0 && (
        <p className="answer">
          <AnswerText text={msg.text} hlSource={hlSource} onMarkClick={(sid) => focusSource(sid, 'card')} />
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
              <div
                key={`${c.source_id}-${c.act_id}-${c.chunk_no}`}
                data-src={c.source_id}
                className={`citation-card${hlSource === c.source_id ? ' citation-card-active' : ''}`}
                onMouseEnter={() => setHlSource(c.source_id)}
                onMouseLeave={() => setHlSource(null)}
                onClick={() => {
                  focusSource(c.source_id, 'mark')
                  void openActModal(c.act_id)
                }}
                title={`Показать ${c.source_id} в тексте ответа`}
              >
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

      <Modal open={actModal.open} onClose={closeActModal} title="Текст акта">
        {actModal.loading && <p className="muted">Загрузка…</p>}
        {actModal.error !== null && <div className="error-box">{actModal.error}</div>}
        {actModal.data !== null && (
          <>
            <dl className="act-meta">
              <dt>ID</dt>
              <dd className="mono">{actModal.data.act.act_id}</dd>
              <dt>Название</dt>
              <dd>{actModal.data.act.title}</dd>
              {actModal.data.act.doc_number != null && (
                <>
                  <dt>Номер</dt>
                  <dd className="mono">{actModal.data.act.doc_number}</dd>
                </>
              )}
              {actModal.data.act.date != null && (
                <>
                  <dt>Дата</dt>
                  <dd>{actModal.data.act.date}</dd>
                </>
              )}
              {actModal.data.act.status != null && (
                <>
                  <dt>Статус</dt>
                  <dd>{actModal.data.act.status}</dd>
                </>
              )}
              <dt>Гриф</dt>
              <dd><ClearanceBadge clearance={actModal.data.act.clearance} /></dd>
              <dt>Чанков</dt>
              <dd>{actModal.data.chunk_count}</dd>
            </dl>
            {isAdmin && (
              <div className="act-clearance-controls">
                <span className="muted">Сменить гриф:</span>
                {(['PUBLIC', 'INTERNAL', 'SECRET'] as const).map((c) => (
                  <button
                    key={c}
                    type="button"
                    className={`btn btn-small ${actModal.data!.act.clearance === c ? 'btn-primary' : 'btn-ghost'}`}
                    disabled={actModal.data!.act.clearance === c}
                    onClick={() => void changeActClearance(c)}
                  >
                    {c}
                  </button>
                ))}
              </div>
            )}
            <pre className="act-text">{actModal.data.full_text}</pre>
          </>
        )}
      </Modal>
    </div>
  )
}

/** Текст ответа с интерактивными маркерами [S#]: клик подсвечивает карточку источника. */
function AnswerText({
  text,
  hlSource,
  onMarkClick,
}: {
  text: string
  hlSource: string | null
  onMarkClick: (sourceId: string) => void
}) {
  const parts = text.split(/(\[S\d+\])/g)
  return (
    <>
      {parts.map((part, i) => {
        const match = /^\[(S\d+)\]$/.exec(part)
        if (match === null) return <Fragment key={i}>{part}</Fragment>
        const sourceId = match[1]
        const active = hlSource === sourceId
        return (
          <button
            key={i}
            type="button"
            data-mark={sourceId}
            className={`cite-ref mono${active ? ' cite-ref-active' : ''}`}
            onClick={() => onMarkClick(sourceId)}
            title="Показать источник"
          >
            {part}
          </button>
        )
      })}
    </>
  )
}
