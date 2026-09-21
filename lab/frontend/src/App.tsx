import {
  ArrowCounterClockwise,
  Pause,
  Play,
  Stop,
  WifiHigh,
  WifiSlash,
} from '@phosphor-icons/react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { FlyViewport } from './FlyViewport'
import { telemetry } from './telemetry'
import type { LabMessage, Metadata, Snapshot } from './types'

type ConnectionState = 'connecting' | 'online' | 'offline' | 'error'

const labels: Record<string, string> = {
  ready: 'Pronta',
  running: 'In corso',
  paused: 'In pausa',
  stopped: 'Arrestata',
  closed: 'Chiusa',
}

function websocketUrl() {
  const url = new URL('/ws', window.location.href)
  url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return url
}

export default function App() {
  const socketRef = useRef<WebSocket | null>(null)
  const reconnectRef = useRef<number | null>(null)
  const summaryTimer = useRef(0)
  const [connection, setConnection] = useState<ConnectionState>('connecting')
  const [metadata, setMetadata] = useState<Metadata | null>(null)
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [error, setError] = useState<string | null>(null)

  const connect = useCallback(() => {
    if (socketRef.current?.readyState === WebSocket.OPEN) return
    setConnection('connecting')
    const socket = new WebSocket(websocketUrl())
    socketRef.current = socket
    socket.addEventListener('open', () => {
      setConnection('online')
      setError(null)
    })
    socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data) as LabMessage
      if (message.type === 'metadata') {
        telemetry.metadata = message.payload
        setMetadata(message.payload)
      } else if (message.type === 'snapshot') {
        telemetry.snapshot = message.payload
        const now = performance.now()
        if (now - summaryTimer.current > 120) {
          setSnapshot(message.payload)
          summaryTimer.current = now
        }
      } else if (message.type === 'error' || message.type === 'fatal') {
        setError(message.error)
        setConnection('error')
      }
    })
    socket.addEventListener('close', () => {
      setConnection('offline')
      reconnectRef.current = window.setTimeout(connect, 1500)
    })
    socket.addEventListener('error', () => setConnection('error'))
  }, [])

  useEffect(() => {
    connect()
    return () => {
      if (reconnectRef.current !== null) window.clearTimeout(reconnectRef.current)
      socketRef.current?.close()
    }
  }, [connect])

  const command = (type: 'start' | 'pause' | 'stop' | 'reset') => {
    const socket = socketRef.current
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setError('Il telefono non e collegato al motore. Attendi la riconnessione.')
      return
    }
    socket.send(JSON.stringify({ type, request_id: crypto.randomUUID() }))
  }

  const status = snapshot?.status ?? 'starting'
  const rootPosition = snapshot?.body_pos_mm?.[0]
  const contacts = snapshot?.contact_found.filter(Boolean).length ?? 0
  const connected = connection === 'online'

  return (
    <main className="lab-shell">
      <header className="topbar">
        <div>
          <p className="project-name">Fruit Fly Lab</p>
          <h1>Spazio vivo per esperimenti ripetibili.</h1>
        </div>
        <div className={`connection connection--${connection}`} role="status" aria-live="polite">
          {connected ? <WifiHigh size={18} weight="regular" /> : <WifiSlash size={18} />}
          <span>{connected ? 'PC collegato' : connection === 'connecting' ? 'Collegamento' : 'Disconnesso'}</span>
        </div>
      </header>

      <section className="workspace" aria-label="Simulazione dal vivo">
        <aside className="measurements" aria-label="Stato esperimento">
          <div className="status-block">
            <span>Stato</span>
            <strong data-status={status}>{labels[status] ?? 'Avvio'}</strong>
          </div>
          <dl>
            <div><dt>Tempo</dt><dd>{(snapshot?.sim_time_s ?? 0).toFixed(3)} s</dd></div>
            <div><dt>Sequenza</dt><dd>{snapshot?.sequence ?? 0}</dd></div>
            <div><dt>Contatti</dt><dd>{contacts} / 6</dd></div>
            <div><dt>Corpi</dt><dd>{metadata?.body_names.length ?? 69}</dd></div>
            <div><dt>Posizione X</dt><dd>{(rootPosition?.[0] ?? 0).toFixed(2)} mm</dd></div>
          </dl>
        </aside>

        <div className="scene-wrap">
          <div className="scene-index">LIVE / {String(snapshot?.sequence ?? 0).padStart(6, '0')}</div>
          <FlyViewport />
          {!snapshot && <div className="loading-state">Il motore sta preparando il corpo della mosca.</div>}
        </div>

        <aside className="experiment-note">
          <span>Esperimento attivo</span>
          <strong>Camminata a tripode</strong>
          <p>Fisica MuJoCo sul PC. Il telefono riceve soltanto lo stato verificato.</p>
        </aside>
      </section>

      {error && <div className="error-strip" role="alert"><strong>Motore:</strong> {error}</div>}

      <nav className="control-rail" aria-label="Controlli simulazione">
        <button onClick={() => command('start')} disabled={!connected || status === 'running'}>
          <Play size={21} weight="fill" /><span>Avvia</span>
        </button>
        <button onClick={() => command('pause')} disabled={!connected || status !== 'running'}>
          <Pause size={21} weight="fill" /><span>Pausa</span>
        </button>
        <button onClick={() => command('reset')} disabled={!connected}>
          <ArrowCounterClockwise size={21} /><span>Reset</span>
        </button>
        <button className="control-stop" onClick={() => command('stop')} disabled={!connected}>
          <Stop size={21} weight="fill" /><span>Arresta</span>
        </button>
      </nav>
    </main>
  )
}

