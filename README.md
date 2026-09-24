# WhyFruitFly

**Run the whole fruit fly brain (FlyWire connectome, ~127k neurons) and body (NeuroMechFly) on a home PC, with an immutable experiment archive, a LAN server and a 3D web interface you can open from a phone.**

`Python 3.12` · `Brian2` · `flygym / MuJoCo` · `FastAPI` · `SQLite + MCAP + Parquet` · `React + three.js` · stato: **fasi 1-3 scritte, in corso**

Un esperimento: far girare in casa il cervello e il corpo del moscerino della frutta. Lo scopo non è copiarlo,
è prenderne il principio (stimolo, stato interno, scelta con rumore, movimento) per il comportamento della
mascotte Aphelios.

## Cosa fa
- **cervello intero**: 127.400 neuroni del connectome FlyWire in un modello leaky integrate-and-fire;
  lo zucchero sui neuroni del gusto accende una cascata di 390 neuroni fino al motoneurone della proboscide;
- **corpo in fisica**: il moscerino cammina in MuJoCo con i generatori di passo di NeuroMechFly;
- **laboratorio**: un motore con un solo proprietario alla volta, controller, worker, e un server nella rete di
  casa protetto da token, con QR per aprirlo dal telefono;
- **archivio immutabile** degli esperimenti: catalogo SQLite, telemetria MCAP, tabelle Parquet, controllo
  d'integrità, niente cancellazioni automatiche;
- **interfaccia 3D** nel browser per vedere il moscerino e comandarlo.

## Come funziona
```
prove/01-zucchero.py ─► Brian2 (CPU) ─► attività dei neuroni ─► grafico
prove/02-cammina.py  ─► flygym / MuJoCo ─► il corpo che cammina

telefono ─► lab/frontend (React + three.js) ─► WebSocket + token
                                                   │
                                  lab/backend: server ─► controller ─► engine ─► worker
                                                   │
                                           archive: SQLite · MCAP · Parquet
```

## Struttura
| Percorso | Cosa contiene |
|---|---|
| `prove/01-zucchero.py` | il cervello: 5 prove da 1 s in 76 s su CPU (circa 10 GB di RAM) |
| `prove/02-cammina.py` | il corpo che cammina |
| `lab/backend/fruitfly_lab/` | `engine`, `controller`, `worker`, `service`, `protocol`, `server` |
| `lab/backend/fruitfly_lab/archive/` | catalogo, registrazione, archivio a oggetti, pacchetti, replay |
| `lab/backend/tests/` | test di engine, controller, server e archivio |
| `lab/frontend/` | interfaccia 3D (Vite, React 19, three.js) |
| `docs/connectome/` | piano e adattatore per collegare il connectome al comportamento, con test |
| `docs/superpowers/` | specifiche e piani fase per fase |
| `DESIGN.md`, `PRODUCT.md` | scelte di design e di prodotto del laboratorio |
| `LEGGIMI.md` | il diario dei primi esperimenti |

## Come si avvia
```
# laboratorio
cd lab/backend
pip install -e .
fruitfly-lab            # link, QR e archivio in lab/data, da qualunque cartella

cd lab/frontend
npm install
npm run dev
```
Per le prove del cervello serve il modello di Shiu et al. 2024 (*Nature*),
[philshiu/Drosophila_brain_model](https://github.com/philshiu/Drosophila_brain_model), scaricato a parte.
Il corpo viene da [NeuroMechFly / flygym](https://github.com/NeLy-EPFL/flygym) dell'EPFL.

## Stato
- fase 1, motore: fatta;
- fase 2, server nella rete di casa e interfaccia 3D: fatta;
- fase 3, archivio immutabile: scritta con i test;
- avvio del server sistemato: indirizzo LAN giusto, cartelle fisse (`--data-dir`, `--archive-dir`);
- da fare: prova vera da iPhone e iPad, campagne di esperimenti, connectome
  collegato ad Aphelios.

Costruito a quattro mani: Codex per le fasi 1 e 2, Claude Code per il resto.

## Perché è nato
Un personaggio che si muove sempre allo stesso modo sembra finto. Un cervello vero è stocastico: risponde agli
stimoli con sequenze di attività che diventano comportamento. Prima di inventare un cervello per Aphelios,
si è voluto vederne girare uno vero.

---

Parte di **[WhyEcosystem 2023-2026](https://github.com/OfficialWhyEd/WhyEcosystem-2023-2026)**: il percorso di WhyEd, producer e sound engineer che costruisce sistemi AI dirigendo gli agenti.  
Costruito da WhyEd con Claude Code
