import { describe, expect, it } from 'vitest'
import {
  dispatchChatEvent,
  SseParser,
  type ChatDoneEvent,
  type StatusEvent,
} from './sse'

describe('SseParser', () => {
  it('разбирает полный фрейм event+data', () => {
    const parser = new SseParser()
    const frames = parser.push('event: token\ndata: {"delta":"Привет"}\n\n')
    expect(frames).toEqual([{ event: 'token', data: '{"delta":"Привет"}' }])
  })

  it('буферизует частичные чанки и собирает фрейм из кусков', () => {
    const parser = new SseParser()
    expect(parser.push('event: sta')).toEqual([])
    expect(parser.push('tus\ndata: {"stage"')).toEqual([])
    const frames = parser.push(': "planner"}\n\n')
    expect(frames).toEqual([{ event: 'status', data: '{"stage": "planner"}' }])
  })

  it('несколько фреймов в одном чанке', () => {
    const parser = new SseParser()
    const frames = parser.push(
      'event: status\ndata: {"a":1}\n\nevent: token\ndata: {"b":2}\n\n',
    )
    expect(frames.map((f) => f.event)).toEqual(['status', 'token'])
  })

  it('поддерживает CRLF-разделители', () => {
    const parser = new SseParser()
    const frames = parser.push('event: done\r\ndata: {"x":true}\r\n\r\n')
    expect(frames).toEqual([{ event: 'done', data: '{"x":true}' }])
  })

  it('игнорирует keepalive-комментарии и пустые фреймы', () => {
    const parser = new SseParser()
    let frames = parser.push(': ping\n\n')
    expect(frames).toEqual([])
    frames = parser.push('\n\n')
    expect(frames).toEqual([])
  })

  it('склеивает несколько data-строк через \\n', () => {
    const parser = new SseParser()
    const frames = parser.push('data: line1\ndata: line2\n\n')
    expect(frames).toEqual([{ event: 'message', data: 'line1\nline2' }])
  })

  it('flush возвращает незавершённый хвост как фрейм', () => {
    const parser = new SseParser()
    parser.push('event: error\ndata: {"code":"x"')
    const frames = parser.flush()
    expect(frames).toEqual([{ event: 'error', data: '{"code":"x"' }])
    expect(parser.flush()).toEqual([])
  })
})

describe('dispatchChatEvent', () => {
  it('маршрутизирует события по имени', () => {
    const seen: string[] = []
    const done: ChatDoneEvent[] = []
    const statuses: StatusEvent[] = []
    dispatchChatEvent(
      'status',
      JSON.stringify({ stage: 'planner', iteration: 0, tools: ['vec'] }),
      { onStatus: (e) => statuses.push(e) },
    )
    dispatchChatEvent('token', JSON.stringify({ delta: 'О' }), {
      onToken: () => seen.push('token'),
    })
    dispatchChatEvent(
      'done',
      JSON.stringify({
        session_id: 's',
        answer: 'a',
        citations: [],
        status: 'ok',
        replans: 0,
        related_acts: [],
        notes: [],
        trace_id: null,
      }),
      { onDone: (e) => done.push(e) },
    )
    dispatchChatEvent('error', JSON.stringify({ code: 'c', message: 'm' }), {
      onError: (e) => seen.push(`error:${e.code}`),
    })
    expect(statuses[0].tools).toEqual(['vec'])
    expect(seen).toEqual(['token', 'error:c'])
    expect(done[0].status).toBe('ok')
  })

  it('битый JSON не роняет обработку', () => {
    expect(() =>
      dispatchChatEvent('token', '{broken', { onToken: () => undefined }),
    ).not.toThrow()
  })

  it('неизвестное имя события игнорируется', () => {
    expect(() =>
      dispatchChatEvent('keepalive', '{}', {}),
    ).not.toThrow()
  })
})
