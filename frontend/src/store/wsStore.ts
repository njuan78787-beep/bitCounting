// =============================================================================
// src/store/wsStore.ts
// Zustand store for WebSocket connection + real-time event bus.
// =============================================================================

import { create } from 'zustand'
import type { WsMessage, WsEventType } from '../types/api'

export type WsStatus = 'disconnected' | 'connecting' | 'connected' | 'error'

type Listener = (msg: WsMessage) => void

interface WsState {
  status:       WsStatus
  lastPing:     string | null
  recentEvents: WsMessage[]   // last 50 events for activity feed

  // Internal (not persisted)
  _socket:    WebSocket | null
  _listeners: Map<WsEventType | '*', Set<Listener>>

  // Actions
  connect:     (token: string) => void
  disconnect:  () => void
  subscribe:   (event: WsEventType | '*', fn: Listener) => () => void
  _dispatch:   (msg: WsMessage) => void
  _setStatus:  (s: WsStatus) => void
}

const MAX_EVENTS = 50
const RECONNECT_DELAY_MS = 3000

let _reconnectTimer: ReturnType<typeof setTimeout> | null = null

export const useWsStore = create<WsState>()((set, get) => ({
  status:       'disconnected',
  lastPing:     null,
  recentEvents: [],
  _socket:      null,
  _listeners:   new Map(),

  connect: (token: string) => {
    const { _socket, disconnect } = get()
    if (_socket) disconnect()

    set({ status: 'connecting' })

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${window.location.host}/ws/events?token=${encodeURIComponent(token)}`

    let socket: WebSocket
    try {
      socket = new WebSocket(url)
    } catch {
      set({ status: 'error' })
      return
    }

    socket.onopen = () => {
      set({ status: 'connected', _socket: socket })
      if (_reconnectTimer) { clearTimeout(_reconnectTimer); _reconnectTimer = null }
    }

    socket.onmessage = (ev: MessageEvent<string>) => {
      try {
        const msg = JSON.parse(ev.data) as WsMessage
        get()._dispatch(msg)
      } catch {
        // ignore malformed messages
      }
    }

    socket.onerror = () => {
      set({ status: 'error' })
    }

    socket.onclose = () => {
      set({ status: 'disconnected', _socket: null })
      // Auto-reconnect after delay
      if (!_reconnectTimer) {
        _reconnectTimer = setTimeout(() => {
          _reconnectTimer = null
          get().connect(token)
        }, RECONNECT_DELAY_MS)
      }
    }

    set({ _socket: socket })
  },

  disconnect: () => {
    const { _socket } = get()
    if (_reconnectTimer) { clearTimeout(_reconnectTimer); _reconnectTimer = null }
    if (_socket) {
      _socket.onclose = null  // prevent reconnect loop
      _socket.close()
    }
    set({ status: 'disconnected', _socket: null })
  },

  subscribe: (event, fn) => {
    const { _listeners } = get()
    if (!_listeners.has(event)) _listeners.set(event, new Set())
    _listeners.get(event)!.add(fn)
    // Return unsubscribe
    return () => {
      _listeners.get(event)?.delete(fn)
    }
  },

  _dispatch: (msg: WsMessage) => {
    const { _listeners } = get()

    // Ping handling
    if (msg.event === 'ping') {
      set({ lastPing: msg.timestamp })
      return
    }

    // Update recent events
    set((s) => ({
      recentEvents: [msg, ...s.recentEvents].slice(0, MAX_EVENTS),
    }))

    // Notify specific listeners
    _listeners.get(msg.event)?.forEach((fn) => fn(msg))
    // Notify wildcard listeners
    _listeners.get('*')?.forEach((fn) => fn(msg))
  },

  _setStatus: (status) => set({ status }),
}))

// Convenience selectors
export const useWsStatus   = () => useWsStore((s) => s.status)
export const useWsEvents   = () => useWsStore((s) => s.recentEvents)
