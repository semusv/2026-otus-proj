// Аутентификация: JWT в sessionStorage (решение этапа 8 — живёт до закрытия вкладки),
// контроль срока по expires_in из TokenResponse, профиль (clearances) из /auth/me.

import type { components } from './api-types'

export type TokenResponse = components['schemas']['TokenResponse']
export type MeResponse = components['schemas']['MeResponse']

const STORAGE_KEY = 'graphrag.auth.v1'

/** Запас в 10 секунд: токен считается протухшим чуть раньше, чем реально истечёт. */
const EXPIRY_MARGIN_MS = 10_000

export interface StoredAuth {
  accessToken: string
  /** Epoch ms, когда токен истекает. */
  expiresAt: number
  username: string
  role: string
  clearances: string[]
}

export function saveAuth(token: TokenResponse): StoredAuth {
  const auth: StoredAuth = {
    accessToken: token.access_token,
    expiresAt: Date.now() + token.expires_in * 1000,
    username: token.username,
    role: token.role,
    clearances: [],
  }
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(auth))
  return auth
}

/** Обогащает сохранённую запись профилем из /auth/me (clearances для бейджа). */
export function applyProfile(me: MeResponse): void {
  const current = getAuth()
  if (current === null) return
  const updated: StoredAuth = { ...current, username: me.username, role: me.role, clearances: me.clearances }
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(updated))
}

export function getAuth(): StoredAuth | null {
  const raw = sessionStorage.getItem(STORAGE_KEY)
  if (raw === null) return null
  try {
    const parsed = JSON.parse(raw) as Partial<StoredAuth>
    if (
      typeof parsed.accessToken !== 'string' ||
      typeof parsed.expiresAt !== 'number' ||
      typeof parsed.username !== 'string' ||
      typeof parsed.role !== 'string'
    ) {
      return null
    }
    if (Date.now() >= parsed.expiresAt - EXPIRY_MARGIN_MS) {
      clearAuth()
      return null
    }
    return { clearances: [], ...parsed } as StoredAuth
  } catch {
    clearAuth()
    return null
  }
}

export function clearAuth(): void {
  sessionStorage.removeItem(STORAGE_KEY)
}
