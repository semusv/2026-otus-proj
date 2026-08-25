import { useCallback, useEffect, useState } from 'react'
import { ApiError, ingestStatus, startIngest, storageStats } from '../lib/api'
import type { components } from '../lib/api-types'

type IngestStatusResponse = components['schemas']['IngestStatusResponse']
type StorageStats = components['schemas']['StorageStatsResponse']

interface AdminPageProps {
  onUnauthorized: () => void
}

const POLL_INTERVAL_MS = 2000

/** Первый прогон corpus_test (~100 XML / ~2300 чанков) на CPU занимает десятки минут. */
const RUNNING_HINT =
  'Эмбеддинги считаются на CPU: полный прогон corpus_test может идти 10–30 минут. ' +
  'Прогресс виден в логах backend (docker logs graphrag-backend).'

export default function AdminPage({ onUnauthorized }: AdminPageProps) {
  const [status, setStatus] = useState<IngestStatusResponse | null>(null)
  const [stats, setStats] = useState<StorageStats | null>(null)
  const [statsError, setStatsError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [nowTick, setNowTick] = useState(() => Date.now())

  const refreshStatus = useCallback(async (): Promise<IngestStatusResponse> => {
    const current = await ingestStatus()
    setStatus(current)
    return current
  }, [])

  const refreshStats = useCallback(async () => {
    try {
      setStats(await storageStats())
      setStatsError(null)
    } catch (err) {
      setStatsError(err instanceof ApiError ? err.message : 'Не удалось получить статистику')
    }
  }, [])

  // первичная загрузка + поллинг статуса и статистики, пока идёт прогон
  useEffect(() => {
    let disposed = false
    let timer: ReturnType<typeof setTimeout> | undefined

    const tick = () => {
      void refreshStatus()
        .then(async (current) => {
          if (current.state === 'running') await refreshStats()
          if (!disposed && current.state === 'running') {
            timer = setTimeout(tick, POLL_INTERVAL_MS)
          }
        })
        .catch(() => {
          // статус может быть временно недоступен — не роняем экран
        })
    }

    timer = setTimeout(() => {
      void refreshStats()
      tick()
    }, 0)

    return () => {
      disposed = true
      if (timer !== undefined) clearTimeout(timer)
    }
  }, [refreshStatus, refreshStats])

  // тикер прошедшего времени во время прогона
  useEffect(() => {
    if (status?.state !== 'running') return
    const id = setInterval(() => setNowTick(Date.now()), 1000)
    return () => clearInterval(id)
  }, [status?.state])

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
      setNotice('Прогон ingestion запущен. ' + RUNNING_HINT)
      await refreshStatus()
      await refreshStats()
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

  const running = status?.state === 'running'

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
          <button className="btn btn-primary" disabled={busy || running} onClick={() => void start()}>
            {busy ? 'Запуск…' : 'Запустить ingestion'}
          </button>
          <StateBadge state={status?.state} />
          {running && status?.started_at !== null && status?.started_at !== undefined && (
            <span className="mono muted" title={RUNNING_HINT}>
              идёт {formatElapsed(status.started_at, nowTick)}
            </span>
          )}
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
            <dt>Статистика прогона</dt>
            <dd>
              {status.stats != null ? (
                <pre className="stats mono">{JSON.stringify(status.stats, null, 2)}</pre>
              ) : running ? (
                '— считается; счётчики хранилищ ниже обновляются автоматически'
              ) : (
                '—'
              )}
            </dd>
          </dl>
        )}

        <h3 className="stats-title">Состояние хранилищ</h3>
        <p className="muted stats-subtitle">
          Текущее наполнение БД (GET /admin/stats): векторы Qdrant, узлы/рёбра графа Neo4j,
          пользователи и история диалогов PG.
        </p>
        {statsError !== null && <div className="error-box">{statsError}</div>}
        {stats === null ? (
          <p className="muted">{statsError === null ? 'Загрузка…' : ''}</p>
        ) : (
          <div className="stat-grid">
            <StatTile label={`Qdrant · ${stats.qdrant.collection}`} value={stats.qdrant.points} hint="векторов чанков" />
            <StatTile label="Neo4j · Act" value={stats.neo4j.acts} hint="актов" />
            <StatTile label="Neo4j · Authority" value={stats.neo4j.authorities} hint="органов власти" />
            <StatTile label="Neo4j · Topic" value={stats.neo4j.topics} hint="тем" />
            <StatTile label="Neo4j · Concept" value={stats.neo4j.concepts} hint="понятий (LLM)" />
            <StatTile label="Neo4j · рёбра" value={stats.neo4j.relationships} hint="связей всего" />
            <StatTile label="PG · пользователи" value={stats.postgres.users} hint="" />
            <StatTile label="PG · диалоги" value={stats.postgres.chat_sessions} hint="chat_sessions" />
            <StatTile label="PG · сообщения" value={stats.postgres.chat_messages} hint="chat_messages" />
          </div>
        )}
        <div className="admin-controls">
          <button type="button" className="btn btn-ghost btn-small" onClick={() => void refreshStats()}>
            Обновить статистику
          </button>
        </div>

        <p className="hint muted">Статус обновляется автоматически каждые {POLL_INTERVAL_MS / 1000} с во время прогона</p>
      </div>
    </section>
  )
}

function StatTile({ label, value, hint }: { label: string; value: number; hint: string }) {
  return (
    <div className="stat-tile" title={label}>
      <span className="stat-value mono">{value.toLocaleString('ru-RU')}</span>
      <span className="stat-label">{label}</span>
      {hint.length > 0 && <span className="stat-hint muted">{hint}</span>}
    </div>
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

function formatElapsed(startedAt: string, nowMs: number): string {
  const started = new Date(startedAt).getTime()
  if (Number.isNaN(started)) return ''
  let sec = Math.max(0, Math.floor((nowMs - started) / 1000))
  const min = Math.floor(sec / 60)
  sec %= 60
  const hours = Math.floor(min / 60)
  const mm = min % 60
  return hours > 0 ? `${hours} ч ${mm} мин` : `${min} мин ${sec.toString().padStart(2, '0')} с`
}
