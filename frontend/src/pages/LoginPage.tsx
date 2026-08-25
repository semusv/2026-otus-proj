import { useState, type FormEvent } from 'react'
import { ApiError, login, me } from '../lib/api'
import { applyProfile, getAuth, saveAuth } from '../lib/auth'

interface LoginPageProps {
  onAuthed: (username: string) => void
  onUnauthorizedError: () => void
}

export default function LoginPage({ onAuthed, onUnauthorizedError }: LoginPageProps) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setPending(true)
    try {
      const token = await login({ username, password })
      saveAuth(token)
      try {
        applyProfile(await me())
      } catch {
        // профиль не критичен: роль уже есть в TokenResponse
      }
      const auth = getAuth()
      if (auth !== null) onAuthed(auth.username)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError('Неверный логин или пароль')
      } else if (err instanceof ApiError) {
        setError(err.message)
        if (err.status === 0) onUnauthorizedError()
      } else {
        setError('Неизвестная ошибка входа')
      }
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="login-wrap">
      <form className="card login-card" onSubmit={(e) => void submit(e)}>
        <div className="login-head">
          <Logo />
          <h1>GraphRAG</h1>
          <p className="muted">Платформа корпоративных знаний · вход по учётной записи</p>
        </div>
        <label className="field">
          <span>Логин</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            required
            minLength={3}
            placeholder="viewer / analyst / admin"
          />
        </label>
        <label className="field">
          <span>Пароль</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
            minLength={8}
            placeholder="••••••••"
          />
        </label>
        {error !== null && (
          <div className="error-box" role="alert">
            {error}
          </div>
        )}
        <button className="btn btn-primary" type="submit" disabled={pending}>
          {pending ? <Spinner /> : 'Войти'}
        </button>
        <p className="hint muted">JWT выдаётся бэкендом и хранится до закрытия вкладки</p>
      </form>
    </div>
  )
}

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg
      className="logo"
      width={size}
      height={size}
      viewBox="0 0 32 32"
      aria-hidden="true"
    >
      <path d="M11 21L14 12M18 11l5 7M12 24h9" stroke="var(--faint)" strokeWidth="1.5" fill="none" />
      <circle cx="8" cy="24" r="4" fill="var(--accent)" />
      <circle cx="24" cy="20" r="3.5" fill="var(--ok)" />
      <circle cx="16" cy="8" r="4.5" fill="var(--accent-hover)" />
    </svg>
  )
}

export function Spinner() {
  return <span className="spinner" aria-label="загрузка" />
}
