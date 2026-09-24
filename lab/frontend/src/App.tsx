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
import type { ArchiveInfo, LabMessage, Metadata, RunSummary, Snapshot } from './types'

type ConnectionState = 'connecting' | 'online' | 'offline' | 'error'

const labels: Record<string, string> = {
  ready: 'Pronta',
  running: 'In corso',
  paused: 'In pausa',
  stopped: 'Arrestata',
  closed: 'Chiusa',
}

const stateLabels: Record<string, string> = {
  recording: 'In registrazione',
  finalizing: 'Chiusura',
  sealed: 'Sigillata',
  failed: 'Errore',
  interrupted: 'Interrotta',
}

function formatBytes(bytes: number) {
  if (bytes >= 1024 ** 3) return (bytes / 1024 ** 3).toFixed(2) + ' GB'
  if (bytes >= 1024 ** 2) return (bytes / 1024 ** 2).toFixed(1) + ' MB'
  return Math.round(bytes / 1024) + ' kB'
}

// crypto.randomUUID exists only on secure origins; the LAN link is plain http.
function requestId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  return hex.slice(0, 8) + '-' + hex.slice(8, 12) + '-' + hex.slice(12, 16) + '-' + hex.slice(16, 20) + '-' + hex.slice(20)
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
  const [archive, setArchive] = useState<ArchiveInfo | null>(null)
  const [runs, setRuns] = useState<RunSummary[]>([])

  const refreshArchive = useCallback(async () => {
    try {
      const [info, list] = await Promise.all([
        fetch('/api/archive', { credentials: 'same-origin' }),
        fetch('/api/runs?limit=6', { credentials: 'same-origin' }),
      ])
      if (info.ok) setArchive(await info.json())
      if (list.ok) setRuns((await list.json()).runs)
    } catch {
      // The live channel shows the connection problem already.
    }
  }, [])

  useEffect(() => {
    refreshArchive()
    const timer = window.setInterval(refreshArchive, 8000)
    return () => window.clearInterval(timer)
  }, [refreshArchive])

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
      } else if (message.type === 'archive') {
        refreshArchive()
        if (message.event === 'failed') setError('Archivio: ' + (message.error ?? 'run non sigillata'))
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
  }, [refreshArchive])

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
    socket.send(JSON.stringify({ type, request_id: requestId() }))
  }

  const status = snapshot?.status ?? 'starting'
  const rootPosition = snapshot?.body_pos_mm?.[0]
  const contacts = snapshot?.contact_found.filter(Boolean).length ?? 0
  const connected = connection === 'online'
  const recording = Boolean(archive?.recording_run_id)
  const freeGb = archive?.free_bytes !== undefined ? archive.free_bytes / 1024 ** 3 : null

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

          <section className="archive" aria-label="Archivio">
            <span>Archivio</span>
            <p className={recording ? 'archive-rec archive-rec--on' : 'archive-rec'}>
              {recording ? 'Registrazione in corso' : 'Nessuna registrazione'}
            </p>
            <dl>
              <div><dt>Run salvate</dt><dd>{archive?.by_state?.sealed ?? 0}</dd></div>
              <div><dt>Interrotte</dt><dd>{(archive?.by_state?.interrupted ?? 0) + (archive?.by_state?.failed ?? 0)}</dd></div>
              <div><dt>Spazio usato</dt><dd>{formatBytes(archive?.stored_bytes ?? 0)}</dd></div>
              <div><dt>Disco libero</dt><dd>{freeGb === null ? '...' : freeGb.toFixed(1) + ' GB'}</dd></div>
            </dl>
            <ol className="run-list">
              {runs.map((run) => (
                <li key={run.id} data-state={run.state}>
                  <code>{run.id.slice(0, 8)}</code>
                  <span>{stateLabels[run.state] ?? run.state}</span>
                  <span>{(run.metrics.sim_duration_s ?? 0).toFixed(2)} s</span>
                  <span>{(run.metrics.displacement_mm ?? 0).toFixed(2)} mm</span>
                </li>
              ))}
            </ol>
          </section>
        </aside>

        <p className="archive-strip" aria-live="polite">
          <i className={recording ? 'rec-dot rec-dot--on' : 'rec-dot'} aria-hidden="true" />
          {recording ? 'Sto registrando' : 'Archivio'} / {archive?.by_state?.sealed ?? 0} run salvate / {freeGb === null ? '...' : freeGb.toFixed(0) + ' GB liberi'}
        </p>
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

