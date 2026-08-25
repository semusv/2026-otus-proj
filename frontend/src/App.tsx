import { useCallback, useState } from 'react'
import AdminPage from './pages/AdminPage'
import ChatPage from './pages/ChatPage'
import LoginPage, { Logo } from './pages/LoginPage'
import { clearAuth, getAuth, type StoredAuth } from './lib/auth'

type Screen = 'chat' | 'admin'

export default function App() {
  const [auth, setAuth] = useState<StoredAuth | null>(() => getAuth())
  const [screen, setScreen] = useState<Screen>('chat')

  const onAuthed = useCallback(() => {
    setAuth(getAuth())
    setScreen('chat')
  }, [])

  const logout = useCallback(() => {
    clearAuth()
    setAuth(null)
  }, [])

  if (auth === null) {
    return <LoginPage onAuthed={onAuthed} onUnauthorizedError={logout} />
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-brand">
          <Logo />
          <span className="app-title">GraphRAG</span>
        </div>
        <nav className="app-nav">
          <button
            className={`nav-btn${screen === 'chat' ? ' nav-btn-active' : ''}`}
            onClick={() => setScreen('chat')}
          >
            Чат
          </button>
          {auth.role === 'admin' && (
            <button
              className={`nav-btn${screen === 'admin' ? ' nav-btn-active' : ''}`}
              onClick={() => setScreen('admin')}
            >
              Админ
            </button>
          )}
        </nav>
        <div className="app-user">
          <span className="user-name">{auth.username}</span>
          <span className={`badge role-${auth.role}`}>{auth.role}</span>
          {auth.clearances.length > 0 && (
            <span className="user-clearances muted mono" title="метки доступа роли">
              [{auth.clearances.join(', ')}]
            </span>
          )}
          <button className="btn btn-ghost" onClick={logout}>
            Выйти
          </button>
        </div>
      </header>

      <main className="app-main">
        {screen === 'chat' ? (
          <ChatPage onUnauthorized={logout} />
        ) : (
          <AdminPage onUnauthorized={logout} />
        )}
      </main>
    </div>
  )
}
