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
    const confirmed = window.confirm(
      'Пересобрать весь корпус?\n\n' +
        'XML-файлы будут прочитаны заново из каталога APP_INGEST_CORPUS_DIR\n' +
        '(в compose: corpus_test/ репозитория → /data/corpus в контейнере backend).\n' +
        'Каждый акт перезаписывается в Qdrant/Neo4j (без дублей).',
    )
    if (!confirmed) return
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
          Полная пересборка корпуса правовых актов: XML → чанки → эмбеддинги → Qdrant,
          граф Act/Authority/Topic → Neo4j. Кнопка доступна только роли admin.
        </p>

        <div className="info-box">
          <b>Откуда берутся файлы:</b> backend читает каталог из переменной окружения{' '}
          <code className="mono">APP_INGEST_CORPUS_DIR</code> — в compose-стеке туда смонтирован
          каталог <code className="mono">corpus_test/</code> из корня репозитория (путь внутри
          контейнера: <code className="mono">/data/corpus</code>). Изменить источник можно без
          правки кода — через env.
        </div>

        <details className="admin-details">
          <summary>Как это работает</summary>
          <ol className="admin-steps">
            <li>
              Парсинг всех <code className="mono">*.xml</code> каталога: метаданные акта, чистка
              разметки, чанки по статьям.
            </li>
            <li>Эмбеддинги bge-m3 (CPU) → upsert в коллекцию Qdrant c payload (clearance и пр.).</li>
            <li>
              Граф в Neo4j: Act / Authority / Topic + рёбра ISSUED_BY, REFERENCES, HAS_TOPIC;
              LLM-экстракция Concept выключена по умолчанию (
              <code className="mono">APP_INGEST_EXTRACT_CONCEPTS=false</code>, полный цикл — CLI).
            </li>
            <li>
              Прогон идемпотентен: акт перезаписывается целиком (Qdrant upsert, Neo4j MERGE),
              повторный запуск не создаёт дублей; во время прогона ответы агента могут быть
              временно неполными.
            </li>
          </ol>
        </details>

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
