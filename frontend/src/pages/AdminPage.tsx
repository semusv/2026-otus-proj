import { useCallback, useEffect, useState } from 'react'
import { ApiError, ingestStatus, startIngest } from '../lib/api'
import type { components } from '../lib/api-types'

type IngestStatusResponse = components['schemas']['IngestStatusResponse']

interface AdminPageProps {
  onUnauthorized: () => void
}

const POLL_INTERVAL_MS = 2000

export default function AdminPage({ onUnauthorized }: AdminPageProps) {
  const [status, setStatus] = useState<IngestStatusResponse | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async (): Promise<IngestStatusResponse> => {
    const current = await ingestStatus()
    setStatus(current)
    return current
  }, [])

  // первичная загрузка + поллинг, пока идёт прогон
  useEffect(() => {
    let disposed = false
    let timer: ReturnType<typeof setTimeout> | undefined

    const tick = () => {
      void refresh()
        .then((current) => {
          if (!disposed && current.state === 'running') {
            timer = setTimeout(tick, POLL_INTERVAL_MS)
          }
        })
        .catch(() => {
          // статус может быть временно недоступен — не роняем экран
        })
    }
    tick()

    return () => {
      disposed = true
      if (timer !== undefined) clearTimeout(timer)
    }
  }, [refresh])

  const start = async () => {
    setBusy(true)
    setNotice(null)
    setError(null)
    try {
      await startIngest()
      setNotice('Прогон ingestion запущен')
      await refresh()
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) onUnauthorized()
        else if (err.status === 409) setNotice('Прогон уже выполняется')
        else setError(err.message)
      } else {
        setError('Не удалось запустить ingestion')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="admin-page">
      <div className="card admin-card">
        <h2>Ingestion корпуса</h2>
        <p className="muted">
          Разбор XML-актов → чанки → эмбеддинги → Qdrant; граф Act/Authority/Topic/Concept → Neo4j.
          Кнопка доступна только роли admin.
        </p>

        <div className="admin-controls">
          <button className="btn btn-primary" disabled={busy || status?.state === 'running'} onClick={() => void start()}>
            {busy ? 'Запуск…' : 'Запустить ingestion'}
          </button>
          <StateBadge state={status?.state} />
        </div>

        {notice !== null && <div className="info-box">{notice}</div>}
        {error !== null && (
          <div className="error-box" role="alert">
            {error}
          </div>
        )}

        {status != null && (
          <dl className="admin-status">
            <dt>Начат</dt>
            <dd className="mono">{formatDate(status.started_at)}</dd>
            <dt>Завершён</dt>
            <dd className="mono">{formatDate(status.finished_at)}</dd>
            <dt>Ошибка</dt>
            <dd className="mono">{status.error ?? '—'}</dd>
            <dt>Статистика</dt>
            <dd>
              {status.stats != null ? (
                <pre className="stats mono">{JSON.stringify(status.stats, null, 2)}</pre>
              ) : (
                '—'
              )}
            </dd>
          </dl>
        )}

        <p className="hint muted">Статус обновляется автоматически каждые {POLL_INTERVAL_MS / 1000} с во время прогона</p>
      </div>
    </section>
  )
}

function StateBadge({ state }: { state?: IngestStatusResponse['state'] }) {
  if (state === undefined) return <span className="badge badge-neutral">—</span>
  const cls =
    state === 'running' ? 'badge-info pulse' : state === 'done' ? 'badge-ok' : state === 'error' ? 'badge-err' : 'badge-neutral'
  return <span className={`badge ${cls}`}>{state}</span>
}

function formatDate(value: string | null | undefined): string {
  if (value == null || value.length === 0) return '—'
  try {
    return new Date(value).toLocaleString('ru-RU')
  } catch {
    return value
  }
}
