import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  ApiError,
  adminCreateUser,
  adminDeleteUser,
  adminListUsers,
  adminUpdateActClearance,
  adminUpdateUserRole,
  getActContent,
  ingestStatus,
  startIngest,
  storageStats,
} from '../lib/api'
import type { components } from '../lib/api-types'

type IngestStatusResponse = components['schemas']['IngestStatusResponse']
type StorageStats = components['schemas']['StorageStatsResponse']
type UserOut = components['schemas']['UserOut']
type UserCreate = components['schemas']['UserCreate']
type ActContentResponse = components['schemas']['ActContentResponse']

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
  const [users, setUsers] = useState<UserOut[] | null>(null)
  const [usersError, setUsersError] = useState<string | null>(null)
  const [newUsername, setNewUsername] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newRole, setNewRole] = useState<'viewer' | 'analyst' | 'admin'>('viewer')
  const [userNotice, setUserNotice] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [nowTick, setNowTick] = useState(() => Date.now())
  const [actId, setActId] = useState('')
  const [actData, setActData] = useState<ActContentResponse | null>(null)
  const [actError, setActError] = useState<string | null>(null)
  const [actNotice, setActNotice] = useState<string | null>(null)
  const [actBusy, setActBusy] = useState(false)

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

  const refreshUsers = useCallback(async () => {
    try {
      const list = await adminListUsers()
      setUsers(list.users)
      setUsersError(null)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setUsersError(err instanceof ApiError ? err.message : 'Не удалось получить список')
    }
  }, [onUnauthorized])

  const createUser = async (event: FormEvent) => {
    event.preventDefault()
    setUserNotice(null)
    setUsersError(null)
    try {
      const created = await adminCreateUser({
        username: newUsername,
        password: newPassword,
        role: newRole,
      })
      setUserNotice(
        `Создан ${created.username} (${created.role}): метки доступа ${created.clearances.join(', ')}`,
      )
      setNewUsername('')
      setNewPassword('')
      await refreshUsers()
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setUsersError('Это имя уже занято')
      else if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setUsersError(err instanceof ApiError ? err.message : 'Не удалось создать пользователя')
    }
  }

  const changeRole = async (target: UserOut, role: UserCreate['role']) => {
    if (role === target.role) return
    const confirmed = window.confirm(
      `Сменить роль ${target.username}: ${target.role} → ${role}?\n\n` +
        'Метки доступа обновятся сразу — пользователю не нужно перелогиниваться.',
    )
    if (!confirmed) return
    setUsersError(null)
    setUserNotice(null)
    try {
      const updated = await adminUpdateUserRole(target.user_id, role)
      setUserNotice(
        `Роль ${updated.username} изменена на ${updated.role}; метки доступа: ${updated.clearances.join(', ')}`,
      )
      await refreshUsers()
    } catch (err) {
      if (err instanceof ApiError && err.status === 403)
        setUsersError('Свою роль менять нельзя (защита от потери последнего админа)')
      else if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setUsersError(err instanceof ApiError ? err.message : 'Не удалось сменить роль')
      await refreshUsers()
    }
  }

  const deleteUser = async (target: UserOut) => {
    const hard = window.confirm(
      `Удалить ${target.username}?\n\n` +
        'ОК — мягкое удаление (деактивация, можно восстановить).\n' +
        'Отмена — физическое удаление (необратимо).',
    )
    if (!hard) {
      const reallyHard = window.confirm(
        `Физически удалить ${target.username}?\n\nЭто необратимо: данные будут удалены.`,
      )
      if (!reallyHard) return
    }
    setUsersError(null)
    setUserNotice(null)
    try {
      const result = await adminDeleteUser(target.user_id, !hard)
      setUserNotice(
        result.mode === 'deleted'
          ? `Пользователь ${result.username} физически удалён`
          : `Пользователь ${result.username} деактивирован`,
      )
      await refreshUsers()
    } catch (err) {
      if (err instanceof ApiError && err.status === 403)
        setUsersError('Нельзя удалить себя')
      else if (err instanceof ApiError && err.status === 409)
        setUsersError('Нельзя удалить последнего активного админа')
      else if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setUsersError(err instanceof ApiError ? err.message : 'Не удалось удалить пользователя')
    }
  }

  const findAct = async () => {
    if (actId.trim().length === 0) return
    setActError(null)
    setActNotice(null)
    setActData(null)
    setActBusy(true)
    try {
      const data = await getActContent(actId.trim())
      setActData(data)
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setActError('Акт не найден')
      else if (err instanceof ApiError && err.status === 403) setActError('Недостаточно прав для просмотра этого акта')
      else if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setActError(err instanceof ApiError ? err.message : 'Не удалось загрузить акт')
    } finally {
      setActBusy(false)
    }
  }

  const changeClearance = async (clearance: 'PUBLIC' | 'INTERNAL' | 'SECRET') => {
    if (actData === null) return
    const confirmed = window.confirm(
      `Сменить гриф акта ${actData.act.act_id}: ${actData.act.clearance} → ${clearance}?\n\n` +
        'Обновится в Neo4j и Qdrant синхронно.',
    )
    if (!confirmed) return
    setActError(null)
    setActNotice(null)
    setActBusy(true)
    try {
      const updated = await adminUpdateActClearance(actData.act.act_id, clearance)
      setActNotice(`Гриф акта ${updated.act_id} изменён на ${updated.clearance}`)
      setActData((prev) => (prev !== null ? { ...prev, act: updated } : null))
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setActError('Акт не найден')
      else if (err instanceof ApiError && err.status === 422) setActError('Недопустимая метка грифа')
      else if (err instanceof ApiError && err.status === 401) onUnauthorized()
      else setActError(err instanceof ApiError ? err.message : 'Не удалось сменить гриф')
    } finally {
      setActBusy(false)
    }
  }

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
      void refreshUsers()
      tick()
    }, 0)

    return () => {
      disposed = true
      if (timer !== undefined) clearTimeout(timer)
    }
  }, [refreshStatus, refreshStats, refreshUsers])

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

        <h3 className="stats-title">Пользователи</h3>
        <p className="muted stats-subtitle">
          Роль определяет метки доступа: viewer → PUBLIC · analyst → PUBLIC+INTERNAL · admin → все
          (включая SECRET). Созданный пользователь сразу может войти.
        </p>

        {usersError !== null && <div className="error-box">{usersError}</div>}
        {userNotice !== null && <div className="info-box">{userNotice}</div>}

        {users === null ? (
          <p className="muted">{usersError === null ? 'Загрузка…' : ''}</p>
        ) : (
          <div className="user-list">
            {users.map((u) => (
              <div key={u.user_id} className={`user-row${u.is_active ? '' : ' user-row-inactive'}`} title={u.created_at}>
                <span className="mono user-name">{u.username}</span>
                <span className={`badge ${u.role === 'admin' ? 'badge-err' : u.role === 'analyst' ? 'badge-info' : 'badge-neutral'}`}>
                  {u.role}
                </span>
                <span className="chip-row">
                  {u.clearances.map((c) => (
                    <span key={c} className={`clearance-chip clearance-${c.toLowerCase()}`}>{c}</span>
                  ))}
                </span>
                {!u.is_active && <span className="badge badge-neutral">деактивирован</span>}
                <select
                  className="role-select"
                  value={u.role}
                  onChange={(e) => void changeRole(u, e.target.value as UserCreate['role'])}
                  title="Сменить роль (права применяются сразу)"
                >
                  <option value="viewer">viewer</option>
                  <option value="analyst">analyst</option>
                  <option value="admin">admin</option>
                </select>
                <button
                  type="button"
                  className="btn-danger-small"
                  onClick={() => void deleteUser(u)}
                  title="Удалить пользователя"
                >
                  Удалить
                </button>
              </div>
            ))}
          </div>
        )}

        <form className="user-create" onSubmit={(e) => void createUser(e)}>
          <input
            className="mono"
            value={newUsername}
            onChange={(e) => setNewUsername(e.target.value)}
            placeholder="логин"
            required
            minLength={3}
            maxLength={64}
            pattern="[a-zA-Z0-9_.\-]+"
            title="Латиница, цифры, . - _ ; от 3 символов"
          />
          <input
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            placeholder="пароль (мин. 8)"
            required
            minLength={8}
            maxLength={72}
          />
          <select value={newRole} onChange={(e) => setNewRole(e.target.value as typeof newRole)}>
            <option value="viewer">viewer (PUBLIC)</option>
            <option value="analyst">analyst (+INTERNAL)</option>
            <option value="admin">admin (все)</option>
          </select>
          <button className="btn btn-primary btn-small" type="submit">
            Создать
          </button>
        </form>

        <p className="hint muted">Статус обновляется автоматически каждые {POLL_INTERVAL_MS / 1000} с во время прогона</p>

        <h3 className="stats-title">Грифы актов</h3>
        <p className="muted stats-subtitle">
          Ручная перекатегоризация: смена грифа clearance обновляет метку в Neo4j и payload всех чанков в Qdrant.
        </p>

        {actError !== null && <div className="error-box">{actError}</div>}
        {actNotice !== null && <div className="info-box">{actNotice}</div>}

        <form className="act-search" onSubmit={(e) => { e.preventDefault(); void findAct() }}>
          <input
            className="mono"
            value={actId}
            onChange={(e) => setActId(e.target.value)}
            placeholder="ID акта (напр. 102010098)"
            required
          />
          <button className="btn btn-primary btn-small" type="submit" disabled={actBusy}>
            {actBusy ? 'Поиск…' : 'Найти'}
          </button>
        </form>

        {actData !== null && (
          <div className="act-card">
            <dl className="act-meta-inline">
              <dt>Название</dt>
              <dd>{actData.act.title}</dd>
              {actData.act.doc_number != null && (
                <>
                  <dt>Номер</dt>
                  <dd className="mono">{actData.act.doc_number}</dd>
                </>
              )}
              {actData.act.date != null && (
                <>
                  <dt>Дата</dt>
                  <dd>{actData.act.date}</dd>
                </>
              )}
              <dt>Гриф</dt>
              <dd>
                <span className={`clearance-chip clearance-${actData.act.clearance.toLowerCase()}`}>
                  {actData.act.clearance}
                </span>
              </dd>
              <dt>Чанков</dt>
              <dd>{actData.chunk_count}</dd>
            </dl>
            <div className="act-clearance-controls">
              <span className="muted">Сменить гриф:</span>
              {(['PUBLIC', 'INTERNAL', 'SECRET'] as const).map((c) => (
                <button
                  key={c}
                  type="button"
                  className={`btn btn-small ${actData.act.clearance === c ? 'btn-primary' : 'btn-ghost'}`}
                  disabled={actData.act.clearance === c || actBusy}
                  onClick={() => void changeClearance(c)}
                >
                  {c}
                </button>
              ))}
            </div>
          </div>
        )}
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
