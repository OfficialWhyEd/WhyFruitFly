# Fruit Fly Lab: laboratorio locale controllabile da telefono

## Obiettivo

Creare un laboratorio dedicato agli esperimenti sulla Drosophila simulata. Il PC esegue la fisica e, in seguito, il connectome. Un iPhone o iPad collegato alla stessa rete Wi-Fi mostra lo stato in tempo reale e controlla l'esperimento senza servizi cloud. Il laboratorio deve poter crescere per anni: nuovi ambienti, stimoli, controllori, modelli neurali e analisi si aggiungono come moduli senza rompere gli esperimenti precedenti.

Il primo traguardo usa la camminata FlyGym/MuJoCo gia funzionante. Il connectome viene collegato dopo tramite un'interfaccia stabile, senza riscrivere il laboratorio.

## Scelte considerate

1. Video trasmesso dal renderer MuJoCo. Molto fedele, ma pesante e poco interattivo dal telefono.
2. Simulazione completa nel browser. Molto fluida, ma duplica la fisica e rischia risultati diversi dal PC.
3. Soluzione scelta, ibrida. MuJoCo resta autorevole sul PC; il browser riceve soltanto lo stato e rende una copia 3D interattiva.

La soluzione ibrida separa la validita scientifica dalla visualizzazione e riduce il traffico sulla rete locale.

## Architettura

### Tecnologie scelte dopo il confronto

- FastAPI per API, WebSocket e pagina locale servita dallo stesso processo.
- React con React Three Fiber per la vista 3D mobile e uPlot per grafici leggeri.
- Protocollo interno versionato con messaggi JSON di controllo e telemetria binaria compatta quando il volume cresce.
- MCAP come formato di registrazione per telemetria e replay, leggibile anche con Foxglove.
- SQLite per indice delle prove, configurazioni, stato della coda e metriche riassuntive.

Foxglove resta uno strumento diagnostico opzionale per analisi profonde e replay, non una dipendenza dell'interfaccia quotidiana. L'utente apre direttamente il Fruit Fly Lab senza account, cloud o configurazione manuale di topic.

### 1. Motore esperimenti

Processo Python separato che possiede la simulazione FlyGym/MuJoCo. Espone comandi limitati e validati: avvio, pausa, arresto, reset, velocita, direzione, stimolo e durata. Pubblica fotogrammi di stato a frequenza regolabile, senza bloccare il passo fisico.

### 2. Adattatore locomozione

Incorpora il controllore a tripode di `prove/02-cammina.py` dietro un'interfaccia comune. Restituisce posizione del corpo, orientamento, angoli articolari, contatti delle zampe, tempo simulato e metriche di stabilita.

### 3. Adattatore connectome

Inizialmente usa uno stato sintetico leggero per verificare l'interfaccia. Successivamente richiama il modello Shiu et al. gia presente e converte gli spike selezionati in segnali comportamentali. Il calcolo da circa 10 GB di RAM non deve girare a ogni fotogramma: produce episodi o decisioni a bassa frequenza, mentre MuJoCo continua la fisica.

### 4. Server locale

Applicazione FastAPI ascoltata solo sulla rete locale. Fornisce pagina web, API di controllo e WebSocket per la telemetria. All'avvio mostra un link del tipo `http://192.168.x.x:8765` e un codice QR. Il link viene salvato anche in un piccolo file di stato leggibile da Claude e Codex. Nessuna porta viene aperta su Internet.

### 5. Interfaccia iPhone e iPad

Pagina installabile come web app. La vista principale contiene:

- mosca 3D con rotazione, zoom, inseguimento e vista libera;
- stato corrente e tempo simulato;
- avvio, pausa, reset e arresto di emergenza;
- selezione dello stimolo e intensita;
- grafici compatti per velocita, contatti, attivita neurale e risultato.

L'interfaccia e responsive, utilizzabile sia in verticale sia in orizzontale. Il browser mantiene il rendering vicino a 60 fps interpolando gli snapshot, mentre la rete riceve soltanto 10-30 aggiornamenti al secondo. La fisica non dipende dagli fps del telefono.

### 6. Modalita laboratorio

- **Live:** osservazione e controllo della prova in corso.
- **Batch:** matrice di parametri, numero di seed, arresto anticipato e coda delle prove.
- **Replay:** riproduzione sincronizzata di movimento, eventi, grafici e attivita neurale.
- **Confronto:** due o piu prove allineate sul tempo con differenze evidenziate.
- **Diagnostica:** metriche grezze e file MCAP apribili anche in Foxglove.

### 7. Spazio di lavoro estendibile

Il laboratorio non usa una singola schermata rigida. Ogni ricerca vive in un workspace con pannelli componibili: scena 3D, configurazione, grafici, neuroni, coda, note, confronto e risultati. I layout vengono salvati per dispositivo e per progetto. Nuovi pannelli e nuovi tipi di esperimento si registrano tramite interfacce versionate, senza modificare il nucleo.

La gerarchia e:

`progetto -> campagna -> esperimento -> run -> artefatti`

- Un progetto raccoglie una domanda di ricerca ampia.
- Una campagna definisce ipotesi, variabili e criteri di successo.
- Un esperimento definisce una configurazione ripetibile.
- Una run e una singola esecuzione con seed e versione del codice.
- Gli artefatti comprendono telemetria, grafici, video, note e risultati derivati.

### 8. Archivio permanente

Le run concluse sono immutabili. Correzioni o nuove analisi producono una nuova versione collegata all'originale. SQLite indicizza tutto, mentre i dati numerici vivono in Parquet compresso e la telemetria temporale in MCAP compresso. I file grandi condivisi, come modelli e ambienti, vengono deduplicati tramite hash.

Ogni run salva automaticamente:

- domanda, ipotesi e note;
- configurazione completa e seed;
- versione del codice, dipendenze e modelli;
- eventi, telemetria e stato finale;
- metriche, errori e motivo dell'eventuale arresto;
- collegamenti agli artefatti e alle run confrontate.

Ricerca e filtri permettono di ritrovare prove per data, tag, stimolo, neuroni, ambiente, risultato, versione e stato. Ogni campagna puo essere esportata in un pacchetto autosufficiente con manifest e checksum.

### 9. Gestione intelligente dello spazio

Il sistema misura lo spazio prima di avviare una coda. Stima il peso previsto, impedisce l'esaurimento del disco e conserva sempre un margine di sicurezza. I dati grezzi restano disponibili finche lo spazio lo consente; anteprime, metriche e manifest non vengono eliminati automaticamente.

Una politica esplicita permette di spostare campagne concluse su un disco o archivio futuro senza cambiare i riferimenti nel laboratorio. Nessun dato viene cancellato automaticamente. Deduplicazione, compressione e livelli di dettaglio riducono il peso, ma lo spazio disponibile resta un limite fisico mostrato chiaramente nell'interfaccia.

## Flusso dati

1. Il telefono invia un comando validato al server.
2. Il server lo inserisce nella coda del motore.
3. Il motore applica il comando tra due passi fisici.
4. Il motore pubblica uno snapshot numerato.
5. Il server invia lo snapshot ai browser con WebSocket.
6. Il browser interpola tra snapshot per mostrare movimento fluido.
7. A fine prova, risultati e configurazione vengono salvati insieme.

Gli snapshot hanno numero progressivo e timestamp di simulazione. Il browser scarta pacchetti vecchi, segnala ritardi e non puo modificare direttamente lo stato fisico: invia soltanto comandi al motore.

## Esperimenti ripetibili

Ogni prova riceve un identificatore, un seed, una configurazione immutabile e una cartella propria. Il sistema salva manifest, eventi, metriche, stato finale, registrazione MCAP ed eventuale video. Una coda permette sweep, repliche e confronti senza intervento manuale. Un limite di RAM e un solo motore MuJoCo attivo impediscono di saturare il PC. Le prove leggere possono essere abilitate in parallelo soltanto dopo un benchmark esplicito.

Le campagne possono generare automaticamente griglie, campionamento casuale e ottimizzazione adattiva dei parametri. Ogni strategia registra perche ha scelto la prova successiva, cosi i risultati restano interpretabili e ripetibili.

## Errori e sicurezza

- Se l'ultimo telefono con controllo attivo perde la connessione, la simulazione si mette in pausa. Le prove batch gia avviate continuano senza dipendere dal telefono.
- Comandi duplicati vengono ignorati tramite identificatore.
- Un heartbeat segnala immediatamente motore fermo o browser disconnesso.
- Il pulsante di arresto interrompe la prova senza cancellarne i dati parziali.
- Il server accetta connessioni soltanto dalla LAN e usa un token breve mostrato localmente.
- Nessun processo parte con Windows nella prima versione.
- Il link locale mostra una pagina di stato chiara anche quando il motore e spento.

## Verifica

La prima versione e completata soltanto quando:

1. la stessa prova produce lo stesso risultato con lo stesso seed;
2. PC e telefono mostrano posizione e tempo coerenti;
3. pausa, ripresa, reset e arresto funzionano da iPhone o iPad;
4. una sessione di almeno 30 minuti non perde il collegamento;
5. dieci prove in coda terminano e salvano risultati distinti;
6. il vecchio test `prove/02-cammina.py` continua a funzionare senza modifiche distruttive;
7. il link funziona realmente da Safari su iPhone o iPad collegato allo stesso Wi-Fi;
8. un file MCAP registrato viene riaperto e riprodotto con telemetria coerente.
9. una campagna esportata viene reimportata mantenendo configurazioni, checksum e relazioni;
10. il laboratorio rifiuta in modo sicuro una coda che supererebbe lo spazio disponibile;
11. una run vecchia resta riproducibile dopo l'aggiunta di un nuovo modulo.

## Ordine di costruzione

1. Estrarre il motore dalla prova di camminata senza cambiare il comportamento.
2. Aggiungere protocollo di stato, server locale e comandi.
3. Creare la vista mobile 3D e provarla su iPhone o iPad reali.
4. Aggiungere archivio versionato, ricerca, coda, risultati e monitoraggio lungo.
5. Aggiungere campagne, sweep e confronto tra run.
6. Collegare l'adattatore del connectome e progettare gli esperimenti scientifici.

## Confini della prima versione

La prima versione non tenta di collegare tutti i 127.400 neuroni direttamente a ogni articolazione, non espone il server a Internet e non avvia simulazioni parallele pesanti. Questi ampliamenti richiedono benchmark separati.
