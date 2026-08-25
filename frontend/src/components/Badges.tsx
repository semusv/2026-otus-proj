import type { ChatStatus } from '../lib/sse'

const CLEARANCE_CLASS: Record<string, string> = {
  PUBLIC: 'badge-clearance-public',
  INTERNAL: 'badge-clearance-internal',
  SECRET: 'badge-clearance-secret',
}

export function ClearanceBadge({ clearance }: { clearance: string }) {
  const key = clearance.toUpperCase()
  return (
    <span className={`badge ${CLEARANCE_CLASS[key] ?? 'badge-neutral'}`}>{clearance}</span>
  )
}

const STATUS_LABEL: Record<ChatStatus, string> = {
  ok: 'ok',
  degraded: 'degraded',
  empty: 'пусто',
}

export function StatusBadge({ status }: { status: ChatStatus }) {
  const cls =
    status === 'ok' ? 'badge-ok' : status === 'degraded' ? 'badge-warn' : 'badge-neutral'
  return <span className={`badge ${cls}`}>{STATUS_LABEL[status]}</span>
}

export default ClearanceBadge
