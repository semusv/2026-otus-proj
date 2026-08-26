// Тонкая fetch-обёртка над API (контракт docs/api/openapi.yaml):
// Bearer из sessionStorage, X-Trace-Id на каждый запрос, разбор ошибок по схемам
// ErrorResponse / HTTPValidationError. Базовый URL пустой — same-origin через nginx-прокси.

import type { components } from './api-types'
import { clearAuth, getAuth } from './auth'
import {
  dispatchChatEvent,
  SseParser,
  type ChatStreamHandlers,
  type SseFrame,
} from './sse'

export type LoginRequest = components['schemas']['LoginRequest']
export type MeResponse = components['schemas']['MeResponse']
export type TokenResponse = components['schemas']['TokenResponse']
export type IngestStartResponse = components['schemas']['IngestStartResponse']
export type IngestStatusResponse = components['schemas']['IngestStatusResponse']
export type ChatRequest = components['schemas']['ChatRequest']
export type StorageStats = components['schemas']['StorageStatsResponse']
export type UserCreate = components['schemas']['UserCreate']
export type UserOut = components['schemas']['UserOut']
export type UsersListResponse = components['schemas']['UsersListResponse']
export type DeleteUserResponse = components['schemas']['DeleteUserResponse']
export type ActContentResponse = components['schemas']['ActContentResponse']
export type ActOut = components['schemas']['ActOut']

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: unknown

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

/** 32 hex-символа — валидный X-Trace-Id для бэкенда (этап 7: берётся как есть). */
export function newTraceId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

const FALLBACK_MESSAGES: Record<number, string> = {
  400: 'Некорректный запрос',
  401: 'Неверные учётные данные или сессия истекла',
  403: 'Недостаточно прав для операции',
  404: 'Ресурс не найден',
  409: 'Конфликт: операция уже выполняется',
  500: 'Внутренняя ошибка сервера',
  502: 'Сервер недоступен',
  503: 'Сервис временно недоступен',
}

/**
 * Разбирает тело ошибки по схемам контракта:
 * ErrorResponse {code, message, details?}, HTTPValidationError {detail: [{loc, msg, type}]}.
 * Чистая функция — покрыта vitest smoke-тестами.
 */
export function parseErrorPayload(payload: unknown, status: number): ApiError {
  if (typeof payload === 'object' && payload !== null) {
    const record = payload as Record<string, unknown>
    if (typeof record.code === 'string' && typeof record.message === 'string') {
      return new ApiError(status, record.code, record.message, record.details)
    }
    if (Array.isArray(record.detail)) {
      const first = record.detail[0] as Record<string, unknown> | undefined
      const message =
        first !== undefined && typeof first.msg === 'string'
          ? first.msg
          : FALLBACK_MESSAGES[status] ?? 'Ошибка запроса'
      return new ApiError(status, 'validation_error', message, record.detail)
    }
  }
  const fallback = FALLBACK_MESSAGES[status] ?? `Неизвестная ошибка (HTTP ${status})`
  return new ApiError(status, `http_${status}`, fallback)
}

function authHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra)
  headers.set('X-Trace-Id', newTraceId())
  const auth = getAuth()
  if (auth !== null) headers.set('Authorization', `Bearer ${auth.accessToken}`)
  return headers
}

async function parseErrorResponse(response: Response): Promise<ApiError> {
  let payload: unknown = null
  try {
    payload = await response.json()
  } catch {
    // тело не JSON — уйдём во fallback ниже
  }
  return parseErrorPayload(payload, response.status)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      headers: authHeaders(init?.headers),
    })
  } catch {
    throw new ApiError(0, 'network', 'Сервер недоступен')
  }
  if (!response.ok) {
    const error = await parseErrorResponse(response)
    if (error.status === 401) clearAuth() // протухший/отозванный JWT — разлогин
    throw error
  }
  return (await response.json()) as T
}

export function login(body: LoginRequest): Promise<TokenResponse> {
  return request<TokenResponse>('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function me(): Promise<MeResponse> {
  return request<MeResponse>('/auth/me')
}

export function startIngest(): Promise<IngestStartResponse> {
  return request<IngestStartResponse>('/admin/ingest', { method: 'POST' })
}

export function ingestStatus(): Promise<IngestStatusResponse> {
  return request<IngestStatusResponse>('/admin/ingest/status')
}

/** Агрегаты наполнения хранилищ (Qdrant/Neo4j/PG) — admin-only, этап 8. */
export function storageStats(): Promise<StorageStats> {
  return request<StorageStats>('/admin/stats')
}

/** Саморегистрация: всегда роль viewer (PUBLIC), JWT выдаётся сразу. */
export function register(body: LoginRequest): Promise<TokenResponse> {
  return request<TokenResponse>('/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** Список учётных записей — admin-only. */
export function adminListUsers(): Promise<UsersListResponse> {
  return request<UsersListResponse>('/admin/users')
}

/** Создать пользователя с указанной ролью — admin-only. */
export function adminCreateUser(body: UserCreate): Promise<UserOut> {
  return request<UserOut>('/admin/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** Сменить роль пользователя — admin-only; права применяются сразу, без перелогина. */
export function adminUpdateUserRole(userId: string, role: UserCreate['role']): Promise<UserOut> {
  return request<UserOut>(`/admin/users/${userId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role }),
  })
}

/** Удалить пользователя — admin-only. hard=false → деактивация, hard=true → физическое удаление. */
export function adminDeleteUser(userId: string, hard: boolean): Promise<DeleteUserResponse> {
  return request<DeleteUserResponse>(`/admin/users/${userId}?hard=${hard}`, {
    method: 'DELETE',
  })
}

/** Деактивировать/реактивировать пользователя — admin-only. */
export function adminSetUserStatus(userId: string, isActive: boolean): Promise<UserOut> {
  return request<UserOut>(`/admin/users/${userId}/status`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_active: isActive }),
  })
}

/** Полный текст акта с метаданными — авторизованный пользователь (ACL по clearance). */
export function getActContent(actId: string): Promise<ActContentResponse> {
  return request<ActContentResponse>(`/api/acts/${actId}/content`)
}

/** Сменить гриф акта — admin-only; обновляет Neo4j + Qdrant синхронно. */
export function adminUpdateActClearance(
  actId: string,
  clearance: 'PUBLIC' | 'INTERNAL' | 'SECRET',
): Promise<ActOut> {
  return request<ActOut>(`/admin/acts/${actId}/clearance`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ clearance }),
  })
}

/**
 * SSE-стрим чата. POST + ReadableStream (EventSource не умеет POST/Bearer),
 * парсинг фреймов через SseParser. Ошибки HTTP бросаются как ApiError.
 */
export async function chatStream(
  body: ChatRequest,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response
  try {
    response = await fetch('/api/chat', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ ...body, stream: true }),
      signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') return
    throw new ApiError(0, 'network', 'Сервер недоступен')
  }
  if (!response.ok) {
    const error = await parseErrorResponse(response)
    if (error.status === 401) clearAuth()
    throw error
  }
  if (response.body === null) {
    throw new ApiError(0, 'no_stream', 'Пустой поток ответа')
  }

  const parser = new SseParser()
  const reader = response.body.getReader()
  const decoder = new TextDecoder()

  const dispatchFrame = (frame: SseFrame) => dispatchChatEvent(frame.event, frame.data, handlers)
  const handle = (chunk: string) => {
    for (const frame of parser.push(chunk)) dispatchFrame(frame)
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    handle(decoder.decode(value, { stream: true }))
  }
  for (const frame of parser.flush()) dispatchFrame(frame)
}
