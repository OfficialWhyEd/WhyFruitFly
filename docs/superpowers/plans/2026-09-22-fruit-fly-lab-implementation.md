# Fruit Fly Lab: piano di implementazione

## Fase 0: API consentite e vincoli verificati

### FlyGym 2.1.0 locale

Fonti: `prove/02-cammina.py`, `venv/Lib/site-packages/flygym/simulation.py`, `venv/Lib/site-packages/flygym/compose/fly/base_fly.py`, `venv/Lib/site-packages/flygym/rendering.py`.

- Un solo processo possiede `Simulation`, `MjModel`, `MjData` e renderer.
- Dopo creazione o reset chiamare `mujoco.mj_forward` prima delle letture cartesiane.
- Avanzamento: `Simulation.step()`. Tempo: `sim.mj_data.time`.
- Letture pubbliche: `get_body_positions`, `get_body_rotations`, `get_joint_angles`, `get_joint_velocities`, `get_actuator_forces`, `get_ground_contact_info`.
- Ordini canonici: `get_bodysegs_order`, `get_jointdofs_order`, `get_actuated_jointdofs_order`, `get_legs_order`.
- Non modificare `prove/02-cammina.py`, SHA-256 baseline `D2FC0E7049F7C601534E0DB588BA1823F5DFD02EDB7E1C82F39DE85CF0235410`.

### Web locale

Fonti: documentazione ufficiale FastAPI WebSockets/frontend, Uvicorn settings, React Three Fiber Canvas/performance, MDN WebSocket, Microsoft New-NetFirewallRule.

- Un solo origin: pagina e `/ws` dallo stesso FastAPI.
- Uvicorn ascolta su `0.0.0.0`; il telefono usa l'IPv4 LAN del PC.
- WebSocket derivato da `window.location`, mai `localhost` hardcoded.
- Firewall solo profilo Private e `LocalSubnet`.
- Viewer mobile con DPR 1-1.5 e interpolazione fuori dal render React.

### Archivio

Fonti: documentazione ufficiale MCAP Python, SQLite transactions/PRAGMA, Python sqlite3/hashlib, PyArrow Parquet.

- SQLite e solo catalogo. MCAP e il log temporale autorevole. Parquet e la proiezione analitica.
- File immutabili in archivio SHA-256, pubblicati con `os.replace` sullo stesso volume.
- Una run viene sigillata nel catalogo solo dopo finalizzazione e verifica degli artefatti.
- SQLite usa migrazioni numerate, foreign keys, WAL e un solo writer.

## Fase 1: fondazioni e regressione

Implementare pacchetto `lab/backend/fruitfly_lab`, configurazione, protocollo versione 1, adattatore locomozione copiato fedelmente dalla prova storica e test unitari senza renderer.

Verifica:

- hash della prova storica invariato;
- snapshot con 69 corpi, 126 DoF, 42 attuatori e 6 zampe;
- start, pause, resume, reset e stop idempotenti;
- stesso seed e stessa configurazione producono metriche entro tolleranza.

Guardie: non importare `02-cammina.py`, non condividere oggetti MuJoCo tra processi, non fare step dal thread web.

## Fase 2: server e link LAN

Implementare processo motore, code tipizzate, FastAPI, WebSocket, heartbeat, token di sessione breve, pagina di stato e discovery dell'IPv4 LAN.

Verifica:

- test FastAPI con `TestClient.websocket_connect`;
- comando duplicato produce un solo effetto;
- pacchetti fuori ordine vengono ignorati;
- disconnessione dell'ultimo controller mette in pausa soltanto la modalita live;
- URL LAN e QR corrispondono all'interfaccia corretta.

Guardie: niente CORS largo, niente bind Internet, niente comandi non validati, niente processo in autostart.

## Fase 3: archivio immutabile

Implementare migrazioni SQLite, lifecycle delle run, writer MCAP, proiezione Parquet, archivio SHA-256, manifest e recupero dopo crash.

Verifica:

- test di crash prima e dopo `os.replace`;
- checksum e CRC verificati al replay;
- una run sigillata non puo essere modificata;
- export e import conservano relazioni e hash;
- spazio insufficiente blocca la coda prima dell'avvio.

Guardie: niente BLOB pesanti in SQLite, niente path assoluti nel catalogo, niente `INSERT OR REPLACE`, niente cancellazione automatica.

## Fase 4: interfaccia mobile 3D

Implementare React, React Three Fiber, store di telemetria, interpolazione, controlli touch, pannelli Live/Batch/Replay/Confronto e qualita adattiva.

Verifica:

- Safari iPhone/iPad reale sulla stessa Wi-Fi;
- camera orbitale, inseguimento, zoom e orientamento;
- comandi con conferma e stato visibile;
- 30 minuti collegati senza perdita;
- degrado controllato sotto rete lenta e WebGL indisponibile.

Guardie: niente fisica nel browser, niente rerender React per ogni articolazione, niente DPR pieno obbligatorio, niente dipendenza cloud.

## Fase 5: campagne ed esperimenti estendibili

Implementare gerarchia progetto/campagna/esperimento/run, sweep, seed, scheduler, note, filtri, confronti ed esportazione.

Verifica:

- dieci run in coda salvano artefatti distinti;
- ricerca per stimolo, seed, stato e versione;
- replay sincronizzato con MCAP;
- moduli nuovi non rendono illeggibili le run vecchie.

## Fase 6: connectome

Implementare adattatore asincrono per il modello Shiu et al., partendo da eventi episodici a bassa frequenza. Mappare gli output neurali a segnali comportamentali versionati, non direttamente ai 42 attuatori.

Verifica:

- il test zucchero storico resta invariato;
- gli spike e la decisione comportamentale vengono archiviati;
- MuJoCo continua senza dipendere dalla durata del calcolo Brian2;
- fallimento o timeout del connectome produce uno stato esplicito e recuperabile.

## Fase finale: verifica completa

- Eseguire test unitari, integrazione, regressione storica, sessione lunga e prova fisica da iPhone/iPad.
- Controllare hash, migrazioni, artefatti orfani, porte, firewall e assenza di segreti.
- Documentare un unico comando gestito per avvio e arresto, senza autostart.
- Salvare nel progetto il report di verifica e lo stato di handoff per Claude Code.

## Stato al 22/09/2026 notte (Claude Code)

Fase 3 fatta, non ancora in commit.

- Codice: `lab/backend/fruitfly_lab/archive/` (catalog, store, recorder, replay, bundle). Archivio in `lab/data/archive`.
- Immutabilita dentro SQLite con trigger: niente DELETE, run chiuse non modificabili, transizioni di stato controllate.
- Oggetti SHA-256 in `objects/ab/cd/<sha>`, sola lettura, pubblicati con `os.replace` e journal `ingest.jsonl` per il crash.
- Ogni Avvia apre una run; Arresta, Reset e chiusura la sigillano. Crash: al riavvio la run diventa `interrupted` con i file parziali conservati.
- Registrazione ogni 100 passi (100 Hz simulati). Misurato: circa 12 kB a fotogramma, 1,2 MB per secondo simulato.
- API: `/api/archive`, `/api/runs`. Pannello Archivio nell'interfaccia.
- Corretto bug della fase 2: `crypto.randomUUID` non esiste su http LAN, i pulsanti dal telefono non mandavano niente.
- Test: 19 su 19 (`python -m unittest discover -s tests` da `lab/backend`), compresa run vera col motore e crash prima e dopo `os.replace`.
- Avvio: da `lab/backend` `..\..\venv\Scripts\python.exe -m fruitfly_lab.server --data-dir ..\data\runtime`.

Prossimo: fase 4, prova vera da iPhone/iPad sulla stessa Wi-Fi e sessione di 30 minuti.