# Piano connectome, Fase 6

## Decisione

Il modello Shiu et al. deve vivere in un processo `fruitfly-connectome` separato dal processo `fruitfly-engine` che possiede MuJoCo. Il connectome riceve eventi episodici, calcola una decisione a bassa frequenza e restituisce soltanto gli spike dei neuroni selezionati e tre segnali comportamentali:

```text
locomotion: go | stop
turn: -1.0 .. 1.0
extend_proboscis: true | false
```

Il connectome non conosce i nomi, gli indici o i valori dei 42 attuatori. Il controllore del corpo traduce in seguito questi segnali in cammino, adesione e movimenti disponibili nel modello fisico.

## 1. Modello locale verificato

### File e versione

- Repository annidato: `cervello/modello`.
- Commit pulito: `91bdd1e7dcf193f3e7ca5a8933497fcef63b7960`.
- Implementazione: `cervello/modello/model.py`.
- Analisi: `cervello/modello/utils.py`.
- Tutorial: `cervello/modello/example.ipynb`.
- Esperimenti del paper: `cervello/modello/figures.ipynb`.
- Neuroni v630: `2023_03_23_completeness_630_final.csv`, 127.400 righe, 3.185.012 byte, SHA-256 `479ED718404E7B1D898EE9A353410F481E6ED7694C30F579A7C2699F1C708A5D`.
- Connettivita v630: `2023_03_23_connectivity_630_final.parquet`, 14.687.178 connessioni, 86.630.944 byte, SHA-256 `94DB8C650533BC36FFA3223F2E62325D5648B8D6BD31C3A4E1C804628C7557B3`.
- Esiste anche la materializzazione v783, ma la prova storica e la prima mappa comportamentale restano sulla v630. Mescolare ID v630 e v783 non e consentito.

### Come funziona

`create_model()` carica l'elenco dei neuroni e la connettivita, poi costruisce:

- un `NeuronGroup` Brian2 leaky integrate-and-fire per tutti i 127.400 neuroni;
- una `Synapses` con 14.687.178 archi;
- pesi dati da `Excitatory x Connectivity * 0.275 mV`;
- ritardo sinaptico di 1,8 ms;
- periodo refrattario di 2,2 ms;
- input Poisson sui neuroni stimolati, 150 Hz di default;
- uno `SpikeMonitor` globale.

`run_trial()` ricostruisce la rete completa per ogni trial. `run_exp()` ripete i trial con processi Joblib `loky`, concatena gli spike e salva un Parquet con colonne `t`, `trial`, `flywire_id`, `exp_name`.

Il modello locale usa Brian2 2.10.1 e Python 3.12, mentre l'ambiente originale dichiarava Python 3.10 e Brian2 2.5.1. Su questo PC il target Cython non e disponibile; `prove/01-zucchero.py` forza correttamente `prefs.codegen.target = "numpy"`.

### Come si lancia

Prova storica:

```powershell
E:\Dev\FruitFly\venv\Scripts\python.exe E:\Dev\FruitFly\prove\01-zucchero.py
```

Senza opzioni rilegge il risultato esistente. Attenzione: l'opzione storica `--rifai` entra nel ramo di simulazione, ma non passa `force_overwrite=True` a `run_exp()`. Se `zucchero.parquet` esiste, il modello stampa `Skipping experiment` e non lo sovrascrive. Non correggere questo file durante la Fase 6.

Il prototipo nuovo esegue un singolo episodio vero senza scrivere in `prove/`:

```powershell
cd E:\Dev\FruitFly\docs\connectome
E:\Dev\FruitFly\venv\Scripts\python.exe .\connectome_adapter.py --model real --stimulus sugar --duration 1 --seed 7 --timeout 300 --summary
```

### Misure reali

Misura del 23/09/2026, stesso venv e target Brian2 `numpy`:

| Prova | Durata simulata | Parallelismo | Tempo reale | Picco RAM | Risultato |
|---|---:|---:|---:|---:|---|
| Prototipo Fase 6, seed 7 | 1 s | 1 worker | 26,37 s | 2.940.985.344 byte, 2,739 GiB | 3.383 spike selezionati, MN9 78 Hz, `stop + extend_proboscis` |
| Prova storica salvata | 5 x 1 s | 4 processi | 75-76 s | circa 10 GB, misura storica | 69.598 spike totali, 407 neuroni attivi, MN9 80,4 Hz |

Quattro worker con il picco misurato oggi richiederebbero circa 10,96 GiB solo per i processi neurali, coerente con la misura storica. Per la Fase 6 il default deve quindi essere un solo episodio Brian2 alla volta. Cinque trial seriali richiederebbero circa 132 s se il costo restasse lineare.

## 2. Alternative considerate

### A. Brian2 nello stesso processo MuJoCo

Scartata. Un episodio dura decine di secondi e usa gigabyte di RAM. Un errore o un garbage collection del modello neurale potrebbe bloccare o abbattere la fisica.

### B. Worker persistente separato, scelta

Un processo Windows `spawn` importa Brian2 e riceve una richiesta alla volta. Il processo MuJoCo continua a fare step. Timeout ed eccezioni ricreano soltanto il worker neurale.

Il prototipo mantiene il processo, ma il codice Shiu ricostruisce comunque rete e sinapsi a ogni episodio. Una fase successiva potra misurare una cache Brian2 con `Network.store()` e `restore()`, senza cambiare il protocollo.

### C. Nuovo processo per ogni episodio

Isolamento massimo, ma costo di import e avvio a ogni richiesta. Resta il fallback se una futura cache produce crescita RAM o stato non ripetibile.

## 3. Architettura da integrare

```text
telefono / batch
       |
       v
FastAPI e EngineService
       |
       +--> fruitfly-engine, unico proprietario di MuJoCo e Archive
       |          |
       |          +--> applica BehaviorSignal tra due batch fisici
       |          +--> registra episodio connectome
       |
       +--> ConnectomeService
                  |
                  +--> fruitfly-connectome, unico proprietario di Brian2
```

Flusso:

1. Lo stimolo viene validato e applicato dal motore a un tempo MuJoCo preciso.
2. Il motore emette una richiesta connectome lossless con `episode_id`, seed, stimolo, durata, versione del modello e versione della mappa.
3. `ConnectomeService` la invia al worker se questo e `idle`. Una seconda richiesta mentre e `busy` viene rifiutata esplicitamente, non accumulata senza limite.
4. MuJoCo continua con l'ultima decisione ancora valida. Se non esiste o scade, usa `stop`.
5. Il worker restituisce solo gli spike dei gruppi selezionati e le frequenze calcolate.
6. Il servizio applica timeout, converte gli spike con la mappa versionata e rimanda il risultato al motore.
7. Il motore applica il segnale fra due batch di step e registra richiesta, spike, decisione, latenza e tempo di applicazione.

Non usare `_put_latest()` per richieste o risultati connectome. Nel worker attuale quella funzione puo eliminare il messaggio piu vecchio quando la coda condivisa e piena. Telemetria lossy e risultati lossless devono usare code diverse.

## 4. Contratto episodio v1

Richiesta serializzabile:

```json
{
  "protocol_version": 1,
  "type": "run_connectome_episode",
  "episode_id": "uuid",
  "run_id": "archive-run-id",
  "requested_sim_time_s": 12.4,
  "requested_wall_ns": 0,
  "seed": 7,
  "stimulus": "sugar",
  "intensity": 1.0,
  "duration_s": 1.0,
  "model_version": "shiu-91bdd1e7-flywire630",
  "map_id": "fruitfly-behavior-v1",
  "map_sha256": "...",
  "deadline_wall_ns": 0
}
```

Risultato serializzabile:

```json
{
  "protocol_version": 1,
  "type": "connectome_episode_result",
  "episode_id": "uuid",
  "status": "ok",
  "spikes": [
    {"flywire_id": "720575940660219265", "t_s": 0.1234}
  ],
  "rates_hz": {"proboscis_motor": 78.0},
  "decision": {
    "locomotion": "stop",
    "turn": 0.0,
    "extend_proboscis": true
  },
  "compute_wall_s": 26.37,
  "completed_wall_ns": 0,
  "applied_sim_time_s": 15.8,
  "error_code": null,
  "error_detail": null
}
```

Gli ID FlyWire viaggiano come stringhe nei payload JSON. Sono maggiori del limite intero esatto di JavaScript.

## 5. Mappa comportamentale v1

Artefatto: `behavior-map-v1.json`.

- `map_id`: `fruitfly-behavior-v1`.
- materializzazione: FlyWire v630.
- SHA-256 attuale: `92B2AB67A41A418ACB3E2B5362F2343DB4F8FCBF2B0F101F868800A1629751CC`. La run deve comunque calcolarlo dal file al momento dell'avvio e registrare il valore effettivo.

| Gruppo | Neuroni v630 | Uso |
|---|---|---|
| `sugar_right` | 21 GRN del labello destro | input sensoriale e spike di controllo |
| `feeding_interneurons` | 2 Fdg e 4 Bract | evidenza del percorso di alimentazione, non comando diretto |
| `proboscis_motor` | MN9 sinistro e destro | `extend_proboscis` |
| `walk_left`, `walk_right` | popolazione DNp09 annotata per lato | `go` e componente direzionale futura |
| `steer_left`, `steer_right` | DNa02 per lato | segnale `turn` ipsilaterale |
| `halt` | Foxglove e Bluebell | override `stop`, coerente con l'arresto durante alimentazione |

Regole iniziali, da trattare come soglie ingegneristiche da calibrare e non come risultati biologici definitivi:

```text
walk = media delle frequenze walk_left e walk_right
halt >= 8 Hz                         -> locomotion = stop
walk >= 10 Hz e halt sotto soglia    -> locomotion = go
altrimenti                           -> locomotion = stop
turn = clip((steer_right - steer_left) / 30 Hz, -1, 1)
proboscis_motor >= 20 Hz             -> extend_proboscis = true
```

`halt` ha precedenza su `go`. `turn` e azzerato quando `locomotion` e `stop`. La convenzione e `-1 = sinistra`, `+1 = destra`.

Evidenza locale sul risultato storico zucchero:

- MN9 sinistro: 80,4 Hz.
- MN9 destro: 60,0 Hz.
- Fdg: 15,2 e 46,2 Hz.
- due Bract attivi: 5,4 e 7,6 Hz.
- DNp09 e DNa02 selezionati: 0 Hz.

Il benchmark del prototipo ha inoltre misurato il gruppo `halt` a 26 Hz. Il risultato `stop + extend_proboscis` e quindi una combinazione prevista, non un errore: l'alimentazione puo fermare la locomozione mentre estende la proboscide.

## 6. Frequenza, validita e applicazione

- Nessuna chiamata Brian2 per frame o per step MuJoCo.
- Una sola richiesta in volo.
- Default iniziale per il modello vero: episodio neurale di 1 s, timeout wall clock di 120 s.
- Il modello finto puo rispondere in millisecondi e serve a testare tutto il percorso.
- Una decisione contiene `applied_sim_time_s` distinto da `requested_sim_time_s`, per rendere visibile la latenza.
- Ogni decisione ha una durata simulata esplicita. Alla scadenza il controllore torna a `stop`, senza mantenere per sempre l'ultimo comando.
- Un risultato arrivato dopo timeout o con un `episode_id` non piu attivo viene archiviato come scartato e non applicato.

## 7. Timeout ed errore recuperabile

Stati del servizio:

```text
starting -> idle -> busy -> idle
                    |
                    +-> timed_out -> restarting -> idle
                    +-> error     -> restarting -> idle
```

Comportamento obbligatorio:

- timeout: `stop`, registra `timed_out`, termina il worker Brian2, crea un worker nuovo;
- eccezione Brian2: `stop`, registra tipo e messaggio, ricrea il worker;
- morte inattesa: `stop`, registra exit code, ricrea il worker;
- coda piena: rifiuta la richiesta con `busy`, non elimina un risultato precedente;
- errore archivio: conserva i dati parziali secondo il lifecycle gia presente e mette in pausa la run, non cancella file;
- errore connectome: non cambia lo stato fatal del processo MuJoCo.

Il prototipo verifica sia timeout sia eccezione, poi esegue con successo un episodio successivo sul worker ricreato.

## 8. Archivio MCAP e Parquet

Il processo Brian2 non apre SQLite e non crea un secondo `Archive`. L'archivio esistente dichiara un solo writer e resta posseduto dal processo `fruitfly-engine`.

### MCAP

Usare lo stesso `telemetry.mcap`, compressione ZSTD e CRC gia attivi. Aggiungere un solo canale:

```text
/connectome/episode
schema: fruitfly_lab.ConnectomeEpisode v1
encoding: json
```

Un messaggio per episodio contiene richiesta, spike selezionati, frequenze, mappa, decisione, stato ed errore. `log_time` e il tempo MuJoCo di applicazione. `publish_time` e il tempo reale di completamento.

### Parquet

Nuovo artifact immutabile `connectome_episodes.parquet`, compressione ZSTD, una riga per episodio:

```text
protocol_version          int16
episode_sequence          int64
episode_id                string
status                    string
stimulus                  string
intensity                 float32
seed                      int64
duration_s                float64
model_kind                string
model_version             string
map_id                    string
map_sha256                string
neuron_ids                list<string>
spike_times_s             list<list<float32>>
rates_json                string
locomotion                string
turn                      float32
extend_proboscis          bool
requested_sim_time_s      float64
applied_sim_time_s        float64 nullable
requested_wall_ns         int64
completed_wall_ns         int64
compute_wall_s            float64
peak_worker_rss_bytes     int64
error_code                string nullable
error_detail              string nullable
```

La tabella conserva tutti gli spike dei neuroni selezionati, non gli spike dei 127.400 neuroni. Il file nativo completo puo diventare un artifact opzionale in una campagna scientifica, ma non deve attraversare la coda live.

### Modifiche future all'archivio

1. Registrare schema e canale in `RunRecorder._open_writers()`.
2. Aggiungere `record_connectome_episode()` e un writer Parquet separato.
3. Verificare in `finalize()` che messaggi MCAP e righe Parquet coincidano.
4. Includere `connectome_episodes.parquet` in `seal_run()` e nel manifest.
5. Estendere `iter_telemetry()` al nuovo topic e aggiungere `read_connectome_table()`.
6. Aggiornare i test che oggi si aspettano esattamente tre artifact.

`_media_type()`, object store SHA-256, bundle export/import e crash recovery sono gia compatibili con un Parquet aggiuntivo.

## 9. Fasi di implementazione

### Fase 0. Contratti e baseline

Riferimenti: `model.py`, `example.ipynb`, `prove/01-zucchero.py`, `fruitfly_lab/service.py`, `worker.py`, `archive/recorder.py`.

- Congelare hash del modello, dati v630, mappa e `prove/02-cammina.py`.
- Salvare il benchmark 1 x 1 s come baseline.
- Vietare ID appartenenti a una materializzazione diversa.

Verifica: hash presenti nel manifest, prova storica invariata.

### Fase 1. Protocollo e mappa

- Portare `EpisodeRequest`, `SpikeEvent`, `BehaviorSignal` ed `EpisodeDecision` nel pacchetto backend.
- Validare ogni campo e versione.
- Caricare la mappa una volta e registrarne SHA-256.
- Aggiungere test delle soglie e della convenzione sinistra/destra.

Guardia: nessun campo per i 42 attuatori nel protocollo connectome.

### Fase 2. ConnectomeService

- Copiare il pattern Windows `spawn` da `EngineService`.
- Usare code dedicate lossless e una sola richiesta in volo.
- Implementare heartbeat, deadline, riavvio e chiusura ordinata.
- Mantenere Brian2 importato soltanto nel figlio.

Verifica: MuJoCo avanza mentre il fake e bloccato, timeout seguito da episodio riuscito.

### Fase 3. Applicazione comportamentale

- Aggiungere un livello tra `BehaviorSignal` e il controllore a tripode.
- `go` abilita il passo, `stop` conserva una posa stabile, `turn` modifica parametri di locomozione.
- `extend_proboscis` resta osservabile e archiviato finche il corpo FlyGym non espone un controllore della proboscide verificato.

Guardia: nessuna finta estensione della proboscide tramite attuatori delle zampe.

### Fase 4. Archivio

- Aggiungere `/connectome/episode` e `connectome_episodes.parquet` allo stesso `RunRecorder`.
- Conservare risultati `ok`, `busy`, `timed_out`, `error` e `discarded_late`.
- Verificare CRC, righe, hash, replay, crash e bundle.

### Fase 5. Backend Shiu

- Attivare `--model real` dietro lo stesso contratto del fake.
- Default un solo trial, un solo worker, target Brian2 `numpy`.
- Misurare almeno 10 seed prima di modificare le soglie.
- Valutare cache della rete solo dopo benchmark di memoria, ripetibilita e reset completo dello stato.

### Fase 6. Verifica completa

- Test unitari fake.
- Test di crash e timeout del processo vero.
- Regressione zucchero e hash di `prove/02-cammina.py`.
- Run integrata con MCAP e Parquet riaperti e confrontati.
- Prova che MuJoCo continua a incrementare `sequence` durante Brian2.
- Prova reale da iPhone o iPad dello stato `busy`, della decisione applicata e dell'errore recuperato.

## 10. Criteri di accettazione

1. Il processo MuJoCo non importa Brian2 e non attende il risultato.
2. Il modello finto e quello vero rispettano lo stesso schema.
3. Timeout o crash producono `stop`, stato esplicito e un worker nuovo.
4. Un episodio successivo al guasto riesce senza riavviare il laboratorio.
5. La decisione non contiene valori per i 42 attuatori.
6. MCAP e Parquet contengono lo stesso numero di episodi e gli stessi `episode_id`.
7. Ogni episodio registra seed, modello, materializzazione, mappa e SHA-256.
8. Gli spike dei neuroni selezionati sono riproducibili con stesso seed entro il comportamento garantito da Brian2.
9. `prove/02-cammina.py` conserva SHA-256 `D2FC0E7049F7C601534E0DB588BA1823F5DFD02EDB7E1C82F39DE85CF0235410`.
10. Il test zucchero storico resta leggibile e il nuovo percorso vero produce estensione della proboscide.

## 11. Rischi aperti

- Le soglie v1 sono iniziali. I nomi neuronali hanno evidenza sperimentale, ma la trasformazione quantitativa da frequenza simulata a comando del corpo deve essere calibrata.
- Il modello Shiu rappresenta il cervello, non la VNC completa. I segnali discendenti sono un'interfaccia verso il controllore del corpo, non una simulazione completa dei circuiti motori.
- La costruzione della rete a ogni episodio costa circa 26 s e 2,739 GiB per worker. La frequenza reale resta nell'ordine delle decine di secondi finche non viene verificata una cache sicura.
- Brian2 2.10.1 e diverso dalla versione 2.5.1 originale. Le campagne scientifiche devono registrare la versione effettiva.
- Gli ID FlyWire cambiano tra materializzazioni. Ogni mappa deve dichiarare v630 o v783 e non puo combinare le due.
- Il frontend JavaScript perde precisione sugli ID numerici a 64 bit. Nei JSON gli ID devono restare stringhe.
- Il canale eventi del worker attuale mescola messaggi lossless e telemetria lossy. L'integrazione connectome richiede code separate prima di essere affidabile.

## 12. Fonti

- Codice locale Shiu et al.: `cervello/modello`, commit `91bdd1e7dcf193f3e7ca5a8933497fcef63b7960`.
- Paper del modello: <https://www.nature.com/articles/s41586-024-07763-9>.
- Annotazioni FlyWire v630: <https://github.com/flyconnectome/flywire_annotations/tree/v1.1.0>.
- DNp09 e cammino in avanti: <https://pmc.ncbi.nlm.nih.gov/articles/PMC9435592/>.
- DNa01/DNa02 e sterzata: <https://elifesciences.org/articles/102230>.
- Foxglove, Bluebell e arresto contestuale: <https://pmc.ncbi.nlm.nih.gov/articles/PMC11446846/>.
- Fdg, Bract e MN9 nel circuito di alimentazione: <https://pmc.ncbi.nlm.nih.gov/articles/PMC9292995/>.
