import { useState, type FormEvent } from 'react'
import { ApiError, login, me, register } from '../lib/api'
import { applyProfile, getAuth, saveAuth } from '../lib/auth'

interface LoginPageProps {
  onAuthed: (username: string) => void
  onUnauthorizedError: () => void
}

export default function LoginPage({ onAuthed, onUnauthorizedError }: LoginPageProps) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setPending(true)
    try {
      // регистрация сознательно даёт минимальную роль viewer (PUBLIC);
      // расширение доступа — только через администратора (Admin → Пользователи)
      const token =
        mode === 'login' ? await login({ username, password }) : await register({ username, password })
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
      } else if (err instanceof ApiError && err.status === 409) {
        setError('Это имя уже занято')
      } else if (err instanceof ApiError) {
        setError(err.message)
        if (err.status === 0) onUnauthorizedError()
      } else {
        setError(mode === 'login' ? 'Неизвестная ошибка входа' : 'Не удалось зарегистрироваться')
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
          <p className="muted">
            {mode === 'login'
              ? 'Платформа корпоративных знаний · вход по учётной записи'
              : 'Создание учётной записи · роль viewer (доступ только к PUBLIC)'}
          </p>
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
            maxLength={64}
            pattern="[a-zA-Z0-9_.\-]+"
            title="Латиница, цифры, точка, дефис, подчёркивание; от 3 символов"
            placeholder={mode === 'login' ? 'viewer / analyst / admin' : 'придумайте логин'}
          />
        </label>
        <label className="field">
          <span>Пароль</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            required
            minLength={8}
            maxLength={72}
            placeholder={mode === 'login' ? '••••••••' : 'минимум 8 символов'}
          />
        </label>
        {error !== null && (
          <div className="error-box" role="alert">
            {error}
          </div>
        )}
        <button className="btn btn-primary" type="submit" disabled={pending}>
          {pending ? <Spinner /> : mode === 'login' ? 'Войти' : 'Зарегистрироваться'}
        </button>
        <button type="button" className="linklike" onClick={() => {
          setMode(mode === 'login' ? 'register' : 'login')
          setError(null)
        }}>
          {mode === 'login' ? 'Нет учётной записи? Зарегистрироваться' : 'Уже есть аккаунт? Войти'}
        </button>
        <p className="hint muted">
          {mode === 'login'
            ? 'JWT выдаётся бэкендом и хранится до закрытия вкладки'
            : 'Роль analyst/admin выдаёт администратор во вкладке Админ'}
        </p>
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
