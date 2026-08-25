import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, newTraceId, parseErrorPayload } from './api'
import { clearAuth, getAuth, saveAuth } from './auth'

const memoryStore = new Map<string, string>()

beforeEach(() => {
  memoryStore.clear()
  vi.stubGlobal('sessionStorage', {
    getItem: (key: string) => memoryStore.get(key) ?? null,
    setItem: (key: string, value: string) => void memoryStore.set(key, value),
    removeItem: (key: string) => void memoryStore.delete(key),
  })
})

describe('parseErrorPayload', () => {
  it(' ErrorResponse из контракта -> ApiError c code/message/status', () => {
    const err = parseErrorPayload(
      { code: 'invalid_credentials', message: 'Неверный логин или пароль', details: null },
      401,
    )
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(401)
    expect(err.code).toBe('invalid_credentials')
    expect(err.message).toBe('Неверный логин или пароль')
  })

  it('HTTPValidationError -> code=validation_error, msg из detail[0]', () => {
    const err = parseErrorPayload(
      {
        detail: [
          { loc: ['body', 'username'], msg: 'String should have at least 3 characters', type: 'too_short' },
        ],
      },
      422,
    )
    expect(err.code).toBe('validation_error')
    expect(err.status).toBe(422)
    expect(err.message).toContain('at least 3 characters')
  })

  it('нечитаемое тело -> fallback по HTTP-статусу', () => {
    const err403 = parseErrorPayload(null, 403)
    expect(err403.code).toBe('http_403')
    expect(err403.message).toContain('прав')

    const err500 = parseErrorPayload('<html>oops</html>', 500)
    expect(err500.code).toBe('http_500')
  })

  it('неизвестный статус -> http_<status>', () => {
    const err = parseErrorPayload(undefined, 599)
    expect(err.code).toBe('http_599')
  })
})

describe('newTraceId', () => {
  it('32 hex-символа (валидный X-Trace-Id для бэкенда)', () => {
    const id = newTraceId()
    expect(id).toMatch(/^[0-9a-f]{32}$/)
    expect(newTraceId()).not.toBe(id)
  })
})

describe('auth storage (sessionStorage)', () => {
  const token = {
    access_token: 'jwt-value',
    token_type: 'bearer' as const,
    expires_in: 1800,
    username: 'analyst',
    role: 'analyst',
  }

  it('save/get roundtrip', () => {
    saveAuth(token)
    const auth = getAuth()
    expect(auth?.accessToken).toBe('jwt-value')
    expect(auth?.role).toBe('analyst')
    expect(auth?.clearances).toEqual([])
  })

  it('протухший токен отбрасывается', () => {
    saveAuth({ ...token, expires_in: -30 })
    expect(getAuth()).toBeNull()
    expect(memoryStore.size).toBe(0)
  })

  it('битая запись отбрасывается', () => {
    memoryStore.set('graphrag.auth.v1', '{not json')
    expect(getAuth()).toBeNull()
  })

  it('clearAuth удаляет запись', () => {
    saveAuth(token)
    clearAuth()
    expect(getAuth()).toBeNull()
  })
})
